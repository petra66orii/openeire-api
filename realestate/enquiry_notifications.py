"""Track enquiry emails independently so failures remain visible and retryable."""

import logging
from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from .emails import (
    send_realestate_client_confirmation_email,
    send_realestate_internal_notification_email,
)
from .models import RealEstateEnquiry

logger = logging.getLogger(__name__)


def _claim_notification(enquiry_id, *, sent_field, started_field):
    started_at = timezone.now()
    stale_before = started_at - timedelta(
        seconds=getattr(settings, "ENQUIRY_EMAIL_CLAIM_TIMEOUT_SECONDS", 600)
    )
    claimed = (
        RealEstateEnquiry.objects.filter(
            pk=enquiry_id, **{f"{sent_field}__isnull": True}
        )
        .filter(
            Q(**{f"{started_field}__isnull": True})
            | Q(**{f"{started_field}__lt": stale_before})
        )
        .update(**{started_field: started_at})
    )
    return started_at if claimed else None


def send_enquiry_notifications(enquiry_id, *, internal_sender=None, client_sender=None, request=None):
    internal_sender = internal_sender or send_realestate_internal_notification_email
    client_sender = client_sender or send_realestate_client_confirmation_email
    results = {}
    for kind, sent_field, started_field, sender in (
        (
            "internal",
            "internal_notification_sent_at",
            "internal_notification_started_at",
            internal_sender,
        ),
        (
            "client",
            "client_confirmation_sent_at",
            "client_confirmation_started_at",
            client_sender,
        ),
    ):
        claimed_at = _claim_notification(
            enquiry_id, sent_field=sent_field, started_field=started_field
        )
        if claimed_at is None:
            enquiry = RealEstateEnquiry.objects.only(sent_field).get(pk=enquiry_id)
            results[kind] = "already_sent" if getattr(enquiry, sent_field) else "already_sending"
            continue

        enquiry = RealEstateEnquiry.objects.get(pk=enquiry_id)
        try:
            delivered = (
                sender(enquiry, request=request)
                if kind == "internal"
                else sender(enquiry)
            )
            if delivered != 1:
                raise RuntimeError("Email backend did not confirm one delivered message")
        except Exception as exc:
            logger.exception("Enquiry %s %s email failed", enquiry_id, kind)
            attempted_at = timezone.now()
            updated = RealEstateEnquiry.objects.filter(
                pk=enquiry_id, **{started_field: claimed_at}
            ).update(
                **{
                    started_field: None,
                    "enquiry_email_last_attempt_at": attempted_at,
                    "enquiry_email_last_error": f"{kind}: {type(exc).__name__}: {exc}"[:2000],
                    "updated_at": attempted_at,
                }
            )
            results[kind] = "failed" if updated else "superseded"
        else:
            attempted_at = timezone.now()
            updated = RealEstateEnquiry.objects.filter(
                pk=enquiry_id,
                **{started_field: claimed_at, f"{sent_field}__isnull": True},
            ).update(
                **{
                    sent_field: attempted_at,
                    started_field: None,
                    "enquiry_email_last_attempt_at": attempted_at,
                    "updated_at": attempted_at,
                }
            )
            if updated:
                current = RealEstateEnquiry.objects.only(
                    "internal_notification_sent_at", "client_confirmation_sent_at"
                ).get(pk=enquiry_id)
                if (
                    current.internal_notification_sent_at
                    and current.client_confirmation_sent_at
                ):
                    RealEstateEnquiry.objects.filter(pk=enquiry_id).update(
                        enquiry_email_last_error=""
                    )
                results[kind] = "sent"
            else:
                logger.warning("Enquiry %s %s email claim was superseded", enquiry_id, kind)
                results[kind] = "superseded"
    return results
