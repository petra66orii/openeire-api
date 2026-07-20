from decimal import Decimal
from html.parser import HTMLParser
from unittest.mock import Mock, call, patch

import stripe
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core import mail
from django.core.cache import caches
from django.db import DataError
from django.template.loader import get_template, render_to_string
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from openeire_api.admin import custom_admin_site
from openeire_api.pdf_markdown import render_markdown_to_flowables

from .admin import RealEstateEnquiryAdmin
from .documents import build_booking_agreement_filename
from .documents import _build_booking_agreement_context
from .documents import _load_booking_agreement_template
from .documents import generate_booking_agreement_pdf
from .documents import render_booking_agreement_markdown
from .emails import build_realestate_email_context
from .emails import format_money
from .emails import send_templated_email
from .finance import ensure_invoices_for_arrangement
from .finance import record_realestate_payment
from .models import RealEstateEnquiry
from .models import RealEstateBookingAgreementSnapshot
from .models import RealEstateInvoice
from .models import RealEstatePayment
from .models import RealEstateTimelineEvent
from .payments import calculate_realestate_deposit_amounts
from .payments import create_realestate_deposit_checkout_session
from .payments import prepare_realestate_deposit_checkout_session


REAL_ESTATE_EMAIL_TEMPLATE_CONTEXT = {
    "first_name": "Jane",
    "agency_name": "Example Estate Agents",
    "company_name": "Example Estate Agents",
    "property_address": "Example House, Salthill, Galway",
    "package_name": "Pro package",
    "addons": ["2D measured floor plan", "Additional social media cuts"],
    "quote_total": "399",
    "vat_total": "0.00",
    "total_including_vat": "399.00",
    "deposit_amount": "119.70",
    "balance_due": "279.30",
    "vat_registered": False,
    "vat_rate_percent": Decimal("0.00"),
    "price_input_is_gross": True,
    "vat_notice": "VAT not applicable — supplier not VAT registered",
    "shoot_date": "2026-06-20",
    "shoot_time": "10:00",
    "booking_reference": "RE-123",
    "delivery_link": "https://openeire.ie/delivery/example",
    "review_link": "https://openeire.ie/review/example",
    "new_date": "2026-06-21",
    "deposit_payment_link": "https://checkout.stripe.com/example",
    "booking_agreement_link": "https://openeire.ie/agreements/example",
    "brand_logo_url": "https://openeire.ie/static/emails/openeire-studios-logo.png",
    "email_logo_url": "https://openeire.ie/static/emails/openeire-studios-logo.png",
    "reply_to_email": "shoots@openeire.ie",
    "quote_reply_email": "shoots@openeire.ie",
    "quote_reply_mailto": "mailto:shoots@openeire.ie",
    "quote_reply_url": "mailto:shoots@openeire.ie",
    "cta_url": "",
    "cta_label": "",
}


class RealEstatePricingTests(TestCase):
    def _enquiry(self, **overrides):
        values = {
            "name": "Jane Agent",
            "email": "jane@example.com",
            "phone": "+353 87 123 4567",
            "client_type": RealEstateEnquiry.ClientType.ESTATE_AGENT,
            "property_address": "Example House",
            "county": "Galway",
            "property_type": "Detached house",
            "preferred_package": RealEstateEnquiry.PreferredPackage.PRO,
            "quoted_price": Decimal("399.00"),
            "consent_to_contact": True,
        }
        values.update(overrides)
        return RealEstateEnquiry.objects.create(**values)

    @override_settings(
        VAT_REGISTERED=False,
        VAT_RATE=Decimal("0.23"),
        REALESTATE_PRICE_INPUT_IS_GROSS=True,
    )
    def test_vat_disabled_treats_quote_as_final_total_and_persists_snapshot(self):
        enquiry = self._enquiry()

        amounts = calculate_realestate_deposit_amounts(enquiry)

        self.assertEqual(amounts["quote_total"], Decimal("399.00"))
        self.assertEqual(amounts["vat_total"], Decimal("0.00"))
        self.assertEqual(amounts["total_including_vat"], Decimal("399.00"))
        self.assertEqual(amounts["deposit_amount"], Decimal("119.70"))
        self.assertEqual(amounts["balance_due"], Decimal("279.30"))
        enquiry.refresh_from_db()
        self.assertFalse(enquiry.vat_registered_at_quote)
        self.assertTrue(enquiry.price_input_is_gross)
        self.assertEqual(enquiry.quoted_total, Decimal("399.00"))

    @override_settings(
        VAT_REGISTERED=True,
        VAT_RATE=Decimal("0.23"),
        REALESTATE_PRICE_INPUT_IS_GROSS=True,
    )
    def test_future_vat_enabled_gross_price_extracts_vat_without_increasing_total(self):
        amounts = calculate_realestate_deposit_amounts(self._enquiry())

        self.assertEqual(amounts["quote_subtotal"], Decimal("324.39"))
        self.assertEqual(amounts["vat_total"], Decimal("74.61"))
        self.assertEqual(amounts["total_including_vat"], Decimal("399.00"))
        self.assertEqual(amounts["deposit_amount"], Decimal("119.70"))

    @override_settings(
        VAT_REGISTERED=True,
        VAT_RATE=Decimal("0.23"),
        REALESTATE_PRICE_INPUT_IS_GROSS=False,
    )
    def test_future_vat_enabled_net_price_adds_configured_vat(self):
        amounts = calculate_realestate_deposit_amounts(self._enquiry())

        self.assertEqual(amounts["vat_total"], Decimal("91.77"))
        self.assertEqual(amounts["total_including_vat"], Decimal("490.77"))
        self.assertEqual(amounts["deposit_amount"], Decimal("147.23"))
        self.assertEqual(amounts["balance_due"], Decimal("343.54"))

    @override_settings(VAT_REGISTERED=False)
    def test_existing_legacy_snapshot_is_not_recalculated(self):
        enquiry = self._enquiry(
            pricing_snapshot_version=1,
            price_input_is_gross=False,
            vat_registered_at_quote=True,
            quoted_vat_rate=Decimal("0.23"),
            quoted_subtotal=Decimal("399.00"),
            quoted_vat_amount=Decimal("91.77"),
            quoted_total=Decimal("490.77"),
            quoted_deposit_amount=Decimal("147.23"),
            quoted_balance_due=Decimal("343.54"),
        )

        amounts = calculate_realestate_deposit_amounts(enquiry)

        self.assertEqual(amounts["vat_total"], Decimal("91.77"))
        self.assertEqual(amounts["total_including_vat"], Decimal("490.77"))
        self.assertEqual(amounts["deposit_amount"], Decimal("147.23"))


@override_settings(STRIPE_SECRET_KEY="sk_test_deposit_sessions")
class RealEstateDepositCheckoutSessionTests(TestCase):
    def setUp(self):
        self.enquiry = RealEstateEnquiry.objects.create(
            name="Jane Agent",
            email="jane@example.com",
            phone="+353 87 123 4567",
            client_type=RealEstateEnquiry.ClientType.ESTATE_AGENT,
            property_address="Example House",
            county="Galway",
            property_type="Detached house",
            preferred_package=RealEstateEnquiry.PreferredPackage.PRO,
            quoted_price=Decimal("399.00"),
            consent_to_contact=True,
        )
        self.deposit_invoice, _balance_invoice = ensure_invoices_for_arrangement(
            self.enquiry
        )
        self.enquiry.stripe_deposit_session_id = "cs_test_existing"
        self.enquiry.deposit_payment_link = "https://checkout.stripe.com/existing"
        self.enquiry.save(
            update_fields=("stripe_deposit_session_id", "deposit_payment_link")
        )

    def _session(self, **overrides):
        values = {
            "id": self.enquiry.stripe_deposit_session_id,
            "url": self.enquiry.deposit_payment_link,
            "status": "open",
            "payment_status": "unpaid",
            "expires_at": int(timezone.now().timestamp()) + 7200,
            "amount_total": 11970,
            "currency": "eur",
            "livemode": False,
            "payment_intent": "pi_test_deposit",
            "metadata": {
                "purpose": "realestate_deposit",
                "realestate_enquiry_id": str(self.enquiry.pk),
                "realestate_invoice_number": self.deposit_invoice.invoice_number,
            },
        }
        values.update(overrides)
        return values

    @patch("realestate.payments.stripe.checkout.Session.create")
    @patch("realestate.payments.stripe.checkout.Session.retrieve")
    def test_valid_open_unpaid_session_is_reused_without_duplicate_checkout(
        self, mock_retrieve, mock_create
    ):
        mock_retrieve.return_value = self._session()

        result = prepare_realestate_deposit_checkout_session(self.enquiry)

        self.assertTrue(result.reused)
        self.assertEqual(result.checkout_url, self.enquiry.deposit_payment_link)
        mock_retrieve.assert_called_once_with("cs_test_existing")
        mock_create.assert_not_called()

    @override_settings(
        REALESTATE_API_URL="https://api.openeire.test",
        REALESTATE_SITE_URL="https://existing-success.openeire.test",
    )
    @patch("realestate.payments.stripe.checkout.Session.create")
    @patch("realestate.payments.stripe.checkout.Session.retrieve")
    def test_expired_session_is_replaced(self, mock_retrieve, mock_create):
        mock_retrieve.return_value = self._session(
            status="expired",
            expires_at=int(timezone.now().timestamp()) - 60,
        )
        mock_create.return_value = {
            "id": "cs_test_replacement",
            "url": "https://checkout.stripe.com/replacement",
        }

        result = prepare_realestate_deposit_checkout_session(self.enquiry)

        self.assertFalse(result.reused)
        self.assertEqual(result.session_id, "cs_test_replacement")
        self.enquiry.refresh_from_db()
        self.assertEqual(self.enquiry.stripe_deposit_session_id, "cs_test_replacement")
        create_kwargs = mock_create.call_args.kwargs
        self.assertEqual(
            create_kwargs["cancel_url"],
            "https://api.openeire.test/api/real-estate/deposit/cancelled/",
        )
        self.assertEqual(
            create_kwargs["success_url"],
            "https://existing-success.openeire.test/real-estate/deposit/success"
            "?session_id={CHECKOUT_SESSION_ID}",
        )
        self.assertEqual(create_kwargs["after_expiration"], {"recovery": {"enabled": True}})
        remaining = create_kwargs["expires_at"] - int(timezone.now().timestamp())
        self.assertGreaterEqual(remaining, (24 * 60 * 60) - 2)
        self.assertLessEqual(remaining, 24 * 60 * 60)

    @patch("realestate.payments.stripe.checkout.Session.create")
    @patch("realestate.payments.stripe.checkout.Session.retrieve")
    def test_missing_session_is_replaced(self, mock_retrieve, mock_create):
        mock_retrieve.side_effect = stripe.error.InvalidRequestError(
            "No such Checkout Session", "id", code="resource_missing"
        )
        mock_create.return_value = {
            "id": "cs_test_missing_replacement",
            "url": "https://checkout.stripe.com/missing-replacement",
        }

        result = prepare_realestate_deposit_checkout_session(self.enquiry)

        self.assertEqual(result.session_id, "cs_test_missing_replacement")
        mock_create.assert_called_once()

    @patch("realestate.payments.stripe.checkout.Session.expire")
    @patch("realestate.payments.stripe.checkout.Session.create")
    @patch("realestate.payments.stripe.checkout.Session.retrieve")
    def test_mismatched_sessions_are_replaced(
        self, mock_retrieve, mock_create, mock_expire
    ):
        mismatch_overrides = {
            "amount": {"amount_total": 11971},
            "currency": {"currency": "usd"},
            "enquiry metadata": {
                "metadata": {
                    "purpose": "realestate_deposit",
                    "realestate_enquiry_id": "999999",
                    "realestate_invoice_number": self.deposit_invoice.invoice_number,
                }
            },
            "invoice metadata": {
                "metadata": {
                    "purpose": "realestate_deposit",
                    "realestate_enquiry_id": str(self.enquiry.pk),
                    "realestate_invoice_number": "OE-RE-2099-9999",
                }
            },
            "environment": {"livemode": True},
        }

        for index, (label, overrides) in enumerate(mismatch_overrides.items(), start=1):
            with self.subTest(mismatch=label):
                stored_id = f"cs_test_invalid_{index}"
                self.enquiry.stripe_deposit_session_id = stored_id
                self.enquiry.deposit_payment_link = f"https://checkout.stripe.com/invalid-{index}"
                self.enquiry.stripe_deposit_creation_key = ""
                self.enquiry.save(
                    update_fields=(
                        "stripe_deposit_session_id",
                        "deposit_payment_link",
                        "stripe_deposit_creation_key",
                    )
                )
                mock_retrieve.return_value = self._session(id=stored_id, **overrides)
                mock_create.return_value = {
                    "id": f"cs_test_valid_{index}",
                    "url": f"https://checkout.stripe.com/valid-{index}",
                }
                mock_create.reset_mock()

                result = prepare_realestate_deposit_checkout_session(self.enquiry)

                self.assertEqual(result.session_id, f"cs_test_valid_{index}")
                mock_create.assert_called_once()
                mock_expire.assert_called_with(stored_id)

    @patch("realestate.payments.stripe.checkout.Session.expire")
    @patch("realestate.payments.stripe.checkout.Session.create")
    @patch("realestate.payments.stripe.checkout.Session.retrieve")
    def test_session_near_expiry_is_replaced(
        self, mock_retrieve, mock_create, mock_expire
    ):
        mock_retrieve.return_value = self._session(
            expires_at=int(timezone.now().timestamp()) + (15 * 60)
        )
        mock_create.return_value = {
            "id": "cs_test_near_expiry_replacement",
            "url": "https://checkout.stripe.com/near-expiry-replacement",
        }

        result = prepare_realestate_deposit_checkout_session(self.enquiry)

        self.assertEqual(result.session_id, "cs_test_near_expiry_replacement")
        mock_expire.assert_called_once_with("cs_test_existing")
        mock_create.assert_called_once()

    @patch("realestate.payments.stripe.checkout.Session.create")
    @patch("realestate.payments.stripe.checkout.Session.retrieve")
    def test_deliberate_replacements_use_fresh_idempotency_keys(
        self, mock_retrieve, mock_create
    ):
        mock_retrieve.return_value = self._session(status="expired")
        mock_create.side_effect = (
            {"id": "cs_test_replacement_1", "url": "https://checkout.stripe.com/new-1"},
            {"id": "cs_test_replacement_2", "url": "https://checkout.stripe.com/new-2"},
        )

        prepare_realestate_deposit_checkout_session(self.enquiry)
        self.enquiry.stripe_deposit_session_id = "cs_test_expired_again"
        self.enquiry.deposit_payment_link = "https://checkout.stripe.com/expired-again"
        self.enquiry.save(
            update_fields=("stripe_deposit_session_id", "deposit_payment_link")
        )
        mock_retrieve.return_value = self._session(status="expired")
        prepare_realestate_deposit_checkout_session(self.enquiry)

        first_key = mock_create.call_args_list[0].kwargs["idempotency_key"]
        second_key = mock_create.call_args_list[1].kwargs["idempotency_key"]
        self.assertNotEqual(first_key, second_key)

    @patch("realestate.payments.stripe.checkout.Session.create")
    def test_network_retry_reuses_one_creation_attempt_idempotency_key(self, mock_create):
        self.enquiry.stripe_deposit_session_id = ""
        self.enquiry.deposit_payment_link = ""
        self.enquiry.save(
            update_fields=("stripe_deposit_session_id", "deposit_payment_link")
        )
        mock_create.side_effect = (
            stripe.error.APIConnectionError("network timeout"),
            {"id": "cs_test_after_retry", "url": "https://checkout.stripe.com/after-retry"},
        )

        with self.assertRaisesRegex(stripe.error.APIConnectionError, "network timeout"):
            prepare_realestate_deposit_checkout_session(self.enquiry)
        self.enquiry.refresh_from_db()
        persisted_key = self.enquiry.stripe_deposit_creation_key
        result = prepare_realestate_deposit_checkout_session(self.enquiry)

        self.assertEqual(result.session_id, "cs_test_after_retry")
        self.assertTrue(persisted_key)
        self.assertEqual(
            mock_create.call_args_list[0].kwargs["idempotency_key"],
            mock_create.call_args_list[1].kwargs["idempotency_key"],
        )
        self.enquiry.refresh_from_db()
        self.assertEqual(self.enquiry.stripe_deposit_creation_key, "")

    @patch("realestate.payments.stripe.checkout.Session.create")
    def test_permanent_creation_error_clears_attempt_key(self, mock_create):
        self.enquiry.stripe_deposit_session_id = ""
        self.enquiry.deposit_payment_link = ""
        self.enquiry.save(
            update_fields=("stripe_deposit_session_id", "deposit_payment_link")
        )
        mock_create.side_effect = stripe.error.InvalidRequestError(
            "Invalid Checkout parameter",
            "after_expiration",
        )

        with self.assertRaises(stripe.error.InvalidRequestError):
            prepare_realestate_deposit_checkout_session(self.enquiry)

        self.enquiry.refresh_from_db()
        self.assertEqual(self.enquiry.stripe_deposit_creation_key, "")

    @patch("realestate.payments.stripe.checkout.Session.create")
    @patch("realestate.payments.stripe.checkout.Session.expire")
    @patch("realestate.payments.stripe.checkout.Session.retrieve")
    def test_concurrent_replacement_reuses_session_saved_by_other_request(
        self,
        mock_retrieve,
        mock_expire,
        mock_create,
    ):
        replacement_id = "cs_test_concurrent_replacement"
        replacement_url = "https://checkout.stripe.com/concurrent-replacement"
        replacement_session = self._session(
            id=replacement_id,
            url=replacement_url,
        )
        mock_retrieve.side_effect = (
            self._session(expires_at=int(timezone.now().timestamp()) + (15 * 60)),
            replacement_session,
        )

        def save_concurrent_replacement(_session_id):
            RealEstateEnquiry.objects.filter(pk=self.enquiry.pk).update(
                stripe_deposit_session_id=replacement_id,
                deposit_payment_link=replacement_url,
            )

        mock_expire.side_effect = save_concurrent_replacement

        result = prepare_realestate_deposit_checkout_session(self.enquiry)

        self.assertTrue(result.reused)
        self.assertEqual(result.session_id, replacement_id)
        mock_create.assert_not_called()

    @patch("realestate.payments.stripe.checkout.Session.create")
    @patch("realestate.payments.stripe.checkout.Session.retrieve")
    def test_complete_paid_session_is_reconciled_without_new_checkout(
        self, mock_retrieve, mock_create
    ):
        mock_retrieve.return_value = self._session(status="complete", payment_status="paid")

        result = prepare_realestate_deposit_checkout_session(self.enquiry)
        second_result = prepare_realestate_deposit_checkout_session(self.enquiry)

        self.assertTrue(result.payment_already_exists)
        self.assertTrue(second_result.payment_already_exists)
        mock_create.assert_not_called()
        payments = RealEstatePayment.objects.filter(
            stripe_checkout_session_id="cs_test_existing"
        )
        self.assertEqual(payments.count(), 1)
        payment = payments.get()
        self.assertEqual(payment.amount, Decimal("119.70"))
        self.enquiry.refresh_from_db()
        self.assertTrue(self.enquiry.deposit_paid)

    @patch("realestate.payments.stripe.checkout.Session.create")
    @patch("realestate.payments.stripe.checkout.Session.retrieve")
    def test_mismatched_paid_session_requires_review_without_replacement(
        self, mock_retrieve, mock_create
    ):
        mock_retrieve.return_value = self._session(
            status="complete",
            payment_status="paid",
            amount_total=1,
        )

        with self.assertRaisesRegex(ValidationError, "manual review"):
            prepare_realestate_deposit_checkout_session(self.enquiry)

        mock_create.assert_not_called()
        self.assertFalse(
            RealEstatePayment.objects.filter(
                stripe_checkout_session_id="cs_test_existing"
            ).exists()
        )


class RealEstateEmailTemplateTests(SimpleTestCase):
    template_names = (
        "enquiry_reply",
        "quote",
        "booking_agreement",
        "deposit_request",
        "confirmation",
        "delivery",
        "follow_up",
        "weather_reschedule",
        "thank_you",
        "invoice_issued",
        "payment_reminder",
        "cash_receipt",
        "payment_received",
        "overdue_payment",
    )

    def test_real_estate_html_and_text_templates_render(self):
        for template_name in self.template_names:
            with self.subTest(template_name=template_name):
                html_template = f"emails/real_estate/{template_name}.html"
                text_template = f"emails/real_estate/{template_name}.txt"

                get_template(html_template)
                get_template(text_template)

                html = render_to_string(
                    html_template,
                    REAL_ESTATE_EMAIL_TEMPLATE_CONTEXT,
                )
                text = render_to_string(
                    text_template,
                    REAL_ESTATE_EMAIL_TEMPLATE_CONTEXT,
                )

                self.assertIn("OpenÉire Studios", html)
                self.assertIn("OpenÉire Studios", text)
                self.assertIn("Example House, Salthill, Galway", html)
                self.assertIn("Example House, Salthill, Galway", text)
                self.assertNotIn("{{", html)
                self.assertNotIn("{{", text)

    def test_base_template_renders_logo_when_logo_url_exists(self):
        html = render_to_string(
            "emails/base_email.html",
            {
                "email_logo_url": "https://openeire.ie/static/emails/openeire-studios-logo.png",
                "brand_logo_url": "https://openeire.ie/static/emails/openeire-studios-logo.png",
            },
        )

        self.assertIn('src="https://openeire.ie/static/emails/openeire-studios-logo.png"', html)
        self.assertIn('alt="OpenÉire Studios"', html)

    def test_base_template_falls_back_to_text_when_logo_url_missing(self):
        html = render_to_string(
            "emails/base_email.html",
            {
                "email_logo_url": "",
                "brand_logo_url": "",
            },
        )

        self.assertIn(">OpenÉire Studios<", html)
        self.assertNotIn("<img", html)

    def test_real_estate_email_templates_keep_required_flow_wording(self):
        quote_text = render_to_string(
            "emails/real_estate/quote.txt",
            REAL_ESTATE_EMAIL_TEMPLATE_CONTEXT,
        )
        booking_text = render_to_string(
            "emails/real_estate/booking_agreement.txt",
            REAL_ESTATE_EMAIL_TEMPLATE_CONTEXT,
        )
        deposit_text = render_to_string(
            "emails/real_estate/deposit_request.txt",
            REAL_ESTATE_EMAIL_TEMPLATE_CONTEXT,
        )
        confirmation_text = render_to_string(
            "emails/real_estate/confirmation.txt",
            REAL_ESTATE_EMAIL_TEMPLATE_CONTEXT,
        )
        delivery_text = render_to_string(
            "emails/real_estate/delivery.txt",
            REAL_ESTATE_EMAIL_TEMPLATE_CONTEXT,
        )
        follow_up_text = render_to_string(
            "emails/real_estate/follow_up.txt",
            REAL_ESTATE_EMAIL_TEMPLATE_CONTEXT,
        )

        self.assertIn("Ready to proceed?", quote_text)
        self.assertIn(
            "Reply to this email and we'll issue the Booking Agreement and booking deposit request.",
            quote_text,
        )
        self.assertIn("Your booking is only confirmed once BOTH:", quote_text)
        self.assertIn("- the Booking Agreement has been signed", quote_text)
        self.assertIn("- the booking deposit has cleared", quote_text)
        self.assertIn(
            "This quote does not confirm a booking or reserve a shoot date.",
            quote_text,
        )
        self.assertIn("Review Booking Agreement:", booking_text)
        self.assertIn("Pay Secure Deposit:", deposit_text)
        self.assertIn(
            "Your booking is confirmed according to the selected payment arrangement.",
            booking_text,
        )
        self.assertIn(
            "Your property shoot is now confirmed.",
            confirmation_text,
        )
        self.assertIn("Download Media:", delivery_text)
        self.assertIn(
            "The delivered files are ready for the agreed property listing",
            delivery_text,
        )
        self.assertIn("Leave a Google Review:", follow_up_text)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="OpenÉire Studios <studio@openeire.ie>",
    )
    def test_send_templated_email_sends_text_and_html_versions(self):
        mail.outbox = []

        sent_count = send_templated_email(
            subject="Your property media quote",
            to="jane@example.com",
            template_base="quote",
            context=REAL_ESTATE_EMAIL_TEMPLATE_CONTEXT,
            reply_to="studio@openeire.ie",
        )

        self.assertEqual(sent_count, 1)
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ["jane@example.com"])
        self.assertEqual(email.reply_to, ["studio@openeire.ie"])
        self.assertIn("Ready to proceed?", email.body)
        self.assertIn("Proceed with this quote: shoots@openeire.ie", email.body)
        self.assertEqual(len(email.alternatives), 1)
        self.assertEqual(email.alternatives[0][1], "text/html")

    def test_format_money_returns_clean_euro_amounts_or_blank(self):
        self.assertEqual(format_money(Decimal("399")), "€399.00")
        self.assertEqual(format_money(150), "€150.00")
        self.assertEqual(format_money("1,234.5"), "€1,234.50")
        self.assertEqual(format_money("€91.775"), "€91.78")
        self.assertEqual(format_money(None), "")
        self.assertEqual(format_money(""), "")
        self.assertEqual(format_money("null"), "")
        self.assertEqual(format_money("EUR 399"), "€399.00")

    def test_quote_email_renders_logo_cta_and_price_summary(self):
        context = {
            **REAL_ESTATE_EMAIL_TEMPLATE_CONTEXT,
            "quote_total": "€399.00",
            "vat_total": "€0.00",
            "total_including_vat": "€399.00",
            "deposit_amount": "€119.70",
            "balance_due": "€279.30",
        }

        html = render_to_string("emails/real_estate/quote.html", context)
        text = render_to_string("emails/real_estate/quote.txt", context)

        self.assertIn("openeire-studios-logo.png", html)
        self.assertIn("Aerial Photography", html)
        self.assertIn("Property Media", html)
        self.assertIn("Visual Licensing", html)
        self.assertIn("Package total", html)
        self.assertIn("€399.00", html)
        self.assertIn("VAT", html)
        self.assertIn("€0.00", html)
        self.assertIn("Total payable", html)
        self.assertIn("Deposit required", html)
        self.assertIn("€119.70", html)
        self.assertIn("Balance on delivery", html)
        self.assertIn("€279.30", html)
        self.assertIn("VAT not applicable", html)
        self.assertIn("Proceed with this quote", html)
        self.assertIn("mailto:shoots@openeire.ie", html)
        self.assertIn("Package total: €399.00", text)
        self.assertIn("Proceed with this quote: shoots@openeire.ie", text)

    def test_full_on_shoot_day_email_templates_render_payment_rule(self):
        context = {
            **REAL_ESTATE_EMAIL_TEMPLATE_CONTEXT,
            "deposit_amount": "",
            "balance_due": "",
            "total_required": "€399.00",
            "payment_arrangement_label": "Full payment on shoot day",
            "is_full_on_shoot_day": True,
            "payment_due_date": "21 July 2026",
            "expected_payment_method": "Cash",
            "booking_confirmation_rule": (
                "Booking may be confirmed while unpaid under the approved "
                "full-payment-on-shoot-day arrangement."
            ),
            "quote_payment_rule": (
                "The full amount is due on the shoot date; final delivery remains locked "
                "until full payment is recorded."
            ),
        }

        quote_html = render_to_string("emails/real_estate/quote.html", context)
        quote_text = render_to_string("emails/real_estate/quote.txt", context)
        booking_html = render_to_string("emails/real_estate/booking_agreement.html", context)
        booking_text = render_to_string("emails/real_estate/booking_agreement.txt", context)
        confirmation_text = render_to_string("emails/real_estate/confirmation.txt", context)
        confirmation_rule = context["booking_confirmation_rule"]

        self.assertIn("Full payment on shoot day", confirmation_text)
        self.assertIn("€399.00 on 21 July 2026 by Cash", confirmation_text)
        self.assertIn("full amount is due on the shoot date", quote_html)
        self.assertIn("Payment due: 21 July 2026", quote_text)
        self.assertEqual(booking_html.count(confirmation_rule), 1)
        self.assertEqual(booking_text.count(confirmation_rule), 1)
        self.assertNotIn("Deposit required", quote_text)
        self.assertNotIn("Balance on delivery", quote_text)

    def test_quote_email_omits_blank_summary_rows_and_broken_cta(self):
        context = {
            **REAL_ESTATE_EMAIL_TEMPLATE_CONTEXT,
            "quote_total": "",
            "vat_total": "",
            "total_including_vat": "",
            "deposit_amount": "",
            "balance_due": "",
            "quote_reply_email": "",
            "quote_reply_mailto": "",
            "quote_reply_url": "",
        }

        html = render_to_string("emails/real_estate/quote.html", context)
        text = render_to_string("emails/real_estate/quote.txt", context)

        self.assertNotIn("Quote total (ex VAT)</td>", html)
        self.assertNotIn("VAT (23%)</td>", html)
        self.assertNotIn("Total incl. VAT</td>", html)
        self.assertNotIn("Deposit required</td>", html)
        self.assertNotIn("Balance on delivery</td>", html)
        self.assertNotIn("€None", html)
        self.assertNotIn("€.00", html)
        self.assertNotIn("Proceed with this quote</a>", html)
        self.assertIn("Reply details are being confirmed", html)
        self.assertIn("Quote total: To be confirmed", text)
        self.assertIn("Reply details are being confirmed", text)

    def test_quote_cta_appears_with_mailto_context(self):
        html = render_to_string(
            "emails/real_estate/quote.html",
            REAL_ESTATE_EMAIL_TEMPLATE_CONTEXT,
        )

        self.assertIn("Proceed with this quote", html)
        self.assertIn('href="mailto:shoots@openeire.ie"', html)

    def test_booking_deposit_cta_appears_with_deposit_link(self):
        checkout_url = (
            "https://checkout.stripe.com/c/pay/cs_test_example"
            "?prefilled_email=jane%40example.com#fidkdWxOYHwnPyd1blpxYHZxWjA0"
        )
        context = {**REAL_ESTATE_EMAIL_TEMPLATE_CONTEXT, "deposit_payment_link": checkout_url}
        html = render_to_string(
            "emails/real_estate/deposit_request.html",
            context,
        )

        self.assertIn("Pay Secure Deposit", html)
        self.assertIn("Review Booking Agreement", html)

        class DepositLinkParser(HTMLParser):
            href = None
            current_href = None

            def handle_starttag(self, tag, attrs):
                if tag == "a":
                    self.current_href = dict(attrs).get("href")

            def handle_endtag(self, tag):
                if tag == "a":
                    self.current_href = None

            def handle_data(self, data):
                if data.strip() == "Pay Secure Deposit":
                    self.href = self.current_href

        parser = DepositLinkParser()
        parser.feed(html)
        self.assertEqual(parser.href, checkout_url)

    def test_delivery_cta_appears_with_delivery_link(self):
        html = render_to_string(
            "emails/real_estate/delivery.html",
            REAL_ESTATE_EMAIL_TEMPLATE_CONTEXT,
        )

        self.assertIn("Download Media", html)
        self.assertIn("https://openeire.ie/delivery/example", html)

    def test_delivery_cta_depends_on_link_not_provider(self):
        for provider in RealEstateEnquiry.DeliveryProvider.values:
            with self.subTest(provider=provider):
                context = {
                    **REAL_ESTATE_EMAIL_TEMPLATE_CONTEXT,
                    "delivery_provider": provider,
                    "delivery_link": f"https://example.com/{provider}/delivery",
                }

                html = render_to_string("emails/real_estate/delivery.html", context)
                text = render_to_string("emails/real_estate/delivery.txt", context)

                self.assertIn("Download Media", html)
                self.assertIn(context["delivery_link"], html)
                self.assertIn(context["delivery_link"], text)

    def test_delivery_cta_is_omitted_without_delivery_link(self):
        context = {
            **REAL_ESTATE_EMAIL_TEMPLATE_CONTEXT,
            "delivery_provider": RealEstateEnquiry.DeliveryProvider.PORTAL,
            "delivery_link": "",
        }

        html = render_to_string("emails/real_estate/delivery.html", context)
        text = render_to_string("emails/real_estate/delivery.txt", context)

        self.assertNotIn("Download Media", html)
        self.assertNotIn("href=\"\"", html)
        self.assertNotIn("Download Media:", text)

    def test_follow_up_cta_appears_with_review_link(self):
        html = render_to_string(
            "emails/real_estate/follow_up.html",
            REAL_ESTATE_EMAIL_TEMPLATE_CONTEXT,
        )

        self.assertIn("Leave a Google Review", html)
        self.assertIn("https://openeire.ie/review/example", html)

    def test_base_template_renders_optional_context_cta_only_when_complete(self):
        context = {
            **REAL_ESTATE_EMAIL_TEMPLATE_CONTEXT,
            "cta_url": "https://openeire.ie/real-estate",
            "cta_label": "View real estate services",
        }

        html = render_to_string("emails/real_estate/enquiry_reply.html", context)

        self.assertIn("View real estate services", html)
        self.assertIn("https://openeire.ie/real-estate", html)

        html_without_label = render_to_string(
            "emails/real_estate/enquiry_reply.html",
            {**context, "cta_label": ""},
        )
        html_without_url = render_to_string(
            "emails/real_estate/enquiry_reply.html",
            {**context, "cta_url": ""},
        )

        self.assertNotIn("View real estate services", html_without_label)
        self.assertNotIn("View real estate services", html_without_url)

    @override_settings(
        SITE_URL="https://openeire.ie",
        REALESTATE_EMAIL_LOGO_URL="",
        EMAIL_LOGO_URL="",
    )
    def test_build_realestate_email_context_formats_money_and_logo_url(self):
        enquiry = RealEstateEnquiry(
            name="Jane Agent",
            email="jane@example.com",
            phone="+353 87 123 4567",
            company_name="Example Estate Agents",
            client_type="estate_agent",
            property_address="Example House, Salthill",
            county="Galway",
            property_type="Detached house",
            preferred_package="pro",
            consent_to_contact=True,
            quoted_price=Decimal("399"),
        )

        context = build_realestate_email_context(
            enquiry,
            vat_total="91.77",
            total_including_vat=Decimal("490.77"),
            deposit_amount=147.23,
            balance_due="343.54",
        )

        self.assertEqual(context["quote_total"], "€399.00")
        self.assertEqual(context["vat_total"], "€91.77")
        self.assertEqual(context["total_including_vat"], "€490.77")
        self.assertEqual(context["deposit_amount"], "€147.23")
        self.assertEqual(context["balance_due"], "€343.54")
        self.assertEqual(context["reply_to_email"], "shoots@openeire.ie")
        self.assertEqual(context["quote_reply_email"], "shoots@openeire.ie")
        self.assertEqual(
            context["quote_reply_mailto"],
            "mailto:shoots@openeire.ie",
        )
        self.assertEqual(context["cta_url"], "")
        self.assertEqual(context["cta_label"], "")
        self.assertTrue(
            context["email_logo_url"].endswith(
                "/static/emails/openeire-studios-logo.png"
            )
        )


class MarkdownPDFRendererTests(SimpleTestCase):
    def test_renderer_supports_core_markdown_blocks(self):
        flowables = render_markdown_to_flowables(
            "# Title\n\n"
            "## Section\n\n"
            "Paragraph with **bold** and *italic* text.\n\n"
            "- First bullet\n"
            "- Second bullet\n\n"
            "1. First item\n"
            "2. Second item\n\n"
            "| Field | Value |\n"
            "| --- | --- |\n"
            "| A | B |\n\n"
            "---\n"
        )

        class_names = [flowable.__class__.__name__ for flowable in flowables]
        self.assertIn("Paragraph", class_names)
        self.assertIn("Table", class_names)
        self.assertGreaterEqual(class_names.count("Paragraph"), 7)

    def test_two_column_tables_use_fixed_pdf_safe_widths_and_row_splitting(self):
        flowables = render_markdown_to_flowables(
            "## Details\n\n| Field | Information |\n| --- | --- |\n| Email | long@example.com |\n",
            table_width=400,
            keep_headings_with_next=True,
        )
        table = next(flowable for flowable in flowables if flowable.__class__.__name__ == "Table")

        self.assertEqual(table._colWidths, [136.0, 264.0])
        self.assertEqual(table.splitByRow, 1)
        self.assertEqual(table.repeatRows, 1)


class BookingAgreementDocumentTests(TestCase):
    def _render_booking_agreement_markdown(self, enquiry):
        from django.template import Context, Template

        return Template(_load_booking_agreement_template()).render(
            Context(_build_booking_agreement_context(enquiry), autoescape=False)
        )

    def test_booking_agreement_pdf_generation_returns_pdf(self):
        enquiry = RealEstateEnquiry.objects.create(
            name="Jane Agent",
            email="jane@example.com",
            phone="+353 87 123 4567",
            company_name="Example Estate Agents",
            client_type=RealEstateEnquiry.ClientType.ESTATE_AGENT,
            property_address="Example House, Salthill",
            county="Galway",
            eircode="H91 XXXX",
            property_type="Detached house",
            preferred_package=RealEstateEnquiry.PreferredPackage.PRO,
            preferred_date="2026-06-20",
            quoted_price="399.00",
            consent_to_contact=True,
        )

        pdf_bytes = generate_booking_agreement_pdf(enquiry)

        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertGreater(len(pdf_bytes), 1000)
        self.assertEqual(
            build_booking_agreement_filename(enquiry),
            f"openeire-booking-agreement-re-{enquiry.id}-jane-agent.pdf",
        )

    def test_booking_agreement_missing_optional_fields_render_as_not_provided(self):
        enquiry = RealEstateEnquiry.objects.create(
            name="Jane Agent",
            email="jane@example.com",
            phone="+353 87 123 4567",
            client_type=RealEstateEnquiry.ClientType.PRIVATE_SELLER,
            property_address="Example House, Salthill",
            county="Galway",
            property_type="Detached house",
            preferred_package=RealEstateEnquiry.PreferredPackage.PRO,
            consent_to_contact=True,
        )

        rendered = self._render_booking_agreement_markdown(enquiry)
        pdf_bytes = generate_booking_agreement_pdf(enquiry)

        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertIn("| Agency / business name | Not provided |", rendered)
        self.assertIn("| Registered / business address | Not provided |", rendered)
        self.assertIn("| Listing type | Not provided |", rendered)
        self.assertIn("| Shoot time | Not provided |", rendered)
        self.assertIn("| Access contact on site | Not provided |", rendered)
        self.assertIn("| Access notes / restrictions | Not provided |", rendered)
        self.assertIn("| Travel details | Not provided |", rendered)
        self.assertIn("| VAT | Not provided |", rendered)
        self.assertIn("| Total fee payable | Not provided |", rendered)
        self.assertIn("| Deposit required | Not provided |", rendered)
        self.assertIn("| Remaining balance | Not provided |", rendered)
        information_sections = rendered.split("## 9. Signatures and Acceptance", 1)[0]
        self.assertNotIn("______________________________", information_sections)
        self.assertNotIn("To be confirmed", rendered)
        self.assertNotIn("To be confirmed by the Client", rendered)

    def test_booking_agreement_quote_amounts_and_signatures_render(self):
        enquiry = RealEstateEnquiry.objects.create(
            name="Jane Agent",
            email="jane@example.com",
            phone="+353 87 123 4567",
            company_name="Example Estate Agents",
            client_type=RealEstateEnquiry.ClientType.ESTATE_AGENT,
            property_address="Example House, Salthill",
            county="Galway",
            property_type="Detached house",
            preferred_package=RealEstateEnquiry.PreferredPackage.PRO,
            preferred_date="2026-06-20",
            quoted_price="399.00",
            consent_to_contact=True,
        )

        rendered = self._render_booking_agreement_markdown(enquiry)

        self.assertIn("| Package total | €399.00 |", rendered)
        self.assertIn("| VAT | €0.00 |", rendered)
        self.assertIn("| Total fee payable | €399.00 |", rendered)
        self.assertIn("| Deposit required | €119.70 |", rendered)
        self.assertIn("| Remaining balance | €279.30 |", rendered)
        self.assertIn("VAT not applicable", rendered)
        self.assertIn("Signed electronically for and on behalf of OpenÉire Studios", rendered)
        self.assertIn("| Name | Gerry Deely |", rendered)
        self.assertIn("| Title | OpenÉire Studios |", rendered)
        self.assertIn("Signed by or on behalf of the Client:", rendered)
        self.assertIn("| Name | ______________________________ |", rendered)
        self.assertIn("| Title | ______________________________ |", rendered)
        self.assertIn("| Date | ______________________________ |", rendered)
        self.assertIn(
            "By signing electronically and by paying the booking deposit after receipt of this Booking Agreement, the Client confirms",
            rendered,
        )
        self.assertIn(
            "private property owner, the Client may permit one appointed estate agent/auctioneer acting on their behalf",
            rendered,
        )

    def test_full_upfront_agreement_contains_no_deposit_or_balance_wording(self):
        enquiry = RealEstateEnquiry.objects.create(
            name="Jane Agent",
            email="jane@example.com",
            phone="+353 87 123 4567",
            client_type=RealEstateEnquiry.ClientType.ESTATE_AGENT,
            property_address="Example House",
            county="Galway",
            property_type="Detached house",
            preferred_package=RealEstateEnquiry.PreferredPackage.PRO,
            quoted_price="399.00",
            consent_to_contact=True,
            payment_arrangement=RealEstateEnquiry.PaymentArrangement.FULL_UPFRONT,
            expected_payment_method=RealEstateEnquiry.ExpectedPaymentMethod.STRIPE,
        )
        rendered = self._render_booking_agreement_markdown(enquiry)

        self.assertIn("| Payment arrangement | Full payment upfront |", rendered)
        self.assertIn("| Full payment due | €399.00 |", rendered)
        self.assertIn("No separate deposit or balance split applies", rendered)
        self.assertIn("less any amount already paid", rendered)
        self.assertNotIn("| Deposit required |", rendered)
        self.assertNotIn("| Remaining balance |", rendered)

    def test_full_on_shoot_day_cash_agreement_for_kevin(self):
        enquiry = RealEstateEnquiry.objects.create(
            name="Kevin O'Flynn",
            email="kevin@example.com",
            phone="+353 87 123 4567",
            client_type=RealEstateEnquiry.ClientType.PRIVATE_SELLER,
            property_address="Confirmed Pro Shoot",
            county="Galway",
            property_type="Detached house",
            preferred_package=RealEstateEnquiry.PreferredPackage.PRO,
            quoted_price="399.00",
            shoot_date="2026-07-21",
            consent_to_contact=True,
            payment_arrangement=RealEstateEnquiry.PaymentArrangement.FULL_ON_SHOOT_DAY,
            expected_payment_method=RealEstateEnquiry.ExpectedPaymentMethod.CASH,
        )
        rendered = self._render_booking_agreement_markdown(enquiry)

        self.assertIn("| Payment arrangement | Full payment on shoot day |", rendered)
        self.assertIn("| Full payment due | €399.00 |", rendered)
        self.assertIn("| Payment due date | 21 July 2026 |", rendered)
        self.assertIn("| Expected payment method | Cash |", rendered)
        self.assertIn("booking may be confirmed after the signed Booking Agreement is received, before payment is made", rendered)
        self.assertIn("Final high-resolution media and usage rights remain withheld until full payment has been received", rendered)
        self.assertIn("a receipt will be issued", rendered)
        self.assertIn("not contingent on the property being sold, let, or otherwise completed", rendered)
        self.assertNotIn("| Deposit required |", rendered)
        self.assertNotIn("| Remaining balance |", rendered)

    def test_long_populated_details_and_all_add_ons_render_in_separate_rows(self):
        enquiry = RealEstateEnquiry.objects.create(
            name="Alexandra-Marguerite Fitzwilliam-Smythe",
            email="alexandra.fitzwilliam-smythe@international-property-partners.example.com",
            phone="+353 87 123 4567",
            company_name="International Property Partners and Residential Advisory Services Limited",
            client_type=RealEstateEnquiry.ClientType.ESTATE_AGENT,
            property_address=(
                "Apartment 42, The Courtyard Residences, 123 Extremely Long Promenade Road, "
                "Salthill"
            ),
            county="Galway",
            eircode="H91 LONG",
            property_type="Detached house",
            preferred_package=RealEstateEnquiry.PreferredPackage.PRO,
            preferred_date="2026-06-20",
            shoot_date="2026-06-20",
            quoted_price="399.00",
            add_ons=["floor_plan", "additional_social_cuts", "travel_supplement"],
            payment_due_date="2026-06-20",
            expected_payment_method=RealEstateEnquiry.ExpectedPaymentMethod.STRIPE,
            consent_to_contact=True,
        )
        enquiry.registered_business_address = (
            "Suite 12, International Property Centre, Galway"
        )
        enquiry.listing_type = "Residential sale"
        enquiry.shoot_time = "10:30"
        enquiry.access_contact = "Property manager - +353 91 555 0101"
        enquiry.access_notes = "Use the courtyard entrance and call on arrival"
        enquiry.travel_details = "Travel supplement agreed for 62 km beyond the included radius"

        rendered = self._render_booking_agreement_markdown(enquiry)
        pdf_bytes = generate_booking_agreement_pdf(enquiry)

        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertIn(f"| Client name | {enquiry.name} |", rendered)
        self.assertIn(f"| Agency / business name | {enquiry.company_name} |", rendered)
        self.assertIn(
            "Apartment 42, The Courtyard Residences, 123 Extremely Long Promenade Road, "
            "Salthill, Galway, H91 LONG",
            rendered,
        )
        self.assertIn("Floor plan, 2D measured - €75", rendered)
        self.assertIn("Additional social media cuts - €50", rendered)
        self.assertIn("Travel supplement beyond 40 km - €0.50 per km", rendered)
        self.assertNotIn("Additional Agreed Add-Ons:\n\n- None", rendered)
        information_sections = rendered.split("Included Deliverables:", 1)[0]
        self.assertNotIn("Not provided", information_sections)

    def test_booking_agreement_snapshot_remains_unchanged_after_enquiry_edits(self):
        enquiry = RealEstateEnquiry.objects.create(
            name="Kevin O'Flynn",
            email="kevin@example.com",
            phone="+353 87 123 4567",
            client_type=RealEstateEnquiry.ClientType.PRIVATE_SELLER,
            property_address="Confirmed Pro Shoot",
            county="Galway",
            property_type="Detached house",
            preferred_package=RealEstateEnquiry.PreferredPackage.PRO,
            quoted_price="399.00",
            shoot_date="2026-07-21",
            consent_to_contact=True,
            payment_arrangement=RealEstateEnquiry.PaymentArrangement.FULL_ON_SHOOT_DAY,
            expected_payment_method=RealEstateEnquiry.ExpectedPaymentMethod.CASH,
        )
        first = render_booking_agreement_markdown(enquiry)
        enquiry.payment_due_date = "2026-07-30"
        enquiry.expected_payment_method = RealEstateEnquiry.ExpectedPaymentMethod.BANK_TRANSFER
        enquiry.save(update_fields=["payment_due_date", "expected_payment_method"])
        second = render_booking_agreement_markdown(enquiry)

        self.assertEqual(first, second)
        snapshot = RealEstateBookingAgreementSnapshot.objects.get(enquiry=enquiry)
        self.assertEqual(snapshot.payment_arrangement, RealEstateEnquiry.PaymentArrangement.FULL_ON_SHOOT_DAY)
        self.assertEqual(snapshot.total_required, Decimal("399.00"))
        self.assertEqual(snapshot.payment_due_date.isoformat(), "2026-07-21")
        self.assertEqual(snapshot.expected_payment_method, RealEstateEnquiry.ExpectedPaymentMethod.CASH)

    def test_new_agreement_version_captures_changed_add_ons_without_mutating_existing_snapshot(self):
        enquiry = RealEstateEnquiry.objects.create(
            name="Jane Agent",
            email="jane@example.com",
            phone="+353 87 123 4567",
            client_type=RealEstateEnquiry.ClientType.ESTATE_AGENT,
            property_address="Example House",
            county="Galway",
            property_type="Detached house",
            preferred_package=RealEstateEnquiry.PreferredPackage.PRO,
            quoted_price="399.00",
            add_ons=["floor_plan"],
            consent_to_contact=True,
        )
        issued_markdown = render_booking_agreement_markdown(enquiry)
        issued_snapshot = enquiry.booking_agreement_snapshots.get()

        enquiry.add_ons = ["floor_plan", "travel_supplement"]
        enquiry.save(update_fields=["add_ons"])

        unchanged_issued_markdown = render_booking_agreement_markdown(enquiry)
        new_markdown = render_booking_agreement_markdown(
            enquiry,
            create_new_version=True,
        )

        issued_snapshot.refresh_from_db()
        self.assertEqual(unchanged_issued_markdown, issued_markdown)
        self.assertEqual(issued_snapshot.rendered_markdown, issued_markdown)
        self.assertNotIn("Travel supplement beyond 40 km", issued_markdown)
        self.assertIn("Floor plan, 2D measured - €75", new_markdown)
        self.assertIn("Travel supplement beyond 40 km - €0.50 per km", new_markdown)
        self.assertEqual(enquiry.booking_agreement_snapshots.count(), 2)


class RealEstateTimelineEventTests(TestCase):
    def test_reference_url_has_explicit_checkout_safe_max_length(self):
        field = RealEstateTimelineEvent._meta.get_field("reference_url")

        self.assertEqual(field.max_length, 2048)

    def test_timeline_event_model_can_be_created(self):
        enquiry = RealEstateEnquiry.objects.create(
            name="Jane Agent",
            email="jane@example.com",
            phone="+353 87 123 4567",
            client_type=RealEstateEnquiry.ClientType.ESTATE_AGENT,
            property_address="Example House, Salthill, Galway",
            county="Galway",
            property_type="Detached house",
            preferred_package=RealEstateEnquiry.PreferredPackage.PRO,
            consent_to_contact=True,
        )

        event = RealEstateTimelineEvent.objects.create(
            enquiry=enquiry,
            event_type=RealEstateTimelineEvent.EventType.NOTE,
            status=RealEstateTimelineEvent.EventStatus.COMPLETED,
            actor_type=RealEstateTimelineEvent.ActorType.ADMIN,
            title="Internal note",
            notes="Useful context for the booking.",
        )

        self.assertEqual(event.enquiry, enquiry)
        self.assertEqual(str(event), f"Note - {enquiry}")


@override_settings(
    FRONTEND_URL="https://openeire.test",
    REALESTATE_REPLY_TO_EMAIL="studio@openeire.test",
)
class RealEstateDepositCancelledViewTests(TestCase):
    def test_cancellation_destination_returns_professional_confirmation(self):
        response = self.client.get(reverse("real-estate-deposit-cancelled"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertContains(response, "Deposit payment cancelled")
        self.assertContains(response, "No payment was taken")
        self.assertContains(response, "Pay Secure Deposit")
        self.assertContains(response, 'href="mailto:studio@openeire.test"')
        self.assertContains(response, 'href="https://openeire.test"')
        self.assertContains(response, 'name="robots" content="noindex,nofollow"')


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="studio@openeire.ie",
    REALESTATE_NOTIFICATION_EMAIL="shoots@openeire.ie",
    REALESTATE_REPLY_TO_EMAIL="shoots@openeire.ie",
    SECURE_SSL_REDIRECT=False,
)
class RealEstateEnquiryTests(APITestCase):
    def setUp(self):
        caches[getattr(settings, "THROTTLE_CACHE_ALIAS", "throttle")].clear()
        self.url = reverse("real-estate-enquiry-create")
        self.payload = {
            "name": "Jane Agent",
            "email": "jane@example.com",
            "phone": "+353 87 123 4567",
            "company_name": "Example Estate Agents",
            "client_type": "estate_agent",
            "property_address": "Example House, Salthill, Galway",
            "eircode": "H91 XXXX",
            "county": "Galway",
            "property_type": "Detached house",
            "preferred_package": "pro",
            "add_ons": ["floor_plan", "additional_social_cuts"],
            "preferred_date": "2026-06-20",
            "how_heard": "google",
            "message": "Vendor prefers morning access. Interested in drone video if weather allows.",
            "consent_to_contact": True,
        }

    def test_successful_enquiry_creates_record_and_returns_public_response(self):
        response = self.client.post(self.url, data=self.payload, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(RealEstateEnquiry.objects.count(), 1)
        enquiry = RealEstateEnquiry.objects.get()
        self.assertEqual(response.data["id"], enquiry.id)
        self.assertEqual(response.data["status"], "new")
        self.assertEqual(response.data["message"], "Enquiry received successfully.")
        self.assertNotIn("internal_notes", response.data)
        for private_pricing_field in (
            "quoted_price",
            "quoted_vat_amount",
            "quoted_total",
            "quoted_deposit_amount",
            "quoted_balance_due",
        ):
            self.assertNotIn(private_pricing_field, response.data)
        self.assertEqual(
            enquiry.delivery_provider,
            RealEstateEnquiry.DeliveryProvider.MYAIRBRIDGE,
        )
        event = enquiry.timeline_events.get()
        self.assertEqual(
            event.event_type,
            RealEstateTimelineEvent.EventType.ENQUIRY_RECEIVED,
        )
        self.assertEqual(event.status, RealEstateTimelineEvent.EventStatus.COMPLETED)
        self.assertEqual(event.actor_type, RealEstateTimelineEvent.ActorType.CLIENT)
        self.assertEqual(event.title, "Enquiry received")
        self.assertIn("Preferred package: Pro", event.notes)
        self.assertIn("Property address: Example House, Salthill, Galway", event.notes)

    def test_internal_notification_email_is_sent(self):
        self.client.post(self.url, data=self.payload, format="json")

        self.assertEqual(len(mail.outbox), 2)
        internal_email = mail.outbox[0]
        self.assertEqual(internal_email.to, ["shoots@openeire.ie"])
        self.assertIn(
            "New Property Shoot Enquiry - Galway - Pro",
            internal_email.subject,
        )
        self.assertIn("Jane Agent", internal_email.body)
        self.assertIn("View in admin:", internal_email.body)

    def test_client_confirmation_email_is_sent(self):
        self.client.post(self.url, data=self.payload, format="json")

        self.assertEqual(len(mail.outbox), 2)
        client_email = mail.outbox[1]
        self.assertEqual(client_email.to, ["jane@example.com"])
        self.assertEqual(client_email.reply_to, ["shoots@openeire.ie"])
        self.assertIn(
            "Property shoot request received - OpenÉire Studios",
            client_email.subject,
        )
        self.assertIn("Example House, Salthill, Galway", client_email.body)
        self.assertEqual(len(client_email.alternatives), 1)
        self.assertEqual(client_email.alternatives[0][1], "text/html")

    def test_consent_to_contact_false_is_rejected(self):
        payload = {**self.payload, "consent_to_contact": False}

        response = self.client.post(self.url, data=payload, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("consent_to_contact", response.data)
        self.assertEqual(RealEstateEnquiry.objects.count(), 0)

    def test_missing_required_fields_are_rejected(self):
        payload = {**self.payload}
        del payload["name"]

        response = self.client.post(self.url, data=payload, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("name", response.data)
        self.assertEqual(RealEstateEnquiry.objects.count(), 0)

    def test_whitespace_only_required_text_fields_are_rejected(self):
        payload = {
            **self.payload,
            "name": "   ",
            "phone": "   ",
        }

        response = self.client.post(self.url, data=payload, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("name", response.data)
        self.assertEqual(RealEstateEnquiry.objects.count(), 0)

    def test_invalid_preferred_package_is_rejected(self):
        payload = {**self.payload, "preferred_package": "ultimate"}

        response = self.client.post(self.url, data=payload, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("preferred_package", response.data)

    def test_invalid_add_ons_are_rejected(self):
        payload = {**self.payload, "add_ons": ["invalid_add_on"]}

        response = self.client.post(self.url, data=payload, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("add_ons", response.data)

    def test_optional_fields_can_be_blank(self):
        payload = {
            **self.payload,
            "company_name": "",
            "eircode": "",
            "add_ons": [],
            "preferred_date": None,
            "how_heard": "",
            "message": "",
        }

        response = self.client.post(self.url, data=payload, format="json")

        self.assertEqual(response.status_code, 201)
        enquiry = RealEstateEnquiry.objects.get()
        self.assertEqual(enquiry.company_name, "")
        self.assertEqual(enquiry.eircode, "")
        self.assertEqual(enquiry.add_ons, [])
        self.assertIsNone(enquiry.preferred_date)
        self.assertEqual(enquiry.how_heard, "")
        self.assertEqual(enquiry.message, "")

    @patch(
        "realestate.views.send_realestate_internal_notification_email",
        side_effect=RuntimeError("smtp timeout"),
    )
    @patch(
        "realestate.views.send_realestate_client_confirmation_email",
        side_effect=RuntimeError("smtp timeout"),
    )
    def test_email_failure_does_not_delete_saved_enquiry_or_return_500(
        self,
        _mock_client_email,
        _mock_internal_email,
    ):
        response = self.client.post(self.url, data=self.payload, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(RealEstateEnquiry.objects.count(), 1)
        self.assertEqual(RealEstateTimelineEvent.objects.count(), 1)

class RealEstateEnquiryAdminActionTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = get_user_model().objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password123",
        )
        self.enquiry = RealEstateEnquiry.objects.create(
            name="Jane Agent",
            email="jane@example.com",
            phone="+353 87 123 4567",
            company_name="Example Estate Agents",
            client_type=RealEstateEnquiry.ClientType.ESTATE_AGENT,
            property_address="Example House, Salthill, Galway",
            county="Galway",
            eircode="H91 XXXX",
            property_type="Detached house",
            preferred_package=RealEstateEnquiry.PreferredPackage.PRO,
            preferred_date="2026-06-20",
            shoot_date="2026-06-22",
            quoted_price="399.00",
            consent_to_contact=True,
        )
        self.model_admin = RealEstateEnquiryAdmin(RealEstateEnquiry, custom_admin_site)
        self.model_admin.message_user = Mock()

    def _request(self):
        request = self.factory.post("/secret-control-panel/realestate/realestateenquiry/")
        request.user = self.user
        return request

    def test_booking_delivery_admin_fields_are_ordered_and_helpful(self):
        request = self._request()
        booking_fieldset = next(
            fieldset
            for fieldset in self.model_admin.fieldsets
            if fieldset[0] == "Booking & Delivery Links"
        )

        self.assertEqual(
            booking_fieldset[1]["fields"],
            (
                "proposed_shoot_date",
                "booking_agreement_received",
                "delivery_provider",
                "delivery_link",
                "review_link",
                "booking_agreement_link",
            ),
        )
        compatibility_fieldset = next(
            fieldset
            for fieldset in self.model_admin.fieldsets
            if fieldset[0] == "Compatibility payment fields"
        )
        self.assertIn("collapse", compatibility_fieldset[1]["classes"])
        self.assertEqual(
            compatibility_fieldset[1]["fields"],
            (
                "deposit_payment_link",
                "stripe_deposit_session_id",
                "deposit_paid",
                "deposit_paid_at",
            ),
        )
        self.assertIn("booking_agreement_received", self.model_admin.list_display)
        self.assertIn("deposit_paid", self.model_admin.list_display)
        self.assertNotIn("delivery_provider", self.model_admin.list_display)
        self.assertIn("stripe_deposit_session_id", self.model_admin.readonly_fields)
        self.assertIn("deposit_paid_at", self.model_admin.readonly_fields)
        self.assertNotIn("deposit_payment_link", self.model_admin.readonly_fields)

        form = self.model_admin.get_form(request)
        self.assertIn(
            "Booking Agreement PDF is attached automatically",
            form.base_fields["booking_agreement_link"].help_text,
        )
        self.assertIn(
            "Where the finished media package is hosted",
            form.base_fields["delivery_provider"].help_text,
        )
        self.assertIn(
            'Secure download URL used for the "Download Files" button',
            form.base_fields["delivery_link"].help_text,
        )
        self.assertIn(
            "Review URL shown as the Follow-up/Thank-you email CTA",
            form.base_fields["review_link"].help_text,
        )

    @patch("realestate.admin.send_templated_email")
    def test_send_quote_email_uses_existing_context(self, mock_send_templated_email):
        request = self._request()

        self.model_admin.send_quote_email(
            request,
            RealEstateEnquiry.objects.filter(pk=self.enquiry.pk),
        )

        mock_send_templated_email.assert_called_once()
        kwargs = mock_send_templated_email.call_args.kwargs
        self.assertEqual(kwargs["template_base"], "quote")
        self.assertEqual(kwargs["to"], ["jane@example.com"])
        self.assertEqual(kwargs["context"]["quote_total"], "€399.00")
        self.assertEqual(kwargs["context"]["shoot_date"], "2026-06-22")
        self.model_admin.message_user.assert_any_call(
            request,
            "Quote email sent for 1 enquiry(s).",
            level=messages.SUCCESS,
        )
        event = self.enquiry.timeline_events.get(
            event_type=RealEstateTimelineEvent.EventType.QUOTE_SENT
        )
        self.assertEqual(event.status, RealEstateTimelineEvent.EventStatus.SENT)
        self.assertEqual(event.actor_type, RealEstateTimelineEvent.ActorType.ADMIN)
        self.assertEqual(event.title, "Quote email sent")
        self.assertEqual(event.email_template, "quote")
        self.assertEqual(event.recipient_email, "jane@example.com")
        self.assertEqual(event.created_by, self.user)

    @patch("realestate.admin.send_templated_email")
    def test_send_delivery_email_is_blocked_when_unpaid(self, mock_send_templated_email):
        request = self._request()

        self.model_admin.send_delivery_email(
            request,
            RealEstateEnquiry.objects.filter(pk=self.enquiry.pk),
        )

        mock_send_templated_email.assert_not_called()
        error_calls = [
            call
            for call in self.model_admin.message_user.call_args_list
            if call.kwargs.get("level") == messages.ERROR
        ]
        self.assertTrue(
            any(
                "Blocked delivery"
                in call.args[1]
                for call in error_calls
            )
        )

    @patch("realestate.admin.send_templated_email")
    def test_follow_up_email_warns_when_review_link_missing(self, mock_send_templated_email):
        request = self._request()

        self.model_admin.send_follow_up_email(
            request,
            RealEstateEnquiry.objects.filter(pk=self.enquiry.pk),
        )

        mock_send_templated_email.assert_called_once()
        warning_calls = [
            call
            for call in self.model_admin.message_user.call_args_list
            if call.kwargs.get("level") == messages.WARNING
        ]
        self.assertTrue(
            any(
                "Review CTA omitted because no review link is stored."
                in call.args[1]
                for call in warning_calls
            )
        )
        self.model_admin.message_user.assert_any_call(
            request,
            "Follow-up email sent for 1 enquiry(s).",
            level=messages.SUCCESS,
        )

    @patch("realestate.admin.send_templated_email")
    def test_thank_you_email_warns_when_review_link_missing(self, mock_send_templated_email):
        request = self._request()

        self.model_admin.send_thank_you_email(
            request,
            RealEstateEnquiry.objects.filter(pk=self.enquiry.pk),
        )

        mock_send_templated_email.assert_called_once()
        warning_calls = [
            call
            for call in self.model_admin.message_user.call_args_list
            if call.kwargs.get("level") == messages.WARNING
        ]
        self.assertTrue(
            any(
                "Review CTA omitted because no review link is stored."
                in call.args[1]
                for call in warning_calls
            )
        )
        self.model_admin.message_user.assert_any_call(
            request,
            "Thank-you email sent for 1 enquiry(s).",
            level=messages.SUCCESS,
        )

    @patch("realestate.admin.send_templated_email")
    def test_send_booking_agreement_email_attaches_pdf_without_agreement_link(self, mock_send_templated_email):
        request = self._request()

        self.model_admin.send_booking_agreement_email(
            request,
            RealEstateEnquiry.objects.filter(pk=self.enquiry.pk),
        )

        mock_send_templated_email.assert_called_once()
        kwargs = mock_send_templated_email.call_args.kwargs
        self.assertEqual(kwargs["template_base"], "booking_agreement")
        self.assertEqual(kwargs["to"], ["jane@example.com"])
        self.assertEqual(kwargs["context"]["booking_agreement_link"], "")
        self.assertEqual(len(kwargs["attachments"]), 1)
        filename, content, mimetype = kwargs["attachments"][0]
        self.assertEqual(
            filename,
            f"openeire-booking-agreement-re-{self.enquiry.id}-jane-agent.pdf",
        )
        self.assertTrue(content.startswith(b"%PDF"))
        self.assertEqual(mimetype, "application/pdf")
        self.model_admin.message_user.assert_any_call(
            request,
            "Booking agreement email sent for 1 enquiry(s).",
            level=messages.SUCCESS,
        )
        event = self.enquiry.timeline_events.get(
            event_type=RealEstateTimelineEvent.EventType.BOOKING_AGREEMENT_SENT
        )
        self.assertEqual(event.status, RealEstateTimelineEvent.EventStatus.SENT)
        self.assertEqual(event.email_template, "booking_agreement")
        self.assertEqual(event.reference_url, "")
        warning_calls = [
            call
            for call in self.model_admin.message_user.call_args_list
            if call.kwargs.get("level") == messages.WARNING
        ]
        self.assertEqual(warning_calls, [])
        snapshot = RealEstateBookingAgreementSnapshot.objects.get(enquiry=self.enquiry)
        self.assertEqual(snapshot.created_by, self.user)

    def test_operations_download_invoice_requires_change_permission(self):
        invoice = ensure_invoices_for_arrangement(self.enquiry)[0]
        limited_user = get_user_model().objects.create_user(
            username="limited",
            email="limited@example.com",
            password="password123",
            is_staff=True,
        )
        request = self.factory.get(
            "/secret-control-panel/realestate/realestateenquiry/ops/",
            data={"invoice": invoice.pk},
        )
        request.user = limited_user

        with self.assertRaises(PermissionDenied):
            self.model_admin.operations_action_view(
                request,
                str(self.enquiry.pk),
                "download-local-invoice",
            )

    def test_operations_invoice_lookup_rejects_void_invoice_id(self):
        invoice = ensure_invoices_for_arrangement(self.enquiry)[0]
        invoice.status = RealEstateInvoice.Status.VOID
        invoice.save(update_fields=["status", "updated_at"])
        request = self.factory.get(
            "/secret-control-panel/realestate/realestateenquiry/ops/",
            data={"invoice": invoice.pk},
        )
        request.user = self.user

        with self.assertRaises(RealEstateInvoice.DoesNotExist):
            self.model_admin._get_ops_invoice(self.enquiry, request)

    def test_email_confirmation_preview_uses_selected_invoice_amount(self):
        deposit, balance = ensure_invoices_for_arrangement(self.enquiry)
        request = self.factory.get(
            "/secret-control-panel/realestate/realestateenquiry/ops/",
            data={"invoice": deposit.pk},
        )
        request.user = self.user

        response = self.model_admin._confirm_email_action(
            request,
            self.enquiry,
            action="send-invoice-issued-email",
            title="Send invoice issued email",
            template_base="invoice_issued",
            invoice=deposit,
        )

        self.assertEqual(response.context_data["invoice"], deposit)
        self.assertIn("119.70", response.context_data["amount"])
        self.assertNotIn(f"{balance.total:.2f}", response.context_data["amount"])

    @patch("realestate.admin.send_templated_email")
    def test_cash_receipt_email_uses_selected_payment_receipt(self, mock_send_templated_email):
        invoice = ensure_invoices_for_arrangement(self.enquiry)[0]
        first_payment, _ = record_realestate_payment(
            invoice=invoice,
            amount=Decimal("40.00"),
            method=RealEstatePayment.Method.CASH,
            paid_at=timezone.now(),
            recorded_by=self.user,
            external_reference="First cash part",
        )
        second_payment, _ = record_realestate_payment(
            invoice=invoice,
            amount=Decimal("30.00"),
            method=RealEstatePayment.Method.CASH,
            paid_at=timezone.now(),
            recorded_by=self.user,
            external_reference="Second cash part",
        )
        request = self.factory.post(
            "/secret-control-panel/realestate/realestateenquiry/ops/",
            data={"payment": first_payment.pk},
        )
        request.user = self.user

        self.model_admin.operations_action_view(
            request,
            str(self.enquiry.pk),
            "send-cash-receipt-email",
        )

        mock_send_templated_email.assert_called_once()
        context = mock_send_templated_email.call_args.kwargs["context"]
        self.assertEqual(context["cash_receipt_number"], first_payment.cash_receipt_number)
        self.assertNotEqual(context["cash_receipt_number"], second_payment.cash_receipt_number)

    @patch("realestate.admin.send_templated_email")
    def test_weather_reschedule_skips_without_revised_date(self, mock_send_templated_email):
        request = self._request()
        self.enquiry.shoot_date = None
        self.enquiry.preferred_date = None
        self.enquiry.save(update_fields=["shoot_date", "preferred_date"])

        self.model_admin.send_weather_reschedule_email(
            request,
            RealEstateEnquiry.objects.filter(pk=self.enquiry.pk),
        )

        mock_send_templated_email.assert_not_called()
        self.model_admin.message_user.assert_any_call(
            request,
            "Skipped 1 enquiry(s) because required data was missing.",
            level=messages.WARNING,
        )

    @patch("realestate.admin.send_templated_email", side_effect=RuntimeError("smtp offline"))
    def test_action_failure_surfaces_admin_error_message(self, mock_send_templated_email):
        request = self._request()
        self.enquiry.deposit_paid = True
        self.enquiry.deposit_paid_at = timezone.now()
        self.enquiry.save(update_fields=["deposit_paid", "deposit_paid_at"])

        self.model_admin.send_confirmation_email(
            request,
            RealEstateEnquiry.objects.filter(pk=self.enquiry.pk),
        )

        mock_send_templated_email.assert_called_once()
        self.model_admin.message_user.assert_any_call(
            request,
            "Confirmation email failed for 1 enquiry(s).",
            level=messages.ERROR,
        )
        event = self.enquiry.timeline_events.get(
            event_type=RealEstateTimelineEvent.EventType.CONFIRMATION_SENT
        )
        self.assertEqual(event.status, RealEstateTimelineEvent.EventStatus.FAILED)
        self.assertEqual(event.actor_type, RealEstateTimelineEvent.ActorType.ADMIN)
        self.assertEqual(event.email_template, "confirmation")
        self.assertEqual(event.recipient_email, "jane@example.com")
        self.assertIn("RuntimeError: smtp offline", event.notes)

    @patch("realestate.admin.send_templated_email")
    @patch("realestate.payments.stripe.checkout.Session.create")
    def test_deposit_request_skips_without_booking_agreement_received(
        self,
        mock_session_create,
        mock_send_templated_email,
    ):
        request = self._request()

        self.model_admin.send_deposit_request_email(
            request,
            RealEstateEnquiry.objects.filter(pk=self.enquiry.pk),
        )

        mock_session_create.assert_not_called()
        mock_send_templated_email.assert_not_called()
        self.model_admin.message_user.assert_any_call(
            request,
            "Skipped 1 enquiry(s) because required data was missing.",
            level=messages.WARNING,
        )

    @patch("realestate.admin.send_templated_email")
    @patch("realestate.payments.stripe.checkout.Session.create")
    def test_deposit_request_creates_stripe_checkout_when_link_missing(
        self,
        mock_session_create,
        mock_send_templated_email,
    ):
        request = self._request()
        self.enquiry.booking_agreement_received = True
        self.enquiry.save(update_fields=["booking_agreement_received"])
        checkout_url = "https://checkout.stripe.com/c/pay/" + ("a" * 250)
        self.assertGreater(len(checkout_url), 200)
        mock_session_create.return_value = {
            "id": "cs_realestate_deposit",
            "url": checkout_url,
        }

        self.model_admin.send_deposit_request_email(
            request,
            RealEstateEnquiry.objects.filter(pk=self.enquiry.pk),
        )

        self.enquiry.refresh_from_db()
        self.assertEqual(
            self.enquiry.deposit_payment_link,
            checkout_url,
        )
        self.assertEqual(self.enquiry.stripe_deposit_session_id, "cs_realestate_deposit")
        mock_session_create.assert_called_once()
        call_kwargs = mock_session_create.call_args.kwargs
        self.assertEqual(call_kwargs["mode"], "payment")
        self.assertEqual(call_kwargs["line_items"][0]["price_data"]["unit_amount"], 11970)
        self.assertNotIn("tax_rates", call_kwargs["line_items"][0])
        self.assertNotIn("automatic_tax", call_kwargs)
        self.assertEqual(call_kwargs["metadata"]["realestate_enquiry_id"], str(self.enquiry.pk))
        self.assertEqual(call_kwargs["metadata"]["purpose"], "realestate_deposit")
        mock_send_templated_email.assert_called_once()
        email_context = mock_send_templated_email.call_args.kwargs["context"]
        self.assertEqual(
            email_context["deposit_payment_link"],
            checkout_url,
        )
        self.assertIn("119.70", email_context["deposit_amount"])
        self.assertIn("279.30", email_context["balance_due"])
        event = self.enquiry.timeline_events.get(
            event_type=RealEstateTimelineEvent.EventType.DEPOSIT_REQUEST_SENT
        )
        self.assertEqual(event.status, RealEstateTimelineEvent.EventStatus.SENT)
        self.assertEqual(event.reference_url, checkout_url)
        self.assertEqual(event.stripe_session_id, "cs_realestate_deposit")

    @override_settings(STRIPE_SECRET_KEY="sk_test_admin")
    @patch("realestate.admin.record_timeline_event", side_effect=DataError("timeline unavailable"))
    @patch("realestate.admin.send_templated_email")
    @patch("realestate.payments.stripe.checkout.Session.create")
    def test_deposit_timeline_failure_after_send_is_warning_not_email_failure(
        self,
        mock_session_create,
        mock_send_templated_email,
        mock_record_timeline_event,
    ):
        request = self._request()
        deposit_invoice = ensure_invoices_for_arrangement(self.enquiry)[0]
        self.enquiry.booking_agreement_received = True
        self.enquiry.deposit_payment_link = "https://checkout.stripe.com/existing"
        self.enquiry.stripe_deposit_session_id = "cs_test_existing"
        self.enquiry.save(
            update_fields=[
                "booking_agreement_received",
                "deposit_payment_link",
                "stripe_deposit_session_id",
            ]
        )

        valid_session = {
            "id": "cs_test_existing",
            "url": self.enquiry.deposit_payment_link,
            "status": "open",
            "payment_status": "unpaid",
            "expires_at": int(timezone.now().timestamp()) + 7200,
            "amount_total": 11970,
            "currency": "eur",
            "livemode": False,
            "metadata": {
                "purpose": "realestate_deposit",
                "realestate_enquiry_id": str(self.enquiry.pk),
                "realestate_invoice_number": deposit_invoice.invoice_number,
            },
        }
        with patch(
            "realestate.payments.stripe.checkout.Session.retrieve",
            return_value=valid_session,
        ):
            with self.assertLogs("realestate.admin", level="ERROR") as logs:
                response = self.model_admin.operations_action_view(
                    request,
                    str(self.enquiry.pk),
                    "send-deposit-request",
                )

        self.assertEqual(response.status_code, 302)
        mock_session_create.assert_not_called()
        mock_send_templated_email.assert_called_once()
        mock_record_timeline_event.assert_called_once()
        self.assertIn("Failed to record real estate email timeline event", logs.output[0])
        self.model_admin.message_user.assert_any_call(
            request,
            "Deposit request email sent for 1 enquiry(s).",
            level=messages.SUCCESS,
        )
        warning_messages = [
            call.args[1]
            for call in self.model_admin.message_user.call_args_list
            if call.kwargs.get("level") == messages.WARNING
        ]
        self.assertTrue(any("the email was sent" in message for message in warning_messages))
        self.assertFalse(
            any(
                call.kwargs.get("level") == messages.ERROR
                for call in self.model_admin.message_user.call_args_list
            )
        )

    @patch("realestate.admin.record_timeline_event", side_effect=DataError("timeline unavailable"))
    @patch("realestate.admin.send_templated_email", side_effect=RuntimeError("smtp offline"))
    def test_email_failure_is_not_masked_when_failed_timeline_write_also_fails(
        self,
        mock_send_templated_email,
        mock_record_timeline_event,
    ):
        request = self._request()
        self.enquiry.deposit_paid = True
        self.enquiry.deposit_paid_at = timezone.now()
        self.enquiry.save(update_fields=["deposit_paid", "deposit_paid_at"])

        with self.assertLogs("realestate.admin", level="ERROR") as logs:
            self.model_admin.send_confirmation_email(
                request,
                RealEstateEnquiry.objects.filter(pk=self.enquiry.pk),
            )

        mock_send_templated_email.assert_called_once()
        mock_record_timeline_event.assert_called_once()
        failed_event_call = mock_record_timeline_event.call_args
        self.assertEqual(
            failed_event_call.kwargs["status"],
            RealEstateTimelineEvent.EventStatus.FAILED,
        )
        self.assertIn("RuntimeError: smtp offline", failed_event_call.kwargs["notes"])
        self.assertIn("Failed to record real estate email timeline event", logs.output[0])
        self.model_admin.message_user.assert_any_call(
            request,
            "Confirmation email failed for 1 enquiry(s).",
            level=messages.ERROR,
        )
        warning_messages = [
            call.args[1]
            for call in self.model_admin.message_user.call_args_list
            if call.kwargs.get("level") == messages.WARNING
        ]
        self.assertTrue(any("the email failed" in message for message in warning_messages))

    @override_settings(STRIPE_SECRET_KEY="sk_test_admin")
    @patch("realestate.admin.send_templated_email")
    @patch("realestate.payments.stripe.checkout.Session.create")
    def test_deposit_request_reuses_existing_link(
        self,
        mock_session_create,
        mock_send_templated_email,
    ):
        request = self._request()
        deposit_invoice = ensure_invoices_for_arrangement(self.enquiry)[0]
        self.enquiry.booking_agreement_received = True
        self.enquiry.deposit_payment_link = "https://checkout.stripe.com/existing"
        self.enquiry.stripe_deposit_session_id = "cs_test_existing"
        self.enquiry.save(
            update_fields=[
                "booking_agreement_received",
                "deposit_payment_link",
                "stripe_deposit_session_id",
            ]
        )

        valid_session = {
            "id": "cs_test_existing",
            "url": self.enquiry.deposit_payment_link,
            "status": "open",
            "payment_status": "unpaid",
            "expires_at": int(timezone.now().timestamp()) + 7200,
            "amount_total": 11970,
            "currency": "eur",
            "livemode": False,
            "metadata": {
                "purpose": "realestate_deposit",
                "realestate_enquiry_id": str(self.enquiry.pk),
                "realestate_invoice_number": deposit_invoice.invoice_number,
            },
        }
        with patch(
            "realestate.payments.stripe.checkout.Session.retrieve",
            return_value=valid_session,
        ):
            self.model_admin.send_deposit_request_email(
                request,
                RealEstateEnquiry.objects.filter(pk=self.enquiry.pk),
            )

        mock_session_create.assert_not_called()
        mock_send_templated_email.assert_called_once()
        self.assertEqual(
            mock_send_templated_email.call_args.kwargs["context"]["deposit_payment_link"],
            "https://checkout.stripe.com/existing",
        )

    @override_settings(STRIPE_SECRET_KEY="sk_test_admin")
    @patch("realestate.admin.send_templated_email")
    @patch("realestate.payments.stripe.checkout.Session.create")
    def test_paid_deposit_session_is_reconciled_without_duplicate_email_or_session(
        self,
        mock_session_create,
        mock_send_templated_email,
    ):
        request = self._request()
        deposit_invoice = ensure_invoices_for_arrangement(self.enquiry)[0]
        self.enquiry.booking_agreement_received = True
        self.enquiry.deposit_payment_link = "https://checkout.stripe.com/already-paid"
        self.enquiry.stripe_deposit_session_id = "cs_test_already_paid"
        self.enquiry.save(
            update_fields=(
                "booking_agreement_received",
                "deposit_payment_link",
                "stripe_deposit_session_id",
            )
        )
        paid_session = {
            "id": "cs_test_already_paid",
            "url": self.enquiry.deposit_payment_link,
            "status": "complete",
            "payment_status": "paid",
            "expires_at": int(timezone.now().timestamp()) + 3600,
            "amount_total": 11970,
            "currency": "eur",
            "livemode": False,
            "payment_intent": "pi_test_already_paid",
            "metadata": {
                "purpose": "realestate_deposit",
                "realestate_enquiry_id": str(self.enquiry.pk),
                "realestate_invoice_number": deposit_invoice.invoice_number,
            },
        }

        with patch(
            "realestate.payments.stripe.checkout.Session.retrieve",
            return_value=paid_session,
        ):
            self.model_admin.send_deposit_request_email(
                request,
                RealEstateEnquiry.objects.filter(pk=self.enquiry.pk),
            )

        mock_session_create.assert_not_called()
        mock_send_templated_email.assert_not_called()
        self.assertTrue(
            RealEstatePayment.objects.filter(
                stripe_checkout_session_id="cs_test_already_paid",
                status=RealEstatePayment.Status.SUCCEEDED,
            ).exists()
        )
        self.model_admin.message_user.assert_any_call(
            request,
            "Payment already exists for 1 enquiry(s); no deposit request email was sent.",
            level=messages.INFO,
        )

    @patch("realestate.admin.send_templated_email")
    @patch("realestate.payments.stripe.checkout.Session.create")
    def test_deposit_request_rejects_non_deposit_payment_arrangements(
        self,
        mock_session_create,
        mock_send_templated_email,
    ):
        request = self._request()
        arrangements = (
            RealEstateEnquiry.PaymentArrangement.FULL_UPFRONT,
            RealEstateEnquiry.PaymentArrangement.FULL_ON_SHOOT_DAY,
            RealEstateEnquiry.PaymentArrangement.CUSTOM,
        )

        for arrangement in arrangements:
            with self.subTest(arrangement=arrangement):
                self.model_admin.message_user.reset_mock()
                self.enquiry.payment_arrangement = arrangement
                self.enquiry.booking_agreement_received = True
                if arrangement == RealEstateEnquiry.PaymentArrangement.CUSTOM:
                    self.enquiry.custom_payment_terms = "50% now, 50% on delivery"
                    self.enquiry.custom_required_total = Decimal("399.00")
                self.enquiry.save()

                self.model_admin.send_deposit_request_email(
                    request,
                    RealEstateEnquiry.objects.filter(pk=self.enquiry.pk),
                )

                self.model_admin.message_user.assert_any_call(
                    request,
                    "Skipped 1 enquiry(s) because required data was missing.",
                    level=messages.WARNING,
                )

        mock_session_create.assert_not_called()
        mock_send_templated_email.assert_not_called()

    @patch("realestate.admin.send_templated_email")
    @patch("realestate.payments.stripe.checkout.Session.create")
    def test_deposit_request_stripe_failure_does_not_send_email(
        self,
        mock_session_create,
        mock_send_templated_email,
    ):
        request = self._request()
        self.enquiry.booking_agreement_received = True
        self.enquiry.save(update_fields=["booking_agreement_received"])
        mock_session_create.side_effect = RuntimeError("stripe timeout")

        self.model_admin.send_deposit_request_email(
            request,
            RealEstateEnquiry.objects.filter(pk=self.enquiry.pk),
        )

        self.enquiry.refresh_from_db()
        self.assertEqual(self.enquiry.deposit_payment_link, "")
        mock_send_templated_email.assert_not_called()
        self.model_admin.message_user.assert_any_call(
            request,
            "Deposit request email failed for 1 enquiry(s).",
            level=messages.ERROR,
        )

    def test_save_model_records_booking_agreement_received_once(self):
        request = self._request()
        self.enquiry.booking_agreement_received = True

        self.model_admin.save_model(request, self.enquiry, form=Mock(), change=True)
        self.model_admin.save_model(request, self.enquiry, form=Mock(), change=True)

        events = self.enquiry.timeline_events.filter(
            event_type=RealEstateTimelineEvent.EventType.BOOKING_AGREEMENT_RECEIVED
        )
        self.assertEqual(events.count(), 1)
        event = events.get()
        self.assertEqual(event.status, RealEstateTimelineEvent.EventStatus.COMPLETED)
        self.assertEqual(event.actor_type, RealEstateTimelineEvent.ActorType.ADMIN)
        self.assertEqual(event.title, "Booking agreement marked as received")
        self.assertEqual(event.created_by, self.user)

    def test_save_model_records_shoot_scheduled_when_date_set_or_changed(self):
        request = self._request()
        self.enquiry.shoot_date = None
        self.enquiry.save(update_fields=["shoot_date"])

        self.enquiry.shoot_date = "2026-07-01"
        self.model_admin.save_model(request, self.enquiry, form=Mock(), change=True)
        self.enquiry.shoot_date = "2026-07-02"
        self.model_admin.save_model(request, self.enquiry, form=Mock(), change=True)
        self.model_admin.save_model(request, self.enquiry, form=Mock(), change=True)

        events = list(
            self.enquiry.timeline_events.filter(
                event_type=RealEstateTimelineEvent.EventType.SHOOT_SCHEDULED
            ).order_by("created_at")
        )
        self.assertEqual(len(events), 2)
        self.assertIn("Shoot date: 2026-07-01", events[0].notes)
        self.assertIn("Shoot date: 2026-07-02", events[1].notes)



