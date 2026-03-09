import shutil
import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models.signals import post_save
from django.test import TestCase, override_settings
from django.urls import reverse

from products.models import (
    LicenseRequest,
    Photo,
    StripeWebhookEvent,
    LicenceDocument,
    LicenseRequestAuditLog,
    generate_variants_for_photo,
)


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='licensing@example.com',
    STRIPE_WEBHOOK_SECRET='whsec_test',
)
class StripeWebhookLicenseTests(TestCase):
    def setUp(self):
        base_media_root = Path(__file__).resolve().parent.parent / ".test_media"
        self.media_root = base_media_root / uuid.uuid4().hex
        self.media_root.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)
        self._media_settings = self.settings(MEDIA_ROOT=self.media_root)
        self._media_settings.enable()
        self.addCleanup(self._media_settings.disable)

        post_save.disconnect(generate_variants_for_photo, sender=Photo)
        self.addCleanup(post_save.connect, generate_variants_for_photo, sender=Photo)

        preview = SimpleUploadedFile("preview.jpg", b"preview", content_type="image/jpeg")
        high_res = SimpleUploadedFile("high_res.jpg", b"high_res", content_type="image/jpeg")
        self.photo = Photo.objects.create(
            title="Test Photo",
            description="Test description",
            collection="Test Collection",
            preview_image=preview,
            high_res_file=high_res,
            price_hd=Decimal("10.00"),
            price_4k=Decimal("20.00"),
            is_active=True,
        )

        self.license_request = LicenseRequest.objects.create(
            content_type=ContentType.objects.get_for_model(self.photo),
            object_id=self.photo.id,
            client_name="Test Client",
            company="Test Co",
            email="test@example.com",
            project_type="COMMERCIAL",
            duration="1_YEAR",
            message="Test message",
            status="PAYMENT_PENDING",
            quoted_price=Decimal("250.00"),
            stripe_payment_link_id="plink_123",
        )

        self.url = reverse("webhook")

    def _event_payload(self, event_id="evt_123"):
        return {
            "id": event_id,
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_123",
                    "payment_link": "plink_123",
                    "payment_status": "paid",
                    "payment_intent": "pi_test_123",
                }
            },
        }

    @patch("checkout.views.stripe.Webhook.construct_event")
    def test_payment_link_maps_to_license_request(self, mock_construct):
        mock_construct.return_value = self._event_payload()

        response = self.client.post(
            self.url,
            data="{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="sig",
        )

        self.assertEqual(response.status_code, 200)
        self.license_request.refresh_from_db()
        self.assertEqual(self.license_request.status, "DELIVERED")
        self.assertIsNotNone(self.license_request.paid_at)
        self.assertIsNotNone(self.license_request.delivered_at)
        self.assertEqual(self.license_request.stripe_checkout_session_id, "cs_test_123")
        self.assertEqual(self.license_request.stripe_payment_intent_id, "pi_test_123")
        statuses = list(
            LicenseRequestAuditLog.objects.filter(license_request=self.license_request)
            .values_list("to_status", flat=True)
        )
        self.assertIn("PAID", statuses)
        self.assertIn("DELIVERED", statuses)
        self.assertEqual(LicenceDocument.objects.filter(license_request=self.license_request).count(), 2)
        self.assertEqual(StripeWebhookEvent.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(len(mail.outbox[0].attachments), 2)

    @patch("checkout.views.stripe.Webhook.construct_event")
    def test_webhook_idempotency(self, mock_construct):
        event = self._event_payload(event_id="evt_idempotent")
        mock_construct.return_value = event

        first = self.client.post(
            self.url,
            data="{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="sig",
        )
        second = self.client.post(
            self.url,
            data="{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="sig",
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(LicenceDocument.objects.filter(license_request=self.license_request).count(), 2)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(StripeWebhookEvent.objects.filter(stripe_event_id="evt_idempotent").count(), 1)

    @patch("checkout.views.stripe.Webhook.construct_event")
    def test_status_transitions_to_delivered(self, mock_construct):
        mock_construct.return_value = self._event_payload(event_id="evt_status")

        response = self.client.post(
            self.url,
            data="{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="sig",
        )

        self.assertEqual(response.status_code, 200)
        self.license_request.refresh_from_db()
        self.assertEqual(self.license_request.status, "DELIVERED")

    @patch("checkout.views.stripe.Webhook.construct_event")
    def test_processing_event_is_not_processed_twice(self, mock_construct):
        StripeWebhookEvent.objects.create(
            stripe_event_id="evt_processing",
            event_type="checkout.session.completed",
            status="PROCESSING",
        )
        mock_construct.return_value = self._event_payload(event_id="evt_processing")

        response = self.client.post(
            self.url,
            data="{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="sig",
        )

        self.assertEqual(response.status_code, 200)
        self.license_request.refresh_from_db()
        self.assertEqual(self.license_request.status, "PAYMENT_PENDING")
        self.assertEqual(LicenceDocument.objects.filter(license_request=self.license_request).count(), 0)
        self.assertEqual(len(mail.outbox), 0)
