from decimal import Decimal
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command, CommandError
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .documents import build_booking_agreement_filename, generate_booking_agreement_pdf
from .finance import (
    _refresh_invoice_and_compatibility,
    can_release_realestate_delivery,
    create_realestate_balance_checkout_session,
    ensure_standard_realestate_invoices,
    ensure_invoices_for_arrangement,
    grant_delivery_override,
    record_realestate_payment,
    revoke_delivery_override,
    void_local_realestate_invoice,
)
from .stripe_invoices import (
    create_stripe_invoice,
    mark_stripe_invoice_paid_out_of_band,
    send_stripe_invoice,
)
from .stripe_invoice_revisions import StripeInvoiceRevisionError
from .admin import RealEstateEnquiryAdmin, RealEstateInvoiceAdmin
from openeire_api.admin import custom_admin_site
from .financial_documents import (
    build_invoice_filename,
    build_receipt_filename,
    generate_cash_receipt_pdf,
    generate_invoice_pdf,
)
from .models import RealEstateEnquiry, RealEstateInvoice, RealEstatePayment, RealEstateTimelineEvent
from .payments import _stripe_metadata, calculate_realestate_deposit_amounts
from checkout.views import StripeWebhookView


class RealEstateFinanceTests(TestCase):
    def setUp(self):
        self.enquiry = RealEstateEnquiry.objects.create(
            name="Jane Agent", email="jane@example.com", phone="123",
            client_type=RealEstateEnquiry.ClientType.ESTATE_AGENT,
            property_address="Example House", county="Galway", property_type="House",
            preferred_package=RealEstateEnquiry.PreferredPackage.PRO,
            consent_to_contact=True, quoted_price=Decimal("399.00"),
        )
        calculate_realestate_deposit_amounts(self.enquiry)
        self.deposit, self.balance = ensure_standard_realestate_invoices(self.enquiry)
        self.staff = get_user_model().objects.create_user("staff", is_staff=True)

    def _expired_test_deposit_session(self, **overrides):
        values = {
            "id": "cs_test_existing",
            "livemode": False,
            "status": "expired",
            "payment_status": "unpaid",
            "currency": "eur",
            "created": int(timezone.now().timestamp()) - 86400,
            "recovered_from": None,
            "metadata": {
                "purpose": "realestate_deposit",
                "realestate_enquiry_id": str(self.enquiry.pk),
                "realestate_invoice_number": self.deposit.invoice_number,
            },
        }
        values.update(overrides)
        return values

    def _stripe_session_list(self, *sessions):
        result = Mock()
        result.auto_paging_iter.return_value = iter(sessions)
        return result

    def test_snapshot_and_invoice_amounts(self):
        self.assertEqual(self.enquiry.quoted_deposit_amount, Decimal("119.70"))
        self.assertEqual(self.enquiry.quoted_balance_due, Decimal("279.30"))
        self.assertEqual(self.deposit.total, Decimal("119.70"))
        self.assertEqual(self.balance.total, Decimal("279.30"))

    def test_numbering_and_filenames_do_not_use_people(self):
        self.assertRegex(self.deposit.invoice_number, r"^OE-RE-\d{4}-\d{4}$")
        self.assertEqual(build_invoice_filename(self.deposit), f"{self.deposit.invoice_number}.pdf")
        self.assertNotIn("jane", build_invoice_filename(self.deposit).lower())

    def test_issued_financial_snapshot_is_immutable(self):
        self.deposit.total = Decimal("1.00")
        with self.assertRaises(ValidationError):
            self.deposit.save()

    def test_unpaid_local_invoice_can_be_voided_with_audit_event(self):
        invoice, changed = void_local_realestate_invoice(
            self.deposit,
            user=self.staff,
        )

        self.assertTrue(changed)
        self.assertEqual(invoice.status, RealEstateInvoice.Status.VOID)
        event = self.enquiry.timeline_events.get(title="Invoice voided")
        self.assertEqual(event.actor_type, RealEstateTimelineEvent.ActorType.ADMIN)
        self.assertEqual(event.created_by, self.staff)
        self.assertIn(self.deposit.invoice_number, event.notes)

        _invoice, changed_again = void_local_realestate_invoice(
            invoice,
            user=self.staff,
        )
        self.assertFalse(changed_again)
        self.assertEqual(self.enquiry.timeline_events.filter(title="Invoice voided").count(), 1)

    def test_invoice_with_any_payment_record_cannot_be_voided(self):
        record_realestate_payment(
            invoice=self.deposit,
            amount="10.00",
            method=RealEstatePayment.Method.OTHER,
            paid_at=timezone.now(),
            status=RealEstatePayment.Status.FAILED,
            notes="Test failure record",
        )

        with self.assertRaisesMessage(ValidationError, "payment records"):
            void_local_realestate_invoice(self.deposit, user=self.staff)

    def test_invoice_with_stripe_reference_cannot_be_voided(self):
        self.deposit.stripe_invoice_id = "in_test_existing"
        self.deposit.save(update_fields=("stripe_invoice_id", "updated_at"))

        with self.assertRaisesMessage(ValidationError, "Stripe references"):
            void_local_realestate_invoice(self.deposit, user=self.staff)

    @patch("realestate.finance.stripe.checkout.Session.list")
    @patch("realestate.finance.stripe.checkout.Session.retrieve")
    def test_verified_expired_unpaid_test_checkout_is_cleared_before_void(
        self,
        mock_retrieve,
        mock_list,
    ):
        self.enquiry.stripe_deposit_session_id = "cs_test_existing"
        self.enquiry.deposit_payment_link = "https://checkout.stripe.com/test"
        self.enquiry.stripe_deposit_creation_key = "realestate-test-attempt"
        self.enquiry.save(
            update_fields=(
                "stripe_deposit_session_id",
                "deposit_payment_link",
                "stripe_deposit_creation_key",
                "updated_at",
            )
        )
        session = self._expired_test_deposit_session()
        session["metadata"]["realestate_invoice_number"] = None
        mock_retrieve.return_value = session
        mock_list.return_value = self._stripe_session_list()

        invoice, changed = void_local_realestate_invoice(
            self.deposit,
            user=self.staff,
        )

        self.assertTrue(changed)
        self.assertEqual(invoice.status, RealEstateInvoice.Status.VOID)
        self.enquiry.refresh_from_db()
        self.assertEqual(self.enquiry.stripe_deposit_session_id, "")
        self.assertEqual(self.enquiry.deposit_payment_link, "")
        self.assertEqual(self.enquiry.stripe_deposit_creation_key, "")
        event = self.enquiry.timeline_events.get(title="Invoice voided")
        self.assertEqual(event.stripe_session_id, "cs_test_existing")
        mock_retrieve.assert_called_once_with("cs_test_existing")
        mock_list.assert_called_once_with(limit=100, created={"gte": session["created"]})

    @patch("realestate.finance.stripe.checkout.Session.list")
    @patch("realestate.finance.stripe.checkout.Session.retrieve")
    def test_expired_unpaid_live_checkout_is_cleared_before_void(
        self,
        mock_retrieve,
        mock_list,
    ):
        self.enquiry.stripe_deposit_session_id = "cs_live_existing"
        self.enquiry.deposit_payment_link = "https://checkout.stripe.com/live"
        self.enquiry.save(
            update_fields=(
                "stripe_deposit_session_id",
                "deposit_payment_link",
                "updated_at",
            )
        )
        mock_retrieve.return_value = self._expired_test_deposit_session(
            id="cs_live_existing",
            livemode=True,
        )
        mock_list.return_value = self._stripe_session_list()

        invoice, changed = void_local_realestate_invoice(
            self.deposit,
            user=self.staff,
        )

        self.assertTrue(changed)
        self.assertEqual(invoice.status, RealEstateInvoice.Status.VOID)

    @patch("realestate.finance.stripe.checkout.Session.retrieve")
    def test_checkout_mode_and_session_identifier_mismatch_is_rejected(
        self,
        mock_retrieve,
    ):
        self.enquiry.stripe_deposit_session_id = "cs_live_existing"
        self.enquiry.deposit_payment_link = "https://checkout.stripe.com/live"
        self.enquiry.save(
            update_fields=(
                "stripe_deposit_session_id",
                "deposit_payment_link",
                "updated_at",
            )
        )
        mock_retrieve.return_value = self._expired_test_deposit_session(
            id="cs_live_existing",
            livemode=False,
        )

        with self.assertRaisesMessage(ValidationError, "environment"):
            void_local_realestate_invoice(self.deposit, user=self.staff)

    @patch("realestate.finance.stripe.checkout.Session.list")
    @patch("realestate.finance.stripe.checkout.Session.retrieve")
    def test_unsafe_or_mismatched_checkout_cannot_be_cleared_or_voided(
        self,
        mock_retrieve,
        mock_list,
    ):
        self.enquiry.stripe_deposit_session_id = "cs_test_existing"
        self.enquiry.deposit_payment_link = "https://checkout.stripe.com/test"
        self.enquiry.save(
            update_fields=(
                "stripe_deposit_session_id",
                "deposit_payment_link",
                "updated_at",
            )
        )
        mock_list.return_value = self._stripe_session_list()
        cases = {
            "live": {"livemode": True},
            "paid": {"payment_status": "paid"},
            "open": {"status": "open"},
            "recovered": {"recovered_from": "cs_test_original"},
            "wrong currency": {"currency": "usd"},
            "wrong id": {"id": "cs_test_other"},
            "missing created": {"created": None},
            "wrong purpose": {
                "metadata": {
                    "purpose": "other",
                    "realestate_enquiry_id": str(self.enquiry.pk),
                    "realestate_invoice_number": self.deposit.invoice_number,
                }
            },
            "wrong enquiry": {
                "metadata": {
                    "purpose": "realestate_deposit",
                    "realestate_enquiry_id": "999",
                    "realestate_invoice_number": self.deposit.invoice_number,
                }
            },
            "wrong invoice": {
                "metadata": {
                    "purpose": "realestate_deposit",
                    "realestate_enquiry_id": str(self.enquiry.pk),
                    "realestate_invoice_number": "OE-RE-2099-9999",
                }
            },
        }

        for label, overrides in cases.items():
            with self.subTest(label=label):
                mock_retrieve.return_value = self._expired_test_deposit_session(
                    **overrides
                )
                with self.assertRaisesMessage(ValidationError, "not an expired"):
                    void_local_realestate_invoice(self.deposit, user=self.staff)

        self.enquiry.refresh_from_db()
        self.deposit.refresh_from_db()
        self.assertEqual(self.enquiry.stripe_deposit_session_id, "cs_test_existing")
        self.assertEqual(
            self.enquiry.deposit_payment_link,
            "https://checkout.stripe.com/test",
        )
        self.assertEqual(self.deposit.status, RealEstateInvoice.Status.ISSUED)
        mock_list.assert_not_called()

    @patch("realestate.finance.stripe.checkout.Session.retrieve")
    def test_missing_checkout_session_fails_closed(self, mock_retrieve):
        self.enquiry.stripe_deposit_session_id = "cs_test_missing"
        self.enquiry.deposit_payment_link = "https://checkout.stripe.com/test"
        self.enquiry.save(
            update_fields=(
                "stripe_deposit_session_id",
                "deposit_payment_link",
                "updated_at",
            )
        )
        mock_retrieve.side_effect = RuntimeError("No such Checkout Session")

        with self.assertRaisesMessage(ValidationError, "could not be retrieved"):
            void_local_realestate_invoice(self.deposit, user=self.staff)

        self.deposit.refresh_from_db()
        self.assertEqual(self.deposit.status, RealEstateInvoice.Status.ISSUED)

    @patch("realestate.finance.stripe.checkout.Session.list")
    @patch("realestate.finance.stripe.checkout.Session.retrieve")
    def test_recovery_created_checkout_blocks_void(self, mock_retrieve, mock_list):
        self.enquiry.stripe_deposit_session_id = "cs_test_existing"
        self.enquiry.deposit_payment_link = "https://checkout.stripe.com/test"
        self.enquiry.save(
            update_fields=(
                "stripe_deposit_session_id",
                "deposit_payment_link",
                "updated_at",
            )
        )
        mock_retrieve.return_value = self._expired_test_deposit_session()
        mock_list.return_value = self._stripe_session_list(
            {"id": "cs_test_recovered", "recovered_from": "cs_test_existing"}
        )

        with self.assertRaisesMessage(ValidationError, "recovery-created"):
            void_local_realestate_invoice(self.deposit, user=self.staff)

    @patch("realestate.finance._verify_expired_deposit_session")
    def test_checkout_reference_change_during_verification_fails_closed(
        self,
        mock_verify,
    ):
        self.enquiry.stripe_deposit_session_id = "cs_test_existing"
        self.enquiry.deposit_payment_link = "https://checkout.stripe.com/test"
        self.enquiry.save(
            update_fields=(
                "stripe_deposit_session_id",
                "deposit_payment_link",
                "updated_at",
            )
        )

        def replace_saved_session(_invoice):
            RealEstateEnquiry.objects.filter(pk=self.enquiry.pk).update(
                stripe_deposit_session_id="cs_test_replacement",
                deposit_payment_link="https://checkout.stripe.com/replacement",
            )
            return "cs_test_existing"

        mock_verify.side_effect = replace_saved_session

        with self.assertRaisesMessage(ValidationError, "changed during Stripe"):
            void_local_realestate_invoice(self.deposit, user=self.staff)

        self.deposit.refresh_from_db()
        self.enquiry.refresh_from_db()
        self.assertEqual(self.deposit.status, RealEstateInvoice.Status.ISSUED)
        self.assertEqual(
            self.enquiry.stripe_deposit_session_id,
            "cs_test_replacement",
        )

    def test_invoice_admin_void_action_requires_confirmation_and_voids(self):
        model_admin = RealEstateInvoiceAdmin(RealEstateInvoice, custom_admin_site)
        request = RequestFactory().post("/admin/realestate/invoices/")
        request.user = self.staff

        confirmation = model_admin.void_local_invoices_action(
            request,
            RealEstateInvoice.objects.filter(pk=self.deposit.pk),
        )

        self.assertEqual(
            confirmation.template_name,
            "admin/realestate/void_local_invoices.html",
        )
        self.deposit.refresh_from_db()
        self.assertEqual(self.deposit.status, RealEstateInvoice.Status.ISSUED)

        confirmed_request = RequestFactory().post(
            "/admin/realestate/invoices/",
            {"confirm_void_local_invoices": "1"},
        )
        confirmed_request.user = self.staff
        model_admin.message_user = Mock()

        model_admin.void_local_invoices_action(
            confirmed_request,
            RealEstateInvoice.objects.filter(pk=self.deposit.pk),
        )

        self.deposit.refresh_from_db()
        self.assertEqual(self.deposit.status, RealEstateInvoice.Status.VOID)
        model_admin.message_user.assert_called_once()

    @patch(
        "realestate.admin.void_local_realestate_invoice",
        side_effect=ValidationError("Stripe verification failed."),
    )
    def test_invoice_admin_formats_void_validation_error_cleanly(self, _mock_void):
        model_admin = RealEstateInvoiceAdmin(RealEstateInvoice, custom_admin_site)
        request = RequestFactory().post(
            "/admin/realestate/invoices/",
            {"confirm_void_local_invoices": "1"},
        )
        request.user = self.staff
        model_admin.message_user = Mock()

        model_admin.void_local_invoices_action(
            request,
            RealEstateInvoice.objects.filter(pk=self.deposit.pk),
        )

        message = model_admin.message_user.call_args.args[1]
        self.assertEqual(
            message,
            f"{self.deposit.invoice_number}: Stripe verification failed.",
        )
        self.assertNotIn("['", message)

    def test_partial_cash_and_bank_balance_and_overpayment(self):
        cash, _ = record_realestate_payment(
            invoice=self.deposit, amount="50", method=RealEstatePayment.Method.CASH,
            paid_at=timezone.now(), recorded_by=self.staff,
            external_reference="Jane Agent", notes="Cash received in studio.",
        )
        self.deposit.refresh_from_db()
        self.assertEqual(self.deposit.status, RealEstateInvoice.Status.PARTIALLY_PAID)
        self.assertRegex(cash.cash_receipt_number, r"^OE-RC-\d{4}-\d{4}$")
        self.assertEqual(build_receipt_filename(cash), f"{cash.cash_receipt_number}.pdf")
        record_realestate_payment(
            invoice=self.deposit, amount="69.70", method=RealEstatePayment.Method.BANK_TRANSFER,
            paid_at=timezone.now(), recorded_by=self.staff, external_reference="BANK-1",
            bank_lodgement_reference="LODGE-1", notes="Transfer received.",
        )
        self.deposit.refresh_from_db()
        self.enquiry.refresh_from_db()
        self.assertEqual(self.deposit.status, RealEstateInvoice.Status.PAID)
        self.assertTrue(self.enquiry.deposit_paid)
        with self.assertRaises(ValidationError):
            record_realestate_payment(
                invoice=self.deposit, amount="0.01", method=RealEstatePayment.Method.OTHER,
                paid_at=timezone.now(), recorded_by=self.staff, external_reference="extra", notes="extra",
            )

    def test_failed_and_refunded_payments_do_not_count(self):
        for status in (RealEstatePayment.Status.FAILED, RealEstatePayment.Status.REFUNDED):
            record_realestate_payment(
                invoice=self.deposit, amount="10", method=RealEstatePayment.Method.OTHER,
                paid_at=timezone.now(), status=status,
            )
        self.assertEqual(self.deposit.amount_paid, Decimal("0.00"))

    def test_delivery_requires_full_payment(self):
        record_realestate_payment(
            invoice=self.deposit, amount=self.deposit.total,
            method=RealEstatePayment.Method.CASH, paid_at=timezone.now(),
            recorded_by=self.staff, external_reference="Jane", notes="deposit",
        )
        self.assertFalse(can_release_realestate_delivery(self.enquiry))
        record_realestate_payment(
            invoice=self.balance, amount=self.balance.total,
            method=RealEstatePayment.Method.BANK_TRANSFER, paid_at=timezone.now(),
            recorded_by=self.staff, external_reference="Jane", notes="balance",
        )
        self.assertTrue(can_release_realestate_delivery(self.enquiry))

    def test_delivery_ready_timeline_event_is_recorded_once(self):
        record_realestate_payment(
            invoice=self.deposit, amount=self.deposit.total,
            method=RealEstatePayment.Method.CASH, paid_at=timezone.now(),
            recorded_by=self.staff, external_reference="Jane", notes="deposit",
        )
        record_realestate_payment(
            invoice=self.balance, amount=self.balance.total,
            method=RealEstatePayment.Method.BANK_TRANSFER, paid_at=timezone.now(),
            recorded_by=self.staff, external_reference="Jane", notes="balance",
        )
        _refresh_invoice_and_compatibility(
            self.balance,
            paid_at=timezone.now(),
            actor=self.staff,
        )

        self.assertEqual(
            self.enquiry.timeline_events.filter(
                event_type=RealEstateTimelineEvent.EventType.DELIVERY_READY,
            ).count(),
            1,
        )
        self.assertEqual(
            self.enquiry.timeline_events.filter(
                event_type=RealEstateTimelineEvent.EventType.INVOICE_PAID,
                notes__contains=self.balance.invoice_number,
            ).count(),
            1,
        )

    def test_override_requires_staff_reason_and_can_be_revoked(self):
        with self.assertRaises(ValidationError):
            grant_delivery_override(self.enquiry, user=self.staff, reason="")
        with self.assertRaises(PermissionDenied):
            grant_delivery_override(self.enquiry, user=None, reason="Emergency release")
        override = grant_delivery_override(self.enquiry, user=self.staff, reason="Approved credit terms")
        self.assertTrue(can_release_realestate_delivery(self.enquiry))
        revoke_delivery_override(override, user=self.staff, reason="Credit approval withdrawn")
        self.assertFalse(can_release_realestate_delivery(self.enquiry))

    @patch("realestate.finance.stripe.checkout.Session.create")
    def test_balance_checkout_is_minimal_and_exact(self, create):
        create.return_value = {"id": "cs_balance", "url": "https://checkout.test/balance"}
        create_realestate_balance_checkout_session(self.enquiry)
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs["line_items"][0]["price_data"]["unit_amount"], 27930)
        self.assertEqual(kwargs["metadata"]["purpose"], "realestate_balance")
        self.assertNotIn("client_name", kwargs["metadata"])
        self.assertNotIn("property_address", kwargs["metadata"])

    def test_private_documents_and_pdf_metadata_follow_identity_policy(self):
        invoice_pdf = generate_invoice_pdf(self.deposit)
        raw_invoice = invoice_pdf.decode("latin-1")
        self.assertIn("Open", raw_invoice)
        self.assertNotIn("Gerry", raw_invoice)
        self.assertIn("VAT not applicable", raw_invoice)
        agreement_pdf = generate_booking_agreement_pdf(self.enquiry)
        self.assertTrue(agreement_pdf.startswith(b"%PDF"))
        self.assertNotIn("Gerry", build_booking_agreement_filename(self.enquiry))

    def test_cash_receipt_pdf(self):
        payment, _ = record_realestate_payment(
            invoice=self.deposit, amount="50", method=RealEstatePayment.Method.CASH,
            paid_at=timezone.now(), recorded_by=self.staff,
            external_reference="Jane", notes="cash",
        )
        text = generate_cash_receipt_pdf(payment).decode("latin-1")
        self.assertIn(payment.cash_receipt_number, text)
        self.assertNotIn("Gerry", text)

    def test_public_stripe_metadata_has_no_personal_data(self):
        metadata = _stripe_metadata(self.enquiry, self.deposit)
        self.assertNotIn("client_name", metadata)
        self.assertNotIn("property_address", metadata)
        self.assertEqual(metadata["brand"], "OpenÉire Studios")

    def test_customer_email_templates_do_not_expose_signatory(self):
        context = {
            "client_name": "Jane", "business_display_name": "OpenÉire Studios",
            "delivery_link": "", "review_link": "", "booking_agreement_link": "",
            "deposit_payment_link": "", "quote_total": "€399.00", "deposit_amount": "€119.70",
            "balance_due": "€279.30", "vat_registered": False,
        }
        for template in ("confirmation", "quote", "deposit_request", "delivery"):
            for suffix in ("html", "txt"):
                rendered = render_to_string(f"emails/real_estate/{template}.{suffix}", context)
                self.assertNotIn("Gerry", rendered)
                self.assertNotIn("Deely", rendered)

    def test_public_api_response_has_no_signatory(self):
        response = self.client.post("/api/real-estate/enquiries/", data={
            "name": "Public Client", "email": "public@example.com", "phone": "123",
            "client_type": "private_seller", "property_address": "Public House",
            "county": "Galway", "property_type": "House", "preferred_package": "pro",
            "consent_to_contact": True,
        }, content_type="application/json")
        self.assertNotIn("Gerry", response.content.decode())
        self.assertNotIn("Deely", response.content.decode())

    def test_duplicate_checkout_session_is_idempotent_across_events(self):
        self.enquiry.stripe_deposit_session_id = "cs_deposit"
        self.enquiry.save(update_fields=("stripe_deposit_session_id",))
        session = {
            "id": "cs_deposit", "payment_status": "paid", "payment_intent": "pi_deposit",
            "amount_total": 11970, "currency": "eur",
            "metadata": {"purpose": "realestate_deposit", "realestate_enquiry_id": str(self.enquiry.pk), "realestate_invoice_number": self.deposit.invoice_number},
        }
        view = StripeWebhookView()
        self.assertTrue(view._handle_realestate_deposit_payment(session))
        self.assertTrue(view._handle_realestate_deposit_payment(session))
        self.assertEqual(RealEstatePayment.objects.filter(stripe_checkout_session_id="cs_deposit").count(), 1)

    def test_balance_webhook_and_currency_validation(self):
        self.balance.stripe_checkout_session_id = "cs_balance"
        self.balance.save(update_fields=("stripe_checkout_session_id", "updated_at"))
        session = {
            "id": "cs_balance", "payment_status": "paid", "payment_intent": "pi_balance",
            "amount_total": 27930, "currency": "eur",
            "metadata": {"purpose": "realestate_balance", "realestate_enquiry_id": str(self.enquiry.pk), "realestate_invoice_number": self.balance.invoice_number},
        }
        self.assertTrue(StripeWebhookView()._handle_realestate_deposit_payment(session))
        self.balance.refresh_from_db()
        self.assertEqual(self.balance.status, RealEstateInvoice.Status.PAID)
        other = RealEstateEnquiry.objects.create(
            name="Other", email="other@example.com", phone="1", client_type="private_seller",
            property_address="Other", county="Galway", property_type="House",
            preferred_package="pro", consent_to_contact=True, quoted_price=Decimal("399.00"),
        )
        calculate_realestate_deposit_amounts(other)
        other_deposit, _ = ensure_standard_realestate_invoices(other)
        other.stripe_deposit_session_id = "cs_currency"
        other.save(update_fields=("stripe_deposit_session_id",))
        session.update({
            "id": "cs_currency", "payment_intent": "pi_currency", "currency": "usd", "amount_total": 11970,
            "metadata": {"purpose": "realestate_deposit", "realestate_enquiry_id": str(other.pk), "realestate_invoice_number": other_deposit.invoice_number},
        })
        with self.assertRaises(RuntimeError):
            StripeWebhookView()._handle_realestate_deposit_payment(session)

    def test_admin_summary_shows_partial_balance_and_delivery_locked(self):
        record_realestate_payment(
            invoice=self.deposit, amount=self.deposit.total, method=RealEstatePayment.Method.CASH,
            paid_at=timezone.now(), recorded_by=self.staff, external_reference="deposit", notes="deposit",
        )
        record_realestate_payment(
            invoice=self.balance, amount="200", method=RealEstatePayment.Method.BANK_TRANSFER,
            paid_at=timezone.now(), recorded_by=self.staff, external_reference="balance", notes="partial",
        )
        summary = str(RealEstateEnquiryAdmin(RealEstateEnquiry, custom_admin_site).financial_summary(self.enquiry))
        self.assertIn("Paid:</strong> EUR 319.70", summary)
        self.assertIn("Outstanding:</strong> EUR 79.30", summary)
        self.assertIn("Delivery:</strong> Locked", summary)

    @patch("realestate.stripe_invoices.stripe.InvoiceItem.create")
    @patch("realestate.stripe_invoices.stripe.Invoice.finalize_invoice")
    @patch("realestate.stripe_invoices.stripe.Invoice.create")
    @patch("realestate.stripe_invoices.stripe.Customer.create")
    def test_stripe_customer_is_reused_for_deposit_and_balance(
        self, customer_create, invoice_create, finalize, _item_create
    ):
        customer_create.return_value = {"id": "cus_shared"}
        invoice_create.side_effect = ({"id": "in_dep"}, {"id": "in_bal"})
        finalize.side_effect = (
            {"id": "in_dep", "status": "open", "created": 1784650000},
            {"id": "in_bal", "status": "open", "created": 1784650001},
        )
        create_stripe_invoice(self.deposit)
        create_stripe_invoice(self.balance)
        self.assertEqual(customer_create.call_count, 1)
        self.enquiry.refresh_from_db()
        self.assertEqual(self.enquiry.stripe_customer_id, "cus_shared")

    def test_ambiguous_legacy_record_is_reported_not_paid(self):
        self.enquiry.deposit_paid = True
        self.enquiry.deposit_paid_at = None
        self.enquiry.stripe_deposit_session_id = "cs_legacy"
        self.enquiry.save(update_fields=("deposit_paid", "deposit_paid_at", "stripe_deposit_session_id"))
        output = BytesIO()
        from io import StringIO
        text_output = StringIO()
        call_command("reconcile_realestate_deposits", stdout=text_output)
        self.assertIn("MANUAL REVIEW", text_output.getvalue())
        self.assertFalse(RealEstatePayment.objects.exists())

    def test_public_and_technical_production_files_have_no_personal_name(self):
        root = Path(__file__).resolve().parents[1]
        files = [
            root / "realestate" / "views.py",
            root / "realestate" / "serializers.py",
            root / "realestate" / "payments.py",
            root / "realestate" / "finance.py",
            root / "checkout" / "views.py",
        ]
        files.extend((root / "templates" / "emails").rglob("*.*"))
        for path in files:
            text = path.read_text(encoding="utf-8").lower()
            for forbidden in ("gerry", "gerard", "deely"):
                self.assertNotIn(forbidden, text, str(path))


class RealEstateLedgerMigrationTests(TransactionTestCase):
    migrate_from = [("realestate", "0007_realestate_pricing_snapshots")]
    migrate_to = [("realestate", "0009_backfill_realestate_invoices")]

    def test_backfill_creates_invoices_but_not_ambiguous_payment(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        Enquiry = old_apps.get_model("realestate", "RealEstateEnquiry")
        enquiry = Enquiry.objects.create(
            name="Legacy", email="legacy@example.com", phone="1",
            client_type="private_seller", property_address="Legacy House", county="Galway",
            property_type="House", preferred_package="pro", consent_to_contact=True,
            quoted_price=Decimal("399.00"), pricing_snapshot_version=1,
            price_input_is_gross=True, vat_registered_at_quote=False,
            quoted_vat_rate=Decimal("0"), quoted_subtotal=Decimal("399.00"),
            quoted_vat_amount=Decimal("0"), quoted_total=Decimal("399.00"),
            quoted_deposit_amount=Decimal("119.70"), quoted_balance_due=Decimal("279.30"),
            deposit_paid=True, stripe_deposit_session_id="cs_ambiguous",
        )
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        apps = executor.loader.project_state(self.migrate_to).apps
        Invoice = apps.get_model("realestate", "RealEstateInvoice")
        Payment = apps.get_model("realestate", "RealEstatePayment")
        self.assertEqual(Invoice.objects.filter(enquiry_id=enquiry.pk).count(), 2)
        self.assertEqual(Payment.objects.count(), 0)

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()


@override_settings(STRIPE_SECRET_KEY="sk_test_realestate_invoices")
class FullPaymentArrangementTests(TestCase):
    def make_enquiry(self, arrangement, **overrides):
        values = {
            "name": "Kevin O'Flynn", "email": "kevin@example.com", "phone": "123",
            "client_type": "private_seller", "property_address": "Confirmed Pro Shoot",
            "county": "Galway", "property_type": "House", "preferred_package": "pro",
            "consent_to_contact": True, "quoted_price": Decimal("399.00"),
            "payment_arrangement": arrangement, "shoot_date": "2026-07-21",
            "expected_payment_method": "cash",
        }
        values.update(overrides)
        enquiry = RealEstateEnquiry.objects.create(**values)
        calculate_realestate_deposit_amounts(enquiry)
        return enquiry

    def stripe_invoice_payload(self, invoice, stripe_id, *, status="open", **overrides):
        values = {
            "id": stripe_id,
            "number": f"STRIPE-{stripe_id}",
            "status": status,
            "currency": invoice.currency.lower(),
            "total": int(invoice.total * Decimal("100")),
            "amount_due": int(invoice.total * Decimal("100")),
            "livemode": False,
            "created": int(timezone.now().timestamp()),
            "status_transitions": {"finalized_at": int(timezone.now().timestamp())},
            "hosted_invoice_url": f"https://invoice.stripe.test/{stripe_id}",
            "invoice_pdf": f"https://invoice.stripe.test/{stripe_id}.pdf",
            "metadata": {
                "realestate_invoice_number": invoice.invoice_number,
                "realestate_enquiry_id": str(invoice.enquiry_id),
                "payment_purpose": f"realestate_{invoice.invoice_type}",
            },
            "latest_revision": None,
            "from_invoice": None,
        }
        values.update(overrides)
        return values

    def test_full_upfront_creates_one_full_invoice(self):
        enquiry = self.make_enquiry(RealEstateEnquiry.PaymentArrangement.FULL_UPFRONT)
        invoices = ensure_invoices_for_arrangement(enquiry)
        self.assertEqual(len(invoices), 1)
        self.assertEqual(invoices[0].invoice_type, RealEstateInvoice.InvoiceType.FULL)
        self.assertEqual(invoices[0].total, Decimal("399.00"))
        self.assertFalse(enquiry.invoices.filter(invoice_type="deposit").exists())
        self.assertFalse(enquiry.invoices.filter(invoice_type="balance").exists())

    def test_full_on_shoot_day_due_date_and_unpaid_booking_lock(self):
        enquiry = self.make_enquiry(RealEstateEnquiry.PaymentArrangement.FULL_ON_SHOOT_DAY)
        enquiry.refresh_from_db()
        self.assertEqual(enquiry.payment_due_date.isoformat(), "2026-07-21")
        invoice = ensure_invoices_for_arrangement(enquiry)[0]
        self.assertEqual(invoice.due_at.date().isoformat(), "2026-07-21")
        self.assertEqual(invoice.subtotal, Decimal("399.00"))
        enquiry.status = RealEstateEnquiry.Status.BOOKED
        enquiry.save(update_fields=("status",))
        self.assertFalse(can_release_realestate_delivery(enquiry))

    def test_full_cash_payment_unlocks_delivery_and_creates_one_receipt(self):
        enquiry = self.make_enquiry(RealEstateEnquiry.PaymentArrangement.FULL_ON_SHOOT_DAY)
        invoice = ensure_invoices_for_arrangement(enquiry)[0]
        staff = get_user_model().objects.create_user("cashier", is_staff=True)
        payment, _ = record_realestate_payment(
            invoice=invoice, amount="399", method=RealEstatePayment.Method.CASH,
            paid_at=timezone.now(), recorded_by=staff,
            external_reference="Kevin O'Flynn", notes="Paid on shoot day.",
        )
        self.assertRegex(payment.cash_receipt_number, r"^OE-RC-")
        self.assertEqual(RealEstatePayment.objects.count(), 1)
        self.assertTrue(can_release_realestate_delivery(enquiry))

    def test_switching_arrangement_after_invoice_is_blocked(self):
        enquiry = self.make_enquiry(RealEstateEnquiry.PaymentArrangement.FULL_UPFRONT)
        ensure_invoices_for_arrangement(enquiry)
        enquiry.payment_arrangement = RealEstateEnquiry.PaymentArrangement.DEPOSIT_THEN_BALANCE
        with self.assertRaises(ValidationError):
            enquiry.save()

    @patch("realestate.stripe_invoices.stripe.InvoiceItem.create")
    @patch("realestate.stripe_invoices.stripe.Invoice.send_invoice")
    @patch("realestate.stripe_invoices.stripe.Invoice.finalize_invoice")
    @patch("realestate.stripe_invoices.stripe.Invoice.create")
    @patch("realestate.stripe_invoices.stripe.Customer.create")
    def test_stripe_full_invoice_customer_creation_content_and_no_duplicate(
        self, customer_create, invoice_create, finalize, send_invoice, item_create
    ):
        enquiry = self.make_enquiry(RealEstateEnquiry.PaymentArrangement.FULL_ON_SHOOT_DAY)
        invoice = ensure_invoices_for_arrangement(enquiry)[0]
        customer_create.return_value = {"id": "cus_kevin"}
        invoice_create.return_value = {"id": "in_full"}
        finalize.return_value = {
            "id": "in_full", "number": "STRIPE-001", "status": "open",
            "hosted_invoice_url": "https://invoice.stripe.test/full",
            "invoice_pdf": "https://invoice.stripe.test/full.pdf", "created": 1784650000,
        }
        local, created = create_stripe_invoice(invoice)
        self.assertTrue(created)
        self.assertEqual(local.stripe_invoice_id, "in_full")
        self.assertEqual(item_create.call_args.kwargs["amount"], 39900)
        create_kwargs = invoice_create.call_args.kwargs
        self.assertEqual(create_kwargs["collection_method"], "send_invoice")
        self.assertFalse(create_kwargs["automatic_tax"]["enabled"])
        self.assertNotIn("Gerry", str(create_kwargs))
        self.assertNotIn("property_address", create_kwargs["metadata"])
        _, created_again = create_stripe_invoice(local)
        self.assertFalse(created_again)
        self.assertEqual(invoice_create.call_count, 1)

    @patch("realestate.stripe_invoices.stripe.Invoice.pay")
    @patch("realestate.stripe_invoice_revisions.stripe.Invoice.retrieve")
    def test_out_of_band_cash_settlement_and_paid_webhook_do_not_duplicate(self, retrieve, pay):
        enquiry = self.make_enquiry(RealEstateEnquiry.PaymentArrangement.FULL_ON_SHOOT_DAY)
        invoice = ensure_invoices_for_arrangement(enquiry)[0]
        invoice.stripe_invoice_id = "in_cash"
        invoice.save(update_fields=("stripe_invoice_id", "updated_at"))
        staff = get_user_model().objects.create_user("manager", is_staff=True)
        record_realestate_payment(
            invoice=invoice, amount="399", method=RealEstatePayment.Method.CASH,
            paid_at=timezone.now(), recorded_by=staff, external_reference="Kevin", notes="cash",
        )
        invoice.refresh_from_db()
        retrieve.return_value = self.stripe_invoice_payload(invoice, "in_cash")
        mark_stripe_invoice_paid_out_of_band(invoice, user=staff)
        pay.assert_called_once_with("in_cash", paid_out_of_band=True)
        payload = self.stripe_invoice_payload(
            invoice, "in_cash", status="paid", amount_paid=39900
        )
        self.assertTrue(StripeWebhookView()._handle_realestate_invoice_event("invoice.paid", payload))
        self.assertEqual(RealEstatePayment.objects.count(), 1)

    def test_invoice_paid_card_webhook_records_full_payment(self):
        enquiry = self.make_enquiry(RealEstateEnquiry.PaymentArrangement.FULL_UPFRONT)
        invoice = ensure_invoices_for_arrangement(enquiry)[0]
        invoice.stripe_invoice_id = "in_card"
        invoice.save(update_fields=("stripe_invoice_id", "updated_at"))
        payload = self.stripe_invoice_payload(
            invoice, "in_card", status="paid", amount_paid=39900,
            payment_intent="pi_card", charge="ch_card",
        )
        self.assertTrue(StripeWebhookView()._handle_realestate_invoice_event("invoice.paid", payload))
        payment = RealEstatePayment.objects.get()
        self.assertEqual(payment.method, RealEstatePayment.Method.STRIPE_INVOICE)
        self.assertEqual(payment.stripe_payment_intent_id, "pi_card")
        self.assertTrue(can_release_realestate_delivery(enquiry))

    def test_invoice_failure_void_and_admin_summary(self):
        enquiry = self.make_enquiry(RealEstateEnquiry.PaymentArrangement.FULL_ON_SHOOT_DAY)
        invoice = ensure_invoices_for_arrangement(enquiry)[0]
        invoice.stripe_invoice_id = "in_state"
        invoice.save(update_fields=("stripe_invoice_id", "updated_at"))
        view = StripeWebhookView()
        base = self.stripe_invoice_payload(invoice, "in_state")
        self.assertTrue(view._handle_realestate_invoice_event("invoice.payment_failed", {**base, "status": "open"}))
        invoice.refresh_from_db()
        self.assertEqual(invoice.stripe_invoice_status, "open")
        self.assertTrue(view._handle_realestate_invoice_event("invoice.voided", {**base, "status": "void"}))
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, RealEstateInvoice.Status.VOID)
        summary = str(RealEstateEnquiryAdmin(RealEstateEnquiry, custom_admin_site).financial_summary(enquiry))
        self.assertIn("EUR 399.00", summary)
        self.assertIn("Locked", summary)


@override_settings(STRIPE_SECRET_KEY="sk_test_revision_tests")
class StripeInvoiceRevisionTests(TestCase):
    def setUp(self):
        self.enquiry = RealEstateEnquiry.objects.create(
            name="Revision Client",
            email="revision@example.com",
            phone="123",
            client_type=RealEstateEnquiry.ClientType.PRIVATE_SELLER,
            property_address="Revision House",
            county="Galway",
            property_type="House",
            preferred_package=RealEstateEnquiry.PreferredPackage.PRO,
            consent_to_contact=True,
            quoted_price=Decimal("399.00"),
            payment_arrangement=RealEstateEnquiry.PaymentArrangement.FULL_UPFRONT,
        )
        calculate_realestate_deposit_amounts(self.enquiry)
        self.invoice = ensure_invoices_for_arrangement(self.enquiry)[0]
        self.invoice.stripe_invoice_id = "in_original"
        self.invoice.stripe_invoice_number = "STRIPE-OLD"
        self.invoice.stripe_invoice_status = "open"
        self.invoice.save(update_fields=(
            "stripe_invoice_id",
            "stripe_invoice_number",
            "stripe_invoice_status",
            "updated_at",
        ))
        self.view = StripeWebhookView()

    def payload(self, stripe_id, *, status="open", **overrides):
        now = int(timezone.now().timestamp())
        values = {
            "id": stripe_id,
            "number": f"STRIPE-{stripe_id}",
            "status": status,
            "currency": "eur",
            "total": 39900,
            "amount_due": 39900,
            "amount_paid": 0,
            "livemode": False,
            "created": now,
            "status_transitions": {"finalized_at": now},
            "hosted_invoice_url": f"https://invoice.stripe.test/{stripe_id}",
            "invoice_pdf": f"https://invoice.stripe.test/{stripe_id}.pdf",
            "metadata": {
                "realestate_invoice_number": self.invoice.invoice_number,
                "realestate_enquiry_id": str(self.enquiry.pk),
                "payment_purpose": "realestate_full",
            },
            "latest_revision": None,
            "from_invoice": None,
        }
        values.update(overrides)
        return values

    def revision_pair(self, *, child_id="in_revision", child_status="open"):
        parent = self.payload(
            "in_original", status="void", latest_revision=child_id
        )
        child = self.payload(
            child_id,
            status=child_status,
            from_invoice={"action": "revision", "invoice": "in_original"},
        )
        return parent, child

    @patch("realestate.stripe_invoice_revisions.stripe.Invoice.retrieve")
    def test_valid_direct_revision_updates_all_external_fields(self, retrieve):
        parent, child = self.revision_pair()
        retrieve.return_value = parent

        self.assertTrue(self.view._handle_realestate_invoice_event("invoice.finalized", child))

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, RealEstateInvoice.Status.ISSUED)
        self.assertEqual(self.invoice.stripe_invoice_id, "in_revision")
        self.assertEqual(self.invoice.stripe_invoice_number, "STRIPE-in_revision")
        self.assertEqual(
            self.invoice.stripe_hosted_invoice_url,
            "https://invoice.stripe.test/in_revision",
        )
        self.assertEqual(
            self.invoice.stripe_invoice_pdf_url,
            "https://invoice.stripe.test/in_revision.pdf",
        )
        self.assertEqual(self.invoice.stripe_invoice_status, "open")
        self.assertIsNotNone(self.invoice.stripe_invoice_created_at)
        self.assertIsNotNone(self.invoice.stripe_invoice_finalized_at)
        self.assertEqual(
            self.enquiry.timeline_events.filter(
                title="Stripe invoice revision reconciled"
            ).count(),
            1,
        )

    @override_settings(STRIPE_SECRET_KEY="sk_live_revision_tests")
    @patch("realestate.stripe_invoice_revisions.stripe.Invoice.retrieve")
    def test_valid_live_mode_revision_is_accepted(self, retrieve):
        parent, child = self.revision_pair()
        parent["livemode"] = True
        child["livemode"] = True
        retrieve.return_value = parent

        self.view._handle_realestate_invoice_event("invoice.finalized", child)

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.stripe_invoice_id, "in_revision")

    @patch("realestate.stripe_invoice_revisions.stripe.Invoice.retrieve")
    def test_multi_step_revision_chain_is_accepted(self, retrieve):
        original = self.payload("in_original", status="void", latest_revision="in_revision_1")
        revision_1 = self.payload(
            "in_revision_1",
            status="void",
            latest_revision="in_revision_2",
            from_invoice={"action": "revision", "invoice": "in_original"},
        )
        revision_2 = self.payload(
            "in_revision_2",
            from_invoice={"action": "revision", "invoice": "in_revision_1"},
        )
        retrieve.side_effect = lambda stripe_id: {
            "in_original": original,
            "in_revision_1": revision_1,
        }[stripe_id]

        self.view._handle_realestate_invoice_event("invoice.finalized", revision_2)

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.stripe_invoice_id, "in_revision_2")

    @patch("realestate.stripe_invoice_revisions.stripe.Invoice.retrieve")
    def test_original_void_before_revised_finalized_does_not_void_local(self, retrieve):
        parent, child = self.revision_pair()
        retrieve.side_effect = lambda stripe_id: child if stripe_id == "in_revision" else parent

        self.view._handle_realestate_invoice_event("invoice.voided", parent)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, RealEstateInvoice.Status.ISSUED)
        self.assertEqual(self.invoice.stripe_invoice_id, "in_revision")

        self.view._handle_realestate_invoice_event("invoice.finalized", child)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, RealEstateInvoice.Status.ISSUED)

    @patch("realestate.stripe_invoice_revisions.stripe.Invoice.retrieve")
    def test_revised_finalized_before_delayed_original_void_is_safe(self, retrieve):
        parent, child = self.revision_pair()
        retrieve.side_effect = lambda stripe_id: parent if stripe_id == "in_original" else child
        self.view._handle_realestate_invoice_event("invoice.finalized", child)

        self.view._handle_realestate_invoice_event("invoice.voided", parent)

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, RealEstateInvoice.Status.ISSUED)
        self.assertEqual(self.invoice.stripe_invoice_id, "in_revision")
        self.assertEqual(self.invoice.stripe_invoice_status, "open")

    @patch("realestate.stripe_invoice_revisions.stripe.Invoice.retrieve")
    def test_revised_sent_event_updates_current_revision(self, retrieve):
        parent, child = self.revision_pair()
        retrieve.return_value = parent

        self.view._handle_realestate_invoice_event("invoice.sent", child)

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.stripe_invoice_id, "in_revision")
        self.assertEqual(self.invoice.stripe_invoice_status, "open")

    @patch("realestate.stripe_invoice_revisions.stripe.Invoice.retrieve")
    def test_revised_paid_event_is_idempotent(self, retrieve):
        parent, child = self.revision_pair(child_status="paid")
        child.update(amount_paid=39900, payment_intent="pi_revision", charge="ch_revision")
        retrieve.return_value = parent

        self.view._handle_realestate_invoice_event("invoice.paid", child)
        self.view._handle_realestate_invoice_event("invoice.paid", child)

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, RealEstateInvoice.Status.PAID)
        self.assertEqual(self.invoice.stripe_invoice_id, "in_revision")
        self.assertEqual(self.invoice.payments.filter(status="succeeded").count(), 1)
        payment = self.invoice.payments.get(status="succeeded")
        self.assertEqual(payment.external_reference, "in_revision")

    @patch("realestate.stripe_invoices.stripe.Invoice.send_invoice")
    @patch("realestate.stripe_invoice_revisions.stripe.Invoice.retrieve")
    def test_reminder_reconciles_and_sends_current_revision(self, retrieve, send):
        parent, child = self.revision_pair()
        retrieve.side_effect = lambda stripe_id: {
            "in_original": parent,
            "in_revision": child,
        }[stripe_id]
        send.return_value = child

        result = send_stripe_invoice(self.invoice)

        send.assert_called_once_with("in_revision")
        self.assertEqual(result.stripe_invoice_id, "in_revision")

    @patch("realestate.stripe_invoices.stripe.Invoice.send_invoice")
    @patch("realestate.stripe_invoice_revisions.stripe.Invoice.retrieve")
    def test_admin_reminder_action_sends_current_revision(self, retrieve, send):
        parent, child = self.revision_pair()
        retrieve.side_effect = lambda stripe_id: {
            "in_original": parent,
            "in_revision": child,
        }[stripe_id]
        send.return_value = child
        model_admin = RealEstateInvoiceAdmin(RealEstateInvoice, custom_admin_site)
        model_admin.message_user = Mock()
        request = RequestFactory().post("/admin/realestate/invoice/")

        model_admin.send_stripe_reminder_action(
            request,
            RealEstateInvoice.objects.filter(pk=self.invoice.pk),
        )

        send.assert_called_once_with("in_revision")

    @patch("realestate.stripe_invoice_revisions.stripe.Invoice.retrieve")
    def test_latest_revision_may_jump_to_most_recent_descendant(self, retrieve):
        original = self.payload(
            "in_original", status="void", latest_revision="in_revision_2"
        )
        revision_1 = self.payload(
            "in_revision_1",
            status="void",
            from_invoice={"action": "revision", "invoice": "in_original"},
            latest_revision="in_revision_2",
        )
        revision_2 = self.payload(
            "in_revision_2",
            from_invoice={"action": "revision", "invoice": "in_revision_1"},
        )
        retrieve.side_effect = lambda stripe_id: {
            "in_original": original,
            "in_revision_1": revision_1,
            "in_revision_2": revision_2,
        }[stripe_id]

        self.view._handle_realestate_invoice_event("invoice.voided", original)

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.stripe_invoice_id, "in_revision_2")
        self.assertEqual(self.invoice.status, RealEstateInvoice.Status.ISSUED)

    def test_ordinary_non_revision_void_still_voids_local_invoice(self):
        original = self.payload("in_original", status="void")

        self.view._handle_realestate_invoice_event("invoice.voided", original)

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, RealEstateInvoice.Status.VOID)

    @patch("realestate.stripe_invoice_revisions.stripe.Invoice.retrieve")
    def test_forged_metadata_without_revision_relationship_is_rejected(self, retrieve):
        unrelated = self.payload("in_unrelated")

        with self.assertRaisesMessage(StripeInvoiceRevisionError, "unrelated"):
            self.view._handle_realestate_invoice_event("invoice.finalized", unrelated)
        retrieve.assert_not_called()

    @patch("realestate.stripe_invoice_revisions.stripe.Invoice.retrieve")
    def test_wrong_revision_parent_is_rejected(self, retrieve):
        unrelated_parent = self.payload(
            "in_other_parent", latest_revision="in_revision"
        )
        child = self.payload(
            "in_revision",
            from_invoice={"action": "revision", "invoice": "in_other_parent"},
        )
        retrieve.return_value = unrelated_parent

        with self.assertRaisesMessage(StripeInvoiceRevisionError, "unrelated"):
            self.view._handle_realestate_invoice_event("invoice.finalized", child)

    def test_revision_security_metadata_amount_currency_and_mode_must_match(self):
        base_metadata = self.payload("in_original")["metadata"]
        cases = {
            "enquiry": {"metadata": {**base_metadata, "realestate_enquiry_id": "999"}},
            "invoice number": {"metadata": {**base_metadata, "realestate_invoice_number": "OTHER"}},
            "purpose": {"metadata": {**base_metadata, "payment_purpose": "realestate_deposit"}},
            "amount": {"total": 39899},
            "amount due": {"amount_due": 39899},
            "currency": {"currency": "usd"},
            "environment": {"livemode": True},
        }
        for label, overrides in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(StripeInvoiceRevisionError):
                    self.view._handle_realestate_invoice_event(
                        "invoice.finalized", self.payload("in_original", **overrides)
                    )

    @patch("realestate.stripe_invoice_revisions.stripe.Invoice.retrieve")
    def test_manually_voided_and_paid_local_records_are_not_reopened(self, retrieve):
        parent, child = self.revision_pair()
        retrieve.return_value = parent
        self.invoice.status = RealEstateInvoice.Status.VOID
        self.invoice.save(update_fields=("status", "updated_at"))
        self.enquiry.timeline_events.create(
            event_type=RealEstateTimelineEvent.EventType.NOTE,
            title="Invoice voided",
            notes=f"Invoice {self.invoice.invoice_number} was voided without a payment record.",
        )

        self.view._handle_realestate_invoice_event("invoice.finalized", child)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, RealEstateInvoice.Status.VOID)

        self.invoice.status = RealEstateInvoice.Status.PAID
        self.invoice.save(update_fields=("status", "updated_at"))
        child["id"] = "in_revision_2"
        child["from_invoice"] = {"action": "revision", "invoice": "in_revision"}
        current_parent = self.payload("in_revision", latest_revision="in_revision_2")
        retrieve.return_value = current_parent
        self.view._handle_realestate_invoice_event("invoice.finalized", child)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, RealEstateInvoice.Status.PAID)

    @patch("realestate.stripe_invoice_revisions.stripe.Invoice.retrieve")
    def test_verified_open_revision_restores_only_non_manual_void(self, retrieve):
        parent, child = self.revision_pair()
        retrieve.return_value = parent
        self.invoice.status = RealEstateInvoice.Status.VOID
        self.invoice.save(update_fields=("status", "updated_at"))

        self.view._handle_realestate_invoice_event("invoice.finalized", child)

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, RealEstateInvoice.Status.ISSUED)

    @patch("realestate.stripe_invoice_revisions.stripe.Invoice.retrieve")
    def test_reconciliation_command_dry_run_and_apply_are_idempotent(self, retrieve):
        parent, child = self.revision_pair()
        retrieve.side_effect = lambda stripe_id: {
            "in_original": parent,
            "in_revision": child,
        }[stripe_id]
        output = StringIO()

        call_command(
            "reconcile_realestate_stripe_invoice_revisions",
            invoice_number=self.invoice.invoice_number,
            dry_run=True,
            stdout=output,
        )
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.stripe_invoice_id, "in_original")
        self.assertIn("local_invoice_status=issued", output.getvalue())
        self.assertIn("stored_stripe_invoice_status=open", output.getvalue())
        self.assertIn("stored_stripe_invoice_id=in_original", output.getvalue())
        self.assertIn("current_stripe_invoice_id=in_revision", output.getvalue())
        self.assertIn("current_stripe_invoice_status=open", output.getvalue())

        call_command(
            "reconcile_realestate_stripe_invoice_revisions",
            invoice_number=self.invoice.invoice_number,
            apply=True,
            stdout=StringIO(),
        )
        call_command(
            "reconcile_realestate_stripe_invoice_revisions",
            invoice_number=self.invoice.invoice_number,
            apply=True,
            stdout=StringIO(),
        )
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, RealEstateInvoice.Status.ISSUED)
        self.assertEqual(self.invoice.stripe_invoice_id, "in_revision")
        self.assertEqual(
            self.enquiry.timeline_events.filter(
                title="Stripe invoice revision reconciled"
            ).count(),
            1,
        )

    @patch("realestate.stripe_invoice_revisions.stripe.Invoice.retrieve")
    def test_reconciliation_command_rejects_cycle(self, retrieve):
        original = self.payload("in_original", latest_revision="in_revision")
        revision = self.payload(
            "in_revision",
            latest_revision="in_original",
            from_invoice={"action": "revision", "invoice": "in_original"},
        )
        retrieve.side_effect = lambda stripe_id: {
            "in_original": original,
            "in_revision": revision,
        }[stripe_id]

        with self.assertRaisesMessage(CommandError, "cycle"):
            call_command(
                "reconcile_realestate_stripe_invoice_revisions",
                invoice_number=self.invoice.invoice_number,
                dry_run=True,
            )

    @patch("realestate.stripe_invoice_revisions.stripe.Invoice.retrieve")
    def test_reconciliation_command_rejects_excessive_depth(self, retrieve):
        invoices = {}
        ids = ["in_original"] + [f"in_revision_{index}" for index in range(1, 10)]
        for index, stripe_id in enumerate(ids):
            latest = ids[index + 1] if index + 1 < len(ids) else None
            from_invoice = (
                None
                if index == 0
                else {"action": "revision", "invoice": ids[index - 1]}
            )
            invoices[stripe_id] = self.payload(
                stripe_id,
                latest_revision=latest,
                from_invoice=from_invoice,
            )
        retrieve.side_effect = lambda stripe_id: invoices[stripe_id]

        with self.assertRaisesMessage(CommandError, "exceeds 8"):
            call_command(
                "reconcile_realestate_stripe_invoice_revisions",
                invoice_number=self.invoice.invoice_number,
                dry_run=True,
            )


class RealEstateAdminOperationsHubTests(TestCase):
    def setUp(self):
        self.admin_user = get_user_model().objects.create_superuser(
            "ops", "ops@example.com", "password"
        )
        self.client.force_login(self.admin_user)
        self.factory = RequestFactory()
        self.model_admin = RealEstateEnquiryAdmin(RealEstateEnquiry, custom_admin_site)
        self.model_admin.message_user = Mock()

    def make_enquiry(self, arrangement, **overrides):
        values = {
            "name": "Kevin O'Flynn",
            "email": "kevin@example.com",
            "phone": "123",
            "client_type": RealEstateEnquiry.ClientType.PRIVATE_SELLER,
            "property_address": "Confirmed Pro Shoot",
            "county": "Galway",
            "property_type": "House",
            "preferred_package": RealEstateEnquiry.PreferredPackage.PRO,
            "consent_to_contact": True,
            "quoted_price": Decimal("399.00"),
            "payment_arrangement": arrangement,
            "shoot_date": "2026-07-21",
            "expected_payment_method": RealEstateEnquiry.ExpectedPaymentMethod.CASH,
            "status": RealEstateEnquiry.Status.BOOKED,
        }
        values.update(overrides)
        enquiry = RealEstateEnquiry.objects.create(**values)
        calculate_realestate_deposit_amounts(enquiry)
        return enquiry

    def change_url(self, enquiry):
        return reverse("customadmin:realestate_realestateenquiry_change", args=(enquiry.pk,))

    def ops_url(self, enquiry, action, invoice=None):
        url = reverse(
            "customadmin:realestate_realestateenquiry_ops_action",
            args=(enquiry.pk, action),
        )
        return f"{url}?invoice={invoice.pk}" if invoice else url

    def test_operations_panel_renders_full_cash_job_state_and_buttons(self):
        enquiry = self.make_enquiry(RealEstateEnquiry.PaymentArrangement.FULL_ON_SHOOT_DAY)
        invoice = ensure_invoices_for_arrangement(enquiry)[0]
        invoice.stripe_invoice_id = "in_full"
        invoice.stripe_hosted_invoice_url = "https://invoice.stripe.test/full"
        invoice.stripe_invoice_pdf_url = "https://invoice.stripe.test/full.pdf"
        invoice.stripe_invoice_status = "open"
        invoice.save(update_fields=(
            "stripe_invoice_id",
            "stripe_hosted_invoice_url",
            "stripe_invoice_pdf_url",
            "stripe_invoice_status",
            "updated_at",
        ))

        response = self.client.get(self.change_url(enquiry))

        self.assertContains(response, "Real-estate operations hub")
        self.assertContains(response, "Recommended next step:</strong> Record full cash payment")
        self.assertContains(response, "€399.00")
        self.assertContains(response, "Outstanding")
        self.assertContains(response, "full payment required")
        self.assertContains(response, "Open Stripe hosted invoice")
        self.assertContains(response, "Download Stripe PDF")
        self.assertContains(response, "Download local invoice PDF")
        self.assertContains(response, "Record cash payment")
        self.assertContains(response, invoice.invoice_number)

    def test_deposit_workflow_buttons_do_not_show_full_cash_action(self):
        enquiry = self.make_enquiry(
            RealEstateEnquiry.PaymentArrangement.DEPOSIT_THEN_BALANCE,
            expected_payment_method=RealEstateEnquiry.ExpectedPaymentMethod.STRIPE,
            status=RealEstateEnquiry.Status.QUOTED,
        )

        response = self.client.get(self.change_url(enquiry))

        self.assertContains(response, "Recommended next step:</strong> Send deposit invoice")
        self.assertContains(response, "Send deposit invoice")
        self.assertNotContains(response, "Record full cash payment")
        self.assertNotContains(response, "Record cash payment")

    def test_local_paid_stripe_open_intermediate_state_recommends_out_of_band(self):
        enquiry = self.make_enquiry(RealEstateEnquiry.PaymentArrangement.FULL_ON_SHOOT_DAY)
        invoice = ensure_invoices_for_arrangement(enquiry)[0]
        invoice.stripe_invoice_id = "in_cash"
        invoice.stripe_invoice_status = "open"
        invoice.save(update_fields=("stripe_invoice_id", "stripe_invoice_status", "updated_at"))
        record_realestate_payment(
            invoice=invoice,
            amount="399",
            method=RealEstatePayment.Method.CASH,
            paid_at=timezone.now(),
            recorded_by=self.admin_user,
            external_reference="cash on shoot day",
            notes="Paid in full.",
        )

        response = self.client.get(self.change_url(enquiry))

        self.assertContains(response, "Paid</dt><dd>€399.00")
        self.assertContains(response, "Outstanding</dt><dd>€0.00")
        self.assertContains(response, "Recommended next step:</strong> Mark Stripe invoice paid out of band")
        self.assertContains(response, "Mark Stripe invoice paid out of band")

    def test_fully_paid_with_delivery_link_recommends_delivery_email(self):
        enquiry = self.make_enquiry(
            RealEstateEnquiry.PaymentArrangement.FULL_ON_SHOOT_DAY,
            delivery_link="https://delivery.example.com/job",
        )
        invoice = ensure_invoices_for_arrangement(enquiry)[0]
        record_realestate_payment(
            invoice=invoice,
            amount="399",
            method=RealEstatePayment.Method.CASH,
            paid_at=timezone.now(),
            recorded_by=self.admin_user,
            external_reference="cash",
            notes="Paid.",
        )

        response = self.client.get(self.change_url(enquiry))

        self.assertContains(response, "Recommended next step:</strong> Send delivery email")
        self.assertContains(response, "Send delivery email")
        self.assertContains(response, "Released")

    def test_issue_invoice_action_is_idempotent(self):
        enquiry = self.make_enquiry(RealEstateEnquiry.PaymentArrangement.FULL_ON_SHOOT_DAY)

        self.client.post(self.ops_url(enquiry, "issue-invoices"), follow=True)
        self.client.post(self.ops_url(enquiry, "issue-invoices"), follow=True)

        self.assertEqual(enquiry.invoices.count(), 1)
        invoice = enquiry.invoices.get()
        self.assertEqual(invoice.invoice_type, RealEstateInvoice.InvoiceType.FULL)

    @patch("realestate.finance.stripe.checkout.Session.create")
    def test_balance_checkout_action_does_not_record_delivery_released(self, create):
        create.return_value = {"id": "cs_balance", "url": "https://checkout.test/balance"}
        enquiry = self.make_enquiry(
            RealEstateEnquiry.PaymentArrangement.DEPOSIT_THEN_BALANCE,
            status=RealEstateEnquiry.Status.QUOTED,
        )
        request = self.factory.post("/admin/realestate/realestateenquiry/")
        request.user = self.admin_user

        self.model_admin.create_balance_checkout(
            request,
            RealEstateEnquiry.objects.filter(pk=enquiry.pk),
        )

        self.assertEqual(
            enquiry.timeline_events.filter(
                event_type=RealEstateTimelineEvent.EventType.DELIVERY_RELEASED,
            ).count(),
            0,
        )
        self.assertEqual(enquiry.invoices.count(), 2)
        balance = enquiry.invoices.get(invoice_type=RealEstateInvoice.InvoiceType.BALANCE)
        self.assertEqual(balance.stripe_checkout_session_id, "cs_balance")

    def test_record_cash_payment_action_creates_one_payment_and_receipt(self):
        enquiry = self.make_enquiry(RealEstateEnquiry.PaymentArrangement.FULL_ON_SHOOT_DAY)
        invoice = ensure_invoices_for_arrangement(enquiry)[0]

        response = self.client.post(
            self.ops_url(enquiry, "record-cash-payment", invoice),
            {
                "invoice": invoice.pk,
                "amount": "399.00",
                "received_at": timezone.now().strftime("%Y-%m-%d %H:%M:%S"),
                "method": RealEstatePayment.Method.CASH,
                "payer_reference": "Kevin cash",
                "bank_lodgement_reference": "",
                "notes": "Paid on shoot day.",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        payment = RealEstatePayment.objects.get()
        self.assertEqual(payment.amount, Decimal("399.00"))
        self.assertRegex(payment.cash_receipt_number, r"^OE-RC-")
        self.assertTrue(can_release_realestate_delivery(enquiry))

    def test_view_only_staff_cannot_post_financial_action(self):
        viewer = get_user_model().objects.create_user(
            "viewer", "viewer@example.com", "password", is_staff=True
        )
        viewer.user_permissions.add(
            Permission.objects.get(codename="view_realestateenquiry"),
            Permission.objects.get(codename="view_realestateinvoice"),
            Permission.objects.get(codename="view_realestatepayment"),
            Permission.objects.get(codename="view_realestatetimelineevent"),
        )
        enquiry = self.make_enquiry(RealEstateEnquiry.PaymentArrangement.FULL_ON_SHOOT_DAY)
        self.client.force_login(viewer)

        response = self.client.post(self.ops_url(enquiry, "issue-invoices"))

        self.assertEqual(response.status_code, 403)
        self.assertFalse(enquiry.invoices.exists())
