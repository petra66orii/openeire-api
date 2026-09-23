"""Track enquiry emails independently so failures remain visible and retryable."""

import logging

from django.db import transaction
from django.utils import timezone

from .emails import (
    send_realestate_client_confirmation_email,
    send_realestate_internal_notification_email,
)
from .models import RealEstateEnquiry

logger = logging.getLogger(__name__)


def send_enquiry_notifications(enquiry_id, *, internal_sender=None, client_sender=None, request=None):
    internal_sender = internal_sender or send_realestate_internal_notification_email
    client_sender = client_sender or send_realestate_client_confirmation_email
    results = {}
    for kind, field, sender in (
        ("internal", "internal_notification_sent_at", internal_sender),
        ("client", "client_confirmation_sent_at", client_sender),
    ):
        # Lock one enquiry at a time so concurrent retries do not send twice.
        with transaction.atomic():
            enquiry = RealEstateEnquiry.objects.select_for_update().get(pk=enquiry_id)
            if getattr(enquiry, field):
                results[kind] = "already_sent"
                continue
            try:
                delivered = sender(enquiry, request=request) if kind == "internal" else sender(enquiry)
                if delivered != 1:
                    raise RuntimeError("Email backend did not confirm one delivered message")
            except Exception as exc:
                logger.exception("Enquiry %s %s email failed", enquiry_id, kind)
                enquiry.enquiry_email_last_error = f"{kind}: {type(exc).__name__}: {exc}"[:2000]
                enquiry.save(update_fields=["enquiry_email_last_error", "updated_at"])
                results[kind] = "failed"
            else:
                setattr(enquiry, field, timezone.now())
                fields = [field, "updated_at"]
                if enquiry.internal_notification_sent_at and enquiry.client_confirmation_sent_at:
                    enquiry.enquiry_email_last_error = ""
                    fields.append("enquiry_email_last_error")
                enquiry.save(update_fields=fields)
                results[kind] = "sent"
    return results
