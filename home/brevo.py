import logging
from typing import Optional, Tuple

import requests
from django.conf import settings
from django.utils import timezone


logger = logging.getLogger(__name__)


def brevo_newsletter_enabled() -> bool:
    return bool(getattr(settings, "BREVO_ENABLED", False))


def brevo_newsletter_configured() -> bool:
    return bool(
        brevo_newsletter_enabled()
        and getattr(settings, "BREVO_API_KEY", "")
        and getattr(settings, "BREVO_NEWSLETTER_LIST_ID", None) is not None
    )


def _response_text(response) -> str:
    try:
        return str(response.text or "")
    except Exception:
        return ""


def _response_json(response):
    try:
        return response.json()
    except Exception:
        return {}


def _is_duplicate_contact_response(response) -> bool:
    if getattr(response, "status_code", None) not in {400, 409}:
        return False

    payload = _response_json(response)
    error_code = str(payload.get("code") or "").strip().lower()
    message = str(payload.get("message") or _response_text(response) or "").strip().lower()

    if error_code in {"duplicate_parameter", "duplicate_request"}:
        return True
    return "already exists" in message or "duplicate" in message


def sync_subscriber_to_brevo(subscriber, *, allow_disabled: bool = True) -> Tuple[bool, str]:
    if not brevo_newsletter_enabled():
        if allow_disabled:
            subscriber.brevo_sync_status = "disabled"
            subscriber.brevo_sync_error = ""
            subscriber.save(update_fields=["brevo_sync_status", "brevo_sync_error"])
            return False, "disabled"
        raise RuntimeError("Brevo newsletter sync is disabled.")

    api_key = str(getattr(settings, "BREVO_API_KEY", "") or "").strip()
    list_id = getattr(settings, "BREVO_NEWSLETTER_LIST_ID", None)
    if not api_key or list_id in {None, ""}:
        if allow_disabled:
            subscriber.brevo_sync_status = "disabled"
            subscriber.brevo_sync_error = "Brevo configuration is incomplete."
            subscriber.save(update_fields=["brevo_sync_status", "brevo_sync_error"])
            return False, "disabled"
        raise RuntimeError("Brevo newsletter configuration is incomplete.")

    payload = {
        "email": subscriber.email,
        "listIds": [int(list_id)],
        "updateEnabled": True,
        "attributes": {
            "EMAIL": subscriber.email,
            "source": subscriber.source or "unknown",
        },
    }
    if subscriber.first_name:
        payload["attributes"]["FIRSTNAME"] = subscriber.first_name

    try:
        response = requests.post(
            "https://api.brevo.com/v3/contacts",
            headers={
                "accept": "application/json",
                "content-type": "application/json",
                "api-key": api_key,
            },
            json=payload,
            timeout=(5, 20),
        )
        if _is_duplicate_contact_response(response):
            subscriber.brevo_synced_at = timezone.now()
            subscriber.brevo_sync_status = "synced"
            subscriber.brevo_sync_error = ""
            subscriber.save(
                update_fields=["brevo_synced_at", "brevo_sync_status", "brevo_sync_error"]
            )
            return True, "synced"
        response.raise_for_status()
    except Exception as exc:
        logger.exception("Brevo newsletter sync failed for subscriber=%s", subscriber.email)
        subscriber.brevo_sync_status = "failed"
        subscriber.brevo_sync_error = f"{exc.__class__.__name__}: {exc}"
        subscriber.save(update_fields=["brevo_sync_status", "brevo_sync_error"])
        return False, "failed"

    subscriber.brevo_synced_at = timezone.now()
    subscriber.brevo_sync_status = "synced"
    subscriber.brevo_sync_error = ""
    subscriber.save(update_fields=["brevo_synced_at", "brevo_sync_status", "brevo_sync_error"])
    return True, "synced"
