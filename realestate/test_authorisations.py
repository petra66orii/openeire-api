from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import fitz
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .authorisation_forms import AuthorisationForm
from .authorisations import accept_authorisation, document_sections, issue_authorisation, send_authorisation
from .authorisation_views import hub_context
from .models import PropertyAuthorisation, PropertyScopeCheck, RealEstateEnquiry


def pdf_text(value):
    with fitz.open(stream=bytes(value), filetype="pdf") as document:
        return " ".join(" ".join(page.get_text() for page in document).split())


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class PropertyAuthorisationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = get_user_model().objects.create_superuser("access-staff", "staff@example.com", "test-password")
        cls.enquiry = RealEstateEnquiry.objects.create(name="Test Client", email="client@example.com", phone="123", client_type="estate_agent", property_address="The Farm, North Parcel", county="Galway", property_type="agricultural", preferred_package="pro", quoted_price=Decimal("419"), company_name="Agency", access_contact_name="Owner", access_contact_phone="456", access_notes="Meet at north gate", property_features="Forestry and lake", shoot_date=timezone.localdate() + timedelta(days=2), shoot_time="10:30", form_schema_version=2)

    def issue(self, **kwargs):
        return issue_authorisation(self.enquiry, location=kwargs.pop("location", self.enquiry.property_address), recipient_email=kwargs.pop("recipient_email", self.enquiry.email), user=self.staff, **kwargs)

    def data(self, **kwargs):
        return {"full_name": "Property Owner", "email": "owner@example.com", "capacity": "owner", "restrictions_choice": "none", "hazards_choice": "none", "capture_choice": "none", "video_choice": "none", "authority_confirmed": "on", "typed_signature": "Property Owner", **kwargs}

    def test_email_download_opens_private_page_before_attachment(self):
        doc = self.issue()
        send_authorisation(doc, request=RequestFactory().get("/"))
        landing = self.url(doc) + "?download=1"
        self.assertIn(landing, mail.outbox[-1].alternatives[0][0])
        response = self.client.get(landing)
        self.assertContains(response, "Your authorisation PDF")
        self.assertContains(response, self.url(doc, pdf=True))
        self.assertNotContains(response, "<form")
        self.assertIn("no-store", response["Cache-Control"])
        pdf = self.client.get(self.url(doc, pdf=True))
        self.assertEqual(pdf.content, bytes(doc.issued_pdf))
        doc.revoked_at = timezone.now()
        doc.save(update_fields=["revoked_at"])
        self.assertEqual(self.client.get(landing).status_code, 410)

    def test_pdf_immutability_compares_contents_across_binary_types(self):
        doc = self.issue()
        doc = accept_authorisation(doc, self.data())
        doc.issued_pdf = memoryview(bytes(doc.issued_pdf)).cast("c")
        doc.accepted_pdf = memoryview(bytes(doc.accepted_pdf)).cast("c")
        doc.revoked_at = timezone.now()
        doc.save(update_fields=["revoked_at"])
        doc.refresh_from_db()
        self.assertIsNotNone(doc.revoked_at)
        for field in ("issued_pdf", "accepted_pdf"):
            with self.subTest(field=field):
                original = getattr(doc, field)
                setattr(doc, field, memoryview(b"changed PDF").cast("c"))
                with self.assertRaises(ValidationError):
                    doc.save()
                setattr(doc, field, original)

    def url(self, doc, pdf=False):
        return reverse("property-authorisation-pdf" if pdf else "property-authorisation", args=[doc.token])

    def ops(self, action):
        return reverse("admin:realestate_realestateenquiry_ops_action", args=[self.enquiry.pk, "authorisation-" + action])

    def test_generation_prefills_booking_details_and_private_identity(self):
        with override_settings(BUSINESS_SIGNATORY_NAME="Private Signatory", BUSINESS_ADDRESS="Business Address", SHOW_SIGNATORY_ON_LEGAL_DOCUMENTS=True):
            doc = self.issue()
        self.assertEqual(doc.template_version, "1.0")
        text = pdf_text(doc.issued_pdf)
        for expected in ("North Parcel", "Forestry and lake", "Meet at north gate", "Owner 456", "10:30", "Agency", "Private Signatory", "Business Address", "RE-", "Signature:", "Email:", "Date:"):
            self.assertIn(expected, text)
        self.assertEqual(doc.status, "draft")
        self.assertEqual(self.enquiry.booking_agreement_snapshots.count(), 0)

    def test_requires_persisted_enquiry(self):
        with self.assertRaises(RealEstateEnquiry.DoesNotExist):
            issue_authorisation(RealEstateEnquiry(), location="Farm", recipient_email="a@example.com", user=self.staff)

    def test_standard_residential_and_custom_land_render(self):
        for package, property_type, scope in [("essential", "house", ""), ("custom", "site_land", "12 aerial photographs of bog, forestry and lake\nOne boundary film of 500 acres")]:
            with self.subTest(package=package):
                self.enquiry.preferred_package = package
                self.enquiry.property_type = property_type
                self.enquiry.agreed_scope = scope
                self.enquiry.save()
                doc = self.issue()
                self.assertTrue(bytes(doc.issued_pdf).startswith(b"%PDF"))
                if scope:
                    self.assertIn("500 acres", pdf_text(doc.issued_pdf))

    def test_no_specific_requirements_blank_and_na(self):
        for value in ("", "N/A"):
            with self.subTest(value=value):
                doc = accept_authorisation(self.issue(), self.data(must_capture=value))
                text = pdf_text(doc.accepted_pdf)
                self.assertIn("No specific requirements", text)
                self.assertIn("professional judgement", text)
                self.assertIn("agreed deliverables remain intact", text)
                self.assertIn("30 professionally edited", text)
                self.assertIn("does not waive or reduce", text)
                self.assertEqual(doc.acceptance_method, "electronic")

    def test_specific_instructions_hazards_and_restrictions_preserved(self):
        doc = self.issue()
        issued = bytes(doc.issued_pdf)
        accepted = accept_authorisation(doc, self.data(capture_choice="specific", must_capture="North boundary fencing and lake", hazards_choice="specific", hazards="Electric fencing near bog", restrictions_choice="specific", restrictions="Do not enter derelict barn"))
        before = deepcopy(accepted.accepted_snapshot)
        self.enquiry.property_address = "Different address"
        self.enquiry.agreed_scope = "Changed scope"
        self.enquiry.save()
        accepted.refresh_from_db()
        self.assertEqual(accepted.accepted_snapshot, before)
        self.assertEqual(bytes(accepted.issued_pdf), issued)
        text = pdf_text(accepted.accepted_pdf)
        for value in ("North boundary fencing and lake", "Electric fencing near bog", "Do not enter derelict barn"):
            self.assertIn(value, text)
        self.assertNotIn("Different address", text)

    def test_issued_wording_survives_template_change_before_acceptance(self):
        doc = self.issue()
        with patch("realestate.authorisations.TERMS", [("Changed", "Different terms")]):
            accepted = accept_authorisation(doc, self.data())
        self.assertEqual(accepted.accepted_snapshot["terms"], doc.issued_snapshot["terms"])
        self.assertNotIn("Different terms", pdf_text(accepted.accepted_pdf))

    def test_video_preferences_optional_or_specific(self):
        for choice, text in [("none", "N/A"), ("", ""), ("specific", "Establishing shot over the lake")]:
            accepted = accept_authorisation(self.issue(), self.data(video_choice=choice, video_requirements=text))
            self.assertEqual(accepted.accepted_snapshot["answers"]["video_requirements"], text)
            self.assertIn(text if choice == "specific" else "No specific video requirements", pdf_text(accepted.accepted_pdf))

    def test_video_fields_not_shown_or_added_when_not_in_scope(self):
        self.enquiry.preferred_package = "essential"
        self.enquiry.save()
        doc = self.issue()
        response = self.client.get(self.url(doc))
        self.assertNotContains(response, 'name="video_choice"')
        accepted = accept_authorisation(doc, self.data(video_choice="specific", video_requirements="Secret new film"))
        self.assertNotIn("video_requirements", accepted.accepted_snapshot["answers"])
        self.assertNotIn("Secret new film", pdf_text(accepted.accepted_pdf))

    def test_conditional_input_validation(self):
        for values, field in [({"capacity": "other"}, "capacity_other"), ({"capture_choice": "specific"}, "must_capture"), ({"capture_choice": "specific", "must_capture": "N/A"}, "must_capture"), ({"hazards_choice": "specific"}, "hazards"), ({"restrictions_choice": "specific"}, "restrictions"), ({"video_choice": "specific"}, "video_requirements"), ({"must_capture": "Actually required"}, "must_capture"), ({"typed_signature": "Someone Else"}, "typed_signature"), ({"email": "invalid"}, "email"), ({"authority_confirmed": ""}, "authority_confirmed")]:
            with self.subTest(values=values):
                form = AuthorisationForm(self.data(**values), video_included=True)
                self.assertFalse(form.is_valid())
                self.assertIn(field, form.errors)

    def test_electronic_acceptance_public_without_login(self):
        doc = self.issue()
        self.assertEqual(self.client.get(self.url(doc)).status_code, 200)
        response = self.client.post(self.url(doc), self.data())
        self.assertEqual(response.status_code, 302)
        doc.refresh_from_db()
        self.assertEqual(doc.accepted_name, "Property Owner")
        self.assertEqual(doc.accepted_capacity, "Property owner")
        self.assertEqual(doc.accepted_email, "owner@example.com")
        self.assertIsNotNone(doc.accepted_at)
        self.assertEqual(doc.acceptance_method, "electronic")
        self.assertEqual(doc.template_version, "1.0")
        text = pdf_text(doc.accepted_pdf)
        self.assertIn("Accepted electronically", text)
        self.assertNotIn("Signature: ____", text)
        self.assertNotIn("Complete for handwritten acceptance", text)

    def test_duplicate_acceptance_is_conflict_and_preserves_original(self):
        doc = accept_authorisation(self.issue(), self.data())
        before = bytes(doc.accepted_pdf)
        response = self.client.post(self.url(doc), self.data(full_name="Another", typed_signature="Another"))
        self.assertEqual(response.status_code, 409)
        with self.assertRaises(ValueError):
            accept_authorisation(doc, self.data())
        doc.refresh_from_db()
        self.assertEqual(bytes(doc.accepted_pdf), before)

    def test_invalid_revoked_expired_and_superseded_links(self):
        self.assertEqual(self.client.get(reverse("property-authorisation", args=["123"])).status_code, 410)
        doc = self.issue()
        doc.revoked_at = timezone.now()
        doc.save()
        for pdf in (False, True):
            self.assertEqual(self.client.get(self.url(doc, pdf)).status_code, 410)
        expired = self.issue()
        with patch("realestate.authorisations.timezone.now", return_value=expired.expires_at + timedelta(seconds=1)):
            self.assertEqual(self.client.post(self.url(expired), self.data()).status_code, 410)
        old = self.issue()
        replacement = self.issue(supersedes=old)
        self.assertEqual(replacement.supersedes_id, old.pk)
        self.assertEqual(self.client.get(self.url(old)).status_code, 410)
        with self.assertRaises(ValueError):
            self.issue(supersedes=old)

    def test_get_pdf_and_invalid_post_do_not_accept(self):
        doc = self.issue()
        response = self.client.get(self.url(doc, pdf=True))
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(self.client.post(self.url(doc), {}).status_code, 400)
        doc.refresh_from_db()
        self.assertIsNone(doc.accepted_at)
        self.assertEqual(doc.status, "draft")

    def test_csrf_required_and_secure_headers(self):
        doc = self.issue()
        client = Client(enforce_csrf_checks=True)
        response = client.get(self.url(doc))
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertIn("noindex", response["X-Robots-Tag"])
        self.assertEqual(client.post(self.url(doc), self.data()).status_code, 403)
        response = client.post(self.url(doc), {**self.data(), "csrfmiddlewaretoken": client.cookies["csrftoken"].value})
        self.assertEqual(response.status_code, 302)

    def test_html_and_pdf_escape_client_markup(self):
        doc = accept_authorisation(self.issue(), self.data(capture_choice="specific", must_capture='<script>alert("x")</script> <b>Gate</b>'))
        response = self.client.get(self.url(doc))
        self.assertNotContains(response, '<script>alert(')
        self.assertContains(response, '&lt;script&gt;')
        self.assertIn('<b>Gate</b>', pdf_text(doc.accepted_pdf))

    def test_document_content_cannot_be_edited_or_deleted(self):
        doc = self.issue()
        doc.location = "Another property"
        with self.assertRaises(ValidationError):
            doc.save()
        doc.refresh_from_db()
        doc = accept_authorisation(doc, self.data())
        doc.accepted_name = "Changed"
        with self.assertRaises(ValidationError):
            doc.save()
        with self.assertRaises(ValidationError):
            doc.delete()

    def test_handwritten_records_correct_method_and_date(self):
        doc = accept_authorisation(self.issue(), self.data(received_on=timezone.localdate().isoformat()), method="handwritten", user=self.staff)
        self.assertEqual(doc.status, "handwritten")
        self.assertEqual(doc.acceptance_method, "handwritten")
        self.assertEqual(doc.received_on, timezone.localdate())
        self.assertEqual(doc.recorded_by, self.staff)
        self.assertEqual(doc.accepted_capacity, "Property owner")
        text = pdf_text(doc.accepted_pdf)
        self.assertIn("Handwritten copy received", text)
        self.assertNotIn("Accepted electronically", text)
        self.assertNotIn("Typed signature:", text)
        self.assertNotIn("Signature: ____", text)

    def test_handwritten_requires_staff_and_received_date(self):
        with self.assertRaises(ValueError):
            accept_authorisation(self.issue(), self.data(), method="handwritten")
        for received in ("", (timezone.localdate() + timedelta(days=1)).isoformat()):
            with self.assertRaises(ValueError):
                accept_authorisation(self.issue(), self.data(received_on=received), method="handwritten", user=self.staff)

    def test_required_scope_clauses_and_no_liability_waiver(self):
        text = " ".join(body for heading, body in self.issue().issued_snapshot["terms"])
        for phrase in ("owner's authority", "known hazards", "professional judgement", "another visit", "expressly included in the agreed written scope", "safely and lawfully", "not automatically amend", "not prevent a complaint"):
            self.assertIn(phrase, text)
        self.assertNotIn("no liability", text.lower())

    def test_send_email_records_sent_only_and_preserves_local_backend(self):
        doc = self.issue(recipient_email="owner@example.com")
        request = RequestFactory().get("/", HTTP_HOST="testserver")
        sent = send_authorisation(doc, request=request)
        self.assertEqual(sent.status, "sent")
        self.assertIsNotNone(sent.sent_at)
        self.assertIsNone(sent.accepted_at)
        self.assertEqual(mail.outbox[0].to, ["owner@example.com"])
        self.assertIn("No specific requirements", mail.outbox[0].body)
        self.assertIn("Both methods are equally acceptable", mail.outbox[0].body)
        self.assertTrue(mail.outbox[0].attachments)
        self.assertIn(doc.token, mail.outbox[0].body)

    def test_failed_email_remains_unsent(self):
        doc = self.issue()
        with patch("realestate.authorisations.EmailMultiAlternatives.send", return_value=0):
            with self.assertRaises(ValueError):
                send_authorisation(doc, request=RequestFactory().get("/"))
        doc.refresh_from_db()
        self.assertIsNone(doc.sent_at)
        self.assertEqual(doc.status, "draft")

    def test_multi_property_has_separate_authority_and_signer(self):
        north = self.issue(location="North parcel")
        south = self.issue(location="South parcel", recipient_email="different-owner@example.com")
        self.assertNotEqual(north.token, south.token)
        accept_authorisation(north, self.data())
        south.refresh_from_db()
        self.assertIsNone(south.accepted_at)
        self.assertEqual(south.issued_snapshot["initial"]["full_name"], "")
        self.assertTrue(hub_context(self.enquiry)["awaiting"])

    def test_warning_and_financial_confirmation_unchanged(self):
        self.enquiry.status = "booked"
        self.enquiry.booking_agreement_received = True
        self.enquiry.deposit_paid = True
        self.enquiry.save()
        before = RealEstateEnquiry.objects.values().get(pk=self.enquiry.pk)
        self.assertTrue(hub_context(self.enquiry)["warning"])
        accept_authorisation(self.issue(), self.data())
        self.assertFalse(hub_context(self.enquiry)["warning"])
        self.assertEqual(RealEstateEnquiry.objects.values().get(pk=self.enquiry.pk), before)
        self.assertEqual(self.enquiry.invoices.count(), 0)

    def test_hub_renders_status_actions_and_warning(self):
        self.client.force_login(self.staff)
        doc = self.issue()
        response = self.client.get(reverse("admin:realestate_realestateenquiry_change", args=[self.enquiry.pk]))
        self.assertEqual(response.status_code, 200)
        for value in ("Property Access &amp; Shoot Authorisation", "Shoot approaching", "Record handwritten", "Generate / Preview", "financial confirmation is independent"):
            self.assertContains(response, value)
        accept_authorisation(doc, self.data())
        response = self.client.get(reverse("admin:realestate_realestateenquiry_change", args=[self.enquiry.pk]))
        self.assertContains(response, "Accepted electronically")

    def test_staff_issue_review_handwritten_and_reissue(self):
        self.client.force_login(self.staff)
        response = self.client.post(self.ops("issue"), {"location": "North parcel", "recipient_email": "owner@example.com"})
        self.assertEqual(response.status_code, 302)
        doc = self.enquiry.property_authorisations.first()
        response = self.client.get(self.ops("review"), {"document": doc.pk})
        self.assertContains(response, doc.token)
        response = self.client.post(self.ops("handwritten"), {"document": doc.pk, **self.data(received_on=timezone.localdate().isoformat())})
        self.assertEqual(response.status_code, 302)
        doc.refresh_from_db()
        self.assertEqual(doc.acceptance_method, "handwritten")
        response = self.client.post(self.ops("reissue"), {"document": doc.pk, "location": "North parcel", "recipient_email": "owner@example.com"})
        self.assertEqual(response.status_code, 302)
        doc.refresh_from_db()
        self.assertIsNotNone(doc.superseded_at)
        self.assertEqual(doc.acceptance_method, "handwritten")

    def test_staff_scope_check_all_outcomes_and_timestamp(self):
        self.client.force_login(self.staff)
        for outcome in PropertyScopeCheck.Outcome.values:
            data = {"location": "North parcel", "person_confirming": "Owner present", "capacity": "Owner", "outcome": outcome, "notes": "Discussed before departure"}
            if outcome == "outstanding":
                data["outstanding_capture"] = "Boundary photographs pending due to livestock"
            else:
                data["all_agreed_completed"] = "on"
            response = self.client.post(self.ops("scope-check"), data)
            self.assertEqual(response.status_code, 302)
            check = self.enquiry.property_scope_checks.first()
            self.assertEqual(check.outcome, outcome)
            self.assertEqual(check.person_confirming, "Owner present")
            self.assertIsNotNone(check.created_at)
            self.assertEqual(check.recorded_by, self.staff)

    def test_outstanding_scope_check_requires_details_and_consistency(self):
        check = PropertyScopeCheck(enquiry=self.enquiry, location="Farm", person_confirming="Owner", recorded_by=self.staff, outcome="outstanding", all_agreed_completed=False)
        with self.assertRaises(ValidationError):
            check.save()
        check.outstanding_capture = "Barn"
        check.all_agreed_completed = True
        with self.assertRaises(ValidationError):
            check.save()
        check.all_agreed_completed = False
        check.save()
        with self.assertRaises(ValidationError):
            check.save()

    def test_staff_permission_and_enquiry_document_binding(self):
        doc = self.issue()
        self.assertEqual(self.client.get(self.ops("review"), {"document": doc.pk}).status_code, 302)
        limited = get_user_model().objects.create_user("limited", is_staff=True)
        self.client.force_login(limited)
        self.assertEqual(self.client.post(self.ops("handwritten"), {"document": doc.pk, **self.data()}).status_code, 403)
        self.client.force_login(self.staff)
        other = RealEstateEnquiry.objects.create(name="Other", email="other@example.com", property_address="Other", preferred_package="essential")
        path = reverse("admin:realestate_realestateenquiry_ops_action", args=[other.pk, "authorisation-review"])
        self.assertEqual(self.client.get(path, {"document": doc.pk}).status_code, 404)

    def test_no_sensitive_admin_or_tracking_fields_on_public_document(self):
        self.enquiry.internal_notes = "PRIVATE STAFF SECRET"
        self.enquiry.stripe_customer_id = "cus_private"
        self.enquiry.save()
        doc = self.issue()
        response = self.client.get(self.url(doc))
        for value in ("PRIVATE STAFF SECRET", "cus_private", "fingerprint", "geolocation"):
            self.assertNotContains(response, value)
        snapshot = accept_authorisation(doc, self.data()).accepted_snapshot
        self.assertNotIn("ip", snapshot["acceptance"])
