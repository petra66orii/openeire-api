import base64
import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.core import signing
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .finance import can_release_realestate_delivery
from .models import (
    RealEstateDeliverable,
    RealEstateDelivery,
    RealEstateDeliveryAccessEvent,
    RealEstateDeliveryRecipient,
    RealEstateEnquiry,
    RealEstateTimelineEvent,
)
from .timeline import record_timeline_event

TOKEN_NAMESPACE = "openeire.realestate.delivery-recipient.v1"
SESSION_NAMESPACE = "openeire.realestate.delivery-session.v1"
PREVIEW_EXCHANGE_NAMESPACE = "openeire.realestate.delivery-preview-exchange.v1"
PREVIEW_SESSION_NAMESPACE = "openeire.realestate.delivery-preview-session.v1"
PREVIEW_SECONDS = 10 * 60
DEFAULT_ACCESS_DAYS = 30
DEFAULT_GRACE_DAYS = 60
DEFAULT_SESSION_SECONDS = 12 * 60 * 60
DOWNLOAD_URL_SECONDS = 5 * 60
SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._ -]+")
DELIVERY_SECRET_SETTINGS = (
    "REAL_ESTATE_DELIVERY_TOKEN_KEY",
    "REAL_ESTATE_DELIVERY_SESSION_KEY",
    "REAL_ESTATE_DELIVERY_INTERNAL_SECRET",
)


def portal_feature_enabled():
    return bool(getattr(settings, "REAL_ESTATE_DELIVERY_PORTAL_ENABLED", False))


def validate_delivery_secret_independence():
    values = {
        setting_name: str(getattr(settings, setting_name, "") or "")
        for setting_name in DELIVERY_SECRET_SETTINGS
    }
    configured = [value for value in values.values() if value]
    if len(configured) != len(set(configured)):
        raise ImproperlyConfigured(
            "Delivery token, session and internal-service secrets must all be different."
        )
    return values


def _strong_delivery_key(setting_name):
    value = validate_delivery_secret_independence().get(
        setting_name,
        str(getattr(settings, setting_name, "") or ""),
    )
    if len(value) < 32 or len(set(value)) < 8:
        raise ImproperlyConfigured(
            f"{setting_name} must be a dedicated high-entropy secret of at least 32 characters."
        )
    return value


def recipient_secret(recipient):
    key = _strong_delivery_key("REAL_ESTATE_DELIVERY_TOKEN_KEY").encode("utf-8")
    canonical = (
        f"{TOKEN_NAMESPACE}:{recipient.public_id}:{recipient.token_salt}:"
        f"{recipient.token_version}"
    ).encode("utf-8")
    digest = hmac.new(key, canonical, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def recipient_secret_matches(recipient, supplied_secret):
    supplied = str(supplied_secret or "")
    return bool(supplied) and hmac.compare_digest(
        recipient_secret(recipient),
        supplied,
    )


def build_recipient_url(recipient):
    frontend_url = str(getattr(settings, "FRONTEND_URL", "") or "").rstrip("/")
    if not frontend_url:
        raise ImproperlyConfigured("FRONTEND_URL is required for portal delivery links.")
    return f"{frontend_url}/delivery/{recipient.public_id}#{recipient_secret(recipient)}"


def issue_delivery_session(recipient):
    now = timezone.now()
    remaining = max(0, int((recipient.delivery.expires_at - now).total_seconds()))
    lifetime = min(
        int(getattr(settings, "REAL_ESTATE_DELIVERY_SESSION_SECONDS", DEFAULT_SESSION_SECONDS)),
        remaining,
    )
    if lifetime <= 0:
        raise PermissionDenied("Delivery is unavailable.")
    payload = {
        "recipient_id": recipient.pk,
        "public_id": str(recipient.public_id),
        "token_version": recipient.token_version,
        "expires": int((now + timedelta(seconds=lifetime)).timestamp()),
        "purpose": SESSION_NAMESPACE,
    }
    key = _strong_delivery_key("REAL_ESTATE_DELIVERY_SESSION_KEY")
    return signing.dumps(payload, key=key, salt=SESSION_NAMESPACE, compress=True), lifetime


def build_staff_preview_url(delivery, user):
    if not user or not user.is_staff:
        raise PermissionDenied("Staff permission is required.")
    key = _strong_delivery_key("REAL_ESTATE_DELIVERY_SESSION_KEY")
    payload = {
        "delivery_id": delivery.pk,
        "public_id": str(delivery.public_id),
        "staff_id": user.pk,
        "purpose": PREVIEW_EXCHANGE_NAMESPACE,
    }
    credential = signing.dumps(
        payload,
        key=key,
        salt=PREVIEW_EXCHANGE_NAMESPACE,
        compress=True,
    )
    frontend_url = str(getattr(settings, "FRONTEND_URL", "") or "").rstrip("/")
    if not frontend_url:
        raise ImproperlyConfigured("FRONTEND_URL is required for delivery previews.")
    return f"{frontend_url}/delivery/{delivery.public_id}#preview.{credential}"


def exchange_staff_preview(public_id, supplied_secret):
    supplied = str(supplied_secret or "")
    if not supplied.startswith("preview."):
        raise PermissionDenied("Delivery is unavailable.")
    key = _strong_delivery_key("REAL_ESTATE_DELIVERY_SESSION_KEY")
    try:
        payload = signing.loads(
            supplied.removeprefix("preview."),
            key=key,
            salt=PREVIEW_EXCHANGE_NAMESPACE,
            max_age=PREVIEW_SECONDS,
        )
    except signing.BadSignature as exc:
        raise PermissionDenied("Delivery is unavailable.") from exc
    if (
        payload.get("purpose") != PREVIEW_EXCHANGE_NAMESPACE
        or payload.get("public_id") != str(public_id)
    ):
        raise PermissionDenied("Delivery is unavailable.")
    delivery = RealEstateDelivery.objects.filter(
        pk=payload.get("delivery_id"),
        public_id=public_id,
    ).first()
    if not delivery:
        raise PermissionDenied("Delivery is unavailable.")
    session_payload = {
        "delivery_id": delivery.pk,
        "public_id": str(delivery.public_id),
        "expires": int((timezone.now() + timedelta(seconds=PREVIEW_SECONDS)).timestamp()),
        "purpose": PREVIEW_SESSION_NAMESPACE,
    }
    session = signing.dumps(
        session_payload,
        key=key,
        salt=PREVIEW_SESSION_NAMESPACE,
        compress=True,
    )
    return delivery, f"preview-session.{session}", PREVIEW_SECONDS


def load_staff_preview_session(session_token):
    supplied = str(session_token or "")
    if not supplied.startswith("preview-session."):
        raise PermissionDenied("Delivery is unavailable.")
    key = _strong_delivery_key("REAL_ESTATE_DELIVERY_SESSION_KEY")
    try:
        payload = signing.loads(
            supplied.removeprefix("preview-session."),
            key=key,
            salt=PREVIEW_SESSION_NAMESPACE,
            max_age=PREVIEW_SECONDS,
        )
    except signing.BadSignature as exc:
        raise PermissionDenied("Delivery is unavailable.") from exc
    if (
        payload.get("purpose") != PREVIEW_SESSION_NAMESPACE
        or int(payload.get("expires", 0)) <= int(timezone.now().timestamp())
    ):
        raise PermissionDenied("Delivery is unavailable.")
    delivery = RealEstateDelivery.objects.select_related("enquiry").filter(
        pk=payload.get("delivery_id"),
        public_id=payload.get("public_id"),
    ).first()
    if not delivery:
        raise PermissionDenied("Delivery is unavailable.")
    return delivery


def load_delivery_session(session_token):
    key = _strong_delivery_key("REAL_ESTATE_DELIVERY_SESSION_KEY")
    try:
        payload = signing.loads(
            str(session_token or ""),
            key=key,
            salt=SESSION_NAMESPACE,
            max_age=int(
                getattr(
                    settings,
                    "REAL_ESTATE_DELIVERY_SESSION_SECONDS",
                    DEFAULT_SESSION_SECONDS,
                )
            ),
        )
    except signing.BadSignature as exc:
        raise PermissionDenied("Delivery is unavailable.") from exc
    if (
        payload.get("purpose") != SESSION_NAMESPACE
        or int(payload.get("expires", 0)) <= int(timezone.now().timestamp())
    ):
        raise PermissionDenied("Delivery is unavailable.")
    recipient = (
        RealEstateDeliveryRecipient.objects.select_related("delivery", "delivery__enquiry")
        .filter(pk=payload.get("recipient_id"), public_id=payload.get("public_id"))
        .first()
    )
    if not recipient or recipient.token_version != payload.get("token_version"):
        raise PermissionDenied("Delivery is unavailable.")
    return recipient


@dataclass(frozen=True)
class DeliveryAccessDecision:
    allowed: bool
    state: str
    reason: str


def evaluate_delivery_access(recipient, *, require_deliverables=True):
    now = timezone.now()
    delivery = recipient.delivery
    if not portal_feature_enabled() or not delivery.portal_enabled:
        return DeliveryAccessDecision(False, "unavailable", "feature_disabled")
    if recipient.revoked_at or delivery.status == RealEstateDelivery.Status.REVOKED:
        return DeliveryAccessDecision(False, "unavailable", "revoked")
    if delivery.status != RealEstateDelivery.Status.ACTIVE:
        return DeliveryAccessDecision(False, "unavailable", "not_active")
    if not delivery.available_from or delivery.available_from > now:
        return DeliveryAccessDecision(False, "temporarily_unavailable", "not_available_yet")
    if not delivery.expires_at or delivery.expires_at <= now:
        return DeliveryAccessDecision(False, "unavailable", "expired")
    if delivery.enquiry.status != RealEstateEnquiry.Status.COMPLETED:
        return DeliveryAccessDecision(False, "temporarily_unavailable", "shoot_not_completed")
    if not can_release_realestate_delivery(delivery.enquiry):
        return DeliveryAccessDecision(False, "payment_locked", "payment_locked")
    if require_deliverables and not delivery.deliverables.filter(
        is_active=True,
        deleted_at__isnull=True,
        available_at__isnull=False,
        available_at__lte=now,
    ).exists():
        return DeliveryAccessDecision(False, "empty", "no_active_deliverables")
    return DeliveryAccessDecision(True, "valid", "allowed")


def record_access_event(
    delivery,
    event_type,
    *,
    recipient=None,
    deliverable=None,
    metadata=None,
):
    safe_metadata = {}
    for key, value in (metadata or {}).items():
        if key in {"reason", "state", "category", "content_version"}:
            safe_metadata[key] = str(value)[:100]
    return RealEstateDeliveryAccessEvent.objects.create(
        delivery=delivery,
        recipient=recipient,
        deliverable=deliverable,
        event_type=event_type,
        metadata=safe_metadata,
    )


def delivery_dto(recipient):
    decision = evaluate_delivery_access(recipient)
    if not decision.allowed:
        return {"state": decision.state}
    now = timezone.now()
    files = recipient.delivery.deliverables.filter(
        is_active=True,
        deleted_at__isnull=True,
        available_at__isnull=False,
        available_at__lte=now,
    )
    grouped = {}
    for item in files:
        grouped.setdefault(item.category, []).append(
            {
                "id": str(item.public_id),
                "category": item.category,
                "category_label": item.get_category_display(),
                "display_name": item.display_name,
                "filename": item.original_filename,
                "size": item.file_size,
                "mime_type": item.mime_type,
            }
        )
    delivery = recipient.delivery
    has_partial_availability = delivery.deliverables.filter(
        deleted_at__isnull=True,
    ).exclude(
        is_active=True,
        available_at__isnull=False,
        available_at__lte=now,
    ).exists()
    return {
        "state": "valid",
        "delivery": {
            "title": delivery.public_title,
            "available_from": delivery.available_from.isoformat(),
            "expires_at": delivery.expires_at.isoformat(),
            "licence_summary": delivery.licence_summary,
            "download_instructions": delivery.download_instructions,
            "review_url": delivery.enquiry.review_link,
            "partial_availability": has_partial_availability,
            "groups": [
                {"category": category, "files": items}
                for category, items in grouped.items()
            ],
        },
    }


def delivery_preview_dto(delivery):
    now = timezone.now()
    files = delivery.deliverables.filter(
        is_active=True,
        deleted_at__isnull=True,
    )
    grouped = {}
    for item in files:
        grouped.setdefault(item.category, []).append(
            {
                "id": str(item.public_id),
                "category": item.category,
                "category_label": item.get_category_display(),
                "display_name": item.display_name,
                "filename": item.original_filename,
                "size": item.file_size,
                "mime_type": item.mime_type,
            }
        )
    return {
        "state": "valid",
        "preview": True,
        "delivery": {
            "title": delivery.public_title,
            "available_from": (
                delivery.available_from or now
            ).isoformat(),
            "expires_at": (
                delivery.expires_at
                or now
                + timedelta(
                    days=int(
                        getattr(
                            settings,
                            "REAL_ESTATE_DELIVERY_ACCESS_DAYS",
                            DEFAULT_ACCESS_DAYS,
                        )
                    )
                )
            ).isoformat(),
            "licence_summary": delivery.licence_summary,
            "download_instructions": delivery.download_instructions,
            "review_url": delivery.enquiry.review_link,
            "partial_availability": False,
            "groups": [
                {"category": category, "files": items}
                for category, items in grouped.items()
            ],
        },
    }


def safe_download_filename(filename):
    basename = Path(str(filename or "download")).name
    cleaned = SAFE_FILENAME_RE.sub("-", basename).strip(" .")
    return (cleaned or "download")[:200]


def content_disposition(filename):
    safe_name = safe_download_filename(filename).replace('"', "")
    return (
        f'attachment; filename="{safe_name}"; '
        f"filename*=UTF-8''{quote(safe_name)}"
    )


@transaction.atomic
def rotate_recipient_secret(recipient, *, actor):
    recipient = RealEstateDeliveryRecipient.objects.select_for_update().get(pk=recipient.pk)
    recipient.token_version += 1
    recipient.token_rotated_at = timezone.now()
    recipient.save(update_fields=("token_version", "token_rotated_at", "updated_at"))
    record_timeline_event(
        recipient.delivery.enquiry,
        RealEstateTimelineEvent.EventType.NOTE,
        actor_type=RealEstateTimelineEvent.ActorType.ADMIN,
        title="Portal recipient link rotated",
        notes=f"Recipient public ID: {recipient.public_id}",
        created_by=actor,
    )
    return recipient


@transaction.atomic
def revoke_recipient_access(recipient, *, actor, reason):
    reason = str(reason or "").strip()
    if not reason:
        raise ValidationError("A revocation reason is required.")
    recipient = RealEstateDeliveryRecipient.objects.select_for_update().get(
        pk=recipient.pk
    )
    if recipient.revoked_at:
        return recipient, False
    recipient.revoked_at = timezone.now()
    recipient.revoked_by = actor
    recipient.revocation_reason = reason
    recipient.save(
        update_fields=(
            "revoked_at",
            "revoked_by",
            "revocation_reason",
            "updated_at",
        )
    )
    record_timeline_event(
        recipient.delivery.enquiry,
        RealEstateTimelineEvent.EventType.NOTE,
        actor_type=RealEstateTimelineEvent.ActorType.ADMIN,
        title="Portal recipient access revoked",
        notes=f"Recipient public ID: {recipient.public_id}",
        created_by=actor,
    )
    return recipient, True


@transaction.atomic
def activate_delivery(delivery, *, actor):
    delivery = RealEstateDelivery.objects.select_for_update().select_related("enquiry").get(
        pk=delivery.pk
    )
    if not portal_feature_enabled():
        raise ValidationError("The delivery portal feature is disabled.")
    if delivery.status == RealEstateDelivery.Status.ACTIVE:
        return delivery
    if delivery.status == RealEstateDelivery.Status.REVOKED:
        raise ValidationError("A revoked delivery cannot be activated.")
    if delivery.status == RealEstateDelivery.Status.ARCHIVED:
        raise ValidationError("An archived delivery cannot be activated.")
    if not delivery.recipients.filter(revoked_at__isnull=True).exists():
        raise ValidationError("At least one active recipient is required.")
    now = timezone.now()
    if delivery.enquiry.status != RealEstateEnquiry.Status.COMPLETED:
        raise ValidationError("The shoot must be completed before activation.")
    if not can_release_realestate_delivery(delivery.enquiry):
        raise ValidationError("Payment is locked and no active override exists.")
    if not delivery.deliverables.filter(
        is_active=True,
        deleted_at__isnull=True,
        available_at__isnull=False,
        available_at__lte=now,
    ).exists():
        raise ValidationError("At least one verified active deliverable is required.")
    if not delivery.available_from:
        delivery.available_from = now
    if not delivery.expires_at:
        delivery.expires_at = delivery.available_from + timedelta(
            days=int(
                getattr(settings, "REAL_ESTATE_DELIVERY_ACCESS_DAYS", DEFAULT_ACCESS_DAYS)
            )
        )
    if delivery.expires_at <= now:
        raise ValidationError("Delivery expiry must be in the future.")
    if delivery.expires_at <= delivery.available_from:
        raise ValidationError("Delivery expiry must be after availability.")
    delivery.status = RealEstateDelivery.Status.ACTIVE
    delivery.portal_enabled = True
    delivery.published_at = delivery.published_at or now
    delivery.full_clean()
    delivery.save(
        update_fields=(
            "status",
            "portal_enabled",
            "available_from",
            "expires_at",
            "published_at",
            "updated_at",
        )
    )
    record_timeline_event(
        delivery.enquiry,
        RealEstateTimelineEvent.EventType.DELIVERY_RELEASED,
        actor_type=RealEstateTimelineEvent.ActorType.ADMIN,
        title="Portal delivery activated",
        notes=f"Delivery ID: {delivery.pk}",
        created_by=actor,
    )
    return delivery
