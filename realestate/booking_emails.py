import logging

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from .booking import (
    build_booking_url,
    credential_unavailable_reason,
    record_booking_access_event,
)
from .emails import get_realestate_reply_to_email, send_templated_email
from .models import (
    RealEstateBookingAccessEvent,
    RealEstateBookingCredential,
    RealEstateBookingEmailAttempt,
)


logger = logging.getLogger(__name__)


def send_booking_link_email(credential, *, kind, idempotency_key):
    if not getattr(settings, "REAL_ESTATE_BOOKING_EMAIL_ENABLED", False):
        raise PermissionDenied(
            "Private booking email is disabled until provider tracking is verified."
        )
    credential = RealEstateBookingCredential.objects.select_related("client").get(
        pk=credential.pk
    )
    attempt, created = RealEstateBookingEmailAttempt.objects.get_or_create(
        idempotency_key=idempotency_key,
        defaults={"credential": credential, "kind": kind},
    )
    if not created:
        return attempt
    reason = credential_unavailable_reason(credential)
    if reason:
        attempt.status = RealEstateBookingEmailAttempt.Status.FAILED
        attempt.failure_code = str(reason)[:64]
        attempt.save(update_fields=("status", "failure_code"))
        return attempt
    private_url = build_booking_url(credential)
    try:
        accepted = send_templated_email(
            subject="Your private OpenÉire booking link",
            to=[credential.client.email],
            template_base="booking_link",
            context={
                "first_name": credential.client.name.split()[0] or "there",
                "booking_url": private_url,
                "cta_url": private_url,
                "cta_label": "Book another property",
                "expires_at": credential.expires_at,
            },
            reply_to=[get_realestate_reply_to_email()],
        )
        if not accepted:
            raise RuntimeError("Email transport did not accept the message.")
    except Exception as exc:
        logger.error(
            "Private booking email failed. credential_id=%s failure_code=%s",
            credential.pk,
            exc.__class__.__name__,
        )
        attempt.status = RealEstateBookingEmailAttempt.Status.FAILED
        attempt.failure_code = exc.__class__.__name__[:64]
        attempt.save(update_fields=("status", "failure_code"))
        return attempt
    attempt.status = RealEstateBookingEmailAttempt.Status.SENT
    attempt.sent_at = timezone.now()
    attempt.save(update_fields=("status", "sent_at"))
    try:
        record_booking_access_event(
            credential,
            RealEstateBookingAccessEvent.EventType.EMAIL_SENT,
            RealEstateBookingAccessEvent.ResultCode.EMAIL_ACCEPTED,
        )
    except Exception as exc:
        logger.error(
            "Private booking email was accepted but audit recording failed. "
            "credential_id=%s attempt_id=%s failure_code=%s",
            credential.pk,
            attempt.pk,
            exc.__class__.__name__,
        )
    return attempt
