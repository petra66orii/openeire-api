from unittest.mock import patch

from django.conf import settings
from django.core import mail
from django.core.cache import caches
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from .models import RealEstateEnquiry


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

    def test_internal_notification_email_is_sent(self):
        self.client.post(self.url, data=self.payload, format="json")

        self.assertEqual(len(mail.outbox), 2)
        internal_email = mail.outbox[0]
        self.assertEqual(internal_email.to, ["shoots@openeire.ie"])
        self.assertIn("New Property Shoot Enquiry - Galway - Pro", internal_email.subject)
        self.assertIn("Jane Agent", internal_email.body)
        self.assertIn("View in admin:", internal_email.body)

    def test_client_confirmation_email_is_sent(self):
        self.client.post(self.url, data=self.payload, format="json")

        self.assertEqual(len(mail.outbox), 2)
        client_email = mail.outbox[1]
        self.assertEqual(client_email.to, ["jane@example.com"])
        self.assertEqual(client_email.reply_to, ["shoots@openeire.ie"])
        self.assertIn("Property Shoot Request Received - OpenÉire Studios", client_email.subject)
        self.assertIn("Example House, Salthill, Galway", client_email.body)

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

    @patch("realestate.views.send_realestate_internal_notification_email", side_effect=RuntimeError("smtp timeout"))
    @patch("realestate.views.send_realestate_client_confirmation_email", side_effect=RuntimeError("smtp timeout"))
    def test_email_failure_does_not_delete_saved_enquiry_or_return_500(
        self,
        _mock_client_email,
        _mock_internal_email,
    ):
        response = self.client.post(self.url, data=self.payload, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(RealEstateEnquiry.objects.count(), 1)
