from copy import deepcopy
from datetime import date
from decimal import Decimal
from html import unescape
from pathlib import Path
from unittest.mock import patch
import re

import fitz
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from reportlab.platypus import Paragraph, Table

from openeire_api.pdf_branding import OpenEirePDFTheme, local_logo
from openeire_api.pdf_markdown import render_markdown_to_flowables
from .authorisations import accept_authorisation, issue_authorisation, send_authorisation
from .documents import generate_booking_agreement_pdf
from .emails import build_realestate_email_context
from .finance import ensure_invoices_for_arrangement, record_realestate_payment
from .financial_documents import generate_invoice_pdf, generate_cash_receipt_pdf
from .models import RealEstateEnquiry
from .payments import calculate_realestate_deposit_amounts


def normalized(text):
    return re.sub(r"\s+", "", unescape(text)).replace("\u2022", "")


def text_from_pdf(data):
    with fitz.open(stream=bytes(data), filetype="pdf") as pdf:
        return " ".join(page.get_text() for page in pdf)


class CustomerDocumentBrandingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = get_user_model().objects.create_superuser("branding", "branding@example.invalid", "unused")
        cls.enquiry = RealEstateEnquiry.objects.create(name="Alex Sample", email="client@example.invalid", phone="123", client_type="estate_agent", company_name="Sample Agency", property_address="12 Example Road", county="Galway", property_type="house", preferred_package="pro", quoted_price=Decimal("419"), shoot_date=date(2026, 10, 12), shoot_time="10:00", form_schema_version=2, payment_arrangement="full_on_shoot_day")

    def issue(self):
        return issue_authorisation(self.enquiry, location=self.enquiry.property_address, recipient_email=self.enquiry.email, user=self.staff)

    def answers(self):
        return {"full_name": "Alex Sample", "email": self.enquiry.email, "capacity": "owner", "restrictions_choice": "none", "hazards_choice": "none", "capture_choice": "none", "video_choice": "none", "authority_confirmed": "on", "typed_signature": "Alex Sample"}

    def test_markdown_theme_is_opt_in_and_does_not_change_content(self):
        markdown = "# Example\n\n## Terms\n\nBody €419\n\n| Key | Value |\n| --- | --- |\n| Name | Alex |"
        default = render_markdown_to_flowables(markdown)
        branded = render_markdown_to_flowables(markdown, theme=OpenEirePDFTheme())
        self.assertEqual(default[0].style.fontSize, 18)  # Existing ReportLab default.
        self.assertNotEqual(default[0].style.alignment, branded[0].style.alignment)
        self.assertEqual([x.getPlainText() for x in default if isinstance(x, Paragraph)], [x.getPlainText() for x in branded if isinstance(x, Paragraph)])
        default_table = next(x for x in default if isinstance(x, Table))
        branded_table = next(x for x in branded if isinstance(x, Table))
        self.assertEqual([[p.getPlainText() for p in row] for row in default_table._cellvalues], [[p.getPlainText() for p in row] for row in branded_table._cellvalues])

    def test_booking_text_and_snapshot_unchanged_by_rendering(self):
        pdf = generate_booking_agreement_pdf(self.enquiry, for_customer=True)
        snapshot = self.enquiry.booking_agreement_snapshots.get()
        original = deepcopy(snapshot.context), snapshot.rendered_markdown
        text = normalized(text_from_pdf(pdf))
        for item in render_markdown_to_flowables(snapshot.rendered_markdown):
            paragraphs = [item] if isinstance(item, Paragraph) else [p for row in item._cellvalues for p in row] if isinstance(item, Table) else []
            for paragraph in paragraphs:
                self.assertIn(normalized(paragraph.getPlainText()), text)
        self.enquiry.name = "Changed live name"
        self.enquiry.save()
        repeated = text_from_pdf(generate_booking_agreement_pdf(self.enquiry))
        self.assertIn("Alex Sample", repeated)
        self.assertNotIn("Changed live name", repeated)
        snapshot.refresh_from_db()
        self.assertEqual((snapshot.context, snapshot.rendered_markdown), original)
        self.assertEqual(self.enquiry.booking_agreement_snapshots.count(), 1)
        self.assertIn("€", repeated)
        self.assertNotIn("\ufffd", repeated)

    def test_invoice_and_receipt_values_preserved_without_mutations(self):
        calculate_realestate_deposit_amounts(self.enquiry)
        invoice = ensure_invoices_for_arrangement(self.enquiry)[0]
        values = (invoice.total, invoice.amount_paid, invoice.amount_outstanding, invoice.status)
        text = normalized(text_from_pdf(generate_invoice_pdf(invoice)))
        for expected in ("Total EUR 419.00", "Paid EUR 0.00", "Outstanding EUR 419.00", "VAT not applicable — supplier not VAT registered."):
            self.assertIn(normalized(expected), text)
        invoice.refresh_from_db()
        self.assertEqual((invoice.total, invoice.amount_paid, invoice.amount_outstanding, invoice.status), values)
        payment, _ = record_realestate_payment(invoice=invoice, amount=invoice.total, method="cash", paid_at=timezone.now(), recorded_by=self.staff, external_reference="Alex")
        invoice.refresh_from_db()
        self.assertIn(normalized("Outstanding EUR 0.00"), normalized(text_from_pdf(generate_invoice_pdf(invoice))))
        receipt = text_from_pdf(generate_cash_receipt_pdf(payment))
        self.assertIn("CASH RECEIPT", receipt)
        self.assertIn(payment.cash_receipt_number, receipt)
        self.assertIn("EUR 419.00", receipt)

    def test_invoice_pdf_itemises_essential_photos_and_vertical_video(self):
        enquiry = RealEstateEnquiry.objects.create(
            name="Jennifer Sample",
            email="jennifer@example.invalid",
            phone="123",
            client_type="estate_agent",
            company_name="Sample Agency",
            property_address="Four Bedroom House",
            county="Galway",
            property_type="house",
            preferred_package="essential",
            quoted_price=Decimal("275.00"),
            add_ons=["additional_stills", "additional_social_cuts"],
            additional_stills_quantity=5,
            shoot_date=date(2026, 10, 14),
            payment_arrangement="full_on_shoot_day",
        )
        calculate_realestate_deposit_amounts(enquiry)
        invoice = ensure_invoices_for_arrangement(enquiry)[0]

        text = normalized(text_from_pdf(generate_invoice_pdf(invoice)))

        for expected in (
            "Essential Package — 10 edited ground photographs EUR 175.00",
            "Additional edited photographs (5 × EUR 10.00) EUR 50.00",
            "Vertical 9:16 social-media property video EUR 50.00",
            "Total EUR 275.00",
        ):
            self.assertIn(normalized(expected), text)

    @override_settings(BUSINESS_SIGNATORY_NAME="PRIVATE LEGAL SIGNATORY", SHOW_SIGNATORY_ON_LEGAL_DOCUMENTS=True)
    def test_identity_privacy_and_logo_fallback(self):
        calculate_realestate_deposit_amounts(self.enquiry)
        invoice = ensure_invoices_for_arrangement(self.enquiry)[0]
        with patch("openeire_api.pdf_branding.local_logo", return_value=None):
            text = text_from_pdf(generate_invoice_pdf(invoice))
            self.assertIn("OpenÉire Studios", text)
            self.assertIn("openeire.ie", text)
            self.assertNotIn("PRIVATE LEGAL SIGNATORY", text)
            self.assertIn("PRIVATE LEGAL SIGNATORY", text_from_pdf(self.issue().issued_pdf))

    def test_logo_missing_on_disk_falls_back_without_network(self):
        local_logo.cache_clear()
        try:
            with patch("openeire_api.pdf_branding.LOGO_PATH", Path("missing-logo.png")):
                self.assertIsNone(local_logo())
        finally:
            local_logo.cache_clear()

    def test_canonical_email_logo_is_discoverable_for_collectstatic(self):
        from django.contrib.staticfiles import finders
        discovered = finders.find("emails/openeire-studios-logo.png")
        self.assertIsNotNone(discovered)
        self.assertEqual(Path(discovered).resolve(), (settings.BASE_DIR / "static" / "emails" / "openeire-studios-logo.png").resolve())

    def test_stored_authorisation_pdfs_and_content_are_not_regenerated(self):
        doc = self.issue()
        issued_pdf, issued_snapshot = bytes(doc.issued_pdf), deepcopy(doc.issued_snapshot)
        doc = accept_authorisation(doc, self.answers())
        accepted_pdf, accepted_snapshot = bytes(doc.accepted_pdf), deepcopy(doc.accepted_snapshot)
        with patch("realestate.authorisations.generate_pdf", side_effect=AssertionError("Must not regenerate stored PDF")):
            response = self.client.get(reverse("property-authorisation-pdf", args=[doc.token]))
        self.assertEqual(response.content, accepted_pdf)
        doc.refresh_from_db()
        self.assertEqual(bytes(doc.issued_pdf), issued_pdf)
        self.assertEqual(doc.issued_snapshot, issued_snapshot)
        self.assertEqual(doc.accepted_snapshot, accepted_snapshot)
        text = text_from_pdf(accepted_pdf)
        for heading, wording in issued_snapshot["terms"]:
            self.assertIn(normalized(wording), normalized(text))
        self.assertIn("Accepted electronically", text)
        self.assertIn("No specific requirements", text)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", REALESTATE_EMAIL_LOGO_URL="https://example.invalid/logo.png")
    def test_authorisation_email_uses_shared_base_and_cta(self):
        doc = self.issue()
        send_authorisation(doc, request=RequestFactory().get("/"))
        message = mail.outbox[0]
        html = message.alternatives[0][0]
        self.assertIn("Aerial Photography", html)
        self.assertIn("Visual Licensing", html)
        self.assertIn("https://example.invalid/logo.png", html)
        self.assertIn("Review &amp; complete authorisation", html)
        self.assertIn("background: #16a34a", html)
        self.assertIn("Property / locations covered", html)
        self.assertIn("No specific requirements", message.body)
        self.assertIn("Both methods are equally acceptable", html)
        self.assertNotIn("business_signatory_name", html)

    def test_all_existing_realestate_email_templates_render_with_shared_branding(self):
        context = build_realestate_email_context(self.enquiry)
        context.update(location=self.enquiry.property_address, cta_url="https://example.invalid/review", cta_label="Review & complete authorisation", review_url="https://example.invalid/review", pdf_url="https://example.invalid/pdf")
        directory = settings.BASE_DIR / "templates" / "emails" / "real_estate"
        for template in directory.glob("*.html"):
            with self.subTest(template=template.name):
                html = render_to_string("emails/real_estate/" + template.name, context)
                text = render_to_string("emails/real_estate/" + template.with_suffix(".txt").name, context)
                self.assertIn("<!doctype html>", html)
                self.assertIn("max-width: 600px", html)
                self.assertIn("Aerial Photography", html)
                self.assertIn("role=\"presentation\"", html)
                self.assertTrue(text.strip())

    def test_long_property_and_description_render_without_narrow_cell_overflow(self):
        calculate_realestate_deposit_amounts(self.enquiry)
        invoice = ensure_invoices_for_arrangement(self.enquiry)[0]
        invoice.property_reference_snapshot = "Long parcel description with multiple access locations. " * 100
        invoice.description = "Detailed agreed scope on multiple parcels. " * 200
        pdf = generate_invoice_pdf(invoice)
        raw = text_from_pdf(pdf)
        raw = re.sub(r"INVOICE\s+OpenÉire Studios \|[^\n]+\nPage \d+", "", raw)
        text = normalized(raw)
        self.assertIn(normalized(invoice.property_reference_snapshot), text)
        self.assertIn(normalized(invoice.description), text)
