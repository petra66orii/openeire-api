import shutil
import uuid
from decimal import Decimal
from pathlib import Path

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models.signals import post_save
from django.urls import reverse
from rest_framework.test import APITestCase

from .models import Photo, generate_variants_for_photo


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

    def _payload(self, asset_id=None, message=None):
        return {
            "client_name": "Test Client",
            "company": "Test Co",
            "email": "test@example.com",
            "project_type": "COMMERCIAL",
            "duration": "1_YEAR",
            "message": message if message is not None else "Test message",
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
