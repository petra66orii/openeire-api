from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from checkout.views import StripeWebhookView

from .delivery import (
    DOWNLOAD_URL_SECONDS,
    build_staff_preview_url,
    delivery_dto,
    evaluate_delivery_access,
    issue_delivery_session,
    load_delivery_session,
    recipient_secret,
    recipient_secret_matches,
    rotate_recipient_secret,
)
from .delivery_emails import send_delivery_recipient_email
from .delivery_storage import complete_upload, download_url
from .models import (
    RealEstateDeliverable,
    RealEstateDelivery,
    RealEstateDeliveryEmailAttempt,
    RealEstateDeliveryRecipient,
    RealEstateDeliveryUploadSession,
    RealEstateEnquiry,
    RealEstateInvoice,
    RealEstatePayment,
    RealEstateTimelineEvent,
)

TOKEN_KEY = "token-key-with-high-entropy-1234567890-ABCDEFGHI"
SESSION_KEY = "session-key-with-high-entropy-0987654321-ZYXWV"
INTERNAL_KEY = "internal-key-with-high-entropy-1357902468-QWERTY"


@override_settings(
    REAL_ESTATE_DELIVERY_PORTAL_ENABLED=True,
    REAL_ESTATE_DELIVERY_TOKEN_KEY=TOKEN_KEY,
    REAL_ESTATE_DELIVERY_SESSION_KEY=SESSION_KEY,
    REAL_ESTATE_DELIVERY_INTERNAL_SECRET=INTERNAL_KEY,
    FRONTEND_URL="https://openeire.test",
    FRONTEND_ORIGIN="https://openeire.test",
)
class DeliveryPortalTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="fictional-operator",
            email="operator@example.test",
            password="not-a-real-password",
            is_staff=True,
        )
        self.enquiry = RealEstateEnquiry.objects.create(
            name="Fictional Client",
            email="client@example.test",
            phone="+353 00 000 0000",
            client_type=RealEstateEnquiry.ClientType.ESTATE_AGENT,
            property_address="Example property",
            county="Test County",
            property_type="house",
            preferred_package=RealEstateEnquiry.PreferredPackage.ESSENTIAL,
            consent_to_contact=True,
            status=RealEstateEnquiry.Status.COMPLETED,
            payment_arrangement=RealEstateEnquiry.PaymentArrangement.FULL_ON_SHOOT_DAY,
            quoted_price=Decimal("100.00"),
        )
        self.invoice = RealEstateInvoice.objects.create(
            enquiry=self.enquiry,
            invoice_type=RealEstateInvoice.InvoiceType.FULL,
            invoice_number="TEST-DELIVERY-0001",
            status=RealEstateInvoice.Status.PAID,
            currency="EUR",
            subtotal=Decimal("100.00"),
            vat_rate=Decimal("0"),
            vat_amount=Decimal("0"),
            total=Decimal("100.00"),
            customer_name_snapshot="Fictional Client",
            customer_email_snapshot="client@example.test",
            property_reference_snapshot="Fictional project",
            job_reference_snapshot="TEST-JOB-1",
            issued_at=timezone.now(),
            paid_at=timezone.now(),
        )
        self.payment = RealEstatePayment.objects.create(
            invoice=self.invoice,
            amount=Decimal("100.00"),
            method=RealEstatePayment.Method.BANK_TRANSFER,
            status=RealEstatePayment.Status.SUCCEEDED,
            paid_at=timezone.now(),
            recorded_by=self.user,
            stripe_charge_id="ch_fictional_delivery",
        )
        now = timezone.now()
        self.delivery = RealEstateDelivery.objects.create(
            enquiry=self.enquiry,
            public_title="Fictional property media",
            status=RealEstateDelivery.Status.ACTIVE,
            portal_enabled=True,
            available_from=now - timedelta(minutes=1),
            expires_at=now + timedelta(days=30),
            published_at=now,
            created_by=self.user,
        )
        self.recipient = RealEstateDeliveryRecipient.objects.create(
            delivery=self.delivery,
            email="RECIPIENT@EXAMPLE.TEST",
            display_name="Fictional Recipient",
            role=RealEstateDeliveryRecipient.Role.AGENT,
        )
        self.file = RealEstateDeliverable.objects.create(
            delivery=self.delivery,
            category=RealEstateDeliverable.Category.PHOTOGRAPHS,
            display_name="Web photographs",
            original_filename="fictional-photos.zip",
            object_key="real-estate-deliveries/1/fictional.zip",
            file_size=1024,
            mime_type="application/zip",
            is_active=True,
            available_at=now - timedelta(seconds=1),
            uploaded_by=self.user,
        )

    def internal_headers(self):
        return {
            "HTTP_X_OPENEIRE_DELIVERY_INTERNAL": INTERNAL_KEY,
            "HTTP_ORIGIN": "https://openeire.test",
        }

    def test_secret_is_deterministic_not_stored_and_constant_time_comparable(self):
        first = recipient_secret(self.recipient)
        self.recipient.refresh_from_db()
        self.assertEqual(first, recipient_secret(self.recipient))
        self.assertTrue(recipient_secret_matches(self.recipient, first))
        self.assertFalse(recipient_secret_matches(self.recipient, f"{first}x"))
        field_names = {field.name for field in self.recipient._meta.fields}
        self.assertNotIn("secret", field_names)
        self.assertNotIn("token", field_names)

    def test_rotation_invalidates_old_secret_and_session(self):
        old_secret = recipient_secret(self.recipient)
        old_session, _ = issue_delivery_session(self.recipient)
        rotate_recipient_secret(self.recipient, actor=self.user)
        self.recipient.refresh_from_db()
        self.assertNotEqual(old_secret, recipient_secret(self.recipient))
        self.assertFalse(recipient_secret_matches(self.recipient, old_secret))
        with self.assertRaises(Exception):
            load_delivery_session(old_session)

    @override_settings(REAL_ESTATE_DELIVERY_TOKEN_KEY="")
    def test_missing_dedicated_token_key_fails_closed(self):
        with self.assertRaises(ImproperlyConfigured):
            recipient_secret(self.recipient)

    def test_session_round_trip_and_dto_never_expose_object_key(self):
        token, lifetime = issue_delivery_session(self.recipient)
        self.assertLessEqual(lifetime, 43_200)
        loaded = load_delivery_session(token)
        self.assertEqual(loaded.pk, self.recipient.pk)
        payload = delivery_dto(loaded)
        self.assertEqual(payload["state"], "valid")
        self.assertNotIn("object_key", str(payload))
        self.assertIn(str(self.file.public_id), str(payload))

    def test_access_rechecks_payment_revocation_expiry_and_shoot(self):
        self.assertTrue(evaluate_delivery_access(self.recipient).allowed)

        self.payment.reversal_status = RealEstatePayment.ReversalStatus.REFUNDED
        self.payment.reversed_amount = self.payment.amount
        self.payment.reversed_at = timezone.now()
        self.payment.save(
            update_fields=(
                "reversal_status",
                "reversed_amount",
                "reversed_at",
                "updated_at",
            )
        )
        decision = evaluate_delivery_access(self.recipient)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.state, "payment_locked")

        self.payment.reversal_status = RealEstatePayment.ReversalStatus.NONE
        self.payment.reversed_amount = Decimal("0")
        self.payment.reversed_at = None
        self.payment.save(
            update_fields=(
                "reversal_status",
                "reversed_amount",
                "reversed_at",
                "updated_at",
            )
        )
        self.recipient.revoked_at = timezone.now()
        self.recipient.revocation_reason = "Fictional revocation"
        self.recipient.save(
            update_fields=("revoked_at", "revocation_reason", "updated_at")
        )
        self.assertEqual(evaluate_delivery_access(self.recipient).state, "unavailable")

        self.recipient.revoked_at = None
        self.recipient.revocation_reason = ""
        self.recipient.save(
            update_fields=("revoked_at", "revocation_reason", "updated_at")
        )
        self.delivery.expires_at = timezone.now() - timedelta(seconds=1)
        self.delivery.save(update_fields=("expires_at", "updated_at"))
        self.assertEqual(evaluate_delivery_access(self.recipient).state, "unavailable")

        self.delivery.expires_at = timezone.now() + timedelta(days=1)
        self.delivery.save(update_fields=("expires_at", "updated_at"))
        self.enquiry.status = RealEstateEnquiry.Status.BOOKED
        self.enquiry.save(update_fields=("status", "updated_at"))
        self.assertEqual(
            evaluate_delivery_access(self.recipient).state,
            "temporarily_unavailable",
        )

    def test_independent_multiple_recipient_revocation(self):
        other = RealEstateDeliveryRecipient.objects.create(
            delivery=self.delivery,
            email="other@example.test",
            display_name="Other Fictional Recipient",
            role=RealEstateDeliveryRecipient.Role.VENDOR,
        )
        self.recipient.revoked_at = timezone.now()
        self.recipient.revocation_reason = "No longer required"
        self.recipient.save(
            update_fields=("revoked_at", "revocation_reason", "updated_at")
        )
        self.assertFalse(evaluate_delivery_access(self.recipient).allowed)
        self.assertTrue(evaluate_delivery_access(other).allowed)

    def test_completed_shoot_event_is_recorded_exactly_once(self):
        events = RealEstateTimelineEvent.objects.filter(
            enquiry=self.enquiry,
            event_type=RealEstateTimelineEvent.EventType.SHOOT_COMPLETED,
        )
        self.assertEqual(events.count(), 1)
        self.enquiry.internal_notes = "Fictional follow-up"
        self.enquiry.save(update_fields=("internal_notes", "updated_at"))
        self.assertEqual(events.count(), 1)

    def test_stripe_refund_and_dispute_events_relock_without_erasing_refund(self):
        webhook = StripeWebhookView()
        handled = webhook._handle_realestate_reversal_event(
            "charge.refunded",
            {
                "id": "ch_fictional_delivery",
                "amount_refunded": 2500,
            },
        )
        self.assertTrue(handled)
        self.payment.refresh_from_db()
        self.assertEqual(
            self.payment.reversal_status,
            RealEstatePayment.ReversalStatus.PARTIALLY_REFUNDED,
        )
        self.assertEqual(self.payment.reversed_amount, Decimal("25.00"))
        self.assertEqual(
            evaluate_delivery_access(self.recipient).state,
            "payment_locked",
        )

        webhook._handle_realestate_reversal_event(
            "charge.dispute.created",
            {
                "id": "dp_fictional",
                "charge": "ch_fictional_delivery",
                "amount": 10000,
                "status": "needs_response",
            },
        )
        self.payment.refresh_from_db()
        self.assertEqual(
            self.payment.reversal_status,
            RealEstatePayment.ReversalStatus.DISPUTED,
        )
        self.assertEqual(
            evaluate_delivery_access(self.recipient).state,
            "payment_locked",
        )

        webhook._handle_realestate_reversal_event(
            "charge.dispute.closed",
            {
                "id": "dp_fictional",
                "charge": "ch_fictional_delivery",
                "amount": 10000,
                "status": "won",
            },
        )
        self.payment.refresh_from_db()
        self.assertEqual(
            self.payment.reversal_status,
            RealEstatePayment.ReversalStatus.PARTIALLY_REFUNDED,
        )
        self.assertEqual(
            evaluate_delivery_access(self.recipient).state,
            "payment_locked",
        )

    def test_fragment_exchange_and_returning_session(self):
        client = APIClient()
        exchange = client.post(
            reverse("delivery-exchange"),
            {
                "public_id": str(self.recipient.public_id),
                "secret": recipient_secret(self.recipient),
            },
            format="json",
            **self.internal_headers(),
        )
        self.assertEqual(exchange.status_code, 200)
        session_token = exchange.data["session"]
        returning = client.post(
            reverse("delivery-session"),
            {"session": session_token},
            format="json",
            **self.internal_headers(),
        )
        self.assertEqual(returning.status_code, 200)
        self.assertEqual(returning.data["state"], "valid")

    def test_staff_preview_is_short_lived_separate_and_cannot_download(self):
        preview_url = build_staff_preview_url(self.delivery, self.user)
        public_id, preview_secret = preview_url.rsplit("/", 1)[1].split("#", 1)
        self.assertEqual(public_id, str(self.delivery.public_id))
        self.assertTrue(preview_secret.startswith("preview."))
        client = APIClient()
        exchange = client.post(
            reverse("delivery-exchange"),
            {"public_id": public_id, "secret": preview_secret},
            format="json",
            **self.internal_headers(),
        )
        self.assertEqual(exchange.status_code, 200)
        self.assertLessEqual(exchange.data["expires_in"], 600)
        preview_session = exchange.data["session"]
        page = client.post(
            reverse("delivery-session"),
            {"session": preview_session},
            format="json",
            **self.internal_headers(),
        )
        self.assertTrue(page.data["preview"])
        denied = client.post(
            reverse("delivery-download"),
            {
                "session": preview_session,
                "deliverable_id": str(self.file.public_id),
            },
            format="json",
            **self.internal_headers(),
        )
        self.assertEqual(denied.status_code, 403)

    def test_exchange_rejects_wrong_origin_and_secret_generically(self):
        client = APIClient()
        wrong_origin = client.post(
            reverse("delivery-exchange"),
            {
                "public_id": str(self.recipient.public_id),
                "secret": recipient_secret(self.recipient),
            },
            format="json",
            HTTP_X_OPENEIRE_DELIVERY_INTERNAL=INTERNAL_KEY,
            HTTP_ORIGIN="https://attacker.example",
        )
        self.assertEqual(wrong_origin.status_code, 403)
        wrong_secret = client.post(
            reverse("delivery-exchange"),
            {"public_id": str(self.recipient.public_id), "secret": "wrong"},
            format="json",
            **self.internal_headers(),
        )
        self.assertEqual(wrong_secret.status_code, 404)
        self.assertEqual(wrong_secret.data["state"], "unavailable")

    @patch("realestate.delivery_views.download_url")
    def test_download_rechecks_access_and_returns_only_short_lived_redirect(self, mocked):
        mocked.return_value = "https://r2.example.test/signed"
        session_token, _ = issue_delivery_session(self.recipient)
        response = APIClient().post(
            reverse("delivery-download"),
            {
                "session": session_token,
                "deliverable_id": str(self.file.public_id),
            },
            format="json",
            **self.internal_headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["redirect_url"], mocked.return_value)
        mocked.assert_called_once_with(self.file)

    @patch("realestate.delivery_views.start_upload")
    def test_staff_upload_session_is_scoped_and_hides_object_key(self, start):
        start.return_value = (
            "fictional-upload-id",
            "real-estate-deliveries/1/private-object.zip",
            10 * 1024 * 1024,
            "fictional.zip",
            "application/zip",
        )
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.post(
            reverse("delivery-upload-start"),
            {
                "delivery_id": self.delivery.pk,
                "filename": "fictional.zip",
                "display_name": "Download all",
                "category": RealEstateDeliverable.Category.ARCHIVE,
                "content_type": "application/zip",
                "file_size": 20 * 1024 * 1024,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertNotIn("object_key", response.data)
        session = RealEstateDeliveryUploadSession.objects.get(
            upload_id="fictional-upload-id"
        )
        self.assertEqual(session.created_by, self.user)
        self.assertEqual(session.delivery, self.delivery)

    @patch("realestate.delivery_storage._client")
    def test_upload_completion_verifies_head_and_download_uses_five_minutes(
        self, client_factory
    ):
        storage_client = client_factory.return_value
        storage_client.head_object.return_value = {
            "ContentLength": 1024,
            "ContentType": "application/zip",
            "ETag": '"fictional-etag"',
        }
        upload = RealEstateDeliveryUploadSession.objects.create(
            delivery=self.delivery,
            created_by=self.user,
            original_filename="fictional.zip",
            display_name="Download all",
            category=RealEstateDeliverable.Category.ARCHIVE,
            object_key="real-estate-deliveries/1/upload.zip",
            upload_id="upload-verification-test",
            expected_size=1024,
            expected_mime_type="application/zip",
            part_size=10 * 1024 * 1024,
        )
        head = complete_upload(
            upload,
            [{"part_number": 1, "etag": '"part-etag"'}],
        )
        self.assertEqual(head["ContentLength"], 1024)

        storage_client.generate_presigned_url.return_value = (
            "https://r2.example.test/signed"
        )
        self.assertEqual(download_url(self.file), "https://r2.example.test/signed")
        self.assertEqual(
            storage_client.generate_presigned_url.call_args.kwargs["ExpiresIn"],
            DOWNLOAD_URL_SECONDS,
        )

    @patch("realestate.delivery_emails.send_templated_email", return_value=1)
    def test_email_is_per_recipient_idempotent_and_audit_has_no_secret(
        self, send_email
    ):
        attempt = send_delivery_recipient_email(
            self.recipient,
            kind=RealEstateDeliveryEmailAttempt.Kind.INITIAL,
            idempotency_key="fictional-send-1",
            actor=self.user,
        )
        duplicate = send_delivery_recipient_email(
            self.recipient,
            kind=RealEstateDeliveryEmailAttempt.Kind.INITIAL,
            idempotency_key="fictional-send-1",
            actor=self.user,
        )
        self.assertEqual(attempt.pk, duplicate.pk)
        self.assertEqual(attempt.status, RealEstateDeliveryEmailAttempt.Status.SENT)
        send_email.assert_called_once()
        link = send_email.call_args.kwargs["context"]["delivery_url"]
        self.assertIn(f"/delivery/{self.recipient.public_id}#", link)
        timeline = RealEstateTimelineEvent.objects.filter(
            event_type=RealEstateTimelineEvent.EventType.DELIVERY_SENT
        ).latest("created_at")
        self.assertEqual(timeline.reference_url, "")
        self.assertNotIn(link, timeline.notes)

    @patch(
        "realestate.delivery_emails.record_timeline_event",
        side_effect=RuntimeError("fictional audit outage"),
    )
    @patch("realestate.delivery_emails.send_templated_email", return_value=1)
    def test_timeline_failure_does_not_erase_successful_email_attempt(
        self, _send_email, _timeline
    ):
        attempt = send_delivery_recipient_email(
            self.recipient,
            kind=RealEstateDeliveryEmailAttempt.Kind.RESEND,
            idempotency_key="fictional-send-audit-failure",
            actor=self.user,
        )
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, RealEstateDeliveryEmailAttempt.Status.SENT)

    def test_cleanup_defaults_to_dry_run(self):
        from io import StringIO

        output = StringIO()
        call_command("maintain_realestate_deliveries", stdout=output)
        self.assertIn("DRY RUN", output.getvalue())
        self.delivery.refresh_from_db()
        self.assertEqual(self.delivery.status, RealEstateDelivery.Status.ACTIVE)

    @override_settings(REAL_ESTATE_DELIVERY_PORTAL_ENABLED=False)
    def test_global_feature_flag_fails_closed_without_changing_legacy_fields(self):
        decision = evaluate_delivery_access(self.recipient)
        self.assertFalse(decision.allowed)
        self.assertEqual(self.enquiry.delivery_provider, "myairbridge")
        self.assertEqual(self.enquiry.delivery_link, "")
