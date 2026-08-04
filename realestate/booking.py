import base64
import hashlib
import hmac
import uuid
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.core import signing
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (
    RealEstateBookingAccessEvent,
    RealEstateBookingCredential,
    RealEstateClient,
)


TOKEN_NAMESPACE = "openeire.realestate.booking-credential.v1"
SESSION_NAMESPACE = "openeire.realestate.booking-session.v1"
DEFAULT_CREDENTIAL_DAYS = 90
DEFAULT_SESSION_SECONDS = 12 * 60 * 60
BOOKING_SECRET_SETTINGS = (
    "REAL_ESTATE_BOOKING_TOKEN_KEY",
    "REAL_ESTATE_BOOKING_SESSION_KEY",
    "REAL_ESTATE_BOOKING_INTERNAL_SECRET",
)


def booking_feature_enabled():
    return bool(getattr(settings, "REAL_ESTATE_BOOKING_PORTAL_ENABLED", False))


def validate_booking_secret_independence():
    values = {
        name: str(getattr(settings, name, "") or "")
        for name in BOOKING_SECRET_SETTINGS
    }
    forbidden = {
        str(getattr(settings, "SECRET_KEY", "") or ""),
        str(getattr(settings, "REAL_ESTATE_DELIVERY_TOKEN_KEY", "") or ""),
        str(getattr(settings, "REAL_ESTATE_DELIVERY_SESSION_KEY", "") or ""),
        str(getattr(settings, "REAL_ESTATE_DELIVERY_INTERNAL_SECRET", "") or ""),
    }
    configured = [value for value in values.values() if value]
    if len(configured) != len(set(configured)) or any(value in forbidden for value in configured):
        raise ImproperlyConfigured(
            "Booking token, session and internal-service secrets must be dedicated and independent."
        )
    return values


def _strong_booking_key(name):
    value = validate_booking_secret_independence().get(name, "")
    if len(value) < 32 or len(set(value)) < 8:
        raise ImproperlyConfigured(
            f"{name} must be a dedicated high-entropy secret of at least 32 characters."
        )
    return value


def credential_secret(credential):
    canonical = (
        f"{TOKEN_NAMESPACE}:{credential.public_id}:{credential.token_salt}:"
        f"{credential.token_version}"
    ).encode("utf-8")
    digest = hmac.new(
        _strong_booking_key("REAL_ESTATE_BOOKING_TOKEN_KEY").encode("utf-8"),
        canonical,
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def credential_secret_matches(credential, supplied_secret):
    supplied = str(supplied_secret or "")
    return bool(supplied) and hmac.compare_digest(
        credential_secret(credential), supplied
    )


def build_booking_url(credential):
    # Link derivation is a security boundary. Reload so a caller holding an ORM
    # object from before a rotation cannot emit the previous secret.
    credential = RealEstateBookingCredential.objects.select_related("client").get(
        pk=credential.pk
    )
    if credential_unavailable_reason(credential):
        raise ValidationError("Booking access is unavailable.")
    frontend_url = str(getattr(settings, "FRONTEND_URL", "") or "").rstrip("/")
    if not frontend_url:
        raise ImproperlyConfigured("FRONTEND_URL is required for booking links.")
    parsed = urlsplit(frontend_url)
    if (
        parsed.scheme not in ({"http", "https"} if settings.DEBUG else {"https"})
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ImproperlyConfigured(
            "FRONTEND_URL must be a canonical HTTPS origin for booking links."
        )
    return f"{frontend_url}/book/{credential.public_id}#{credential_secret(credential)}"


def credential_unavailable_reason(credential):
    if not booking_feature_enabled():
        return "feature_disabled"
    if credential.revoked_at:
        return RealEstateBookingAccessEvent.ResultCode.REVOKED
    if credential.expires_at <= timezone.now():
        return RealEstateBookingAccessEvent.ResultCode.EXPIRED
    if credential.client.status != RealEstateClient.Status.ACTIVE:
        return RealEstateBookingAccessEvent.ResultCode.CLIENT_INACTIVE
    return ""


def record_booking_access_event(credential, event_type, result_code, *, enquiry=None):
    return RealEstateBookingAccessEvent.objects.create(
        credential=credential,
        client=credential.client,
        enquiry=enquiry,
        event_type=event_type,
        result_code=result_code,
    )


def issue_booking_session(credential):
    credential = RealEstateBookingCredential.objects.select_related("client").get(
        pk=credential.pk
    )
    reason = credential_unavailable_reason(credential)
    if reason:
        raise PermissionDenied("Booking access is unavailable.")
    now = timezone.now()
    remaining = max(0, int((credential.expires_at - now).total_seconds()))
    lifetime = min(
        int(getattr(settings, "REAL_ESTATE_BOOKING_SESSION_SECONDS", DEFAULT_SESSION_SECONDS)),
        remaining,
    )
    if lifetime <= 0:
        raise PermissionDenied("Booking access is unavailable.")
    payload = {
        "credential_id": credential.pk,
        "client_id": credential.client_id,
        "public_id": str(credential.public_id),
        "token_version": credential.token_version,
        "expires": int((now + timedelta(seconds=lifetime)).timestamp()),
        "purpose": SESSION_NAMESPACE,
    }
    token = signing.dumps(
        payload,
        key=_strong_booking_key("REAL_ESTATE_BOOKING_SESSION_KEY"),
        salt=SESSION_NAMESPACE,
        compress=True,
    )
    return token, lifetime


def load_booking_session(session_token):
    try:
        payload = signing.loads(
            str(session_token or ""),
            key=_strong_booking_key("REAL_ESTATE_BOOKING_SESSION_KEY"),
            salt=SESSION_NAMESPACE,
            max_age=int(
                getattr(settings, "REAL_ESTATE_BOOKING_SESSION_SECONDS", DEFAULT_SESSION_SECONDS)
            ),
        )
    except signing.BadSignature as exc:
        raise PermissionDenied("Booking access is unavailable.") from exc
    if (
        payload.get("purpose") != SESSION_NAMESPACE
        or int(payload.get("expires", 0)) <= int(timezone.now().timestamp())
    ):
        raise PermissionDenied("Booking access is unavailable.")
    credential = (
        RealEstateBookingCredential.objects.select_related("client")
        .filter(
            pk=payload.get("credential_id"),
            client_id=payload.get("client_id"),
            public_id=payload.get("public_id"),
        )
        .first()
    )
    if not credential or credential.token_version != payload.get("token_version"):
        raise PermissionDenied("Booking access is unavailable.")
    if credential_unavailable_reason(credential):
        raise PermissionDenied("Booking access is unavailable.")
    return credential


def mask_email(value):
    local, separator, domain = str(value or "").partition("@")
    if not separator:
        return "Unavailable"
    return f"{local[:1]}***@{domain}"


def mask_phone(value):
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return f"*** *** {digits[-4:]}" if len(digits) >= 4 else "Unavailable"


def booking_client_dto(credential):
    client = credential.client
    return {
        "display_name": client.name,
        "company_name": client.company_name,
        "masked_email": mask_email(client.email),
        "masked_phone": mask_phone(client.phone),
        "credential_expires_at": credential.expires_at.isoformat(),
    }


@transaction.atomic
def generate_primary_credential(client, *, actor):
    client = RealEstateClient.objects.select_for_update().get(pk=client.pk)
    if client.status != RealEstateClient.Status.ACTIVE:
        raise ValidationError("Only active clients can receive booking access.")
    credential = client.booking_credentials.filter(
        is_primary=True, revoked_at__isnull=True
    ).first()
    now = timezone.now()
    if credential and credential.expires_at > now:
        return credential, False
    expiry = now + timedelta(
        days=int(getattr(settings, "REAL_ESTATE_BOOKING_CREDENTIAL_DAYS", DEFAULT_CREDENTIAL_DAYS))
    )
    if credential:
        credential.token_version += 1
        credential.token_salt = uuid.uuid4()
        credential.expires_at = expiry
        credential.rotated_at = now
        credential.rotated_by = actor
        credential.save()
        return credential, True
    return RealEstateBookingCredential.objects.create(
        client=client,
        expires_at=expiry,
        created_by=actor,
    ), True


@transaction.atomic
def rotate_booking_credential(credential, *, actor):
    credential = RealEstateBookingCredential.objects.select_for_update().select_related("client").get(
        pk=credential.pk
    )
    if credential.revoked_at:
        raise ValidationError("A revoked credential cannot be rotated.")
    credential.token_version += 1
    credential.token_salt = uuid.uuid4()
    credential.expires_at = timezone.now() + timedelta(
        days=int(getattr(settings, "REAL_ESTATE_BOOKING_CREDENTIAL_DAYS", DEFAULT_CREDENTIAL_DAYS))
    )
    credential.rotated_at = timezone.now()
    credential.rotated_by = actor
    credential.save()
    record_booking_access_event(
        credential,
        RealEstateBookingAccessEvent.EventType.ROTATED,
        RealEstateBookingAccessEvent.ResultCode.ROTATED,
    )
    return credential


@transaction.atomic
def revoke_booking_credential(credential, *, actor, reason):
    reason = str(reason or "").strip()
    if not reason:
        raise ValidationError("A revocation reason is required.")
    credential = RealEstateBookingCredential.objects.select_for_update().select_related("client").get(
        pk=credential.pk
    )
    if credential.revoked_at:
        return credential, False
    credential.revoked_at = timezone.now()
    credential.revoked_by = actor
    credential.revocation_reason = reason
    credential.save(update_fields=("revoked_at", "revoked_by", "revocation_reason", "updated_at"))
    record_booking_access_event(
        credential,
        RealEstateBookingAccessEvent.EventType.REVOKED,
        RealEstateBookingAccessEvent.ResultCode.REVOKED,
    )
    return credential, True
