import shutil
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from smtplib import SMTPAuthenticationError
from unittest.mock import patch

from django.core.cache import cache, caches
from django.core.cache.backends.base import InvalidCacheBackendError
from django.core import mail
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models.signals import post_save
from django.contrib.auth.models import User
from django.urls import reverse
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from django.contrib.contenttypes.models import ContentType
from checkout.models import Order, OrderItem
from openeire_api.throttling import SharedScopedRateThrottle
from .models import (
    LicenseRequest,
    LicenceDeliveryToken,
    GalleryAccess,
    Photo,
    PrintTemplate,
    ProductVariant,
    Video,
    generate_variants_for_photo,
)
from .licensing import send_licence_quote_email


class LicenseRequestTests(APITestCase):
    def setUp(self):
        cache.clear()
        caches[getattr(settings, "THROTTLE_CACHE_ALIAS", "throttle")].clear()
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

    def _create_photo(self, is_active=True, is_printable=False):
        preview = SimpleUploadedFile("preview.jpg", b"preview", content_type="image/jpeg")
        high_res = SimpleUploadedFile("high_res.jpg", b"high_res", content_type="image/jpeg")
        return Photo.objects.create(
            title="Test Photo",
            description="Test description",
            collection="Test Collection",
            preview_image=preview,
            high_res_file=high_res,
            price=Decimal("20.00"),
            is_active=is_active,
            is_printable=is_printable,
        )

    def _create_video(self, is_active=True):
        thumbnail = SimpleUploadedFile("thumb.jpg", b"thumbnail", content_type="image/jpeg")
        video = SimpleUploadedFile("video.mp4", b"video-bytes", content_type="video/mp4")
        return Video.objects.create(
            title="Test Video",
            description="Video description",
            collection="Test Collection",
            thumbnail_image=thumbnail,
            video_file=video,
            price=Decimal("24.00"),
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

    def test_license_request_throttle_cache_outage_fail_open_allows_request(self):
        class BrokenCache:
            def get(self, *args, **kwargs):
                raise InvalidCacheBackendError("cache unavailable")

        with self.settings(THROTTLE_FAIL_OPEN=True):
            with patch.object(SharedScopedRateThrottle, "_resolve_cache", return_value=BrokenCache()):
                response = self.client.post(self.url, self._payload(), format="json")

        self.assertEqual(response.status_code, 201)

    def test_license_request_throttle_cache_outage_fail_closed_returns_429(self):
        class BrokenCache:
            def get(self, *args, **kwargs):
                raise InvalidCacheBackendError("cache unavailable")

        with self.settings(THROTTLE_FAIL_OPEN=False):
            with patch.object(SharedScopedRateThrottle, "_resolve_cache", return_value=BrokenCache()):
                response = self.client.post(self.url, self._payload(), format="json")

        self.assertEqual(response.status_code, 429)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="noreply@example.com",
    )
    def test_gallery_request_endpoint_throttles(self):
        url = reverse("gallery_request")
        limit = int(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["gallery_access_request"].split("/")[0])

        for i in range(limit):
            response = self.client.post(url, {"email": f"throttle{i}@example.com"}, format="json")
            self.assertEqual(response.status_code, 200)

        blocked = self.client.post(url, {"email": "throttle-final@example.com"}, format="json")
        self.assertEqual(blocked.status_code, 429)

    @patch("products.views.send_mail", side_effect=SMTPAuthenticationError(535, b"5.7.8 Authentication failed"))
    def test_gallery_request_rolls_back_when_email_send_fails(self, mock_send_mail):
        url = reverse("gallery_request")

        response = self.client.post(url, {"email": "smtp-fail@example.com"}, format="json")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"error": "Unable to send access code right now. Please try again later."},
        )
        self.assertFalse(GalleryAccess.objects.filter(email="smtp-fail@example.com").exists())
        mock_send_mail.assert_called_once()

    @patch("products.views.send_mail", side_effect=OSError("smtp timeout"))
    def test_gallery_request_handles_mail_transport_os_errors(self, mock_send_mail):
        url = reverse("gallery_request")

        response = self.client.post(url, {"email": "smtp-timeout@example.com"}, format="json")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"error": "Unable to send access code right now. Please try again later."},
        )
        self.assertFalse(GalleryAccess.objects.filter(email="smtp-timeout@example.com").exists())
        mock_send_mail.assert_called_once()

    def test_photo_creation_generates_variants_only_for_printable_photos(self):
        post_save.connect(generate_variants_for_photo, sender=Photo)
        self.addCleanup(post_save.disconnect, generate_variants_for_photo, sender=Photo)

        template = PrintTemplate.objects.create(
            material="eco_canvas",
            size="12x18",
            production_cost=Decimal("40.00"),
            sku_suffix="CAN-12x18",
            prodigi_sku="GLOBAL-CAN-12X18",
        )
        printable_photo = self._create_photo(is_active=True, is_printable=True)
        digital_only_photo = self._create_photo(is_active=True, is_printable=False)

        variant = ProductVariant.objects.get(
            photo=printable_photo,
            material="eco_canvas",
            size="12x18",
        )
        self.assertEqual(variant.price, template.retail_price)
        self.assertEqual(variant.sku, f"PHOTO-{printable_photo.id}-CAN-12x18")
        self.assertEqual(variant.prodigi_sku, "GLOBAL-CAN-12X18")
        self.assertFalse(ProductVariant.objects.filter(photo=digital_only_photo).exists())

    def test_gallery_verify_endpoint_throttles(self):
        access = GalleryAccess.objects.create(email="verify-throttle@example.com")
        url = reverse("gallery_verify")
        limit = int(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["gallery_access_verify"].split("/")[0])

        for _ in range(limit):
            response = self.client.post(url, {"access_code": access.access_code}, format="json")
            self.assertEqual(response.status_code, 200)

        blocked = self.client.post(url, {"access_code": access.access_code}, format="json")
        self.assertEqual(blocked.status_code, 429)

    def test_gallery_verify_invalid_and_expired_codes_return_same_response(self):
        expired = GalleryAccess.objects.create(
            email="expired-code@example.com",
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        url = reverse("gallery_verify")

        expired_response = self.client.post(
            url, {"access_code": expired.access_code}, format="json"
        )
        invalid_response = self.client.post(
            url, {"access_code": "ZZZZZZZZ"}, format="json"
        )

        self.assertEqual(expired_response.status_code, 403)
        self.assertEqual(invalid_response.status_code, 403)
        self.assertEqual(
            expired_response.json(),
            {"error": "Invalid or expired code"},
        )
        self.assertEqual(
            invalid_response.json(),
            {"error": "Invalid or expired code"},
        )

    def test_bag_recommendations_returns_up_to_four_active_photos(self):
        for _ in range(5):
            self._create_photo(is_active=True)
        inactive = self._create_photo(is_active=False)

        url = reverse("bag-recommendations")
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data), 0)
        self.assertLessEqual(len(response.data), 4)
        returned_ids = {item["id"] for item in response.data}
        self.assertNotIn(inactive.id, returned_ids)

    def test_bag_recommendations_returns_empty_when_no_active_photos(self):
        Photo.objects.update(is_active=False)
        url = reverse("bag-recommendations")
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])

    def test_bag_recommendations_filters_inactive_ids_from_selected_pool(self):
        active = self._create_photo(is_active=True)
        inactive = self._create_photo(is_active=False)
        url = reverse("bag-recommendations")

        with patch(
            "products.views.ShoppingBagRecommendationsView._pick_recommendation_ids",
            return_value=[active.id, inactive.id],
        ):
            response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        returned_ids = {item["id"] for item in response.data}
        self.assertIn(active.id, returned_ids)
        self.assertNotIn(inactive.id, returned_ids)

    def test_bag_recommendations_uses_row_offset_sampling_over_active_rows(self):
        Photo.objects.update(is_active=False)
        active_a = self._create_photo(is_active=True)
        self._create_photo(is_active=False)  # gap
        active_b = self._create_photo(is_active=True)
        self._create_photo(is_active=False)  # gap
        active_c = self._create_photo(is_active=True)
        active_d = self._create_photo(is_active=True)
        active_e = self._create_photo(is_active=True)
        active_ids = [active_a.id, active_b.id, active_c.id, active_d.id, active_e.id]

        with patch("products.views.random.randint", return_value=3) as mocked_randint:
            response = self.client.get(reverse("bag-recommendations"))

        self.assertEqual(response.status_code, 200)
        mocked_randint.assert_called_once_with(0, len(active_ids) - 1)
        # Start at offset 3 => D, E, then wrap to A, B
        self.assertEqual(
            [item["id"] for item in response.data],
            [active_d.id, active_e.id, active_a.id, active_b.id],
        )

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

    def test_license_request_infers_reach_caps_from_message(self):
        payload = self._payload(
            message="Campaign details here. Reach cap: 2 million impressions.",
            reach_caps="",
        )
        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, 201)
        obj = LicenseRequest.objects.latest("id")
        self.assertEqual(obj.reach_caps, "2 million impressions")

    def test_license_request_normalizes_na_reach_caps_to_none(self):
        payload = self._payload(
            message="No specific cap provided.",
            reach_caps="N/A",
        )
        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, 201)
        obj = LicenseRequest.objects.latest("id")
        self.assertEqual(obj.reach_caps, "NONE")

    def test_license_request_duplicate_rejected(self):
        payload = self._payload()
        first = self.client.post(self.url, payload, format="json")
        self.assertEqual(first.status_code, 201)

        second = self.client.post(self.url, payload, format="json")
        self.assertEqual(second.status_code, 400)
        self.assertIn("email", second.data)

    def test_license_request_supports_multiple_assets(self):
        photo_two = self._create_photo(is_active=True)
        payload = self._payload()
        payload.pop("asset_id", None)
        payload["asset_ids"] = [self.photo.id, photo_two.id]
        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(response.data["created"]), 2)

    def test_license_request_single_asset_ids_maps_to_asset_id(self):
        payload = self._payload(asset_id=None)
        payload.pop("asset_id", None)
        payload["asset_ids"] = [self.photo.id]
        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["id"], LicenseRequest.objects.latest("id").id)

    def test_license_request_email_normalized(self):
        payload = self._payload()
        payload["email"] = "Test@Example.com "
        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, 201)
        obj = LicenseRequest.objects.latest("id")
        self.assertEqual(obj.email, "test@example.com")

    def test_license_request_authenticated_user_email_overrides_payload(self):
        user = User.objects.create_user(
            username="licensedbuyer",
            email="account@example.com",
            password="StrongPass123!",
        )
        self.client.force_authenticate(user=user)
        payload = self._payload()
        payload["email"] = "different@example.com"

        response = self.client.post(self.url, payload, format="json")

        self.assertEqual(response.status_code, 201)
        obj = LicenseRequest.objects.latest("id")
        self.assertEqual(obj.email, user.email)

    def test_status_context_does_not_leak_across_non_status_save(self):
        req = LicenseRequest.objects.create(
            content_type=ContentType.objects.get_for_model(self.photo),
            object_id=self.photo.id,
            client_name="Context Client",
            company="Context Co",
            email="context@example.com",
            project_type="COMMERCIAL",
            duration="1_YEAR",
            message="Initial",
            status="SUBMITTED",
        )
        req.set_status_change_context(note="should_not_leak")
        req.message = "Edited text only"
        req.save(update_fields=["message", "updated_at"])
        req.transition_to("NEEDS_INFO")
        latest_log = req.audit_logs.get(to_status="NEEDS_INFO")
        self.assertEqual(latest_log.to_status, "NEEDS_INFO")
        self.assertEqual(latest_log.note, "")

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
            status="SUBMITTED",
        )
        with self.settings(AI_WORKER_SECRET="testsecret"):
            response = self.client.get(url, HTTP_AUTHORIZATION="Bearer testsecret")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

    @override_settings(
        AI_WORKER_SECRET="testsecret",
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        LICENCE_SEND_INITIAL_DRAFT_EMAIL=True,
    )
    def test_ai_worker_draft_update_sends_initial_draft_email(self):
        obj = LicenseRequest.objects.create(
            content_type=ContentType.objects.get_for_model(self.photo),
            object_id=self.photo.id,
            client_name="Draft Client",
            company="Draft Co",
            email="draft@example.com",
            project_type="COMMERCIAL",
            duration="1_YEAR",
            message="Draft request",
            status="SUBMITTED",
        )
        url = reverse("ai-draft-update", args=[obj.id])
        response = self.client.post(
            url,
            {"draft_text": "This is your initial draft licence response."},
            format="json",
            HTTP_AUTHORIZATION="Bearer testsecret",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["draft@example.com"])
        self.assertIn("licence draft", mail.outbox[0].subject.lower())
        self.assertIn("This is your initial draft licence response.", mail.outbox[0].body)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_quote_email_sent_with_payment_link_and_fee(self):
        req = LicenseRequest.objects.create(
            content_type=ContentType.objects.get_for_model(self.photo),
            object_id=self.photo.id,
            client_name="Test Client",
            company="Test Co",
            email="quote@example.com",
            project_type="COMMERCIAL",
            duration="1_YEAR",
            message="Need a licence",
            status="APPROVED",
            quoted_price=Decimal("250.00"),
            stripe_payment_link="https://buy.stripe.com/test-link",
            stripe_payment_link_id="plink_test",
            territory="IRELAND",
            permitted_media="WEB_SOCIAL",
            exclusivity="NON_EXCLUSIVE",
            reach_caps="NONE",
            ai_draft_response="Draft reviewed by human.",
        )

        send_licence_quote_email(req)

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, ["quote@example.com"])
        self.assertIn("https://buy.stripe.com/test-link", sent.body)
        self.assertIn("EUR 250.00", sent.body)

    def test_license_download_token_not_burned_if_file_unavailable(self):
        req = LicenseRequest.objects.create(
            content_type=ContentType.objects.get_for_model(self.photo),
            object_id=self.photo.id,
            client_name="Download Client",
            company="Download Co",
            email="download@example.com",
            project_type="COMMERCIAL",
            duration="1_YEAR",
            message="Need a licence",
            status="DELIVERED",
        )
        token = LicenceDeliveryToken.objects.create(
            license_request=req,
            expires_at=timezone.now() + timedelta(days=1),
        )
        self.photo.high_res_file.storage.delete(self.photo.high_res_file.name)
        url = reverse("license-asset-download", args=[str(token.token)])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)
        token.refresh_from_db()
        self.assertIsNone(token.used_at)

    def test_personal_licence_text_endpoint_public(self):
        url = reverse("personal-licence-text")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("Personal Terms Version: PERSONAL v1.1 - March 2026", body)
        self.assertIn("PERSONAL USE LICENSE CERTIFICATE", body)

    def test_secure_download_preview_returns_personal_terms_context(self):
        user = User.objects.create_user(
            username="digitalbuyer",
            email="digitalbuyer@example.com",
            password="testpass123",
        )
        order = Order.objects.create(
            user_profile=user.userprofile,
            email=user.email,
            stripe_pid="pi_preview_test",
            personal_terms_version="PERSONAL v1.1 - March 2026",
        )
        OrderItem.objects.create(
            order=order,
            quantity=1,
            item_total=Decimal("10.00"),
            content_type=ContentType.objects.get_for_model(Photo),
            object_id=self.photo.id,
            details={"license": "hd"},
        )

        self.client.force_authenticate(user=user)
        url = reverse("secure-download", args=["photo", self.photo.id])
        response = self.client.get(url, {"preview": "1"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["personal_terms_version"], "PERSONAL v1.1 - March 2026")
        self.assertTrue(response.data["personal_terms_url"].endswith("/api/licence/personal-use/"))
        self.assertTrue(response.data["download_url"].endswith(f"/api/products/download/photo/{self.photo.id}/"))
        self.assertGreater(len(response.data["personal_terms_summary"]), 0)

    def test_physical_product_page_uses_physical_purchase_flow(self):
        self.photo.is_printable = True
        self.photo.save(update_fields=["is_printable"])
        ProductVariant.objects.create(
            photo=self.photo,
            material="eco_canvas",
            size="12x18",
            price=Decimal("99.00"),
        )
        url = reverse("physical_product_page", args=[self.photo.id])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["product_type"], "physical")
        self.assertEqual(response.data["default_purchase_flow"], "PHYSICAL_PRINT_CHECKOUT")
        self.assertEqual(response.data["purchase_flows"], ["PHYSICAL_PRINT_CHECKOUT"])
        self.assertNotIn("high_res_file", response.data)
        self.assertNotIn("price_hd", response.data)
        self.assertNotIn("price_4k", response.data)

    def test_digital_photo_page_exposes_personal_and_commercial_flows(self):
        access = GalleryAccess.objects.create(email="gated@example.com")
        url = reverse("photo_detail", args=[self.photo.id])
        response = self.client.get(url, HTTP_X_GALLERY_ACCESS_TOKEN=access.access_code)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["product_type"], "photo")
        self.assertEqual(response.data["default_purchase_flow"], "PERSONAL_CHECKOUT")
        self.assertEqual(
            response.data["purchase_flows"],
            ["PERSONAL_CHECKOUT", "COMMERCIAL_REQUEST"],
        )
        self.assertNotIn("high_res_file", response.data)

    def test_video_detail_does_not_expose_video_file(self):
        video = self._create_video(is_active=True)
        access = GalleryAccess.objects.create(email="video-gated@example.com")
        url = reverse("video_detail", args=[video.id])
        response = self.client.get(url, HTTP_X_GALLERY_ACCESS_TOKEN=access.access_code)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["product_type"], "video")
        self.assertNotIn("video_file", response.data)
        self.assertEqual(response.data["default_purchase_flow"], "PERSONAL_CHECKOUT")

    def test_physical_gallery_items_are_not_serialized_as_digital_photos(self):
        self.photo.is_printable = True
        self.photo.save(update_fields=["is_printable"])
        ProductVariant.objects.create(
            photo=self.photo,
            material="eco_canvas",
            size="12x18",
            price=Decimal("99.00"),
        )
        url = reverse("gallery_list")
        response = self.client.get(url, {"type": "physical"})

        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data), 0)
        self.assertEqual(response.data[0]["product_type"], "physical")
        self.assertEqual(response.data[0]["default_purchase_flow"], "PHYSICAL_PRINT_CHECKOUT")

    def test_variant_detail_does_not_expose_high_res_in_nested_photo(self):
        self.photo.is_printable = True
        self.photo.save(update_fields=["is_printable"])
        variant = ProductVariant.objects.create(
            photo=self.photo,
            material="eco_canvas",
            size="12x18",
            price=Decimal("99.00"),
        )
        url = reverse("variant_detail", args=[variant.id])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["product_type"], "physical")
        self.assertIn("photo", response.data)
        self.assertNotIn("high_res_file", response.data["photo"])
        self.assertNotIn("price_hd", response.data["photo"])
        self.assertNotIn("price_4k", response.data["photo"])
        self.assertEqual(response.data["photo"]["product_type"], "physical")

    def test_physical_related_products_include_only_printable_items(self):
        self.photo.is_printable = True
        self.photo.save(update_fields=["is_printable"])
        ProductVariant.objects.create(
            photo=self.photo,
            material="eco_canvas",
            size="12x18",
            price=Decimal("99.00"),
        )
        printable_photo = self._create_photo(is_active=True, is_printable=True)
        ProductVariant.objects.create(
            photo=printable_photo,
            material="eco_canvas",
            size="16x24",
            price=Decimal("120.00"),
        )
        non_printable_photo = self._create_photo(is_active=True)

        url = reverse("physical_product_page", args=[self.photo.id])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        related_ids = [item["id"] for item in response.data["related_products"]]
        self.assertIn(printable_photo.id, related_ids)
        self.assertNotIn(non_printable_photo.id, related_ids)
        for item in response.data["related_products"]:
            self.assertIsNotNone(item["starting_price"])
