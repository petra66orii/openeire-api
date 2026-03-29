import shutil
import uuid
import json
import os
import requests
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock

from django.contrib.contenttypes.models import ContentType
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models.signals import post_save
from django.test import TestCase, override_settings, SimpleTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

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
from .address_validation import validate_physical_shipping_address
from .prodigi import create_prodigi_order, _get_prodigi_asset_url


class PhysicalAddressValidationTests(SimpleTestCase):
    def test_valid_us_zip_five_digit(self):
        errors = validate_physical_shipping_address(
            country="US",
            line1="123 Test St",
            town="Austin",
            postcode="73301",
            county="TX",
        )
        self.assertEqual(errors, {})

    def test_valid_us_zip_plus_four(self):
        errors = validate_physical_shipping_address(
            country="US",
            line1="123 Test St",
            town="Austin",
            postcode="73301-1234",
            county="TX",
        )
        self.assertEqual(errors, {})

    def test_valid_ie_eircode_standard_and_d6w(self):
        standard = validate_physical_shipping_address(
            country="IE",
            line1="1 Main Street",
            town="Dublin",
            postcode="D01 F5P2",
            county="Dublin",
        )
        d6w = validate_physical_shipping_address(
            country="IE",
            line1="2 Main Street",
            town="Dublin",
            postcode="D6W F8X2",
            county="Dublin",
        )
        self.assertEqual(standard, {})
        self.assertEqual(d6w, {})

    def test_ie_rejects_us_zip_format(self):
        errors = validate_physical_shipping_address(
            country="IE",
            line1="1 Main Street",
            town="Galway",
            postcode="90210",
            county="Galway",
        )
        self.assertIn("postcode", errors)

    def test_missing_required_fields(self):
        errors = validate_physical_shipping_address(
            country="IE",
            line1="",
            town="",
            postcode="",
            county="",
        )
        self.assertIn("street_address1", errors)
        self.assertIn("town", errors)
        self.assertIn("postcode", errors)

    def test_unsupported_country_rejected(self):
        errors = validate_physical_shipping_address(
            country="GB",
            line1="1 Test St",
            town="London",
            postcode="SW1A 1AA",
            county="London",
        )
        self.assertIn("country", errors)


class ProdigiIntegrationSecurityTests(SimpleTestCase):
    class _ItemsManager:
        def __init__(self, items):
            self._items = items

        def all(self):
            return self._items

    class _BadFileHandle:
        @property
        def url(self):
            raise RuntimeError("signed-url-token-should-not-leak")

    class _StorageBackedFileHandle:
        def __init__(self, *, name, storage_url, fallback_url=None):
            self.name = name
            self.storage = SimpleNamespace(url=Mock(return_value=storage_url))
            self._fallback_url = fallback_url or storage_url

        @property
        def url(self):
            return self._fallback_url

    def _build_order(self, *, bad_asset_url=False, prodigi_sku="ECO-CAN-12X18"):
        high_res_file = (
            self._BadFileHandle()
            if bad_asset_url
            else SimpleNamespace(url="https://cdn.example.com/high-res.jpg")
        )
        product = SimpleNamespace(
            prodigi_sku=prodigi_sku,
            material="eco_canvas",
            photo=SimpleNamespace(
                high_res_file=high_res_file
            ),
        )
        item = SimpleNamespace(product=product, quantity=1)
        return SimpleNamespace(
            order_number="ORDER123",
            first_name="Test Buyer",
            email="buyer@example.com",
            street_address1="1 Test Street",
            street_address2="",
            town="Dublin",
            county="Dublin",
            postcode="D01 F5P2",
            country="IE",
            shipping_method="budget",
            items=self._ItemsManager([item]),
        )

    def _build_mixed_physical_order(self):
        valid_product = SimpleNamespace(
            prodigi_sku="ECO-CAN-12X18",
            material="eco_canvas",
            photo=SimpleNamespace(
                high_res_file=SimpleNamespace(url="https://cdn.example.com/high-res.jpg")
            ),
        )
        missing_sku_product = SimpleNamespace(
            prodigi_sku=None,
            material="eco_canvas",
            photo=SimpleNamespace(
                high_res_file=SimpleNamespace(url="https://cdn.example.com/high-res-2.jpg")
            ),
        )
        items = [
            SimpleNamespace(product=valid_product, quantity=1),
            SimpleNamespace(product=missing_sku_product, quantity=1),
        ]
        return SimpleNamespace(
            order_number="ORDER124",
            first_name="Test Buyer",
            email="buyer@example.com",
            street_address1="1 Test Street",
            street_address2="",
            town="Dublin",
            county="Dublin",
            postcode="D01 F5P2",
            country="IE",
            shipping_method="budget",
            items=self._ItemsManager(items),
        )

    @override_settings(PRODIGI_CONNECT_TIMEOUT_SECONDS=3, PRODIGI_READ_TIMEOUT_SECONDS=9)
    @patch("checkout.prodigi.requests.post")
    def test_prodigi_uses_timeout_and_sanitizes_upstream_validation_error(self, mock_post):
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.headers = {}
        mock_response.text = (
            '{"outcome":"ValidationFailed","failures":{"recipient.address.postalOrZipCode":'
            '[{"code":"MustBeAValidUSZipCodeFormat","providedValue":"90210"}]}}'
        )
        mock_response.json.return_value = {
            "outcome": "ValidationFailed",
            "failures": {
                "recipient.address.postalOrZipCode": [
                    {"code": "MustBeAValidUSZipCodeFormat", "providedValue": "90210"},
                ],
                "recipient.email": [
                    {"code": "MustNotBeEmptyOrWhitespace", "providedValue": "pii@example.com"},
                ],
            },
            "traceParent": "00-test-trace",
        }
        mock_post.return_value = mock_response

        with patch.dict(os.environ, {"PRODIGI_API_KEY": "test_key", "PRODIGI_SANDBOX": "True"}, clear=False):
            with self.assertLogs("checkout.prodigi", level="WARNING") as captured_logs:
                with self.assertRaises(RuntimeError) as raised:
                    create_prodigi_order(self._build_order())

        self.assertEqual(
            str(raised.exception),
            "Prodigi fulfillment failed (status=400, outcome=ValidationFailed).",
        )
        self.assertNotIn("pii@example.com", str(raised.exception))
        self.assertEqual(mock_post.call_args.kwargs["timeout"], (3.0, 9.0))
        self.assertEqual(mock_post.call_args.kwargs["headers"]["X-API-Key"], "test_key")
        log_output = " ".join(captured_logs.output)
        self.assertIn("ValidationFailed", log_output)
        self.assertIn("trace_parent=00-test-trace", log_output)
        self.assertNotIn("pii@example.com", log_output)
        self.assertNotIn(mock_response.text, log_output)

    @patch("checkout.prodigi.requests.post", side_effect=requests.Timeout("Read timed out"))
    def test_prodigi_timeout_raises_sanitized_error(self, mock_post):
        with patch.dict(os.environ, {"PRODIGI_API_KEY": "test_key", "PRODIGI_SANDBOX": "True"}, clear=False):
            with self.assertLogs("checkout.prodigi", level="ERROR") as captured_logs:
                with self.assertRaises(RuntimeError) as raised:
                    create_prodigi_order(self._build_order())

        self.assertEqual(str(raised.exception), "Prodigi fulfillment timed out.")
        self.assertNotIn("Read timed out", str(raised.exception))
        self.assertTrue(raised.exception.__suppress_context__)
        self.assertEqual(mock_post.call_count, 1)
        self.assertIn("timed out", " ".join(captured_logs.output).lower())

    @patch("checkout.prodigi.requests.post")
    def test_prodigi_non_json_success_response_is_sanitized(self, mock_post):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.text = "<html>unexpected success body with internal details</html>"
        mock_response.json.side_effect = ValueError("No JSON object could be decoded")
        mock_post.return_value = mock_response

        with patch.dict(os.environ, {"PRODIGI_API_KEY": "test_key", "PRODIGI_SANDBOX": "True"}, clear=False):
            with self.assertLogs("checkout.prodigi", level="WARNING") as captured_logs:
                with self.assertRaises(RuntimeError) as raised:
                    create_prodigi_order(self._build_order())

        self.assertEqual(str(raised.exception), "Prodigi fulfillment returned an invalid response.")
        self.assertNotIn("unexpected success body", str(raised.exception))
        self.assertIn("non-JSON success response", " ".join(captured_logs.output))
        self.assertNotIn("unexpected success body", " ".join(captured_logs.output))

    @patch("checkout.prodigi.requests.post")
    def test_prodigi_asset_url_failure_logs_error_type_only(self, mock_post):
        with patch.dict(os.environ, {"PRODIGI_API_KEY": "test_key", "PRODIGI_SANDBOX": "True"}, clear=False):
            with self.assertLogs("checkout.prodigi", level="WARNING") as captured_logs:
                with self.assertRaises(RuntimeError) as raised:
                    create_prodigi_order(self._build_order(bad_asset_url=True))

        self.assertEqual(
            str(raised.exception),
            "Prodigi fulfillment could not prepare all physical items.",
        )
        self.assertEqual(mock_post.call_count, 0)
        logs = " ".join(captured_logs.output)
        self.assertIn("Failed to prepare Prodigi asset URL", logs)
        self.assertIn("could not prepare all physical items", logs)
        self.assertIn("error_type=RuntimeError", logs)
        self.assertNotIn("signed-url-token-should-not-leak", logs)

    @patch("checkout.prodigi.requests.post")
    def test_prodigi_missing_sku_raises_fulfillment_error(self, mock_post):
        with patch.dict(os.environ, {"PRODIGI_API_KEY": "test_key", "PRODIGI_SANDBOX": "True"}, clear=False):
            with self.assertLogs("checkout.prodigi", level="WARNING") as captured_logs:
                with self.assertRaises(RuntimeError) as raised:
                    create_prodigi_order(self._build_order(prodigi_sku=None))

        self.assertEqual(
            str(raised.exception),
            "Prodigi fulfillment could not prepare all physical items.",
        )
        self.assertEqual(mock_post.call_count, 0)
        self.assertIn("missing_sku=1", " ".join(captured_logs.output))

    @patch("checkout.prodigi.requests.post")
    def test_prodigi_partial_physical_payload_is_rejected(self, mock_post):
        with patch.dict(os.environ, {"PRODIGI_API_KEY": "test_key", "PRODIGI_SANDBOX": "True"}, clear=False):
            with self.assertLogs("checkout.prodigi", level="WARNING") as captured_logs:
                with self.assertRaises(RuntimeError) as raised:
                    create_prodigi_order(self._build_mixed_physical_order())

        self.assertEqual(
            str(raised.exception),
            "Prodigi fulfillment could not prepare all physical items.",
        )
        self.assertEqual(mock_post.call_count, 0)
        logs = " ".join(captured_logs.output)
        self.assertIn("physical_items=2", logs)
        self.assertIn("prepared_items=1", logs)
        self.assertIn("missing_sku=1", logs)

    def test_prodigi_prefers_storage_signed_url_for_private_assets(self):
        signed_url = "https://private-r2.example.com/digital_products/photos/high-res.jpg?X-Amz-Signature=test"
        file_handle = self._StorageBackedFileHandle(
            name="digital_products/photos/high-res.jpg",
            storage_url=signed_url,
            fallback_url="digital_products/photos/high-res.jpg",
        )
        product = SimpleNamespace(
            photo=SimpleNamespace(high_res_file=file_handle)
        )

        image_url = _get_prodigi_asset_url(product, site_url="https://openeire.ie")

        self.assertEqual(image_url, signed_url)
        file_handle.storage.url.assert_called_once_with("digital_products/photos/high-res.jpg")

    def test_prodigi_joins_relative_asset_paths_safely(self):
        file_handle = SimpleNamespace(url="digital_products/photos/high-res.jpg")
        product = SimpleNamespace(
            photo=SimpleNamespace(high_res_file=file_handle)
        )

        image_url = _get_prodigi_asset_url(product, site_url="https://media.openeire.ie")

        self.assertEqual(
            image_url,
            "https://media.openeire.ie/digital_products/photos/high-res.jpg",
        )

    @patch("checkout.prodigi.requests.post")
    def test_prodigi_error_parser_ignores_non_string_fields(self, mock_post):
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.headers = {"traceparent": 12345}
        mock_response.json.return_value = {
            "outcome": {"unexpected": "dict"},
            "traceParent": {"not": "string"},
            "failures": {
                "recipient.address.postalOrZipCode": [
                    {"code": {"not": "string"}},
                    {"code": "MustBeAValidUSZipCodeFormat"},
                ],
                99: [
                    {"code": "ShouldBeIgnored"},
                ],
            },
        }
        mock_post.return_value = mock_response

        with patch.dict(os.environ, {"PRODIGI_API_KEY": "test_key", "PRODIGI_SANDBOX": "True"}, clear=False):
            with self.assertLogs("checkout.prodigi", level="WARNING") as captured_logs:
                with self.assertRaises(RuntimeError) as raised:
                    create_prodigi_order(self._build_order())

        self.assertEqual(
            str(raised.exception),
            "Prodigi fulfillment failed (status=400, outcome=unknown).",
        )
        log_output = " ".join(captured_logs.output)
        self.assertIn("failure_codes=recipient.address.postalOrZipCode:MustBeAValidUSZipCodeFormat", log_output)
        self.assertIn("trace_parent=n/a", log_output)
        self.assertNotIn("ShouldBeIgnored", log_output)
        self.assertNotIn("{'unexpected': 'dict'}", log_output)


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
            price=Decimal("20.00"),
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
            price=Decimal("25.00"),
            is_active=True,
        )
        self.variant = ProductVariant.objects.create(
            photo=self.photo,
            material="eco_canvas",
            size="12x18",
            price=Decimal("99.00"),
        )
        self.user = get_user_model().objects.create_user(
            username="consumerbuyer",
            email="buyer@example.com",
            password="StrongPass123!",
        )
        self.url = reverse("webhook")

    def _payment_intent_event(self, license_value="hd", username=None, user_id=None):
        cart = [
            {
                "product_id": self.photo.id,
                "product_type": "photo",
                "quantity": 1,
                "options": {"license": license_value},
            }
        ]
        metadata_username = username if username is not None else self.user.username
        metadata_user_id = (
            str(user_id)
            if user_id is not None
            else (str(self.user.id) if metadata_username != "Guest" else "")
        )
        return {
            "id": "evt_consumer_1",
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": "pi_consumer_1",
                    "receipt_email": "buyer@example.com",
                    "metadata": {
                        "cart": json.dumps(cart),
                        "username": metadata_username,
                        "user_id": metadata_user_id,
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
    def test_webhook_ignores_invalid_digital_license_option(self, mock_construct):
        mock_construct.return_value = self._payment_intent_event(license_value="tampered")

        response = self.client.post(
            self.url,
            data="{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="sig",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    @patch("checkout.views.stripe.Webhook.construct_event")
    def test_webhook_rejects_guest_digital_order_creation(self, mock_construct):
        mock_construct.return_value = self._payment_intent_event(username="Guest")

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
    def test_webhook_binds_digital_order_using_user_id_when_username_mismatches(self, mock_construct):
        other_user = get_user_model().objects.create_user(
            username="otherbuyer",
            email="other@example.com",
            password="StrongPass123!",
        )
        mock_construct.return_value = self._payment_intent_event(
            username=other_user.username,
            user_id=self.user.id,
        )

        response = self.client.post(
            self.url,
            data="{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="sig",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Order.objects.count(), 1)
        order = Order.objects.first()
        self.assertIsNotNone(order.user_profile)
        self.assertEqual(order.user_profile.user_id, self.user.id)

    @patch("checkout.views.stripe.Webhook.construct_event")
    def test_webhook_rejects_digital_order_when_user_id_missing_even_if_username_exists(self, mock_construct):
        mock_construct.return_value = self._payment_intent_event(
            username=self.user.username,
            user_id="",
        )

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
    @override_settings(CHECKOUT_ALLOW_LEGACY_USERNAME_FALLBACK=True)
    def test_webhook_allows_legacy_username_fallback_when_enabled(self, mock_construct):
        mock_construct.return_value = self._payment_intent_event(
            username=self.user.username,
            user_id="",
        )

        response = self.client.post(
            self.url,
            data="{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="sig",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Order.objects.count(), 1)
        order = Order.objects.first()
        self.assertIsNotNone(order.user_profile)
        self.assertEqual(order.user_profile.user_id, self.user.id)

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

    @patch("checkout.views.stripe.Webhook.construct_event")
    def test_webhook_rejects_non_printable_physical_variant_order(self, mock_construct):
        self.photo.is_printable = False
        self.photo.save(update_fields=["is_printable"])
        cart = [
            {
                "product_id": self.variant.id,
                "product_type": "physical",
                "quantity": 1,
                "options": {},
            }
        ]
        mock_construct.return_value = {
            "id": "evt_non_printable_physical",
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": "pi_non_printable_physical",
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
                            "city": "Galway",
                            "line1": "1 Test Street",
                            "line2": "",
                            "postal_code": "H62 X254",
                            "state": "Galway",
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
        event = StripeWebhookEvent.objects.get(stripe_event_id="evt_non_printable_physical")
        self.assertEqual(event.status, "FAILED")
        self.assertIn("Physical product", event.error_message)


@override_settings(STRIPE_SECRET_KEY="sk_test_123")
class CreatePaymentIntentSecurityTests(TestCase):
    def setUp(self):
        self.client = APIClient()
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
            price=Decimal("20.00"),
            is_active=True,
        )
        self.variant = ProductVariant.objects.create(
            photo=self.photo,
            material="eco_canvas",
            size="12x18",
            price=Decimal("99.00"),
        )
        self.user = get_user_model().objects.create_user(
            username="checkoutuser",
            email="checkout@example.com",
            password="StrongPass123!",
        )
        self.url = reverse("create_payment_intent")

    @patch("checkout.views.stripe.PaymentIntent.create")
    def test_invalid_digital_license_option_is_ignored(self, mock_create):
        self.client.force_authenticate(user=self.user)
        mock_create.return_value = Mock(client_secret="cs_test_123")
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

        self.assertEqual(response.status_code, 200)
        self.assertIn("clientSecret", response.data)
        mock_create.assert_called_once()

    @patch("checkout.views.stripe.PaymentIntent.create")
    def test_invalid_options_payload_shape_is_rejected(self, mock_create):
        self.client.force_authenticate(user=self.user)
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
    def test_invalid_cart_payload_shape_is_rejected(self, mock_create):
        self.client.force_authenticate(user=self.user)
        payload = {
            "cart": "not-a-list",
            "shipping_details": {"email": "buyer@example.com"},
        }
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["error"],
            "Invalid cart payload. Expected a list of cart items.",
        )
        mock_create.assert_not_called()

    @patch("checkout.views.stripe.PaymentIntent.create")
    def test_truthy_non_dict_shipping_details_payload_is_rejected(self, mock_create):
        self.client.force_authenticate(user=self.user)
        payload = {
            "cart": [
                {
                    "product_id": self.photo.id,
                    "product_type": "photo",
                    "quantity": 1,
                    "options": {"license": "hd"},
                }
            ],
            "shipping_details": "invalid-shape",
        }
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("shipping_details", response.data)
        self.assertEqual(
            response.data["shipping_details"]["address"],
            "Invalid shipping_details payload. Expected an object.",
        )
        mock_create.assert_not_called()

    @patch("checkout.views.stripe.PaymentIntent.create")
    def test_invalid_cart_item_shape_returns_sanitized_400(self, mock_create):
        self.client.force_authenticate(user=self.user)
        payload = {
            "cart": [
                {
                    "product_type": "photo",
                    "quantity": 1,
                    "options": {"license": "hd"},
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
        self.assertEqual(response.data["code"], "INVALID_CART_PAYLOAD")
        self.assertEqual(response.data["error"], "Invalid cart data provided.")
        self.assertNotIn("product_id", json.dumps(response.data))
        mock_create.assert_not_called()

    @patch("checkout.views.stripe.PaymentIntent.create")
    def test_non_object_cart_item_returns_sanitized_400(self, mock_create):
        self.client.force_authenticate(user=self.user)
        payload = {
            "cart": [
                "not-an-object",
            ],
            "shipping_details": {"email": "buyer@example.com"},
        }
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "INVALID_CART_PAYLOAD")
        self.assertEqual(
            response.data["error"],
            "Invalid cart item payload. Expected an object.",
        )
        mock_create.assert_not_called()

    @patch("checkout.views.stripe.PaymentIntent.create")
    def test_stripe_error_returns_sanitized_500_without_internal_message(self, mock_create):
        self.client.force_authenticate(user=self.user)
        mock_create.side_effect = RuntimeError("Stripe timeout internal details: req_12345")
        payload = {
            "cart": [
                {
                    "product_id": self.photo.id,
                    "product_type": "photo",
                    "quantity": 1,
                    "options": {"license": "hd"},
                }
            ],
            "shipping_details": {"email": "buyer@example.com"},
        }
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.data["code"], "PAYMENT_INTENT_CREATION_FAILED")
        self.assertEqual(
            response.data["error"],
            "Unable to initialize checkout right now. Please try again.",
        )
        self.assertNotIn("Stripe timeout internal details", json.dumps(response.data))

    @patch("checkout.views.stripe.PaymentIntent.create")
    def test_guest_digital_cart_is_rejected(self, mock_create):
        payload = {
            "cart": [
                {
                    "product_id": self.photo.id,
                    "product_type": "photo",
                    "quantity": 1,
                    "options": {"license": "hd"},
                }
            ],
            "shipping_details": {"email": "buyer@example.com"},
        }
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["code"], "AUTH_REQUIRED_DIGITAL_CHECKOUT")
        mock_create.assert_not_called()

    @patch("checkout.views.stripe.PaymentIntent.create")
    def test_guest_mixed_cart_is_rejected(self, mock_create):
        payload = {
            "cart": [
                {
                    "product_id": self.photo.id,
                    "product_type": "photo",
                    "quantity": 1,
                    "options": {"license": "hd"},
                },
                {
                    "product_id": self.variant.id,
                    "product_type": "physical",
                    "quantity": 1,
                },
            ],
            "shipping_details": {
                "email": "buyer@example.com",
                "address": {
                    "line1": "1 Test Street",
                    "city": "Dublin",
                    "country": "IE",
                    "postal_code": "D01 F5P2",
                    "state": "Dublin",
                },
            },
        }
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["code"], "AUTH_REQUIRED_DIGITAL_CHECKOUT")
        mock_create.assert_not_called()

    @patch("checkout.views.stripe.PaymentIntent.create")
    def test_guest_physical_only_cart_is_allowed(self, mock_create):
        self.photo.is_printable = True
        self.photo.save(update_fields=["is_printable"])
        mock_create.return_value = Mock(client_secret="cs_test_123")
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
                    "city": "Dublin",
                    "country": "IE",
                    "postal_code": "D01 F5P2",
                    "state": "Dublin",
                },
            },
        }
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("clientSecret", response.data)
        mock_create.assert_called_once()
        sent_metadata = mock_create.call_args.kwargs["metadata"]
        self.assertEqual(sent_metadata["user_id"], "")

    @patch("checkout.views.stripe.PaymentIntent.create")
    def test_authenticated_digital_cart_is_allowed(self, mock_create):
        self.client.force_authenticate(user=self.user)
        mock_create.return_value = Mock(client_secret="cs_test_123")
        payload = {
            "cart": [
                {
                    "product_id": self.photo.id,
                    "product_type": "photo",
                    "quantity": 1,
                    "options": {"license": "hd"},
                }
            ]
        }
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("clientSecret", response.data)
        mock_create.assert_called_once()
        sent_metadata = mock_create.call_args.kwargs["metadata"]
        self.assertEqual(sent_metadata["user_id"], str(self.user.id))

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

    @patch("checkout.views.stripe.PaymentIntent.create")
    def test_non_printable_physical_variant_is_rejected(self, mock_create):
        self.photo.is_printable = False
        self.photo.save(update_fields=["is_printable"])
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
                    "city": "Galway",
                    "country": "IE",
                    "postal_code": "H62 X254",
                    "state": "Galway",
                },
            },
        }
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "INVALID_CART_PAYLOAD")
        mock_create.assert_not_called()
