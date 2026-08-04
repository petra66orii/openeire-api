import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .booking import credential_secret, generate_primary_credential
from .models import (
    RealEstateBookingAccessEvent,
    RealEstateBookingCredential,
    RealEstateClient,
    RealEstateEnquiry,
)


TOKEN_KEY = "local-postgres-token-key-abcdefghijklmnopqrstuvwxyz-123456"
SESSION_KEY = "local-postgres-session-key-abcdefghijklmnopqrstuvwxyz-654321"
INTERNAL_KEY = "local-postgres-internal-key-abcdefghijklmnopqrstuvwxyz-987654"
BOOKING_SETTINGS = {
    "REAL_ESTATE_BOOKING_PORTAL_ENABLED": True,
    "REAL_ESTATE_BOOKING_EMAIL_ENABLED": False,
    "REAL_ESTATE_BOOKING_TOKEN_KEY": TOKEN_KEY,
    "REAL_ESTATE_BOOKING_SESSION_KEY": SESSION_KEY,
    "REAL_ESTATE_BOOKING_INTERNAL_SECRET": INTERNAL_KEY,
    "REAL_ESTATE_DELIVERY_TOKEN_KEY": "local-delivery-token-distinct-abcdefghijklmnopqrstuvwxyz",
    "REAL_ESTATE_DELIVERY_SESSION_KEY": "local-delivery-session-distinct-abcdefghijklmnopqrstuvwxyz",
    "REAL_ESTATE_DELIVERY_INTERNAL_SECRET": "local-delivery-internal-distinct-abcdefghijklmnopqrstuvwxyz",
    "FRONTEND_URL": "https://openeire.test",
    "FRONTEND_ORIGIN": "https://openeire.test",
}


@override_settings(**BOOKING_SETTINGS)
@skipUnless(connection.vendor == "postgresql", "PostgreSQL concurrency coverage")
class PostgreSQLBookingConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="postgres-booking-staff",
            email="staff@example.test",
            password="test-only",
        )
        self.client_record = self._client("Fictional Agent One", "one@example.test")
        self.credential, _ = generate_primary_credential(
            self.client_record, actor=self.staff
        )
        self.headers = {
            "HTTP_ORIGIN": "https://openeire.test",
            "HTTP_X_OPENEIRE_BOOKING_INTERNAL": INTERNAL_KEY,
        }

    def _client(self, name, email):
        return RealEstateClient.objects.create(
            name=name,
            email=email,
            phone="087 000 0000",
            client_type="estate_agent",
            company_name="Fictional Homes",
            identity_confirmed_at=timezone.now(),
            identity_confirmed_by=self.staff,
        )

    def _session(self, credential):
        api = APIClient()
        response = api.post(
            "/api/real-estate/booking/exchange/",
            data=json.dumps(
                {
                    "public_id": str(credential.public_id),
                    "secret": credential_secret(credential),
                }
            ),
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["session"]

    def _payload(self, session, submission_id):
        return {
            "session": session,
            "submission_id": str(submission_id),
            "property_address": "Concurrency Test House, Galway",
            "county": "Galway",
            "eircode": "H91 X4K8",
            "no_eircode": False,
            "property_type": "house",
            "bedroom_count": "3",
            "floor_count": "2",
            "preferred_package": "starter",
            "scheduling_preference": "flexible",
            "preferred_time_window": "morning",
            "access_provider": "enquirer",
            "readiness_acknowledged": True,
            "saved_details_confirmed": True,
            "privacy_acknowledged": True,
        }

    def _concurrent_posts(self, payloads):
        barrier = threading.Barrier(len(payloads))

        def submit(payload):
            close_old_connections()
            api = APIClient()
            api.raise_request_exception = False
            try:
                barrier.wait(timeout=10)
                response = api.post(
                    "/api/real-estate/booking/enquiries/",
                    data=json.dumps(payload),
                    content_type="application/json",
                    **self.headers,
                )
                try:
                    body = response.json()
                except ValueError:
                    body = {"non_json": response.content.decode("utf-8", errors="replace")[:200]}
                return response.status_code, body
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=len(payloads)) as pool:
            return list(pool.map(submit, payloads))

    @patch("realestate.booking_views.send_realestate_client_confirmation_email")
    @patch("realestate.booking_views.send_realestate_internal_notification_email")
    def test_same_client_same_uuid_is_exactly_once(self, internal_send, client_send):
        submission_id = uuid.uuid4()
        session = self._session(self.credential)
        payload = self._payload(session, submission_id)

        responses = self._concurrent_posts([payload, dict(payload)])

        self.assertEqual(sorted(status for status, _ in responses), [200, 201])
        self.assertEqual(
            sorted(body["duplicate"] for _, body in responses), [False, True]
        )
        self.assertTrue(all(set(body) == {"state", "duplicate"} for _, body in responses))
        enquiries = RealEstateEnquiry.objects.filter(submission_id=submission_id)
        self.assertEqual(enquiries.count(), 1)
        enquiry = enquiries.get()
        self.assertEqual(
            enquiry.timeline_events.filter(event_type="enquiry_received").count(), 1
        )
        self.assertEqual(
            RealEstateBookingAccessEvent.objects.filter(
                enquiry=enquiry,
                event_type=RealEstateBookingAccessEvent.EventType.SUBMISSION,
                result_code=RealEstateBookingAccessEvent.ResultCode.SUBMITTED,
            ).count(),
            1,
        )
        self.assertEqual(internal_send.call_count, 1)
        self.assertEqual(client_send.call_count, 1)

    @patch("realestate.booking_views.send_realestate_client_confirmation_email")
    @patch("realestate.booking_views.send_realestate_internal_notification_email")
    def test_different_clients_same_uuid_returns_generic_conflict(
        self, internal_send, client_send
    ):
        other = self._client("Fictional Agent Two", "two@example.test")
        other_credential, _ = generate_primary_credential(other, actor=self.staff)
        submission_id = uuid.uuid4()
        payloads = [
            self._payload(self._session(self.credential), submission_id),
            self._payload(self._session(other_credential), submission_id),
        ]

        responses = self._concurrent_posts(payloads)

        self.assertEqual(sorted(status for status, _ in responses), [201, 409])
        conflict = next(body for status, body in responses if status == 409)
        self.assertEqual(
            conflict,
            {"state": "unavailable", "detail": "Booking access is unavailable."},
        )
        enquiries = RealEstateEnquiry.objects.filter(submission_id=submission_id)
        self.assertEqual(enquiries.count(), 1)
        enquiry = enquiries.get()
        self.assertEqual(
            enquiry.timeline_events.filter(event_type="enquiry_received").count(), 1
        )
        self.assertEqual(
            RealEstateBookingAccessEvent.objects.filter(
                enquiry=enquiry,
                event_type=RealEstateBookingAccessEvent.EventType.SUBMISSION,
                result_code=RealEstateBookingAccessEvent.ResultCode.SUBMITTED,
            ).count(),
            1,
        )
        self.assertEqual(internal_send.call_count, 1)
        self.assertEqual(client_send.call_count, 1)

    def test_concurrent_credential_generation_has_one_active_primary(self):
        client_record = self._client("Credential Race Agent", "race@example.test")
        barrier = threading.Barrier(2)

        def generate():
            close_old_connections()
            try:
                current_client = RealEstateClient.objects.get(pk=client_record.pk)
                actor = get_user_model().objects.get(pk=self.staff.pk)
                barrier.wait(timeout=10)
                credential, changed = generate_primary_credential(
                    current_client, actor=actor
                )
                return credential.pk, changed
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: generate(), range(2)))

        active = RealEstateBookingCredential.objects.filter(
            client=client_record,
            is_primary=True,
            revoked_at__isnull=True,
        )
        self.assertEqual(active.count(), 1)
        self.assertEqual({pk for pk, _ in results}, {active.get().pk})
        self.assertEqual(sorted(changed for _, changed in results), [False, True])
