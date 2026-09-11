"""Generate realistic agreement samples in an isolated, in-memory database.

Run from the repository root: python scripts/preview_booking_agreements.py
Add --mailpit to capture the four sample emails at localhost:1025.
No customer records or production SMTP credentials are used.
"""
import argparse
from datetime import date
from decimal import Decimal
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mailpit", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("tmp/booking-agreement-samples"))
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ["DEBUG"] = "True"
    os.environ["DJANGO_SETTINGS_MODULE"] = "openeire_api.settings_test"
    from django.conf import settings
    settings.DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
    import django
    django.setup()
    from django.core.management import call_command
    from realestate.documents import generate_booking_agreement_pdf
    from realestate.emails import build_realestate_email_context, send_templated_email
    from realestate.models import RealEstateEnquiry
    from realestate.package_catalogue import get_package

    call_command("migrate", verbosity=0, interactive=False)
    args.output.mkdir(parents=True, exist_ok=True)
    for arrangement in RealEstateEnquiry.PaymentArrangement.values:
        custom = arrangement == "custom"
        enquiry = RealEstateEnquiry.objects.create(
            name="Alex Murphy (sample)", email="agreement-preview@example.invalid",
            company_name="Example Property Agency", phone="091 555 0101",
            property_address="12 Example Road", county="Galway", property_type="Detached house",
            preferred_package="custom" if custom else "pro",
            quoted_price=Decimal("700") if custom else Decimal(get_package("pro").price_eur),
            shoot_date=date(2026, 10, 12), shoot_time="10:00", payment_due_date=date(2026, 10, 12),
            access_contact="Alex Murphy - 091 555 0101", access_notes="Meet at the front gate.",
            payment_arrangement=arrangement, expected_payment_method="cash" if arrangement == "full_on_shoot_day" else "bank_transfer",
            custom_required_total=Decimal("700") if custom else None,
            custom_payment_terms="Acceptance confirms the booking. EUR 700 is due on 12 October 2026." if custom else "",
            agreed_scope="12 aerial photographs of the north land parcel\nOne 90-second boundary film, delivered within five business days" if custom else "",
            consent_to_contact=True,
        )
        pdf = generate_booking_agreement_pdf(enquiry, for_customer=True)
        filename = f"booking-agreement-{arrangement}.pdf"
        (args.output / filename).write_bytes(pdf)
        if args.mailpit:
            assert (settings.EMAIL_HOST, settings.EMAIL_PORT) == ("localhost", 1025)
            assert not any((settings.EMAIL_HOST_USER, settings.EMAIL_HOST_PASSWORD, settings.EMAIL_USE_TLS, settings.EMAIL_USE_SSL))
            snapshot = enquiry.booking_agreement_snapshots.first()
            context = build_realestate_email_context(enquiry, package_name=snapshot.context["package_name"])
            send_templated_email(
                subject=f"LOCAL SAMPLE - Booking Agreement - {arrangement}", to=[enquiry.email],
                template_base="booking_agreement", context=context,
                attachments=[(filename, pdf, "application/pdf")],
            )
        print(args.output / filename)


if __name__ == "__main__":
    main()
