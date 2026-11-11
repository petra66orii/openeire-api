"""Concurrency-safe state transitions for order confirmation emails."""

from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from .models import Order


def claim_confirmation_email(order):
    started_at = timezone.now()
    stale_before = started_at - timedelta(
        seconds=getattr(settings, "CONFIRMATION_EMAIL_CLAIM_TIMEOUT_SECONDS", 600)
    )
    claimed = (
        Order.objects.filter(pk=order.pk)
        .exclude(confirmation_email_status="SENT")
        .filter(
            ~Q(confirmation_email_status="SENDING")
            | Q(confirmation_email_started_at__isnull=True)
            | Q(confirmation_email_started_at__lt=stale_before)
        )
        .update(
            confirmation_email_status="SENDING",
            confirmation_email_started_at=started_at,
        )
    )
    if claimed:
        order.confirmation_email_status = "SENDING"
        order.confirmation_email_started_at = started_at
        return started_at
    return None


def mark_confirmation_email_sent(order, claimed_at):
    sent_at = timezone.now()
    updated = Order.objects.filter(
        pk=order.pk,
        confirmation_email_status="SENDING",
        confirmation_email_started_at=claimed_at,
    ).update(
        confirmation_email_status="SENT",
        confirmation_email_started_at=None,
        confirmation_email_sent_at=sent_at,
        confirmation_email_failed_at=None,
        confirmation_email_error="",
    )
    if updated:
        order.confirmation_email_status = "SENT"
        order.confirmation_email_started_at = None
        order.confirmation_email_sent_at = sent_at
        order.confirmation_email_failed_at = None
        order.confirmation_email_error = ""
    return bool(updated)


def mark_confirmation_email_failed(order, claimed_at, exc):
    failed_at = timezone.now()
    updated = Order.objects.filter(
        pk=order.pk,
        confirmation_email_status="SENDING",
        confirmation_email_started_at=claimed_at,
    ).update(
        confirmation_email_status="FAILED",
        confirmation_email_started_at=None,
        confirmation_email_failed_at=failed_at,
        confirmation_email_error=f"{exc.__class__.__name__}: {exc}",
    )
    if updated:
        order.confirmation_email_status = "FAILED"
        order.confirmation_email_started_at = None
        order.confirmation_email_failed_at = failed_at
        order.confirmation_email_error = f"{exc.__class__.__name__}: {exc}"
    return bool(updated)


def deliver_confirmation_email(order, *, sender, request=None):
    claimed_at = claim_confirmation_email(order)
    if claimed_at is None:
        return "skipped"
    try:
        sender(order, request=request)
    except Exception as exc:
        return "failed" if mark_confirmation_email_failed(order, claimed_at, exc) else "superseded"
    return "sent" if mark_confirmation_email_sent(order, claimed_at) else "superseded"
