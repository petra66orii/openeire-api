import logging
import os
from typing import List, Optional, Tuple
from urllib.parse import urljoin

import requests
from django.conf import settings
from django.urls import reverse

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
    if not isinstance(trace_parent, str):
        trace_parent = None
    failure_codes: List[str] = []

    try:
        payload = response.json()
    except ValueError:
        payload = {}

    if isinstance(payload, dict):
        payload_outcome = payload.get("outcome")
        if isinstance(payload_outcome, str):
            outcome = payload_outcome

        payload_trace_parent = payload.get("traceParent")
        if not trace_parent and isinstance(payload_trace_parent, str):
            trace_parent = payload_trace_parent
        failures = payload.get("failures")
        if isinstance(failures, dict):
            for field, entries in failures.items():
                if not isinstance(field, str):
                    continue
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    code = entry.get("code")
                    if isinstance(code, str) and code:
                        failure_codes.append(f"{field}:{code}")

    return outcome, trace_parent, failure_codes[:10]


def _normalize_absolute_asset_url(raw_url: str, *, base_url: str) -> str:
    if raw_url.startswith(("http://", "https://")):
        return raw_url
    return urljoin(f"{base_url.rstrip('/')}/", str(raw_url).lstrip("/"))


def _get_prodigi_asset_url(product, *, site_url: str) -> str:
    """
    Return an absolute URL that Prodigi can fetch for a physical print asset.

    Preference order:
    1. Storage-generated URL for the private asset (signed in production R2)
    2. Field ``url`` if already provided as an absolute URL
    3. Site URL + relative path fallback for local/dev storage
    """
    file_field = product.photo.high_res_file
    storage = getattr(file_field, "storage", None)
    file_name = getattr(file_field, "name", None)

    if storage is not None and file_name:
        storage_url = storage.url(file_name)
        if isinstance(storage_url, str) and storage_url:
            return _normalize_absolute_asset_url(storage_url, base_url=site_url)

    raw_url = file_field.url
    if isinstance(raw_url, str) and raw_url:
        return _normalize_absolute_asset_url(raw_url, base_url=site_url)

    raise ValueError("No accessible asset URL available for Prodigi fulfillment.")


def _get_prodigi_callback_url() -> Optional[str]:
    base_url = getattr(settings, "PRODIGI_CALLBACK_BASE_URL", None) or os.environ.get(
        "PRODIGI_CALLBACK_BASE_URL"
    )
    if not base_url:
        return None

    callback_token = getattr(settings, "PRODIGI_CALLBACK_TOKEN", "")
    if not callback_token:
        logger.warning(
            "Prodigi callback token is not configured; callback URL will not be attached."
        )
        return None

    callback_path = reverse("prodigi_callback")
    callback_url = urljoin(f"{str(base_url).rstrip('/')}/", callback_path.lstrip("/"))
    separator = "&" if "?" in callback_url else "?"
    return f"{callback_url}{separator}token={callback_token}"


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
    physical_items_seen = 0
    skipped_missing_sku = 0
    skipped_asset_preparation = 0
    for item in order.items.all():
        product = item.product

        if hasattr(product, "prodigi_sku") and product.prodigi_sku:
            if hasattr(product, "photo"):
                physical_items_seen += item.quantity
            try:
                image_url = _get_prodigi_asset_url(product, site_url=site_url)

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
                skipped_asset_preparation += item.quantity
                continue
        elif hasattr(product, "photo"):
            physical_items_seen += item.quantity
            skipped_missing_sku += item.quantity

    if physical_items_seen and (skipped_missing_sku or skipped_asset_preparation):
        logger.warning(
            "Prodigi fulfillment could not prepare all physical items "
            "(order=%s, physical_items=%s, prepared_items=%s, missing_sku=%s, asset_prepare_failures=%s)",
            order.order_number,
            physical_items_seen,
            sum(item["copies"] for item in items_payload),
            skipped_missing_sku,
            skipped_asset_preparation,
        )
        raise RuntimeError("Prodigi fulfillment could not prepare all physical items.")

    if not items_payload:
        if physical_items_seen:
            logger.warning(
                "Prodigi fulfillment could not prepare any physical items "
                "(order=%s, physical_items=%s, missing_sku=%s, asset_prepare_failures=%s)",
                order.order_number,
                physical_items_seen,
                skipped_missing_sku,
                skipped_asset_preparation,
            )
            raise RuntimeError("Prodigi fulfillment could not prepare any physical items.")

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
        "merchantReference": order.order_number,
    }

    callback_url = _get_prodigi_callback_url()
    if callback_url:
        payload["callbackUrl"] = callback_url
    else:
        logger.warning(
            "Prodigi callback URL is not configured; tracking updates will not be pushed automatically (order=%s)",
            order.order_number,
        )

    try:
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=_request_timeout(),
        )
    except requests.Timeout:
        logger.error("Prodigi request timed out (order=%s)", order.order_number)
        raise RuntimeError("Prodigi fulfillment timed out.") from None
    except requests.RequestException:
        logger.exception("Prodigi request failed (order=%s)", order.order_number)
        raise RuntimeError("Prodigi fulfillment request failed.") from None

    if 200 <= response.status_code < 300:
        try:
            data = response.json()
        except ValueError:
            logger.warning(
                "Prodigi returned non-JSON success response (order=%s, status=%s)",
                order.order_number,
                response.status_code,
            )
            raise RuntimeError("Prodigi fulfillment returned an invalid response.") from None
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
