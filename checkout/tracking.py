import hashlib
import hmac
import json
import logging
from typing import Iterable, List, Optional

from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.utils import timezone

logger = logging.getLogger(__name__)


def callback_token_is_valid(provided_token: Optional[str]) -> bool:
    expected_token = getattr(settings, "PRODIGI_CALLBACK_TOKEN", "")
    if not expected_token:
        return False
    return hmac.compare_digest(str(provided_token or ""), str(expected_token))


def normalize_prodigi_shipments(shipments_payload: object) -> List[dict]:
    shipments: List[dict] = []
    if not isinstance(shipments_payload, list):
        return shipments

    for shipment in shipments_payload:
        if not isinstance(shipment, dict):
            continue

        carrier = shipment.get("carrier")
        tracking = shipment.get("tracking")

        carrier_name = carrier.get("name") if isinstance(carrier, dict) else None
        carrier_service = carrier.get("service") if isinstance(carrier, dict) else None
        tracking_number = tracking.get("number") if isinstance(tracking, dict) else None
        tracking_url = tracking.get("url") if isinstance(tracking, dict) else None

        shipments.append(
            {
                "id": str(shipment.get("id") or "").strip(),
                "status": str(shipment.get("status") or "").strip(),
                "carrier_name": str(carrier_name or "").strip(),
                "carrier_service": str(carrier_service or "").strip(),
                "tracking_number": str(tracking_number or "").strip(),
                "tracking_url": str(tracking_url or "").strip(),
                "dispatch_date": str(shipment.get("dispatchDate") or "").strip(),
            }
        )

    return shipments


def tracked_shipments(shipments: Iterable[dict]) -> List[dict]:
    normalized: List[dict] = []
    for shipment in shipments:
        if not isinstance(shipment, dict):
            continue
        tracking_number = str(shipment.get("tracking_number") or "").strip()
        tracking_url = str(shipment.get("tracking_url") or "").strip()
        if not tracking_number and not tracking_url:
            continue
        normalized.append(
            {
                "id": str(shipment.get("id") or "").strip(),
                "status": str(shipment.get("status") or "").strip(),
                "carrier_name": str(shipment.get("carrier_name") or "").strip(),
                "carrier_service": str(shipment.get("carrier_service") or "").strip(),
                "tracking_number": tracking_number,
                "tracking_url": tracking_url,
                "dispatch_date": str(shipment.get("dispatch_date") or "").strip(),
            }
        )

    normalized.sort(
        key=lambda shipment: (
            shipment["id"],
            shipment["tracking_number"],
            shipment["tracking_url"],
        )
    )
    return normalized


def build_tracking_signature(shipments: Iterable[dict]) -> Optional[str]:
    tracked = tracked_shipments(shipments)
    if not tracked:
        return None

    encoded = json.dumps(tracked, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def update_order_from_prodigi_payload(order, prodigi_order_payload: dict):
    shipments = normalize_prodigi_shipments(prodigi_order_payload.get("shipments"))
    status_payload = prodigi_order_payload.get("status")
    prodigi_stage = ""
    if isinstance(status_payload, dict):
        prodigi_stage = str(status_payload.get("stage") or "").strip()

    update_fields: List[str] = []
    prodigi_order_id = str(prodigi_order_payload.get("id") or "").strip() or None
    if prodigi_order_id and order.prodigi_order_id != prodigi_order_id:
        order.prodigi_order_id = prodigi_order_id
        update_fields.append("prodigi_order_id")

    if order.prodigi_status != prodigi_stage:
        order.prodigi_status = prodigi_stage
        update_fields.append("prodigi_status")

    if order.prodigi_shipments != shipments:
        order.prodigi_shipments = shipments
        update_fields.append("prodigi_shipments")

    order.prodigi_last_callback_at = timezone.now()
    update_fields.append("prodigi_last_callback_at")

    if update_fields:
        order.save(update_fields=update_fields)

    return shipments


def send_tracking_email(order, shipments: Iterable[dict]):
    tracked = tracked_shipments(shipments)
    if not tracked:
        logger.info(
            "Skipping tracking email because no tracked shipments are available (order=%s)",
            order.order_number,
        )
        return False

    context = {
        "order": order,
        "shipments": tracked,
        "contact_email": settings.DEFAULT_FROM_EMAIL,
    }
    subject = render_to_string(
        "checkout/tracking_emails/tracking_email_subject.txt",
        context,
    ).strip()
    body = render_to_string(
        "checkout/tracking_emails/tracking_email_body.txt",
        context,
    )

    email = EmailMessage(
        subject=subject,
        body=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[order.email],
    )
    email.send(fail_silently=False)
    return True
