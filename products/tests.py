import shutil
import uuid
from decimal import Decimal
from pathlib import Path

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models.signals import post_save
from django.urls import reverse
from rest_framework.test import APITestCase

from django.contrib.contenttypes.models import ContentType
from .models import LicenseRequest, Photo, generate_variants_for_photo


class LicenseRequestTests(APITestCase):
    def setUp(self):
        cache.clear()
        base_media_root = Path(__file__).resolve().parent.parent / ".test_media"
        self.media_root = base_media_root / uuid.uuid4().hex
        self.media_root.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)
        self._media_settings = self.settings(MEDIA_ROOT=self.media_root)
        self._media_settings.enable()
        self.addCleanup(self._media_settings.disable)

        post_save.disconnect(generate_variants_for_photo, sender=Photo)
        self.addCleanup(post_save.connect, generate_variants_for_photo, sender=Photo)

        self.photo = self._create_photo(is_active=True)
        self.url = reverse('license-request-create')

    def _create_photo(self, is_active=True):
        preview = SimpleUploadedFile("preview.jpg", b"preview", content_type="image/jpeg")
        high_res = SimpleUploadedFile("high_res.jpg", b"high_res", content_type="image/jpeg")
        return Photo.objects.create(
            title="Test Photo",
            description="Test description",
            collection="Test Collection",
            preview_image=preview,
            high_res_file=high_res,
            price_hd=Decimal("10.00"),
            price_4k=Decimal("20.00"),
            is_active=is_active,
        )

    def _payload(self, asset_id=None, message=None, reach_caps=None):
        return {
            "client_name": "Test Client",
            "company": "Test Co",
            "email": "test@example.com",
            "project_type": "COMMERCIAL",
            "duration": "1_YEAR",
            "message": message if message is not None else "Test message",
            "reach_caps": reach_caps if reach_caps is not None else "NONE",
            "asset_type": "photo",
            "asset_id": asset_id if asset_id is not None else self.photo.id,
        }

    def test_license_request_throttled_after_10(self):
        for i in range(10):
            payload = self._payload()
            payload["email"] = f"test+{i}@example.com"
            response = self.client.post(self.url, payload, format="json")
            self.assertEqual(response.status_code, 201)

        payload = self._payload()
        payload["email"] = "test+final@example.com"
        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, 429)

    def test_license_request_message_max_length(self):
        payload = self._payload(message="a" * 3000)
        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("message", response.data)

    def test_license_request_hidden_asset_rejected(self):
        hidden_photo = self._create_photo(is_active=False)
        payload = self._payload(asset_id=hidden_photo.id)
        response = self.client.post(self.url, payload, format="json")
        self.assertIn(response.status_code, (400, 404))

    def test_license_request_sanitizes_free_text(self):
        payload = self._payload(
            message="<b>Hello</b><script>alert(1)</script>",
            reach_caps="<img src=x onerror=alert(1)>Worldwide",
        )
        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, 201)
        obj = LicenseRequest.objects.latest("id")
        self.assertNotIn("<", obj.message or "")
        self.assertNotIn(">", obj.message or "")
        self.assertNotIn("<", obj.reach_caps or "")
        self.assertNotIn(">", obj.reach_caps or "")

    def test_license_request_duplicate_rejected(self):
        payload = self._payload()
        first = self.client.post(self.url, payload, format="json")
        self.assertEqual(first.status_code, 201)

        second = self.client.post(self.url, payload, format="json")
        self.assertEqual(second.status_code, 400)
        self.assertIn("email", second.data)

    def test_license_request_email_normalized(self):
        payload = self._payload()
        payload["email"] = "Test@Example.com "
        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, 201)
        obj = LicenseRequest.objects.latest("id")
        self.assertEqual(obj.email, "test@example.com")

    def test_license_request_rejected_allows_resubmit_until_limit(self):
        for _ in range(3):
            LicenseRequest.objects.create(
                content_type=ContentType.objects.get_for_model(self.photo),
                object_id=self.photo.id,
                client_name="Test Client",
                company="Test Co",
                email="test+rejects@example.com",
                project_type="COMMERCIAL",
                duration="1_YEAR",
                message="Rejected request",
                status="REJECTED",
            )

        payload = self._payload()
        payload["email"] = "test+rejects@example.com"
        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.data)

    def test_license_request_rejected_allows_new_if_under_limit(self):
        for _ in range(2):
            LicenseRequest.objects.create(
                content_type=ContentType.objects.get_for_model(self.photo),
                object_id=self.photo.id,
                client_name="Test Client",
                company="Test Co",
                email="test+rejects2@example.com",
                project_type="COMMERCIAL",
                duration="1_YEAR",
                message="Rejected request",
                status="REJECTED",
            )

        payload = self._payload()
        payload["email"] = "test+rejects2@example.com"
        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, 201)

    def test_ai_worker_queue_requires_auth(self):
        url = reverse("ai-draft-queue")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_ai_worker_queue_allows_valid_token(self):
        url = reverse("ai-draft-queue")
        content_type = ContentType.objects.get_for_model(self.photo)
        LicenseRequest.objects.create(
            content_type=content_type,
            object_id=self.photo.id,
            client_name="Test Client",
            company="Test Co",
            email="test+ai@example.com",
            project_type="COMMERCIAL",
            duration="1_YEAR",
            message="Test message",
            status="NEW",
        )
        with self.settings(AI_WORKER_SECRET="testsecret"):
            response = self.client.get(url, HTTP_AUTHORIZATION="Bearer testsecret")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
