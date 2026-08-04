import hashlib
import logging
import secrets
from types import SimpleNamespace

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from products.permissions import IsStaffUser
from openeire_api.throttling import SharedScopedRateThrottle

from .delivery import (
    delivery_dto,
    delivery_preview_dto,
    evaluate_delivery_access,
    exchange_staff_preview,
    issue_delivery_session,
    load_delivery_session,
    load_staff_preview_session,
    recipient_secret_matches,
    record_access_event,
    validate_delivery_secret_independence,
)
from .delivery_serializers import (
    DeliveryDownloadSerializer,
    DeliveryExchangeSerializer,
    DeliverySessionSerializer,
    DeliveryUploadCompleteSerializer,
    DeliveryUploadPartSerializer,
    DeliveryUploadSessionSerializer,
    DeliveryUploadStartSerializer,
)
from .delivery_storage import (
    abort_upload,
    complete_upload,
    download_url,
    get_max_files,
    part_url,
    start_upload,
)
from .models import (
    RealEstateDeliverable,
    RealEstateDelivery,
    RealEstateDeliveryAccessEvent,
    RealEstateDeliveryRecipient,
    RealEstateDeliveryUploadSession,
)

logger = logging.getLogger(__name__)
GENERIC_UNAVAILABLE = {"state": "unavailable", "detail": "Delivery is unavailable."}


def _record_denied_decision(recipient, decision):
    event_type = RealEstateDeliveryAccessEvent.EventType.ACCESS_DENIED
    if decision.reason == "expired":
        event_type = RealEstateDeliveryAccessEvent.EventType.EXPIRED
    elif decision.reason == "revoked":
        event_type = RealEstateDeliveryAccessEvent.EventType.REVOKED
    record_access_event(
        recipient.delivery,
        event_type,
        recipient=recipient,
        metadata={"state": decision.state, "reason": decision.reason},
    )


class DeliveryScopedRateThrottle(SharedScopedRateThrottle):
    def get_cache_key(self, request, view):
        raw_identifier = (
            request.data.get("public_id")
            if getattr(view, "throttle_scope", "") == "real_estate_delivery_exchange"
            else request.data.get("session")
        )
        if raw_identifier:
            identifier = hashlib.sha256(
                str(raw_identifier).encode("utf-8")
            ).hexdigest()
        else:
            identifier = self.get_ident(request)
        return self.cache_format % {
            "scope": self.scope,
            "ident": identifier,
        }


class IsDeliveryInternalRequest(BasePermission):
    message = "Delivery is unavailable."

    def has_permission(self, request, view):
        try:
            expected = validate_delivery_secret_independence()[
                "REAL_ESTATE_DELIVERY_INTERNAL_SECRET"
            ]
        except ImproperlyConfigured:
            return False
        supplied = str(request.headers.get("X-OpenEire-Delivery-Internal", "") or "")
        if len(expected) < 32 or not supplied or not secrets.compare_digest(expected, supplied):
            return False
        expected_origin = str(getattr(settings, "FRONTEND_ORIGIN", "") or "")
        supplied_origin = str(request.headers.get("Origin", "") or "").rstrip("/")
        return bool(expected_origin and supplied_origin == expected_origin.rstrip("/"))


class DeliveryInternalView(APIView):
    authentication_classes = []
    permission_classes = [IsDeliveryInternalRequest]


class DeliveryExchangeView(DeliveryInternalView):
    throttle_classes = [DeliveryScopedRateThrottle]
    throttle_scope = "real_estate_delivery_exchange"

    def post(self, request):
        serializer = DeliveryExchangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if str(serializer.validated_data["secret"]).startswith("preview."):
            try:
                delivery, session_token, lifetime = exchange_staff_preview(
                    serializer.validated_data["public_id"],
                    serializer.validated_data["secret"],
                )
            except Exception:
                return Response(GENERIC_UNAVAILABLE, status=status.HTTP_404_NOT_FOUND)
            record_access_event(
                delivery,
                RealEstateDeliveryAccessEvent.EventType.SESSION_ACCESSED,
                metadata={"state": "preview"},
            )
            return Response(
                {
                    "state": "valid",
                    "session": session_token,
                    "expires_in": lifetime,
                }
            )
        recipient = (
            RealEstateDeliveryRecipient.objects.select_related(
                "delivery", "delivery__enquiry"
            )
            .filter(public_id=serializer.validated_data["public_id"])
            .first()
        )
        if not recipient or not recipient_secret_matches(
            recipient, serializer.validated_data["secret"]
        ):
            return Response(GENERIC_UNAVAILABLE, status=status.HTTP_404_NOT_FOUND)
        decision = evaluate_delivery_access(recipient)
        if not decision.allowed:
            _record_denied_decision(recipient, decision)
            visible_state = (
                decision.state
                if decision.state in {"payment_locked", "temporarily_unavailable", "empty"}
                else "unavailable"
            )
            return Response(
                {"state": visible_state, "detail": "Delivery is unavailable."},
                status=status.HTTP_423_LOCKED,
            )
        session_token, lifetime = issue_delivery_session(recipient)
        now = timezone.now()
        update_fields = ["last_accessed_at", "updated_at"]
        recipient.last_accessed_at = now
        if not recipient.first_accessed_at:
            recipient.first_accessed_at = now
            update_fields.append("first_accessed_at")
        recipient.save(update_fields=update_fields)
        record_access_event(
            recipient.delivery,
            RealEstateDeliveryAccessEvent.EventType.SESSION_ACCESSED,
            recipient=recipient,
        )
        return Response(
            {"state": "valid", "session": session_token, "expires_in": lifetime},
            status=status.HTTP_200_OK,
        )


class DeliverySessionView(DeliveryInternalView):
    throttle_classes = [DeliveryScopedRateThrottle]
    throttle_scope = "real_estate_delivery_session"

    def post(self, request):
        serializer = DeliverySessionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if str(serializer.validated_data["session"]).startswith("preview-session."):
            try:
                delivery = load_staff_preview_session(
                    serializer.validated_data["session"]
                )
            except Exception:
                return Response(GENERIC_UNAVAILABLE, status=status.HTTP_404_NOT_FOUND)
            return Response(delivery_preview_dto(delivery))
        try:
            recipient = load_delivery_session(serializer.validated_data["session"])
        except Exception:
            return Response(GENERIC_UNAVAILABLE, status=status.HTTP_404_NOT_FOUND)
        dto = delivery_dto(recipient)
        if dto["state"] != "valid":
            _record_denied_decision(
                recipient,
                evaluate_delivery_access(recipient),
            )
            return Response(dto, status=status.HTTP_423_LOCKED)
        recipient.last_accessed_at = timezone.now()
        recipient.save(update_fields=("last_accessed_at", "updated_at"))
        return Response(dto)


class DeliveryDownloadView(DeliveryInternalView):
    throttle_classes = [DeliveryScopedRateThrottle]
    throttle_scope = "real_estate_delivery_download"

    def post(self, request):
        serializer = DeliveryDownloadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if str(serializer.validated_data["session"]).startswith("preview-session."):
            return Response(GENERIC_UNAVAILABLE, status=status.HTTP_403_FORBIDDEN)
        try:
            recipient = load_delivery_session(serializer.validated_data["session"])
        except Exception:
            return Response(GENERIC_UNAVAILABLE, status=status.HTTP_404_NOT_FOUND)
        decision = evaluate_delivery_access(recipient)
        if not decision.allowed:
            _record_denied_decision(recipient, decision)
            return Response(GENERIC_UNAVAILABLE, status=status.HTTP_423_LOCKED)
        deliverable = RealEstateDeliverable.objects.filter(
            delivery=recipient.delivery,
            public_id=serializer.validated_data["deliverable_id"],
            is_active=True,
            deleted_at__isnull=True,
            available_at__isnull=False,
            available_at__lte=timezone.now(),
        ).first()
        if not deliverable:
            record_access_event(
                recipient.delivery,
                RealEstateDeliveryAccessEvent.EventType.ACCESS_DENIED,
                recipient=recipient,
                metadata={"state": "unavailable", "reason": "file_unavailable"},
            )
            return Response(GENERIC_UNAVAILABLE, status=status.HTTP_404_NOT_FOUND)
        url = download_url(deliverable)
        now = timezone.now()
        if not recipient.first_download_url_issued_at:
            recipient.first_download_url_issued_at = now
            recipient.save(
                update_fields=("first_download_url_issued_at", "updated_at")
            )
        record_access_event(
            recipient.delivery,
            RealEstateDeliveryAccessEvent.EventType.DOWNLOAD_URL_ISSUED,
            recipient=recipient,
            deliverable=deliverable,
            metadata={"category": deliverable.category},
        )
        return Response({"redirect_url": url})


class StaffDeliveryUploadView(APIView):
    authentication_classes = [SessionAuthentication, JWTAuthentication]
    permission_classes = [IsStaffUser]

    def require_delivery_change_permission(self, request):
        if not request.user.has_perm("realestate.change_realestatedelivery"):
            raise PermissionDenied("Delivery change permission is required.")

    def get_session(self, request, serializer):
        self.require_delivery_change_permission(request)
        return RealEstateDeliveryUploadSession.objects.filter(
            created_by=request.user,
            upload_id=serializer.validated_data["upload_id"],
        ).first()


class StaffDeliveryUploadStartView(StaffDeliveryUploadView):
    def post(self, request):
        self.require_delivery_change_permission(request)
        serializer = DeliveryUploadStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        upload_id, object_key, part_size, filename, mime_type = start_upload(
            data["delivery"],
            data["filename"],
            data["content_type"],
            data["file_size"],
            data["category"],
        )
        try:
            session = RealEstateDeliveryUploadSession.objects.create(
                delivery=data["delivery"],
                created_by=request.user,
                original_filename=filename,
                display_name=data["display_name"],
                category=data["category"],
                replaces=data["replacement"],
                object_key=object_key,
                upload_id=upload_id,
                expected_size=data["file_size"],
                expected_mime_type=mime_type,
                part_size=part_size,
                sort_order=data["sort_order"],
            )
        except Exception:
            logger.exception("Unable to persist real-estate delivery upload session.")
            try:
                abort_upload(
                    SimpleNamespace(upload_id=upload_id, object_key=object_key)
                )
            except Exception:
                logger.exception(
                    "Unable to abort unpersisted real-estate multipart upload."
                )
            raise
        return Response(
            {
                "upload_id": session.upload_id,
                "part_size": session.part_size,
                "max_concurrency": 4,
            },
            status=status.HTTP_201_CREATED,
        )


class StaffDeliveryUploadPartView(StaffDeliveryUploadView):
    def post(self, request):
        serializer = DeliveryUploadPartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session = self.get_session(request, serializer)
        if not session or session.status != session.Status.INITIATED:
            return Response({"detail": "Upload is unavailable."}, status=400)
        return Response(
            {"url": part_url(session, serializer.validated_data["part_number"])}
        )


class StaffDeliveryUploadCompleteView(StaffDeliveryUploadView):
    def post(self, request):
        serializer = DeliveryUploadCompleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session = self.get_session(request, serializer)
        if not session:
            return Response({"detail": "Upload is unavailable."}, status=404)
        with transaction.atomic():
            session = RealEstateDeliveryUploadSession.objects.select_for_update().get(
                pk=session.pk
            )
            if session.status == session.Status.COMPLETED:
                deliverable = session.delivery.deliverables.filter(
                    object_key=session.object_key
                ).first()
                return Response(
                    {"success": True, "deliverable_id": str(deliverable.public_id)}
                )
            if session.status != session.Status.INITIATED:
                return Response({"detail": "Upload is unavailable."}, status=400)
            session.status = session.Status.COMPLETING
            session.save(update_fields=("status", "updated_at"))
        try:
            head = complete_upload(session, serializer.validated_data["parts"])
        except Exception:
            logger.exception(
                "Delivery upload verification failed. upload_session_id=%s", session.pk
            )
            RealEstateDeliveryUploadSession.objects.filter(
                pk=session.pk, status=session.Status.COMPLETING
            ).update(status=session.Status.FAILED, error_code="verification_failed")
            return Response({"detail": "Upload verification failed."}, status=502)
        with transaction.atomic():
            session = RealEstateDeliveryUploadSession.objects.select_for_update().get(
                pk=session.pk
            )
            existing = session.delivery.deliverables.filter(
                object_key=session.object_key
            ).first()
            if existing:
                session.status = session.Status.COMPLETED
                session.completed_at = session.completed_at or timezone.now()
                session.save(update_fields=("status", "completed_at", "updated_at"))
                return Response(
                    {"success": True, "deliverable_id": str(existing.public_id)}
                )
            delivery = RealEstateDelivery.objects.select_for_update().get(
                pk=session.delivery_id
            )
            replacement = None
            if session.replaces_id:
                replacement = (
                    RealEstateDeliverable.objects.select_for_update()
                    .filter(
                        pk=session.replaces_id,
                        delivery=delivery,
                        is_active=True,
                        deleted_at__isnull=True,
                    )
                    .first()
                )
                if not replacement:
                    session.status = session.Status.FAILED
                    session.error_code = "replacement_unavailable"
                    session.save(
                        update_fields=("status", "error_code", "updated_at")
                    )
                    return Response(
                        {"detail": "Replacement target is no longer available."},
                        status=status.HTTP_409_CONFLICT,
                    )
            elif delivery.deliverables.filter(
                is_active=True,
                deleted_at__isnull=True,
            ).count() >= get_max_files():
                session.status = session.Status.FAILED
                session.error_code = "file_limit_reached"
                session.save(update_fields=("status", "error_code", "updated_at"))
                return Response(
                    {"detail": "This delivery has reached its configured file limit."},
                    status=status.HTTP_409_CONFLICT,
                )
            version = (replacement.version + 1) if replacement else 1
            deliverable = RealEstateDeliverable.objects.create(
                delivery=delivery,
                category=session.category,
                display_name=session.display_name,
                original_filename=session.original_filename,
                object_key=session.object_key,
                file_size=session.expected_size,
                mime_type=session.expected_mime_type,
                checksum_algorithm="sha256" if head.get("ChecksumSHA256") else "",
                checksum_value=str(head.get("ChecksumSHA256", "")),
                version=version,
                is_active=True,
                available_at=timezone.now(),
                sort_order=session.sort_order,
                uploaded_by=request.user,
                replaces=replacement,
            )
            event_type = RealEstateDeliveryAccessEvent.EventType.UPLOAD_COMPLETED
            if replacement:
                replacement.is_active = False
                replacement.replaced_at = timezone.now()
                replacement.save(update_fields=("is_active", "replaced_at", "updated_at"))
                event_type = RealEstateDeliveryAccessEvent.EventType.FILE_REPLACED
            session.status = session.Status.COMPLETED
            session.completed_at = timezone.now()
            session.save(update_fields=("status", "completed_at", "updated_at"))
            record_access_event(
                session.delivery,
                event_type,
                deliverable=deliverable,
                metadata={"category": deliverable.category},
            )
        return Response(
            {"success": True, "deliverable_id": str(deliverable.public_id)}
        )


class StaffDeliveryUploadAbortView(StaffDeliveryUploadView):
    def post(self, request):
        serializer = DeliveryUploadSessionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session = self.get_session(request, serializer)
        if not session:
            return Response({"detail": "Upload is unavailable."}, status=404)
        with transaction.atomic():
            session = RealEstateDeliveryUploadSession.objects.select_for_update().get(
                pk=session.pk
            )
            if session.status == session.Status.ABORTED:
                return Response({"success": True})
            if session.status != session.Status.INITIATED:
                return Response({"detail": "Upload is unavailable."}, status=400)
            session.status = session.Status.ABORTING
            session.save(update_fields=("status", "updated_at"))
        try:
            abort_upload(session)
        except Exception:
            logger.exception("Delivery multipart abort failed. upload_session_id=%s", session.pk)
            session.status = session.Status.FAILED
            session.error_code = "abort_failed"
            session.save(update_fields=("status", "error_code", "updated_at"))
            return Response({"detail": "Upload abort failed."}, status=502)
        session.status = session.Status.ABORTED
        session.aborted_at = timezone.now()
        session.save(update_fields=("status", "aborted_at", "updated_at"))
        return Response({"success": True})
