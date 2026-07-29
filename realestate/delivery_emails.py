import logging

from django.utils import timezone

from .delivery import build_recipient_url, evaluate_delivery_access
from .emails import get_realestate_reply_to_email, send_templated_email
from .models import (
    RealEstateDeliveryEmailAttempt,
    RealEstateTimelineEvent,
)
from .timeline import record_timeline_event

logger = logging.getLogger(__name__)

SUBJECTS = {
    RealEstateDeliveryEmailAttempt.Kind.INITIAL: "Your private media delivery is ready",
    RealEstateDeliveryEmailAttempt.Kind.RESEND: "Your private media delivery link",
    RealEstateDeliveryEmailAttempt.Kind.EXPIRY_REMINDER: "Your media delivery expires soon",
    RealEstateDeliveryEmailAttempt.Kind.EXTENSION: "Your media delivery access was extended",
    RealEstateDeliveryEmailAttempt.Kind.REPLACEMENT: "Your media delivery was updated",
}


def send_delivery_recipient_email(recipient, *, kind, idempotency_key, actor=None):
    attempt, created = RealEstateDeliveryEmailAttempt.objects.get_or_create(
        idempotency_key=idempotency_key,
        defaults={
            "recipient": recipient,
            "kind": kind,
            "status": RealEstateDeliveryEmailAttempt.Status.PENDING,
        },
    )
    if not created:
        return attempt
    decision = evaluate_delivery_access(
        recipient,
        require_deliverables=kind
        not in {
            RealEstateDeliveryEmailAttempt.Kind.EXTENSION,
            RealEstateDeliveryEmailAttempt.Kind.REPLACEMENT,
        },
    )
    if not decision.allowed:
        attempt.status = RealEstateDeliveryEmailAttempt.Status.FAILED
        attempt.failure_code = decision.reason[:64]
        attempt.failure_message = "Delivery access is not currently available."
        attempt.save(
            update_fields=("status", "failure_code", "failure_message")
        )
        return attempt
    delivery = recipient.delivery
    context = {
        "first_name": recipient.display_name.split()[0] or "there",
        "delivery_title": delivery.public_title,
        "delivery_url": build_recipient_url(recipient),
        "expires_at": delivery.expires_at,
        "access_days": max(
            1, (delivery.expires_at.date() - timezone.localdate()).days
        ),
        "download_instructions": delivery.download_instructions,
        "licence_summary": delivery.licence_summary,
    }
    try:
        # The current SMTP transport does not expose a provider message ID or
        # per-message click-tracking control. Portal links must have tracking
        # disabled in the provider account; see the operations runbook.
        accepted = send_templated_email(
            subject=SUBJECTS[kind],
            to=[recipient.email],
            template_base="portal_delivery",
            context=context,
            reply_to=[get_realestate_reply_to_email()],
        )
        if not accepted:
            raise RuntimeError("Email transport did not accept the message.")
    except Exception as exc:
        logger.exception(
            "Portal delivery email failed. delivery_id=%s recipient_id=%s",
            delivery.pk,
            recipient.pk,
        )
        attempt.status = RealEstateDeliveryEmailAttempt.Status.FAILED
        attempt.failure_code = exc.__class__.__name__[:64]
        attempt.failure_message = "Email transport failed."
        attempt.save(
            update_fields=("status", "failure_code", "failure_message")
        )
        return attempt
    attempt.status = RealEstateDeliveryEmailAttempt.Status.SENT
    attempt.sent_at = timezone.now()
    attempt.save(update_fields=("status", "sent_at"))
    try:
        record_timeline_event(
            delivery.enquiry,
            RealEstateTimelineEvent.EventType.DELIVERY_SENT,
            actor_type=(
                RealEstateTimelineEvent.ActorType.ADMIN
                if actor
                else RealEstateTimelineEvent.ActorType.SYSTEM
            ),
            title="Portal delivery email sent",
            notes=f"Recipient public ID: {recipient.public_id}",
            recipient_email=_masked_email(recipient.email),
            created_by=actor,
        )
    except Exception:
        logger.exception(
            "Portal email was accepted but timeline recording failed. "
            "delivery_id=%s recipient_id=%s attempt_id=%s",
            delivery.pk,
            recipient.pk,
            attempt.pk,
        )
    return attempt


def _masked_email(email):
    local, separator, domain = str(email or "").partition("@")
    if not separator:
        return ""
    return f"{local[:1]}***@{domain}"
