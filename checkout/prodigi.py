import logging
import os
from typing import List, Optional, Tuple

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def _request_timeout() -> Tuple[float, float]:
    """Return connect/read timeout tuple for outbound Prodigi requests."""
    connect_timeout = float(getattr(settings, "PRODIGI_CONNECT_TIMEOUT_SECONDS", 5))
    read_timeout = float(getattr(settings, "PRODIGI_READ_TIMEOUT_SECONDS", 20))
    return (connect_timeout, read_timeout)


def _parse_prodigi_error(response: requests.Response) -> Tuple[Optional[str], Optional[str], List[str]]:
    """Extract safe diagnostic fields from a Prodigi error response."""
    outcome = None
    trace_parent = response.headers.get("traceparent")
    failure_codes: List[str] = []

    try:
        payload = response.json()
    except ValueError:
        payload = {}

    if isinstance(payload, dict):
        outcome = payload.get("outcome")
        trace_parent = trace_parent or payload.get("traceParent")
        failures = payload.get("failures")
        if isinstance(failures, dict):
            for field, entries in failures.items():
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    code = entry.get("code")
                    if code:
                        failure_codes.append(f"{field}:{code}")

    return outcome, trace_parent, failure_codes[:10]


def create_prodigi_order(order):
    """
    Formats an OpenEire order and sends it to the Prodigi API.
    """
    is_sandbox = os.environ.get("PRODIGI_SANDBOX", "True") == "True"
    base_url = "https://api.sandbox.prodigi.com/v4.0/" if is_sandbox else "https://api.prodigi.com/v4.0/"
    url = f"{base_url}orders"
    api_key = os.environ.get("PRODIGI_API_KEY")
    site_url = os.environ.get("SITE_URL", "http://127.0.0.1:8000")

    if not api_key:
        logger.error("Prodigi API key missing; cannot fulfill order %s", order.order_number)
        raise RuntimeError("Prodigi fulfillment is unavailable right now.")

    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json",
    }

    items_payload = []
    for item in order.items.all():
        product = item.product

        if hasattr(product, "prodigi_sku") and product.prodigi_sku:
            try:
                raw_url = product.photo.high_res_file.url
                image_url = raw_url if raw_url.startswith("http") else f"{site_url}{raw_url}"

                if "127.0.0.1" in image_url or "localhost" in image_url:
                    logger.warning(
                        "Prodigi cannot access localhost asset URL; using placeholder image "
                        "(order=%s, sku=%s)",
                        order.order_number,
                        product.prodigi_sku,
                    )
                    # Public placeholder image only for local validation/testing paths.
                    image_url = "https://images.unsplash.com/photo-1506744626753-1fa28f67c9bf?w=2400&q=80"

                item_payload = {
                    "sku": product.prodigi_sku,
                    "copies": item.quantity,
                    "sizing": "fillPrintArea",
                    "assets": [{"printArea": "default", "url": image_url}],
                }

                if "canvas" in product.material.lower():
                    item_payload["attributes"] = {
                        "wrap": "MirrorWrap",  # Options: MirrorWrap, ImageWrap, White, Black
                    }

                items_payload.append(item_payload)

            except Exception as exc:
                logger.warning(
                    "Failed to prepare Prodigi asset URL (order=%s, sku=%s, error_type=%s)",
                    order.order_number,
                    product.prodigi_sku,
                    exc.__class__.__name__,
                )
                continue

    if not items_payload:
        logger.info("No physical items found for Prodigi fulfillment (order=%s)", order.order_number)
        return None

    address_payload = {
        "line1": order.street_address1,
        "postalOrZipCode": order.postcode,
        "countryCode": str(order.country),
        "townOrCity": order.town,
        "stateOrCounty": order.county,
    }

    if order.street_address2 and order.street_address2.strip():
        address_payload["line2"] = order.street_address2

    prodigi_shipping_method = order.shipping_method.capitalize()

    payload = {
        "shippingMethod": prodigi_shipping_method,
        "recipient": {
            "name": f"{order.first_name}",
            "address": address_payload,
            "email": order.email,
        },
        "items": items_payload,
        "idempotencyKey": order.order_number,
    }

    try:
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=_request_timeout(),
        )
    except requests.Timeout:
        logger.error("Prodigi request timed out (order=%s)", order.order_number)
        raise RuntimeError("Prodigi fulfillment timed out.")
    except requests.RequestException:
        logger.exception("Prodigi request failed (order=%s)", order.order_number)
        raise RuntimeError("Prodigi fulfillment request failed.")

    if 200 <= response.status_code < 300:
        try:
            data = response.json()
        except ValueError:
            logger.warning(
                "Prodigi returned non-JSON success response (order=%s, status=%s)",
                order.order_number,
                response.status_code,
            )
            raise RuntimeError("Prodigi fulfillment returned an invalid response.")
        logger.info(
            "Prodigi order created successfully (order=%s, prodigi_order_id=%s)",
            order.order_number,
            data.get("order", {}).get("id"),
        )
        return data

    outcome, trace_parent, failure_codes = _parse_prodigi_error(response)
    logger.warning(
        "Prodigi API rejected fulfillment (order=%s, status=%s, outcome=%s, trace_parent=%s, failure_codes=%s)",
        order.order_number,
        response.status_code,
        outcome or "unknown",
        trace_parent or "n/a",
        ",".join(failure_codes) if failure_codes else "none",
    )
    raise RuntimeError(
        f"Prodigi fulfillment failed (status={response.status_code}, outcome={outcome or 'unknown'})."
    )
