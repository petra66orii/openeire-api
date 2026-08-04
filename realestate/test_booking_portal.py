import json
import re
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from openeire_api.admin import custom_admin_site

from .admin import (
    RealEstateBookingCredentialAdmin,
    RealEstateClientAdmin,
    RealEstateEnquiryAdmin,
)
from .booking import (
    booking_client_dto,
    build_booking_url,
    credential_secret,
    generate_primary_credential,
    issue_booking_session,
    load_booking_session,
    rotate_booking_credential,
    validate_booking_secret_independence,
)
from .booking_emails import send_booking_link_email
from .models import (
    RealEstateBookingAccessEvent,
    RealEstateBookingCredential,
    RealEstateBookingEmailAttempt,
    RealEstateClient,
    RealEstateEnquiry,
)


TOKEN_KEY = "booking-token-key-abcdefghijklmnopqrstuvwxyz-123456"
SESSION_KEY = "booking-session-key-abcdefghijklmnopqrstuvwxyz-654321"
INTERNAL_KEY = "booking-internal-key-abcdefghijklmnopqrstuvwxyz-987654"
BOOKING_SETTINGS = {
    "REAL_ESTATE_BOOKING_PORTAL_ENABLED": True,
    "REAL_ESTATE_BOOKING_EMAIL_ENABLED": True,
    "REAL_ESTATE_BOOKING_TOKEN_KEY": TOKEN_KEY,
    "REAL_ESTATE_BOOKING_SESSION_KEY": SESSION_KEY,
    "REAL_ESTATE_BOOKING_INTERNAL_SECRET": INTERNAL_KEY,
    "REAL_ESTATE_BOOKING_CREDENTIAL_DAYS": 90,
    "REAL_ESTATE_BOOKING_SESSION_SECONDS": 43200,
    "REAL_ESTATE_DELIVERY_TOKEN_KEY": "delivery-token-distinct-abcdefghijklmnopqrstuvwxyz",
    "REAL_ESTATE_DELIVERY_SESSION_KEY": "delivery-session-distinct-abcdefghijklmnopqrstuvwxyz",
    "REAL_ESTATE_DELIVERY_INTERNAL_SECRET": "delivery-internal-distinct-abcdefghijklmnopqrstuvwxyz",
    "FRONTEND_URL": "https://openeire.test",
    "FRONTEND_ORIGIN": "https://openeire.test",
    "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    "REAL_ESTATE_NOTIFICATION_EMAIL": "studio@example.test",
    "DEFAULT_FROM_EMAIL": "studio@example.test",
}


@override_settings(**BOOKING_SETTINGS)
class ReturningBookingPortalTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="booking-staff", email="staff@example.test", password="test"
        )
        self.client_record = RealEstateClient.objects.create(
            name="Fiona Agent",
            email="FIONA@EXAMPLE.TEST",
            phone="+353 87 123 4567",
            client_type="estate_agent",
            company_name="Fictional Homes",
            identity_confirmed_at=timezone.now(),
            identity_confirmed_by=self.staff,
        )
        self.credential, _ = generate_primary_credential(
            self.client_record, actor=self.staff
        )
        self.headers = {
            "HTTP_ORIGIN": "https://openeire.test",
            "HTTP_X_OPENEIRE_BOOKING_INTERNAL": INTERNAL_KEY,
        }

    def post_json(self, path, payload):
        return self.client.post(
            path,
            data=json.dumps(payload),
            content_type="application/json",
            **self.headers,
        )

    def session(self):
        token, lifetime = issue_booking_session(self.credential)
        self.assertEqual(lifetime, 43200)
        return token

    def payload(self, *, submission_id=None, address="Fictional House, Galway"):
        return {
            "session": self.session(),
            "submission_id": str(submission_id or uuid.uuid4()),
            "property_address": address,
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

    def test_normalizes_without_unique_identity_or_authentication(self):
        self.assertEqual(self.client_record.normalized_email, "fiona@example.test")
        self.assertEqual(self.client_record.normalized_phone, "353871234567")
        second = RealEstateClient.objects.create(
            name="Shared Inbox Colleague", email="fiona@example.test", phone="+353871234567",
            client_type="estate_agent", company_name="Fictional Homes",
            identity_confirmed_at=timezone.now(), identity_confirmed_by=self.staff,
        )
        self.assertNotEqual(second.pk, self.client_record.pk)

    def test_default_credential_is_reusable_for_ninety_days(self):
        remaining = self.credential.expires_at - timezone.now()
        self.assertGreater(remaining, timedelta(days=89))
        same, changed = generate_primary_credential(self.client_record, actor=self.staff)
        self.assertFalse(changed)
        self.assertEqual(same.pk, self.credential.pk)
        self.assertEqual(build_booking_url(same), build_booking_url(self.credential))

    def test_exchange_returns_masked_dto_without_history(self):
        response = self.post_json("/api/real-estate/booking/exchange/", {
            "public_id": str(self.credential.public_id), "secret": credential_secret(self.credential),
        })
        self.assertEqual(response.status_code, 200)
        session_response = self.post_json("/api/real-estate/booking/session/", {"session": response.json()["session"]})
        self.assertEqual(session_response.status_code, 200)
        dto = session_response.json()["client"]
        self.assertEqual(dto["masked_email"], "F***@EXAMPLE.TEST")
        self.assertEqual(dto["masked_phone"], "*** *** 4567")
        self.assertNotIn("id", dto)
        self.assertNotIn("enquiries", dto)
        self.assertNotIn("phone", dto)
        self.assertNotIn("email", dto)

    def test_invalid_expired_revoked_and_inactive_are_generic(self):
        invalid = self.post_json("/api/real-estate/booking/exchange/", {
            "public_id": str(self.credential.public_id), "secret": "wrong",
        })
        self.assertEqual(invalid.status_code, 404)
        self.assertEqual(invalid.json()["state"], "unavailable")
        self.credential.expires_at = timezone.now() - timedelta(seconds=1)
        self.credential.save(update_fields=("expires_at", "updated_at"))
        expired = self.post_json("/api/real-estate/booking/exchange/", {
            "public_id": str(self.credential.public_id), "secret": credential_secret(self.credential),
        })
        self.assertEqual(expired.status_code, 404)
        self.credential.expires_at = timezone.now() + timedelta(days=1)
        self.credential.revoked_at = timezone.now()
        self.credential.revocation_reason = "Fictional test revocation"
        self.credential.save()
        revoked = self.post_json("/api/real-estate/booking/exchange/", {
            "public_id": str(self.credential.public_id), "secret": credential_secret(self.credential),
        })
        self.assertEqual(revoked.json(), expired.json())

        self.credential.revoked_at = None
        self.credential.revocation_reason = ""
        self.credential.save(update_fields=("revoked_at", "revocation_reason", "updated_at"))
        self.client_record.status = RealEstateClient.Status.INACTIVE
        self.client_record.save(update_fields=("status", "updated_at"))
        inactive = self.post_json("/api/real-estate/booking/exchange/", {
            "public_id": str(self.credential.public_id), "secret": credential_secret(self.credential),
        })
        self.assertEqual(inactive.json(), expired.json())

    def test_rotation_invalidates_existing_link_and_session(self):
        old_secret = credential_secret(self.credential)
        old_session = self.session()
        self.credential = rotate_booking_credential(self.credential, actor=self.staff)
        self.assertNotEqual(old_secret, credential_secret(self.credential))
        with self.assertRaises(PermissionDenied):
            load_booking_session(old_session)

    def test_stale_orm_instances_can_only_emit_the_current_rotated_link(self):
        stale = RealEstateBookingCredential.objects.get(pk=self.credential.pk)
        old_url = build_booking_url(stale)
        rotate_booking_credential(self.credential, actor=self.staff)
        current = RealEstateBookingCredential.objects.get(pk=self.credential.pk)
        current_url = build_booking_url(stale)
        self.assertNotEqual(old_url, current_url)
        self.assertEqual(current_url, build_booking_url(current))

        old_secret = old_url.split("#", 1)[1]
        current_secret = current_url.split("#", 1)[1]
        old_response = self.post_json("/api/real-estate/booking/exchange/", {
            "public_id": str(current.public_id), "secret": old_secret,
        })
        current_response = self.post_json("/api/real-estate/booking/exchange/", {
            "public_id": str(current.public_id), "secret": current_secret,
        })
        self.assertEqual(old_response.status_code, 404)
        self.assertEqual(current_response.status_code, 200)

        send_booking_link_email(
            stale,
            kind=RealEstateBookingEmailAttempt.Kind.RESEND,
            idempotency_key="stale-instance-resend",
        )
        self.assertIn(current_url, mail.outbox[-1].body)
        self.assertNotIn(old_url, mail.outbox[-1].body)

    def test_generation_rechecks_authoritative_client_status_under_lock(self):
        stale_client = RealEstateClient.objects.get(pk=self.client_record.pk)
        self.client_record.status = RealEstateClient.Status.ARCHIVED
        self.client_record.save(update_fields=("status", "updated_at"))
        with self.assertRaises(ValidationError):
            generate_primary_credential(stale_client, actor=self.staff)

    def test_exactly_once_submission_snapshots_verified_identity(self):
        submission_id = uuid.uuid4()
        payload = self.payload(submission_id=submission_id)
        first = self.post_json("/api/real-estate/booking/enquiries/", payload)
        second = self.post_json("/api/real-estate/booking/enquiries/", payload)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["duplicate"])
        enquiries = RealEstateEnquiry.objects.filter(submission_id=submission_id)
        self.assertEqual(enquiries.count(), 1)
        enquiry = enquiries.get()
        self.assertEqual(enquiry.name, "Fiona Agent")
        self.assertEqual(enquiry.email, "FIONA@EXAMPLE.TEST")
        self.assertEqual(enquiry.company_name, "Fictional Homes")
        self.assertEqual(enquiry.submission_source, "returning_client")
        self.assertEqual(enquiry.timeline_events.filter(event_type="enquiry_received").count(), 1)
        self.assertEqual(len(mail.outbox), 2)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                RealEstateEnquiry.objects.create(submission_id=submission_id)

    @patch("realestate.booking_views.send_realestate_client_confirmation_email", side_effect=RuntimeError("smtp offline"))
    @patch("realestate.booking_views.send_realestate_internal_notification_email", side_effect=RuntimeError("smtp offline"))
    def test_email_failures_do_not_roll_back_a_persisted_enquiry(self, _internal, _client):
        submission_id = uuid.uuid4()
        response = self.post_json(
            "/api/real-estate/booking/enquiries/",
            self.payload(submission_id=submission_id),
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(RealEstateEnquiry.objects.filter(submission_id=submission_id).exists())

    def test_reusable_link_submits_multiple_properties(self):
        first = self.post_json("/api/real-estate/booking/enquiries/", self.payload(address="Fictional One"))
        second = self.post_json("/api/real-estate/booking/enquiries/", self.payload(address="Fictional Two"))
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertEqual(self.client_record.enquiries.count(), 2)

    def test_browser_identity_fields_are_rejected(self):
        payload = self.payload()
        payload.update({"name": "Attacker", "email": "attacker@example.test", "client_id": 999})
        response = self.post_json("/api/real-estate/booking/enquiries/", payload)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(RealEstateEnquiry.objects.count(), 0)

    def test_unknown_and_additional_authority_fields_are_rejected(self):
        for field, value in (("client", 1), ("submission_source", "public_form"), ("unexpected", "value")):
            payload = self.payload()
            payload[field] = value
            response = self.post_json("/api/real-estate/booking/enquiries/", payload)
            self.assertEqual(response.status_code, 400)
        self.assertEqual(RealEstateEnquiry.objects.count(), 0)

    def test_land_and_agricultural_scope_is_authoritatively_conditional(self):
        payload = self.payload()
        payload["property_type"] = "agricultural"
        response = self.post_json("/api/real-estate/booking/enquiries/", payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn("grounds_size", response.json())
        self.assertIn("outbuildings", response.json())
        payload.update({"grounds_size": "1_to_5_acres", "outbuildings": "no"})
        response = self.post_json("/api/real-estate/booking/enquiries/", payload)
        self.assertEqual(response.status_code, 201)

    def test_failed_validation_does_not_consume_submission_uuid(self):
        submission_id = uuid.uuid4()
        invalid = self.payload(submission_id=submission_id)
        invalid["eircode"] = "invalid"
        self.assertEqual(
            self.post_json("/api/real-estate/booking/enquiries/", invalid).status_code,
            400,
        )
        self.assertEqual(
            self.post_json(
                "/api/real-estate/booking/enquiries/",
                self.payload(submission_id=submission_id),
            ).status_code,
            201,
        )
        self.assertEqual(RealEstateEnquiry.objects.filter(submission_id=submission_id).count(), 1)

    def test_other_client_cannot_claim_an_existing_submission_uuid(self):
        submission_id = uuid.uuid4()
        self.assertEqual(
            self.post_json(
                "/api/real-estate/booking/enquiries/",
                self.payload(submission_id=submission_id),
            ).status_code,
            201,
        )
        other_client = RealEstateClient.objects.create(
            name="Other Fictional Agent", email="other@example.test", phone="0870000000",
            client_type="estate_agent", company_name="Other Fictional Homes",
            identity_confirmed_at=timezone.now(), identity_confirmed_by=self.staff,
        )
        other_credential, _ = generate_primary_credential(other_client, actor=self.staff)
        other_session, _ = issue_booking_session(other_credential)
        payload = self.payload(submission_id=submission_id)
        payload["session"] = other_session
        response = self.post_json("/api/real-estate/booking/enquiries/", payload)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["state"], "unavailable")

    def test_contact_update_request_is_stored_without_mutating_client(self):
        payload = self.payload()
        payload.update({"contact_update_requested": True, "contact_update_request": "Please ask me about a new office number."})
        response = self.post_json("/api/real-estate/booking/enquiries/", payload)
        self.assertEqual(response.status_code, 201)
        enquiry = RealEstateEnquiry.objects.get()
        self.assertTrue(enquiry.contact_update_requested)
        self.client_record.refresh_from_db()
        self.assertEqual(self.client_record.phone, "+353 87 123 4567")

    def test_booking_email_html_and_text_contain_same_untracked_url(self):
        attempt = send_booking_link_email(
            self.credential,
            kind=RealEstateBookingEmailAttempt.Kind.INITIAL,
            idempotency_key="fictional-booking-email",
        )
        self.assertEqual(attempt.status, RealEstateBookingEmailAttempt.Status.SENT)
        expected = build_booking_url(self.credential)
        message = mail.outbox[-1]
        self.assertIn(expected, message.body)
        self.assertIn(expected, message.alternatives[0][0])
        self.assertNotRegex(message.body, re.compile(r"https?://[^\s]*track", re.I))

    @override_settings(REAL_ESTATE_BOOKING_EMAIL_ENABLED=False)
    def test_booking_email_is_operationally_disabled_by_default(self):
        with self.assertRaises(PermissionDenied):
            send_booking_link_email(
                self.credential,
                kind=RealEstateBookingEmailAttempt.Kind.INITIAL,
                idempotency_key="disabled-email",
            )
        self.assertEqual(len(mail.outbox), 0)

    @patch("realestate.booking_emails.record_booking_access_event", side_effect=RuntimeError("audit unavailable"))
    def test_audit_failure_does_not_erase_an_accepted_email_attempt(self, _audit):
        attempt = send_booking_link_email(
            self.credential,
            kind=RealEstateBookingEmailAttempt.Kind.INITIAL,
            idempotency_key="audit-failure-email",
        )
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, RealEstateBookingEmailAttempt.Status.SENT)
        self.assertEqual(len(mail.outbox), 1)

    def test_audit_model_has_no_freeform_metadata_or_contact_fields(self):
        names = {field.name for field in RealEstateBookingAccessEvent._meta.fields}
        self.assertEqual(names, {"id", "credential", "client", "enquiry", "event_type", "result_code", "created_at"})

    def test_secret_reuse_is_rejected(self):
        with override_settings(REAL_ESTATE_BOOKING_TOKEN_KEY=TOKEN_KEY, REAL_ESTATE_DELIVERY_TOKEN_KEY=TOKEN_KEY):
            with self.assertRaises(ImproperlyConfigured):
                validate_booking_secret_independence()

    @override_settings(REAL_ESTATE_BOOKING_PORTAL_ENABLED=False)
    def test_backend_feature_flag_fails_closed(self):
        response = self.post_json("/api/real-estate/booking/exchange/", {
            "public_id": str(self.credential.public_id),
            "secret": credential_secret(self.credential),
        })
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["state"], "unavailable")

    def test_public_api_stamps_public_source_without_accepting_source_input(self):
        response = self.client.post(
            "/api/real-estate/enquiries/",
            data=json.dumps({
                "name": "Public Fictional Client",
                "email": "public@example.test",
                "phone": "0879990000",
                "client_type": "private_seller",
                "property_address": "Public Fictional House",
                "county": "Galway",
                "property_type": "house",
                "preferred_package": "starter",
                "consent_to_contact": True,
                "submission_source": "returning_client",
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        enquiry = RealEstateEnquiry.objects.get(name="Public Fictional Client")
        self.assertEqual(enquiry.submission_source, RealEstateEnquiry.SubmissionSource.PUBLIC_FORM)


@override_settings(**BOOKING_SETTINGS)
class ReturningBookingAdminSafetyTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="restricted-booking-staff",
            email="restricted@example.test",
            password="test",
            is_staff=True,
        )
        self.factory = RequestFactory()
        self.client_admin = RealEstateClientAdmin(RealEstateClient, custom_admin_site)
        self.credential_admin = RealEstateBookingCredentialAdmin(
            RealEstateBookingCredential, custom_admin_site
        )
        self.enquiry_admin = RealEstateEnquiryAdmin(RealEstateEnquiry, custom_admin_site)
        self.enquiry = RealEstateEnquiry.objects.create(
            name="Fictional Reviewed Agent",
            email="reviewed@example.test",
            phone="087 111 2222",
            client_type="estate_agent",
            company_name="Reviewed Fictional Homes",
            property_address="Fictional House",
            county="Galway",
            property_type="house",
            preferred_package="starter",
        )

    def request(self, data=None):
        request = self.factory.post("/admin/realestate/", data or {})
        request.user = self.staff
        return request

    def grant(self, codename):
        self.staff.user_permissions.add(Permission.objects.get(codename=codename))
        self.staff = get_user_model().objects.get(pk=self.staff.pk)

    def test_client_creation_requires_resolution_permission_and_is_repeat_safe(self):
        queryset = RealEstateEnquiry.objects.filter(pk=self.enquiry.pk)
        with self.assertRaises(PermissionDenied):
            self.enquiry_admin.create_returning_client_from_enquiry(
                self.request({"confirm_create_client": "1"}), queryset
            )
        self.grant("resolve_realestateclient")
        with patch.object(self.enquiry_admin, "message_user"):
            self.enquiry_admin.create_returning_client_from_enquiry(
                self.request({"confirm_create_client": "1"}), queryset
            )
            self.enquiry_admin.create_returning_client_from_enquiry(
                self.request({"confirm_create_client": "1"}), queryset
            )
        self.assertEqual(RealEstateClient.objects.count(), 1)
        self.enquiry.refresh_from_db()
        self.assertIsNotNone(self.enquiry.client_id)
        self.assertEqual(self.enquiry.name, "Fictional Reviewed Agent")
        self.assertIn("client", self.enquiry_admin.readonly_fields)
        self.assertIn(
            "submission_source",
            self.enquiry_admin.get_readonly_fields(self.request(), self.enquiry),
        )

    def test_link_generation_copy_rotation_and_revocation_permissions_are_server_side(self):
        client_record = RealEstateClient.objects.create(
            name="Admin Fictional Agent", email="admin-agent@example.test", phone="0873334444",
            client_type="estate_agent", company_name="Admin Fictional Homes",
            identity_confirmed_at=timezone.now(), identity_confirmed_by=self.staff,
        )
        with self.assertRaises(PermissionDenied):
            self.client_admin.generate_booking_access(
                self.request(), RealEstateClient.objects.filter(pk=client_record.pk)
            )
        self.assertNotIn("generate_booking_access", self.client_admin.get_actions(self.request()))
        self.assertNotIn("private_booking_link", self.credential_admin.get_fields(self.request(), None))

        self.grant("generate_realestatebookingcredential")
        confirmation = self.client_admin.generate_booking_access(
            self.request(), RealEstateClient.objects.filter(pk=client_record.pk)
        )
        self.assertEqual(confirmation.template_name, "admin/realestate/booking_credential_action.html")
        self.assertIn("generate_booking_access", self.client_admin.get_actions(self.request()))
        credential, _ = generate_primary_credential(client_record, actor=self.staff)
        self.assertIn("private_booking_link", self.credential_admin.get_fields(self.request(), credential))
        queryset = RealEstateBookingCredential.objects.filter(pk=credential.pk)
        with self.assertRaises(PermissionDenied):
            self.credential_admin.rotate_selected(self.request(), queryset)
        with self.assertRaises(PermissionDenied):
            self.credential_admin.revoke_selected(self.request(), queryset)

    @override_settings(REAL_ESTATE_BOOKING_EMAIL_ENABLED=False)
    def test_admin_email_action_fails_closed_before_sending(self):
        self.grant("generate_realestatebookingcredential")
        client_record = RealEstateClient.objects.create(
            name="Email Fictional Agent", email="email-agent@example.test", phone="0875556666",
            client_type="estate_agent", company_name="Email Fictional Homes",
            identity_confirmed_at=timezone.now(), identity_confirmed_by=self.staff,
        )
        credential, _ = generate_primary_credential(client_record, actor=self.staff)
        with self.assertRaises(PermissionDenied):
            self.credential_admin.send_or_resend_email(
                self.request(), RealEstateBookingCredential.objects.filter(pk=credential.pk)
            )
        self.assertEqual(len(mail.outbox), 0)

    def test_admin_creation_stamps_internal_source_and_source_is_immutable_in_ui(self):
        enquiry = RealEstateEnquiry(
            name="Admin Created Fictional Client",
            email="admin-created@example.test",
            phone="0877778888",
            client_type="private_seller",
            property_address="Admin Fictional House",
            county="Galway",
            property_type="house",
            preferred_package="starter",
        )
        self.enquiry_admin.save_model(self.request(), enquiry, form=None, change=False)
        self.assertEqual(
            enquiry.submission_source,
            RealEstateEnquiry.SubmissionSource.DJANGO_ADMIN,
        )
        self.assertIn(
            "submission_source",
            self.enquiry_admin.get_readonly_fields(self.request(), enquiry),
        )
