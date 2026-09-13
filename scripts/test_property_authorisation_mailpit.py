"""Exercise real SMTP, Mailpit and HTTP acceptance using synthetic local records.

Run: python scripts/test_property_authorisation_mailpit.py --serve
Uses existing Mailpit on 127.0.0.1:1025/8025 and a separate SQLite database in tmp.
With --serve, leaves a loopback review server running for manual inspection.
Never connects to production SMTP or uses the project's customer database.
"""
import argparse
from datetime import timedelta
from email import policy
from email.parser import BytesParser
import json
import os
from pathlib import Path
import re
import secrets
import sys
from threading import Thread
from socketserver import ThreadingMixIn
from urllib.parse import urlparse
from wsgiref.simple_server import make_server, WSGIRequestHandler, WSGIServer


class PreviewServer(ThreadingMixIn, WSGIServer):
    # Browser preconnections must not block PDF and page requests.
    daemon_threads = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--serve-existing", action="store_true", help="Serve existing preview links without creating records or sending emails.")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Use an unprivileged local port between 1024 and 65535.")
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    os.environ["DEBUG"] = "True"
    os.environ["DJANGO_SETTINGS_MODULE"] = "openeire_api.settings_test"
    from django.conf import settings
    database = root / "tmp" / "property-authorisation-mailpit.sqlite3"
    database.parent.mkdir(parents=True, exist_ok=True)
    settings.DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(database), "OPTIONS": {"timeout": 20}}}
    settings.ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]
    settings.EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    settings.EMAIL_HOST = "127.0.0.1"
    settings.EMAIL_PORT = 1025
    settings.EMAIL_USE_TLS = False
    settings.EMAIL_USE_SSL = False
    settings.EMAIL_HOST_USER = ""
    settings.EMAIL_HOST_PASSWORD = ""
    settings.EMAIL_TIMEOUT = 10
    import django
    django.setup()
    from django.contrib.auth import get_user_model
    from django.core.management import call_command
    from django.core.wsgi import get_wsgi_application
    from django.contrib.staticfiles.handlers import StaticFilesHandler
    from django.test import RequestFactory
    from django.urls import reverse
    from django.utils import timezone
    from realestate.authorisations import issue_authorisation, send_authorisation
    from realestate.models import RealEstateEnquiry
    import requests
    import fitz

    client = requests.Session()
    client.trust_env = False
    mailpit = "http://127.0.0.1:8025"
    client.get(mailpit + "/api/v1/info", timeout=10).raise_for_status()
    call_command("migrate", verbosity=0, interactive=False)

    class QuietHandler(WSGIRequestHandler):
        def log_message(self, *args):
            pass  # Do not log capability URLs; assertions provide the test result.

    base = f"http://127.0.0.1:{args.port}"
    settings.REALESTATE_EMAIL_LOGO_URL = base + "/static/emails/openeire-studios-logo.png"
    server = make_server("127.0.0.1", args.port, StaticFilesHandler(get_wsgi_application()), server_class=PreviewServer, handler_class=QuietHandler)
    if args.serve_existing:
        print(f"Serving existing preview links on {base}", flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
        return
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    run_id = secrets.token_hex(4)
    password = secrets.token_urlsafe(24)
    staff = get_user_model().objects.create_superuser("mailpit-" + run_id, f"staff-{run_id}@example.invalid", password)
    results = []
    review_urls = {}

    def csrf(response):
        match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', response.text)
        assert match, "CSRF token not present"
        return match.group(1)

    def pdf_text(content):
        with fitz.open(stream=content, filetype="pdf") as pdf:
            return " ".join(" ".join(page.get_text() for page in pdf).split())

    try:
        for scenario in ("no-preferences", "custom-land", "handwritten", "manual-review"):
            recipient = f"authorisation-{scenario}-{run_id}@example.invalid"
            enquiry = RealEstateEnquiry.objects.create(
                name=f"MAILPIT TEST - {scenario}", email=recipient, phone="000 TEST ONLY",
                client_type="estate_agent", company_name="Synthetic Mailpit Test Agency",
                property_address="TEST ONLY - North Parcel, Example Farm" if scenario == "custom-land" else "TEST ONLY - 12 Example Road",
                county="Galway", property_type="agricultural" if scenario == "custom-land" else "house",
                preferred_package="custom" if scenario == "custom-land" else "pro",
                agreed_scope="12 aerial photographs of the north parcel\nOne 90-second boundary film" if scenario == "custom-land" else "",
                property_features="500 acres, forestry, bog and lakeside land" if scenario == "custom-land" else "Garden and exterior views",
                access_notes="TEST ONLY - meet at the gate", shoot_date=timezone.localdate() + timedelta(days=2),
                shoot_time="10:00", form_schema_version=2,
            )
            before = RealEstateEnquiry.objects.values().get(pk=enquiry.pk)
            doc = issue_authorisation(enquiry, location=enquiry.property_address, recipient_email=recipient, user=staff)
            request = RequestFactory().get("/", HTTP_HOST=f"127.0.0.1:{args.port}")
            send_authorisation(doc, request=request)
            doc.refresh_from_db()
            assert doc.status == "sent" and doc.accepted_at is None

            listing = client.get(mailpit + "/api/v1/messages", timeout=10).json()
            message = next(m for m in listing["messages"] if any(to["Address"] == recipient for to in m["To"]))
            raw = client.get(mailpit + f"/api/v1/message/{message['ID']}/raw", timeout=10)
            raw.raise_for_status()
            email = BytesParser(policy=policy.default).parsebytes(raw.content)
            body = email.get_body(preferencelist=("plain",)).get_content()
            html = email.get_body(preferencelist=("html",)).get_content()
            assert "No specific requirements" in body and "Both methods are equally acceptable" in body
            assert "Review &amp; complete authorisation" in html
            attachment = next(email.iter_attachments())
            assert attachment.get_content_type() == "application/pdf"
            assert "Signature:" in pdf_text(attachment.get_payload(decode=True))
            url = re.search(r"Review & complete authorisation: (http[^\s]+)", body).group(1)
            assert urlparse(url).netloc == f"127.0.0.1:{args.port}"
            review_urls[scenario] = url
            session = requests.Session()
            session.trust_env = False
            page = session.get(url, timeout=10)
            assert page.status_code == 200 and "Your confirmation" in page.text
            assert page.headers["Referrer-Policy"] == "no-referrer"
            assert url + "?download=1" in html
            download_page = session.get(url + "?download=1", timeout=10)
            assert download_page.status_code == 200 and "Your authorisation PDF" in download_page.text
            assert session.get(url + "pdf/", timeout=10).content == bytes(doc.issued_pdf)
            if scenario == "manual-review":
                results.append({"scenario": scenario, "result": "Email, PDF and live form verified; left awaiting manual acceptance", "mailpit_message": message["ID"]})
                continue

            data = {"full_name": "Alex Test", "email": recipient, "capacity": "representative", "restrictions_choice": "none", "hazards_choice": "none", "capture_choice": "none", "must_capture": "N/A", "video_choice": "none", "video_requirements": "N/A", "authority_confirmed": "on", "typed_signature": "Alex Test"}
            if scenario == "custom-land":
                data.update(restrictions_choice="specific", restrictions="Do not enter the derelict barn", hazards_choice="specific", hazards="Electric fencing and livestock", capture_choice="specific", must_capture="North parcel boundary, forestry and lake frontage", video_choice="specific", video_requirements="Establishing shot over the lake")
            if scenario == "handwritten":
                login_url = base + reverse("admin:login")
                login = session.get(login_url, timeout=10)
                response = session.post(login_url, data={"username": staff.username, "password": password, "csrfmiddlewaretoken": csrf(login), "next": reverse("admin:index")}, timeout=10)
                assert response.status_code == 200 and "Log out" in response.text
                staff_url = base + reverse("admin:realestate_realestateenquiry_ops_action", args=[enquiry.pk, "authorisation-handwritten"])
                page = session.get(staff_url, params={"document": doc.pk}, timeout=10)
                response = session.post(staff_url, data={**data, "document": doc.pk, "received_on": timezone.localdate().isoformat(), "csrfmiddlewaretoken": csrf(page)}, timeout=10, allow_redirects=False)
                assert response.status_code == 302, response.status_code
            else:
                assert session.post(url, data=data, timeout=10).status_code == 403
                invalid = session.post(url, data={"csrfmiddlewaretoken": csrf(page)}, timeout=10)
                assert invalid.status_code == 400 and "This field is required" in invalid.text
                response = session.post(url, data={**data, "csrfmiddlewaretoken": csrf(invalid)}, timeout=10)
                assert response.status_code == 200 and "Accepted electronically" in response.text
                duplicate = session.post(url, data={**data, "csrfmiddlewaretoken": csrf(invalid)}, timeout=10)
                assert duplicate.status_code == 409
            doc.refresh_from_db()
            assert doc.acceptance_method == ("handwritten" if scenario == "handwritten" else "electronic")
            pdf = session.get(url + "pdf/", timeout=10)
            assert pdf.content == bytes(doc.accepted_pdf)
            text = pdf_text(pdf.content)
            assert ("Handwritten copy received" if scenario == "handwritten" else "Accepted electronically") in text
            assert "Complete for handwritten acceptance" not in text
            assert RealEstateEnquiry.objects.values().get(pk=enquiry.pk) == before
            results.append({"scenario": scenario, "result": "PASS", "method": doc.acceptance_method, "mailpit_message": message["ID"]})

        report = {"mailpit_url": mailpit, "review_urls": review_urls, "results": results}
        (root / "tmp" / "property-authorisation-mailpit-results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)
        if args.serve:
            print(f"Review server remains available on {base}. Stop this process when finished.", flush=True)
            thread.join()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
