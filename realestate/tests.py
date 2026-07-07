from decimal import Decimal
from unittest.mock import Mock, call, patch

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import caches
from django.template.loader import get_template, render_to_string
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from openeire_api.admin import custom_admin_site
from openeire_api.pdf_markdown import render_markdown_to_flowables

from .admin import RealEstateEnquiryAdmin
from .documents import build_booking_agreement_filename
from .documents import _build_booking_agreement_context
from .documents import _load_booking_agreement_template
from .documents import generate_booking_agreement_pdf
from .emails import build_realestate_email_context
from .emails import format_money
from .emails import send_templated_email
from .models import RealEstateEnquiry
from .models import RealEstateTimelineEvent


REAL_ESTATE_EMAIL_TEMPLATE_CONTEXT = {
    "first_name": "Jane",
    "agency_name": "Example Estate Agents",
    "company_name": "Example Estate Agents",
    "property_address": "Example House, Salthill, Galway",
    "package_name": "Pro package",
    "addons": ["2D measured floor plan", "Additional social media cuts"],
    "quote_total": "399",
    "vat_total": "91.77",
    "total_including_vat": "490.77",
    "deposit_amount": "147.23",
    "balance_due": "343.54",
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

                self.assertIn("OpenEire Studios", html)
                self.assertIn("OpenEire Studios", text)
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
        self.assertIn('alt="OpenEire Studios"', html)

    def test_base_template_falls_back_to_text_when_logo_url_missing(self):
        html = render_to_string(
            "emails/base_email.html",
            {
                "email_logo_url": "",
                "brand_logo_url": "",
            },
        )

        self.assertIn(">OpenEire Studios<", html)
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
            "Your booking remains provisional until the signed agreement has been received and the booking deposit has cleared.",
            booking_text,
        )
        self.assertIn(
            "We have received the signed agreement and deposit in cleared funds",
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
        DEFAULT_FROM_EMAIL="OpenEire Studios <studio@openeire.ie>",
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
        self.assertEqual(format_money("EUR 399+VAT"), "")

    def test_quote_email_renders_logo_cta_and_price_summary(self):
        context = {
            **REAL_ESTATE_EMAIL_TEMPLATE_CONTEXT,
            "quote_total": "€399.00",
            "vat_total": "€91.77",
            "total_including_vat": "€490.77",
            "deposit_amount": "€147.23",
            "balance_due": "€343.54",
        }

        html = render_to_string("emails/real_estate/quote.html", context)
        text = render_to_string("emails/real_estate/quote.txt", context)

        self.assertIn("openeire-studios-logo.png", html)
        self.assertIn("Aerial Photography", html)
        self.assertIn("Property Media", html)
        self.assertIn("Visual Licensing", html)
        self.assertIn("Quote total (ex VAT)", html)
        self.assertIn("€399.00", html)
        self.assertIn("VAT (23%)", html)
        self.assertIn("€91.77", html)
        self.assertIn("Total incl. VAT", html)
        self.assertIn("€490.77", html)
        self.assertIn("Deposit required", html)
        self.assertIn("€147.23", html)
        self.assertIn("Balance on delivery", html)
        self.assertIn("€343.54", html)
        self.assertIn("Proceed with this quote", html)
        self.assertIn("mailto:shoots@openeire.ie", html)
        self.assertIn("Quote total (ex VAT): €399.00", text)
        self.assertIn("Proceed with this quote: shoots@openeire.ie", text)

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
        html = render_to_string(
            "emails/real_estate/deposit_request.html",
            REAL_ESTATE_EMAIL_TEMPLATE_CONTEXT,
        )

        self.assertIn("Pay Secure Deposit", html)
        self.assertIn("https://checkout.stripe.com/example", html)
        self.assertIn("Review Booking Agreement", html)

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

    def test_booking_agreement_missing_optional_fields_render_as_blank_lines(self):
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
        self.assertIn("**Agency / business name:** ______________________________", rendered)
        self.assertIn("**Registered / business address:** ______________________________", rendered)
        self.assertIn("**Listing type:** ______________________________", rendered)
        self.assertIn("**Shoot time:** ______________________________", rendered)
        self.assertIn("**Access contact on site:** ______________________________", rendered)
        self.assertIn("**Access notes / restrictions:** ______________________________", rendered)
        self.assertIn("**Travel details:** ______________________________", rendered)
        self.assertIn("**VAT:** ______________________________", rendered)
        self.assertIn("**Total fee including VAT:** ______________________________", rendered)
        self.assertIn("**Deposit required:** ______________________________", rendered)
        self.assertIn("**Balance due on delivery:** ______________________________", rendered)
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

        self.assertIn("**Package fee excluding VAT:** EUR 399.00", rendered)
        self.assertIn("**VAT:** EUR 91.77", rendered)
        self.assertIn("**Total fee including VAT:** EUR 490.77", rendered)
        self.assertIn("**Deposit required:** EUR 147.23", rendered)
        self.assertIn("**Balance due on delivery:** EUR 343.54", rendered)
        self.assertIn("Signed electronically for and on behalf of OpenEire Studios", rendered)
        self.assertIn("Name: Gerard Deely", rendered)
        self.assertIn("Title: OpenEire Studios", rendered)
        self.assertIn("Signed by or on behalf of the Client:", rendered)
        self.assertIn("Name: ______________________________", rendered)
        self.assertIn("Title: ______________________________", rendered)
        self.assertIn("Date: ______________________________", rendered)
        self.assertIn(
            "By signing electronically and by paying the booking deposit after receipt of this Booking Agreement, the Client confirms",
            rendered,
        )
        self.assertIn(
            "private property owner, the Client may permit one appointed estate agent/auctioneer acting on their behalf",
            rendered,
        )


class RealEstateTimelineEventTests(TestCase):
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
            "Property shoot request received - OpenEire Studios",
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
                "deposit_payment_link",
                "stripe_deposit_session_id",
                "deposit_paid",
                "deposit_paid_at",
                "delivery_provider",
                "delivery_link",
                "review_link",
                "booking_agreement_link",
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
    def test_send_delivery_email_warns_when_delivery_link_missing(self, mock_send_templated_email):
        request = self._request()

        self.model_admin.send_delivery_email(
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
                "Delivery email sent, but no delivery CTA was included because no delivery link is stored."
                in call.args[1]
                for call in warning_calls
            )
        )
        self.model_admin.message_user.assert_any_call(
            request,
            "Delivery email sent for 1 enquiry(s).",
            level=messages.SUCCESS,
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
        mock_session_create.return_value = {
            "id": "cs_realestate_deposit",
            "url": "https://checkout.stripe.com/c/pay/realestate",
        }

        self.model_admin.send_deposit_request_email(
            request,
            RealEstateEnquiry.objects.filter(pk=self.enquiry.pk),
        )

        self.enquiry.refresh_from_db()
        self.assertEqual(
            self.enquiry.deposit_payment_link,
            "https://checkout.stripe.com/c/pay/realestate",
        )
        self.assertEqual(self.enquiry.stripe_deposit_session_id, "cs_realestate_deposit")
        mock_session_create.assert_called_once()
        call_kwargs = mock_session_create.call_args.kwargs
        self.assertEqual(call_kwargs["mode"], "payment")
        self.assertEqual(call_kwargs["line_items"][0]["price_data"]["unit_amount"], 14723)
        self.assertEqual(call_kwargs["metadata"]["realestate_enquiry_id"], str(self.enquiry.pk))
        self.assertEqual(call_kwargs["metadata"]["purpose"], "realestate_deposit")
        mock_send_templated_email.assert_called_once()
        email_context = mock_send_templated_email.call_args.kwargs["context"]
        self.assertEqual(
            email_context["deposit_payment_link"],
            "https://checkout.stripe.com/c/pay/realestate",
        )
        self.assertIn("147.23", email_context["deposit_amount"])
        self.assertIn("343.54", email_context["balance_due"])
        event = self.enquiry.timeline_events.get(
            event_type=RealEstateTimelineEvent.EventType.DEPOSIT_REQUEST_SENT
        )
        self.assertEqual(event.status, RealEstateTimelineEvent.EventStatus.SENT)
        self.assertEqual(event.reference_url, "https://checkout.stripe.com/c/pay/realestate")
        self.assertEqual(event.stripe_session_id, "cs_realestate_deposit")

    @patch("realestate.admin.send_templated_email")
    @patch("realestate.payments.stripe.checkout.Session.create")
    def test_deposit_request_reuses_existing_link(
        self,
        mock_session_create,
        mock_send_templated_email,
    ):
        request = self._request()
        self.enquiry.booking_agreement_received = True
        self.enquiry.deposit_payment_link = "https://checkout.stripe.com/existing"
        self.enquiry.save(
            update_fields=["booking_agreement_received", "deposit_payment_link"]
        )

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



