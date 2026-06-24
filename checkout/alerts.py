import logging
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.core.mail import EmailMessage
from django.urls import reverse
from django.utils import timezone

from openeire_api.mail_utils import get_default_from_email


logger = logging.getLogger(__name__)

ALERT_SEND_LOCK_SECONDS = 300


def _safe_alert_value(value: Any, *, fallback: str, max_length: int = 200) -> str:
    """Keep provider-supplied diagnostics compact and single-line in alerts."""
    text = " ".join(str(value or "").split())
    return (text or fallback)[:max_length]


def get_fulfilment_alert_recipients() -> list[str]:
    configured = list(
        getattr(settings, "FULFILMENT_ALERT_RECIPIENTS", []) or []
    )
    if configured:
        return configured

    licence_recipients = list(
        getattr(settings, "LICENCE_ADMIN_NOTIFICATION_RECIPIENTS", []) or []
    )
    if licence_recipients:
        return licence_recipients

    admins = getattr(settings, "ADMINS", ()) or ()
    return [email for _, email in admins if email]


def send_fulfilment_failure_alert(order, error):
    """Send one actionable alert per failed order within the cooldown window."""
    recipients = get_fulfilment_alert_recipients()
    if not recipients:
        logger.error(
            "Paid order fulfilment failed but no alert recipients are configured. order=%s",
            order.order_number,
        )
        return False

    sent_cache_key = f"fulfilment-failure-alert-sent:{order.pk}"
    lock_cache_key = f"fulfilment-failure-alert-lock:{order.pk}"
    cooldown = max(
        int(getattr(settings, "FULFILMENT_ALERT_COOLDOWN_SECONDS", 86400)),
        60,
    )
    cache_available = True
    try:
        if cache.get(sent_cache_key):
            return False
        should_send = cache.add(
            lock_cache_key,
            "1",
            timeout=ALERT_SEND_LOCK_SECONDS,
        )
    except Exception:
        logger.exception(
            "Fulfilment alert cache unavailable; sending without deduplication. order=%s",
            order.order_number,
        )
        cache_available = False
        should_send = True

    if not should_send:
        return False

    trace_parent = _safe_alert_value(
        getattr(error, "trace_parent", None),
        fallback="Not provided",
    )
    outcome = _safe_alert_value(
        getattr(error, "outcome", None),
        fallback=error.__class__.__name__,
    )
    status_code = getattr(error, "status_code", None)
    prodigi_order_id = _safe_alert_value(
        getattr(order, "prodigi_order_id", None),
        fallback="Not recorded",
    )
    admin_path = reverse(
        "customadmin:checkout_order_change",
        args=[order.pk],
    )
    admin_base_url = (
        getattr(settings, "FULFILMENT_ADMIN_BASE_URL", None)
        or getattr(settings, "PRODIGI_CALLBACK_BASE_URL", None)
        or getattr(settings, "SITE_URL", None)
    )
    admin_url = (
        f"{str(admin_base_url).rstrip('/')}{admin_path}"
        if admin_base_url
        else admin_path
    )

    prodigi_acceptance_recorded = prodigi_order_id != "Not recorded"
    incident_summary = (
        "Prodigi returned an order ID, but local post-submission processing failed."
        if prodigi_acceptance_recorded
        else "The application could not confirm that Prodigi accepted the physical order."
    )
    required_action = (
        "Do not submit a duplicate order. Open the existing Prodigi order and review its status."
        if prodigi_acceptance_recorded
        else "Search Prodigi using the OpenEire order number before manually submitting anything. "
        "If no order exists, manually fulfil or refund the paid order as appropriate."
    )

    subject = f"URGENT: paid order needs fulfilment review - {order.order_number}"
    body = (
        f"A customer payment succeeded. {incident_summary}\n\n"
        f"Order: {order.order_number}\n"
        f"Detected: {timezone.localtime().strftime('%Y-%m-%d %H:%M %Z')}\n"
        f"Order total: EUR {order.total_price}\n"
        f"Prodigi status: {order.prodigi_status or 'FULFILMENT_FAILED'}\n"
        f"Prodigi order ID: {prodigi_order_id}\n"
        f"Prodigi HTTP status: {status_code or 'Not provided'}\n"
        f"Prodigi outcome: {outcome}\n"
        f"Prodigi trace: {trace_parent}\n\n"
        f"ACTION REQUIRED: {required_action}\n\n"
        f"Review the order in admin: {admin_url}\n"
    )

    email = EmailMessage(
        subject=subject,
        body=body,
        from_email=get_default_from_email(),
        to=recipients,
    )
    try:
        email.send(fail_silently=False)
    except Exception:
        if cache_available:
            try:
                cache.delete(lock_cache_key)
            except Exception:
                pass
        raise

    if cache_available:
        try:
            # Record successful delivery after sending. A cache failure may cause a
            # duplicate alert on retry, but it cannot hide an unsent alert.
            cache.set(sent_cache_key, "1", timeout=cooldown)
            cache.delete(lock_cache_key)
        except Exception:
            logger.exception(
                "Could not persist fulfilment alert deduplication state. order=%s",
                order.order_number,
            )
    return True
