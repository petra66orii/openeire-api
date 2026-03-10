import shutil
import uuid
import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models.signals import post_save
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from products.models import (
    LicenseRequest,
    Photo,
    ProductVariant,
    StripeWebhookEvent,
    LicenceDocument,
    LicenseRequestAuditLog,
    generate_variants_for_photo,
)
from .models import Order


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

    @patch("checkout.views.stripe.Webhook.construct_event")
    @override_settings(STRIPE_WEBHOOK_STALE_PROCESSING_SECONDS=1)
    def test_stale_processing_event_is_retried(self, mock_construct):
        event = StripeWebhookEvent.objects.create(
            stripe_event_id="evt_stale_processing",
            event_type="checkout.session.completed",
            status="PROCESSING",
            processed_at=None,
        )
        StripeWebhookEvent.objects.filter(pk=event.pk).update(
            received_at=timezone.now() - timedelta(minutes=5)
        )
        mock_construct.return_value = self._event_payload(event_id="evt_stale_processing")

        response = self.client.post(
            self.url,
            data="{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="sig",
        )

        self.assertEqual(response.status_code, 200)
        self.license_request.refresh_from_db()
        self.assertEqual(self.license_request.status, "DELIVERED")
        event.refresh_from_db()
        self.assertEqual(event.status, "SUCCESS")


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='orders@example.com',
    STRIPE_WEBHOOK_SECRET='whsec_test',
)
class ConsumerDigitalOrderLicenceTests(TestCase):
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
            title="Order Photo",
            description="Test description",
            collection="Test Collection",
            preview_image=preview,
            high_res_file=high_res,
            price_hd=Decimal("15.00"),
            price_4k=Decimal("25.00"),
            is_active=True,
        )
        self.variant = ProductVariant.objects.create(
            photo=self.photo,
            material="eco_canvas",
            size="12x18",
            price=Decimal("99.00"),
        )
        self.url = reverse("webhook")

    def _payment_intent_event(self, license_value="hd"):
        cart = [
            {
                "product_id": self.photo.id,
                "product_type": "photo",
                "quantity": 1,
                "options": {"license": license_value},
            }
        ]
        return {
            "id": "evt_consumer_1",
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": "pi_consumer_1",
                    "receipt_email": "buyer@example.com",
                    "metadata": {
                        "cart": json.dumps(cart),
                        "username": "Guest",
                        "save_info": "false",
                        "shipping_cost": "0",
                        "shipping_method": "budget",
                    },
                    "shipping": {
                        "name": "Buyer",
                        "phone": "+3530000000",
                        "address": {
                            "country": "IE",
                            "city": "Dublin",
                            "line1": "1 Test Street",
                            "line2": "",
                            "postal_code": "D01",
                            "state": "Dublin",
                        },
                    },
                }
            },
        }

    @patch("checkout.views.stripe.Webhook.construct_event")
    def test_digital_order_stores_personal_terms_and_includes_it_in_email(self, mock_construct):
        mock_construct.return_value = self._payment_intent_event()

        response = self.client.post(
            self.url,
            data="{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="sig",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Order.objects.count(), 1)

        order = Order.objects.first()
        self.assertEqual(order.personal_terms_version, "PERSONAL v1.1 - March 2026")

        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertIn("PERSONAL USE LICENCE", body)
        self.assertIn(order.personal_terms_version, body)
        self.assertIn("http://testserver/api/licence/personal-use/", body)

        body_lower = body.lower()
        self.assertNotIn("rights-managed", body_lower)
        self.assertNotIn("indemnity", body_lower)
        self.assertNotIn("audit", body_lower)

    @patch("checkout.views.stripe.Webhook.construct_event")
    def test_webhook_rejects_invalid_digital_license_option(self, mock_construct):
        mock_construct.return_value = self._payment_intent_event(license_value="tampered")

        response = self.client.post(
            self.url,
            data="{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="sig",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    @patch("checkout.views.stripe.Webhook.construct_event")
    def test_webhook_rejects_invalid_us_address_for_physical_item(self, mock_construct):
        cart = [
            {
                "product_id": self.variant.id,
                "product_type": "physical",
                "quantity": 1,
                "options": {},
            }
        ]
        mock_construct.return_value = {
            "id": "evt_consumer_bad_us_address",
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": "pi_consumer_bad_us_address",
                    "receipt_email": "buyer@example.com",
                    "metadata": {
                        "cart": json.dumps(cart),
                        "username": "Guest",
                        "save_info": "false",
                        "shipping_cost": "0",
                        "shipping_method": "budget",
                    },
                    "shipping": {
                        "name": "Buyer",
                        "phone": "+3530000000",
                        "address": {
                            "country": "US",
                            "city": "Loughrea",
                            "line1": "1 Test Street",
                            "line2": "",
                            "postal_code": "H62 X254",
                            "state": "",
                        },
                    },
                }
            },
        }

        response = self.client.post(
            self.url,
            data="{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="sig",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)


@override_settings(STRIPE_SECRET_KEY="sk_test_123")
class CreatePaymentIntentSecurityTests(TestCase):
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
            title="Security Photo",
            description="Test description",
            collection="Test Collection",
            preview_image=preview,
            high_res_file=high_res,
            price_hd=Decimal("10.00"),
            price_4k=Decimal("20.00"),
            is_active=True,
        )
        self.variant = ProductVariant.objects.create(
            photo=self.photo,
            material="eco_canvas",
            size="12x18",
            price=Decimal("99.00"),
        )
        self.url = reverse("create_payment_intent")

    @patch("checkout.views.stripe.PaymentIntent.create")
    def test_invalid_digital_license_option_is_rejected(self, mock_create):
        payload = {
            "cart": [
                {
                    "product_id": self.photo.id,
                    "product_type": "photo",
                    "quantity": 1,
                    "options": {"license": "tampered"},
                }
            ],
            "shipping_details": {"email": "buyer@example.com"},
        }
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid digital license option", response.data["error"])
        mock_create.assert_not_called()

    @patch("checkout.views.stripe.PaymentIntent.create")
    def test_invalid_options_payload_shape_is_rejected(self, mock_create):
        payload = {
            "cart": [
                {
                    "product_id": self.photo.id,
                    "product_type": "photo",
                    "quantity": 1,
                    "options": ["not-an-object"],
                }
            ],
            "shipping_details": {"email": "buyer@example.com"},
        }
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid options payload", response.data["error"])
        mock_create.assert_not_called()

    @patch("checkout.views.stripe.PaymentIntent.create")
    def test_invalid_us_address_for_physical_item_is_rejected(self, mock_create):
        payload = {
            "cart": [
                {
                    "product_id": self.variant.id,
                    "product_type": "physical",
                    "quantity": 1,
                }
            ],
            "shipping_details": {
                "email": "buyer@example.com",
                "address": {
                    "line1": "1 Test Street",
                    "city": "Loughrea",
                    "country": "US",
                    "postal_code": "H62 X254",
                    "state": "",
                },
            },
        }
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("shipping_details", response.data)
        self.assertIn("state", response.data["shipping_details"])
        self.assertIn("postal_code", response.data["shipping_details"])
        mock_create.assert_not_called()

    @patch("checkout.views.stripe.PaymentIntent.create")
    def test_invalid_address_payload_shape_is_rejected(self, mock_create):
        payload = {
            "cart": [
                {
                    "product_id": self.variant.id,
                    "product_type": "physical",
                    "quantity": 1,
                }
            ],
            "shipping_details": {
                "email": "buyer@example.com",
                "address": "invalid-shape",
            },
        }
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("shipping_details", response.data)
        self.assertIn("address", response.data["shipping_details"])
        mock_create.assert_not_called()
