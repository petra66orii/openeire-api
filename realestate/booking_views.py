import hashlib
import logging
import secrets

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from openeire_api.throttling import SharedScopedRateThrottle

from .booking import (
    booking_client_dto,
    credential_secret_matches,
    credential_unavailable_reason,
    issue_booking_session,
    load_booking_session,
    record_booking_access_event,
    validate_booking_secret_independence,
)
from .booking_serializers import (
    BookingExchangeSerializer,
    BookingSessionSerializer,
    ReturningBookingSubmissionSerializer,
    credential_for_public_id,
)
from .emails import (
    send_realestate_client_confirmation_email,
    send_realestate_internal_notification_email,
)
from .models import (
    RealEstateBookingAccessEvent,
    RealEstateEnquiry,
)
from .timeline import record_timeline_event


logger = logging.getLogger(__name__)
GENERIC_UNAVAILABLE = {"state": "unavailable", "detail": "Booking access is unavailable."}


class BookingScopedRateThrottle(SharedScopedRateThrottle):
    def get_cache_key(self, request, view):
        raw = request.data.get("public_id") or request.data.get("session") or ""
        hashed = hashlib.sha256(str(raw).encode("utf-8")).hexdigest() if raw else "none"
        return self.cache_format % {
            "scope": self.scope,
            "ident": f"{self.get_ident(request)}:{hashed}",
        }


class IsBookingInternalRequest(BasePermission):
    message = "Booking access is unavailable."

    def has_permission(self, request, view):
        try:
            expected = validate_booking_secret_independence()[
                "REAL_ESTATE_BOOKING_INTERNAL_SECRET"
            ]
        except ImproperlyConfigured:
            return False
        supplied = str(request.headers.get("X-OpenEire-Booking-Internal", "") or "")
        if len(expected) < 32 or not supplied or not secrets.compare_digest(expected, supplied):
            return False
        expected_origin = str(getattr(settings, "FRONTEND_ORIGIN", "") or "").rstrip("/")
        supplied_origin = str(request.headers.get("Origin", "") or "").rstrip("/")
        return bool(expected_origin and supplied_origin == expected_origin)


class BookingInternalView(APIView):
    authentication_classes = []
    permission_classes = [IsBookingInternalRequest]


class BookingExchangeView(BookingInternalView):
    throttle_classes = [BookingScopedRateThrottle]
    throttle_scope = "real_estate_booking_exchange"

    def post(self, request):
        serializer = BookingExchangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        credential = credential_for_public_id(serializer.validated_data["public_id"])
        if not credential or not credential_secret_matches(
            credential, serializer.validated_data["secret"]
        ):
            return Response(GENERIC_UNAVAILABLE, status=status.HTTP_404_NOT_FOUND)
        reason = credential_unavailable_reason(credential)
        if reason:
            record_booking_access_event(
                credential,
                RealEstateBookingAccessEvent.EventType.ACCESS_DENIED,
                reason if reason in RealEstateBookingAccessEvent.ResultCode.values else RealEstateBookingAccessEvent.ResultCode.INVALID_SESSION,
            )
            return Response(GENERIC_UNAVAILABLE, status=status.HTTP_404_NOT_FOUND)
        session_token, lifetime = issue_booking_session(credential)
        credential.last_exchanged_at = timezone.now()
        credential.save(update_fields=("last_exchanged_at", "updated_at"))
        record_booking_access_event(
            credential,
            RealEstateBookingAccessEvent.EventType.EXCHANGE,
            RealEstateBookingAccessEvent.ResultCode.ALLOWED,
        )
        return Response({"state": "valid", "session": session_token, "expires_in": lifetime})


class BookingSessionView(BookingInternalView):
    throttle_classes = [BookingScopedRateThrottle]
    throttle_scope = "real_estate_booking_session"

    def post(self, request):
        serializer = BookingSessionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            credential = load_booking_session(serializer.validated_data["session"])
        except Exception:
            return Response(GENERIC_UNAVAILABLE, status=status.HTTP_404_NOT_FOUND)
        record_booking_access_event(
            credential,
            RealEstateBookingAccessEvent.EventType.SESSION,
            RealEstateBookingAccessEvent.ResultCode.ALLOWED,
        )
        return Response({"state": "valid", "client": booking_client_dto(credential)})


class ReturningBookingSubmissionView(BookingInternalView):
    throttle_classes = [BookingScopedRateThrottle]
    throttle_scope = "real_estate_booking_submit"

    def _send_emails(self, enquiry):
        try:
            send_realestate_internal_notification_email(enquiry, request=self.request)
        except Exception as exc:
            logger.error(
                "Returning booking internal notification failed. enquiry_id=%s failure_code=%s",
                enquiry.pk,
                exc.__class__.__name__,
            )
        try:
            send_realestate_client_confirmation_email(enquiry)
        except Exception as exc:
            logger.error(
                "Returning booking client confirmation failed. enquiry_id=%s failure_code=%s",
                enquiry.pk,
                exc.__class__.__name__,
            )

    def post(self, request):
        session_serializer = BookingSessionSerializer(data={"session": request.data.get("session")})
        session_serializer.is_valid(raise_exception=True)
        try:
            credential = load_booking_session(session_serializer.validated_data["session"])
        except Exception:
            return Response(GENERIC_UNAVAILABLE, status=status.HTTP_404_NOT_FOUND)

        payload = dict(request.data)
        payload.pop("session", None)
        serializer = ReturningBookingSubmissionSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        submission_id = data.pop("submission_id")
        data.pop("saved_details_confirmed")
        data.pop("privacy_acknowledged")

        existing = RealEstateEnquiry.objects.filter(submission_id=submission_id).first()
        if existing:
            if existing.client_id != credential.client_id:
                return Response(GENERIC_UNAVAILABLE, status=status.HTTP_409_CONFLICT)
            record_booking_access_event(
                credential,
                RealEstateBookingAccessEvent.EventType.SUBMISSION,
                RealEstateBookingAccessEvent.ResultCode.DUPLICATE,
                enquiry=existing,
            )
            return Response({"state": "submitted", "duplicate": True}, status=status.HTTP_200_OK)

        created = False
        try:
            with transaction.atomic():
                client = credential.client
                enquiry = RealEstateEnquiry.objects.create(
                    **data,
                    submission_id=submission_id,
                    client=client,
                    returning_credential=credential,
                    submission_source=RealEstateEnquiry.SubmissionSource.RETURNING_CLIENT,
                    contact_details_reviewed_at=timezone.now(),
                    consent_to_contact=True,
                    name=client.name,
                    email=client.email,
                    phone=client.phone,
                    client_type=client.client_type,
                    company_name=client.company_name,
                )
                notes = [
                    "Returning-client booking request",
                    f"Preferred package: {enquiry.get_preferred_package_summary()}",
                    f"Property address: {enquiry.property_address}",
                ]
                record_timeline_event(
                    enquiry,
                    "enquiry_received",
                    status="completed",
                    actor_type="client",
                    title="Returning-client enquiry received",
                    notes="\n".join(notes),
                )
                record_booking_access_event(
                    credential,
                    RealEstateBookingAccessEvent.EventType.SUBMISSION,
                    RealEstateBookingAccessEvent.ResultCode.SUBMITTED,
                    enquiry=enquiry,
                )
                created = True
        except IntegrityError:
            enquiry = RealEstateEnquiry.objects.filter(submission_id=submission_id).first()
            if not enquiry:
                raise
            if enquiry.client_id != credential.client_id:
                return Response(GENERIC_UNAVAILABLE, status=status.HTTP_409_CONFLICT)

        if created:
            self._send_emails(enquiry)
            return Response({"state": "submitted", "duplicate": False}, status=status.HTTP_201_CREATED)
        return Response({"state": "submitted", "duplicate": True}, status=status.HTTP_200_OK)
