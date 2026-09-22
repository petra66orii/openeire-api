from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
import threading
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connections
from django.test import TestCase, TransactionTestCase, RequestFactory, override_settings, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone

from openeire_api.admin import custom_admin_site
from .admin import RealEstateEnquiryAdmin, RealEstateInvoiceAdmin
from .finance import (
    can_release_realestate_delivery, create_custom_instalment_invoice,
    get_custom_instalment_capacity, record_realestate_payment,
    void_local_realestate_invoice,
)
from .models import RealEstateEnquiry, RealEstateInvoice, RealEstatePayment
from .payments import calculate_realestate_deposit_amounts
from .stripe_invoices import create_stripe_invoice


@override_settings(VAT_REGISTERED=False, STRIPE_SECRET_KEY="sk_test_mock_only")
class CustomInstalmentTests(TestCase):
    def setUp(self):
        self.enquiry = RealEstateEnquiry.objects.create(
            name="Fixture Client", company_name="Fixture Agency", email="fixture@example.invalid",
            phone="123", property_address="Fixture property", county="Galway",
            preferred_package="premium", quoted_price=Decimal("549.00"),
            payment_arrangement="custom", custom_required_total=Decimal("599.00"),
            custom_payment_terms="EUR 250 now; EUR 349 after the second visit.",
            payment_due_date=timezone.localdate(),
        )
        calculate_realestate_deposit_amounts(self.enquiry)
        self.user = get_user_model().objects.create_superuser("instalments", "staff@example.invalid", "password")
        self.client.force_login(self.user)
        self.url = reverse("customadmin:realestate_realestateenquiry_ops_action",
                           args=(self.enquiry.pk, "create-custom-instalment"))

    def create(self, amount="250.00", **kwargs):
        return create_custom_instalment_invoice(
            enquiry=self.enquiry, amount=amount, description="Property shoot instalment",
            user=self.user, **kwargs,
        )

    def pay(self, invoice, amount):
        return record_realestate_payment(
            invoice=invoice, amount=amount, method=RealEstatePayment.Method.BANK_TRANSFER,
            paid_at=timezone.now(), recorded_by=self.user,
        )[0]

    def assert_stripe_amount(self, invoice, cents):
        with patch("realestate.stripe_invoices.stripe") as stripe:
            stripe.Customer.create.return_value = {"id": "cus_fixture"}
            stripe.Invoice.create.return_value = {"id": f"in_fixture_{invoice.pk}"}
            stripe.Invoice.finalize_invoice.return_value = {"status": "open"}
            create_stripe_invoice(invoice)
            self.assertEqual(stripe.InvoiceItem.create.call_args.kwargs["amount"], cents)

    def test_two_instalments_preserve_total_stripe_amounts_and_delivery(self):
        first = self.create()
        self.assertRegex(first.invoice_number, r"^OE-RE-\d{4}-\d{4}$")
        self.assertEqual(first.total, Decimal("250"))
        self.assertEqual(first.subtotal, Decimal("250"))
        self.assertEqual(first.vat_amount, Decimal("0"))
        self.assertEqual(first.status, RealEstateInvoice.Status.ISSUED)
        self.assertEqual(first.customer_name_snapshot, self.enquiry.name)
        self.assertEqual(first.customer_email_snapshot, self.enquiry.email)
        self.assertEqual(first.company_name_snapshot, self.enquiry.company_name)
        self.assertEqual(first.customer_phone_snapshot, self.enquiry.phone)
        self.assertEqual(first.property_reference_snapshot, self.enquiry.property_address)
        self.assertEqual(first.job_reference_snapshot, f"RE-{self.enquiry.pk}")
        self.assertEqual(first.due_at.date(), self.enquiry.payment_due_date)
        self.enquiry.refresh_from_db()
        self.assertEqual(self.enquiry.original_required_total, Decimal("599"))
        self.assertEqual(self.enquiry.quoted_total, Decimal("549"))
        self.assert_stripe_amount(first, 25000)
        self.pay(first, "250")
        self.assertEqual(self.enquiry.total_cleared_payments, Decimal("250"))
        self.assertEqual(self.enquiry.adjusted_balance_due, Decimal("349"))
        self.assertFalse(can_release_realestate_delivery(self.enquiry))
        second = self.create("349")
        self.assertNotEqual(first.invoice_number, second.invoice_number)
        self.assertEqual(second.total, Decimal("349"))
        self.assert_stripe_amount(second, 34900)
        with self.assertRaises(ValidationError):
            self.create("0.01")
        self.pay(second, "349")
        self.assertEqual(self.enquiry.total_cleared_payments, Decimal("599"))
        self.assertEqual(self.enquiry.adjusted_balance_due, Decimal("0"))
        self.assertTrue(can_release_realestate_delivery(self.enquiry))
        with self.assertRaises(ValidationError):
            self.create("0.01")
        second.total = Decimal("350")
        with self.assertRaises(ValidationError):
            second.save()

    def test_invalid_amounts_and_wrong_arrangement(self):
        for amount in ("0", "-1", "600", "1.001", "NaN", "Infinity", "bad", None):
            with self.subTest(amount=amount), self.assertRaises(ValidationError):
                self.create(amount)
        self.assertFalse(self.enquiry.invoices.exists())
        self.enquiry.payment_arrangement = "deposit_then_balance"
        self.enquiry.save()
        with self.assertRaises(ValidationError):
            self.create()

    def test_missing_custom_configuration_and_description(self):
        for fields in ({"custom_required_total": None}, {"custom_payment_terms": ""}):
            with self.subTest(fields=fields):
                # Reproduce invalid historical data without bypassing the service guard.
                RealEstateEnquiry.objects.filter(pk=self.enquiry.pk).update(**fields)
                with self.assertRaises(ValidationError):
                    self.create()
                RealEstateEnquiry.objects.filter(pk=self.enquiry.pk).update(
                    custom_required_total=Decimal("599"), custom_payment_terms="250 then 349")
        for description in ("", "x" * 256):
            with self.assertRaises(ValidationError):
                create_custom_instalment_invoice(enquiry=self.enquiry, amount="250", description=description)

    def test_unpaid_and_partial_invoices_reserve_debt_without_double_counting(self):
        invoice = self.create()
        with self.assertRaisesMessage(ValidationError, "existing unpaid invoice"):
            self.create()
        self.pay(invoice, "100")
        capacity = get_custom_instalment_capacity(self.enquiry)
        self.assertEqual(capacity["paid"], Decimal("100"))
        self.assertEqual(capacity["balance"], Decimal("499"))
        self.assertEqual(capacity["active_unpaid"], Decimal("150"))
        self.assertEqual(capacity["uninvoiced"], Decimal("349"))
        self.assertEqual(capacity["maximum"], Decimal("0"))
        with self.assertRaises(ValidationError):
            self.create("349")
        self.pay(invoice, "150")
        with self.assertRaises(ValidationError):
            self.create("349.01")
        self.assertEqual(self.create("349").total, Decimal("349"))

    def test_void_superseded_invoice_does_not_reserve_debt(self):
        invoice = self.create()
        void_local_realestate_invoice(invoice, user=self.user)
        self.assertEqual(get_custom_instalment_capacity(self.enquiry)["maximum"], Decimal("599"))
        replacement = self.create()
        replacement.supersedes = invoice
        replacement.save(update_fields=("supersedes",))
        self.assertEqual(get_custom_instalment_capacity(self.enquiry)["active_unpaid"], Decimal("250"))

    def test_draft_invoice_also_reserves_debt(self):
        invoice = self.create()
        RealEstateInvoice.objects.filter(pk=invoice.pk).update(status="draft")
        with self.assertRaises(ValidationError):
            self.create()

    def test_reversals_and_failed_payments_use_effective_paid_amount(self):
        invoice = self.create()
        payment = self.pay(invoice, "100")
        payment.reversed_amount = Decimal("40")
        payment.reversal_status = RealEstatePayment.ReversalStatus.PARTIALLY_REFUNDED
        payment.save(update_fields=("reversed_amount", "reversal_status"))
        record_realestate_payment(invoice=invoice, amount="10", method="bank_transfer",
                                 paid_at=timezone.now(), status="failed")
        capacity = get_custom_instalment_capacity(self.enquiry)
        self.assertEqual(capacity["paid"], Decimal("60"))
        self.assertEqual(capacity["active_unpaid"], Decimal("190"))
        self.assertEqual(capacity["uninvoiced"], Decimal("349"))

    def test_all_terms_locked_for_issued_and_void_invoices(self):
        invoice = self.create()
        for status in ("issued", "void"):
            if status == "void":
                void_local_realestate_invoice(invoice, user=self.user)
            for field, value in (("payment_arrangement", "full_on_shoot_day"),
                                 ("custom_required_total", Decimal("349")),
                                 ("custom_payment_terms", "Changed terms")):
                with self.subTest(status=status, field=field):
                    self.enquiry.refresh_from_db()
                    setattr(self.enquiry, field, value)
                    with self.assertRaises(ValidationError):
                        self.enquiry.save()

    def test_admin_context_confirmation_duplicate_and_readonly_terms(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context_data["capacity"]["maximum"], Decimal("599"))
        self.assertContains(response, "Authoritative booking total")
        self.assertFalse(self.enquiry.invoices.exists())
        payload = {"amount": "250", "description": "First visit"}
        self.assertEqual(self.client.post(self.url, payload).status_code, 200)
        self.assertFalse(self.enquiry.invoices.exists())
        payload["confirmation"] = "on"
        self.assertEqual(self.client.post(self.url, payload).status_code, 302)
        repeated = self.client.post(self.url, payload)
        self.assertContains(repeated, "existing unpaid invoice")
        self.assertEqual(self.enquiry.invoices.count(), 1)
        request = RequestFactory().get("/")
        request.user = self.user
        model_admin = RealEstateEnquiryAdmin(RealEstateEnquiry, custom_admin_site)
        form = model_admin.get_form(request, self.enquiry)
        for field in ("payment_arrangement", "custom_required_total", "custom_payment_terms"):
            self.assertNotIn(field, form.base_fields)
        self.assertFalse(RealEstateInvoiceAdmin(RealEstateInvoice, custom_admin_site).has_add_permission(request))

    def test_admin_requires_invoice_creation_permission(self):
        staff = get_user_model().objects.create_user("limited", is_staff=True)
        staff.user_permissions.add(Permission.objects.get(codename="change_realestateenquiry"))
        self.client.force_login(staff)
        response = self.client.post(self.url, {"amount": "250", "description": "First", "confirmation": "on"})
        self.assertIn(response.status_code, (302, 403))
        self.assertFalse(self.enquiry.invoices.exists())

    def test_failure_rolls_back_invoice_and_number_allocation(self):
        with patch("realestate.finance.record_timeline_event", side_effect=RuntimeError("fixture failure")):
            with self.assertRaises(RuntimeError):
                self.create()
        self.assertFalse(self.enquiry.invoices.exists())
        self.assertTrue(self.create().invoice_number.endswith("-0001"))

    def test_custom_instalment_paid_webhook_is_compatible_and_idempotent(self):
        from checkout.views import StripeWebhookView

        invoice = self.create()
        self.assert_stripe_amount(invoice, 25000)
        invoice.refresh_from_db()
        payload = {
            "id": invoice.stripe_invoice_id, "number": "STRIPE-FIXTURE",
            "status": "paid", "currency": "eur", "total": 25000,
            "amount_due": 25000, "amount_paid": 25000, "livemode": False,
            "metadata": {
                "realestate_enquiry_id": str(self.enquiry.pk),
                "realestate_invoice_number": invoice.invoice_number,
                "payment_purpose": "realestate_adjustment",
            },
            "latest_revision": None, "from_invoice": None,
            "payment_intent": "pi_fixture_custom", "charge": "ch_fixture_custom",
        }
        view = StripeWebhookView()
        with patch("realestate.stripe_invoice_revisions.stripe.Invoice.retrieve", return_value=payload):
            self.assertTrue(view._handle_realestate_invoice_event("invoice.paid", payload))
            self.assertTrue(view._handle_realestate_invoice_event("invoice.paid", payload))
        self.assertEqual(invoice.payments.count(), 1)
        self.assertEqual(self.enquiry.adjusted_balance_due, Decimal("349"))
        self.assertFalse(can_release_realestate_delivery(self.enquiry))


@skipUnlessDBFeature("has_select_for_update")
@override_settings(VAT_REGISTERED=False)
class CustomInstalmentConcurrencyTests(TransactionTestCase):
    def test_concurrent_requests_create_only_one_unpaid_instalment(self):
        enquiry = RealEstateEnquiry.objects.create(
            name="Concurrent fixture", quoted_price=Decimal("599"),
            payment_arrangement="custom", custom_required_total=Decimal("599"),
            custom_payment_terms="250 then 349",
        )
        calculate_realestate_deposit_amounts(enquiry)
        barrier = threading.Barrier(2)

        def create():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                create_custom_instalment_invoice(enquiry=enquiry, amount="250", description="First instalment")
                return "created"
            except ValidationError:
                return "rejected"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(create) for _ in range(2)]
            results = [future.result(timeout=20) for future in futures]
        self.assertCountEqual(results, ["created", "rejected"])
        self.assertEqual(enquiry.invoices.count(), 1)
