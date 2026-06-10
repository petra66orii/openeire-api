import hashlib
import json
import logging
from datetime import timedelta
from typing import Iterable, List, Optional

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.mail import EmailMessage
from django.db import transaction
from django.db.models import Q
from django.template.loader import render_to_string
from django.utils import timezone

from products.models import PrintTemplate
from openeire_api.mail_utils import get_contact_email_address, get_default_from_email

from .models import Order
from .prodigi import fetch_prodigi_order

logger = logging.getLogger(__name__)
SHIPPING_NOTIFICATION_STATES = {
    "shipped",
    "dispatched",
    "partiallyshipped",
    "partiallydispatched",
}
FINAL_PRODIGI_SYNC_STATES = {
    "Shipped",
    "Dispatched",
    "PartiallyShipped",
    "PartiallyDispatched",
    "Delivered",
    "Complete",
    "Completed",
    "Cancelled",
}


def _normalize_prodigi_state(value: object) -> str:
    return "".join(ch for ch in str(value or "").strip().lower() if ch.isalnum())


def is_shipping_notification_state(value: object) -> bool:
    return _normalize_prodigi_state(value) in SHIPPING_NOTIFICATION_STATES


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


def shipping_notification_shipments(shipments: Iterable[dict]) -> List[dict]:
    normalized: List[dict] = []
    for shipment in shipments:
        if not isinstance(shipment, dict):
            continue
        status = str(shipment.get("status") or "").strip()
        tracking_number = str(shipment.get("tracking_number") or "").strip()
        tracking_url = str(shipment.get("tracking_url") or "").strip()
        if (
            not tracking_number
            and not tracking_url
            and not is_shipping_notification_state(status)
        ):
            continue
        normalized.append(
            {
                "id": str(shipment.get("id") or "").strip(),
                "status": status,
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
            shipment["status"],
            shipment["tracking_number"],
            shipment["tracking_url"],
        )
    )
    return normalized


def build_tracking_signature(shipments: Iterable[dict], *, order_stage: object = "") -> Optional[str]:
    normalized_stage = _normalize_prodigi_state(order_stage)
    eligible_shipments = shipping_notification_shipments(shipments)
    if not eligible_shipments and not is_shipping_notification_state(order_stage):
        return None

    encoded = json.dumps(
        {
            "order_stage": normalized_stage,
            "shipments": eligible_shipments,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def update_order_from_prodigi_payload(order, prodigi_order_payload: dict, *, mark_callback_received: bool = False):
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

    if mark_callback_received:
        order.prodigi_last_callback_at = timezone.now()
        update_fields.append("prodigi_last_callback_at")

    if update_fields:
        order.save(update_fields=update_fields)

    return shipments

def get_prodigi_order_stage(prodigi_order_payload: dict) -> str:
    status_payload = prodigi_order_payload.get("status")
    if isinstance(status_payload, dict):
        return str(status_payload.get("stage") or "").strip()
    return ""


def sync_order_shipping_from_prodigi(order, prodigi_order_payload: dict, *, mark_callback_received: bool = False):
    old_status = str(order.prodigi_status or "").strip()
    order_stage = get_prodigi_order_stage(prodigi_order_payload)
    shipments = update_order_from_prodigi_payload(
        order,
        prodigi_order_payload,
        mark_callback_received=mark_callback_received,
    )
    tracking_signature = build_tracking_signature(shipments, order_stage=order_stage)
    tracked = tracked_shipments(shipments)

    result = {
        "order_stage": order_stage,
        "shipments": shipments,
        "tracked_shipments": tracked,
        "tracking_signature": tracking_signature,
        "old_status": old_status,
        "new_status": str(order.prodigi_status or "").strip(),
        "email_sent": False,
        "email_skipped_reason": "",
    }

    if not tracking_signature:
        result["email_skipped_reason"] = "not_shipped_or_dispatched"
        return result

    if order.tracking_email_signature == tracking_signature:
        result["email_skipped_reason"] = "duplicate_signature"
        return result

    if send_tracking_email(order, shipments, order_stage=order_stage):
        order.tracking_email_signature = tracking_signature
        order.tracking_email_sent_at = timezone.now()
        order.save(update_fields=["tracking_email_signature", "tracking_email_sent_at"])
        result["email_sent"] = True
        return result

    result["email_skipped_reason"] = "no_notifiable_shipments"
    return result


def refresh_order_from_prodigi(order, *, mark_polled: bool = False):
    if not str(order.prodigi_order_id or "").strip():
        raise ValueError("Order does not have a Prodigi order id.")

    polled_at = timezone.now()
    try:
        prodigi_order = fetch_prodigi_order(order.prodigi_order_id)
    except Exception:
        if mark_polled:
            Order.objects.filter(pk=order.pk).update(prodigi_last_polled_at=polled_at)
        raise

    with transaction.atomic():
        locked_order = Order.objects.select_for_update().get(pk=order.pk)
        sync_result = sync_order_shipping_from_prodigi(locked_order, prodigi_order)
        if mark_polled:
            locked_order.prodigi_last_polled_at = polled_at
            locked_order.save(update_fields=["prodigi_last_polled_at"])

    return sync_result


def get_prodigi_sync_candidates(*, lookback_days: int = 90):
    physical_content_type = ContentType.objects.get_for_model(PrintTemplate)
    lookback_days = max(int(lookback_days or 0), 1)
    cutoff = timezone.now() - timedelta(days=lookback_days)

    return (
        Order.objects.filter(
            date__gte=cutoff,
            prodigi_order_id__isnull=False,
            items__content_type=physical_content_type,
        )
        .exclude(prodigi_order_id="")
        .filter(
            Q(prodigi_status__isnull=True)
            | Q(prodigi_status="")
            | ~Q(prodigi_status__in=FINAL_PRODIGI_SYNC_STATES)
            | Q(tracking_email_sent_at__isnull=True)
            | Q(prodigi_last_polled_at__isnull=True)
        )
        .distinct()
    )


def send_tracking_email(order, shipments: Iterable[dict], *, order_stage: object = ""):
    notifiable_shipments = shipping_notification_shipments(shipments)
    has_tracking = bool(tracked_shipments(notifiable_shipments))
    if not notifiable_shipments and not is_shipping_notification_state(order_stage):
        logger.info(
            "Skipping tracking email because no shipped/dispatched shipments are available (order=%s, order_stage=%s)",
            order.order_number,
            str(order_stage or "").strip() or "n/a",
        )
        return False

    context = {
        "order": order,
        "shipments": notifiable_shipments,
        "has_tracking": has_tracking,
        "order_stage": str(order_stage or "").strip(),
        "contact_email": get_contact_email_address(),
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
        from_email=get_default_from_email(),
        to=[order.email],
    )
    email.send(fail_silently=False)
    return True
