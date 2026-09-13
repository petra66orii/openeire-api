"""Generate three synthetic authorisation PDFs in an isolated in-memory database.

Run: python scripts/preview_property_authorisations.py
Requires requirements-test.txt for PNG rendering. Never sends email.
"""
from datetime import date
import os
from pathlib import Path
import sys


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ["DEBUG"] = "True"
    os.environ["DJANGO_SETTINGS_MODULE"] = "openeire_api.settings_test"
    from django.conf import settings
    settings.DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
    import django
    django.setup()
    from django.contrib.auth import get_user_model
    from django.core.management import call_command
    from django.utils import timezone
    from realestate.authorisations import accept_authorisation, issue_authorisation
    from realestate.models import RealEstateEnquiry
    import fitz

    call_command("migrate", verbosity=0, interactive=False)
    staff = get_user_model().objects.create_superuser("sample-staff", "staff@example.invalid", "unused")
    enquiry = RealEstateEnquiry.objects.create(name="Alex Murphy (sample)", email="client@example.invalid", phone="091 555 0101", client_type="estate_agent", company_name="Example Property Agency", property_address="North land parcel, Example Farm", county="Galway", property_type="agricultural", preferred_package="custom", agreed_scope="12 aerial photographs of the north land parcel\nOne 90-second boundary film", property_features="500 acres; forestry, bog and lakeside land", access_contact="Alex Murphy - 091 555 0101", access_notes="Meet at the north gate", shoot_date=date(2026, 10, 12), shoot_time="10:00", form_schema_version=2)
    output = Path("output/pdf/property-authorisation")
    previews = Path("tmp/pdfs/property-authorisation")
    output.mkdir(parents=True, exist_ok=True)
    previews.mkdir(parents=True, exist_ok=True)
    data = {"full_name": "Alex Murphy", "email": "client@example.invalid", "capacity": "representative", "restrictions_choice": "specific", "restrictions": "Do not enter the derelict barn in the south parcel.", "hazards_choice": "specific", "hazards": "Electric fencing along the eastern boundary. Livestock in the lower field.", "capture_choice": "specific", "must_capture": "North access point, lake frontage and forestry boundary. Bog parcel from the marked safe access point only.", "video_choice": "none", "video_requirements": "N/A", "authority_confirmed": "on", "typed_signature": "Alex Murphy", "received_on": timezone.localdate().isoformat()}
    for method in ("print-ready", "electronic", "handwritten"):
        doc = issue_authorisation(enquiry, location=enquiry.property_address, recipient_email=enquiry.email, user=staff)
        if method != "print-ready":
            doc = accept_authorisation(doc, data, method=method, user=staff if method == "handwritten" else None)
        pdf = bytes(doc.accepted_pdf or doc.issued_pdf)
        path = output / f"{method}.pdf"
        path.write_bytes(pdf)
        with fitz.open(stream=pdf, filetype="pdf") as rendered:
            for number, page in enumerate(rendered, 1):
                page.get_pixmap(matrix=fitz.Matrix(1.2, 1.2)).save(str(previews / f"{method}-{number}.png"))
            print(f"{path}: {len(rendered)} pages")


if __name__ == "__main__":
    main()
