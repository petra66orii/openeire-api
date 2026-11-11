"""Brevo transactional delivery with provider-side idempotency."""

import base64
from email.utils import parseaddr

import requests
from django.conf import settings

from home.brevo import brevo_timeout


BREVO_TRANSACTIONAL_URL = "https://api.brevo.com/v3/smtp/email"


def brevo_transactional_configured():
    return bool(
        getattr(settings, "BREVO_TRANSACTIONAL_ENABLED", False)
        and str(getattr(settings, "BREVO_API_KEY", "") or "").strip()
    )


def _recipient(value):
    name, address = parseaddr(str(value or "").strip())
    recipient = {"email": address}
    if name:
        recipient["name"] = name
    return recipient


def _is_duplicate_response(response):
    if getattr(response, "status_code", None) not in {400, 409}:
        return False
    try:
        payload = response.json()
    except Exception:
        payload = {}
    code = str(payload.get("code") or "").strip().lower()
    return code in {"duplicate_parameter", "duplicate_request"}


def send_brevo_transactional_email(
    *,
    subject,
    text_body,
    to,
    idempotency_key,
    html_body=None,
    from_email=None,
    reply_to=None,
    attachments=None,
):
    """Send one logical message; repeated keys are accepted as already delivered."""
    if not brevo_transactional_configured():
        raise RuntimeError("Brevo transactional email is not configured.")

    sender_name, sender_address = parseaddr(
        str(from_email or getattr(settings, "DEFAULT_FROM_EMAIL", "") or "").strip()
    )
    if not sender_address:
        raise RuntimeError("DEFAULT_FROM_EMAIL is required for Brevo transactional email.")

    payload = {
        "sender": {"email": sender_address},
        "to": [_recipient(value) for value in to],
        "subject": str(subject).strip(),
        "textContent": str(text_body),
        "headers": {"IdempotencyKey": str(idempotency_key)},
    }
    if sender_name:
        payload["sender"]["name"] = sender_name
    if html_body:
        payload["htmlContent"] = str(html_body)
    reply_recipients = [_recipient(value) for value in (reply_to or [])]
    if reply_recipients:
        payload["replyTo"] = reply_recipients[0]
    encoded_attachments = []
    for attachment in attachments or []:
        if not isinstance(attachment, tuple) or len(attachment) < 2:
            raise ValueError("Brevo attachments must be (filename, content, mimetype) tuples.")
        filename, content = attachment[:2]
        if isinstance(content, str):
            content = content.encode()
        encoded_attachments.append(
            {
                "name": str(filename),
                "content": base64.b64encode(bytes(content)).decode("ascii"),
            }
        )
    if encoded_attachments:
        payload["attachment"] = encoded_attachments

    response = requests.post(
        BREVO_TRANSACTIONAL_URL,
        headers={
            "accept": "application/json",
            "content-type": "application/json",
            "api-key": str(settings.BREVO_API_KEY).strip(),
        },
        json=payload,
        timeout=brevo_timeout(),
    )
    if _is_duplicate_response(response):
        return 1
    response.raise_for_status()
    return 1
