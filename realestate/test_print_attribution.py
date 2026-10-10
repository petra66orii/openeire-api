from unittest.mock import patch

from django.conf import settings
from django.core.cache import caches
from django.urls import reverse
from rest_framework.test import APITestCase

from .models import RealEstateEnquiry


class PrintEnquiryAttributionTests(APITestCase):
    def setUp(self):
        caches[getattr(settings, "THROTTLE_CACHE_ALIAS", "throttle")].clear()
        self.url = reverse("real-estate-enquiry-create")
        self.payload = {
            "name": "Jane Agent", "email": "jane@example.com",
            "phone": "+353 87 123 4567", "property_address": "Test House, Galway",
            "county": "Galway", "property_type": "house",
            "preferred_package": "starter", "consent_to_contact": True,
            "client_type": "estate_agent",
            "how_heard": "referral",
        }

    @patch("realestate.views.send_enquiry_notifications")
    def test_each_print_source_is_persisted_without_overwriting_customer_answer(self, notify):
        for source in ("flyer", "portfolio-card", "office-drop", "qr-sticker"):
            with self.subTest(source=source):
                response = self.client.post(self.url, {**self.payload, "print_source": source}, format="json")
                self.assertEqual(response.status_code, 201, response.data)
                enquiry = RealEstateEnquiry.objects.get(pk=response.data["id"])
                self.assertEqual(enquiry.print_source, source)
                self.assertEqual(enquiry.how_heard, "referral")

    @patch("realestate.views.send_enquiry_notifications")
    def test_old_and_non_print_clients_can_omit_source(self, notify):
        response = self.client.post(self.url, self.payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(RealEstateEnquiry.objects.get().print_source, "")

    @patch("realestate.views.send_enquiry_notifications")
    def test_invalid_source_is_rejected_before_creating_or_notifying(self, notify):
        response = self.client.post(self.url, {**self.payload, "print_source": "arbitrary-campaign"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("print_source", response.data)
        self.assertFalse(RealEstateEnquiry.objects.exists())
        notify.assert_not_called()
