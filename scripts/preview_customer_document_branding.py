"""Generate synthetic document-family samples without using customer records.

python scripts/preview_customer_document_branding.py --output output/pdf/branding
Add --mailpit to send local-only email previews to the existing Mailpit instance.
"""
import argparse
from datetime import date
from decimal import Decimal
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("output/pdf/branding"))
    parser.add_argument("--mailpit", action="store_true")
    parser.add_argument("--review-url", default="", help="Live local authorisation URL for the email previews")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ["DEBUG"] = "True"
    os.environ["DJANGO_SETTINGS_MODULE"] = "openeire_api.settings_test"
    from django.conf import settings
    settings.DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
    import django
    django.setup()
    from django.contrib.auth import get_user_model
    from django.core.management import call_command
    from django.template.loader import render_to_string
    from django.test import override_settings
    from django.utils import timezone
    from realestate.authorisations import accept_authorisation, issue_authorisation
    from realestate.documents import generate_booking_agreement_pdf
    from realestate.emails import build_realestate_email_context, send_templated_email
    from realestate.finance import ensure_invoices_for_arrangement, record_realestate_payment
    from realestate.financial_documents import generate_invoice_pdf, generate_cash_receipt_pdf
    from realestate.models import RealEstateEnquiry
    from realestate.payments import calculate_realestate_deposit_amounts
    import fitz

    call_command("migrate", verbosity=0, interactive=False)
    args.output.mkdir(parents=True, exist_ok=True)
    staff = get_user_model().objects.create_superuser("preview", "staff@example.invalid", "unused")
    captured = {}

    def save(name, content):
        (args.output / f"{name}.pdf").write_bytes(content)
        with fitz.open(stream=content, filetype="pdf") as document:
            captured[name] = "\n".join(page.get_text() for page in document)
            for index, page in enumerate(document, 1):
                page.get_pixmap(matrix=fitz.Matrix(1.2, 1.2)).save(str(args.output / f"{name}-{index}.png"))
            print(f"{name}: {len(document)} pages")

    for arrangement in ("deposit_then_balance", "full_on_shoot_day"):
        enquiry = RealEstateEnquiry.objects.create(name="Alex Murphy (sample)", email="branding-preview@example.invalid", phone="091 555 0101", client_type="estate_agent", company_name="Example Property Agency", property_address="12 Example Road, Loughrea", county="Galway", property_type="House", preferred_package="pro", quoted_price=Decimal("419"), shoot_date=date(2026, 10, 12), shoot_time="10:00", access_contact="Alex Murphy - 091 555 0101", access_notes="Meet at the front gate.", payment_arrangement=arrangement, expected_payment_method="cash", form_schema_version=2)
        calculate_realestate_deposit_amounts(enquiry)
        invoices = ensure_invoices_for_arrangement(enquiry)
        save("booking-" + arrangement, generate_booking_agreement_pdf(enquiry, for_customer=True))
    invoice = invoices[0]
    save("invoice-outstanding", generate_invoice_pdf(invoice))
    payment, _ = record_realestate_payment(invoice=invoice, amount=invoice.total, method="cash", paid_at=timezone.now(), recorded_by=staff, external_reference="Alex Murphy (sample)")
    invoice.refresh_from_db()
    save("invoice-paid", generate_invoice_pdf(invoice))
    save("cash-receipt", generate_cash_receipt_pdf(payment))
    for variant in ("print-ready", "electronic", "specific", "no-specific"):
        doc = issue_authorisation(enquiry, location=enquiry.property_address, recipient_email=enquiry.email, user=staff)
        if variant != "print-ready":
            data = {"full_name": "Alex Murphy", "email": enquiry.email, "capacity": "agent", "restrictions_choice": "none", "hazards_choice": "none", "capture_choice": "none", "must_capture": "N/A", "video_choice": "none", "video_requirements": "N/A", "authority_confirmed": "on", "typed_signature": "Alex Murphy"}
            if variant == "specific":
                data.update(restrictions_choice="specific", restrictions="Do not enter the detached barn.", hazards_choice="specific", hazards="Livestock behind the north fence; keep the gate closed.", capture_choice="specific", must_capture="Garden frontage, north boundary, access lane and lake view. Capture the forestry parcel from the marked safe access point.", video_choice="specific", video_requirements="Establishing shot showing the driveway and lake view.")
            doc = accept_authorisation(doc, data)
        save("authorisation-" + variant, bytes(doc.accepted_pdf or doc.issued_pdf))
    (args.output / "extracted-text.json").write_text(json.dumps(captured, ensure_ascii=False, indent=2), encoding="utf-8")
    context = build_realestate_email_context(enquiry)
    review_url = args.review_url or "https://example.invalid/authorisation/"
    context.update(location=enquiry.property_address, review_url=review_url, pdf_url=review_url + "?download=1", cta_url=review_url, cta_label="Review & complete authorisation", invoice_number=invoice.invoice_number, outstanding_amount="€0.00", stripe_hosted_invoice_url="https://example.invalid/invoice", deposit_payment_link="https://example.invalid/deposit", booking_agreement_link="https://example.invalid/agreement", delivery_link="https://example.invalid/delivery", cash_receipt_number=payment.cash_receipt_number, amount_paid="€419.00")
    if args.review_url:
        from urllib.parse import urlsplit
        parsed = urlsplit(args.review_url)
        if parsed.hostname not in ("127.0.0.1", "localhost"):
            parser.error("Preview review URLs must refer to the local test server.")
        context["email_logo_url"] = f"{parsed.scheme}://{parsed.netloc}/static/emails/openeire-studios-logo.png"
    for template in ("enquiry_reply", "confirmation", "booking_agreement", "deposit_request", "invoice_issued", "cash_receipt", "delivery", "property_authorisation"):
        (args.output / f"email-{template}.html").write_text(render_to_string(f"emails/real_estate/{template}.html", context), encoding="utf-8")
        if args.mailpit:
            with override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend", EMAIL_HOST="127.0.0.1", EMAIL_PORT=1025, EMAIL_HOST_USER="", EMAIL_HOST_PASSWORD="", EMAIL_USE_TLS=False, EMAIL_USE_SSL=False):
                send_templated_email(subject=f"LOCAL BRANDING PREVIEW - {template}", to=[enquiry.email], template_base=template, context=context)
    print(args.output)


if __name__ == "__main__":
    main()
