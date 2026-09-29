import logging
import os
import ipaddress
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.urls import reverse

logger = logging.getLogger(__name__)


class ProdigiFulfillmentError(RuntimeError):
    def __init__(self, *, status_code, outcome=None, trace_parent=None):
        self.status_code = status_code
        self.outcome = outcome or "unknown"
        self.trace_parent = trace_parent
        super().__init__(
            f"Prodigi fulfillment failed (status={status_code}, outcome={self.outcome})."
        )


def _is_non_public_prodigi_asset_url(url: str) -> bool:
    raw_url = str(url or "").strip()
    if not raw_url:
        return True

    try:
        parsed = urlsplit(raw_url)
    except ValueError:
        return True

    hostname = str(parsed.hostname or "").strip().lower()
    if not hostname:
        return True
    if hostname == "localhost" or hostname.endswith(".local"):
        return True

    try:
        ip_address = ipaddress.ip_address(hostname)
    except ValueError:
        return False

    return (
        ip_address.is_private
        or ip_address.is_loopback
        or ip_address.is_link_local
        or ip_address.is_multicast
        or ip_address.is_reserved
        or ip_address.is_unspecified
    )


def _parse_prodigi_sandbox(raw_value: Optional[str]) -> bool:
    normalized = str(raw_value or "").strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise ImproperlyConfigured(
        "PRODIGI_SANDBOX must be explicitly set to true or false."
    )


def _prodigi_base_url() -> str:
    raw_prodigi_sandbox = os.environ.get("PRODIGI_SANDBOX")
    if raw_prodigi_sandbox is None:
        if getattr(settings, "DEBUG", False) or getattr(settings, "IS_TEST_ENV", False):
            is_sandbox = True
        else:
            raise ImproperlyConfigured(
                "PRODIGI_SANDBOX must be set explicitly when DEBUG is False."
            )
    else:
        is_sandbox = _parse_prodigi_sandbox(raw_prodigi_sandbox)
    return "https://api.sandbox.prodigi.com/v4.0/" if is_sandbox else "https://api.prodigi.com/v4.0/"


def _prodigi_headers(order_number: Optional[str] = None) -> Dict[str, str]:
    api_key = os.environ.get("PRODIGI_API_KEY")
    if not api_key:
        if order_number:
            logger.error("Prodigi API key missing; cannot fulfill order %s", order_number)
        else:
            logger.error("Prodigi API key missing; cannot verify callback against Prodigi.")
        raise RuntimeError("Prodigi fulfillment is unavailable right now.")

    return {
        "X-API-Key": api_key,
        "Content-Type": "application/json",
    }


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
    base_url = getattr(settings, "PRODIGI_CALLBACK_BASE_URL", None)
    if not base_url:
        return None

    callback_path = reverse("prodigi_callback")
    callback_url = urljoin(f"{str(base_url).rstrip('/')}/", callback_path.lstrip("/"))
    callback_token = str(getattr(settings, "PRODIGI_CALLBACK_TOKEN", "") or "").strip()
    if not callback_token:
        return callback_url

    parsed = urlsplit(callback_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["token"] = callback_token
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(query),
            parsed.fragment,
        )
    )


def _redact_callback_url(callback_url: Optional[str]) -> str:
    raw_url = str(callback_url or "").strip()
    if not raw_url:
        return "omitted"

    def _redacted_query(query_string: str) -> str:
        query_items = []
        for key, value in parse_qsl(query_string, keep_blank_values=True):
            if key == "token" and value:
                query_items.append((key, "[redacted]"))
            else:
                query_items.append((key, value))
        return urlencode(query_items)

    try:
        parsed = urlsplit(raw_url)
    except ValueError:
        base_and_query, has_fragment, fragment = raw_url.partition("#")
        base, has_query, query_string = base_and_query.partition("?")
        if not has_query:
            return raw_url
        rebuilt = f"{base}?{_redacted_query(query_string)}"
        if has_fragment:
            return f"{rebuilt}#{fragment}"
        return rebuilt

    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            _redacted_query(parsed.query),
            parsed.fragment,
        )
    )


def fetch_prodigi_order(prodigi_order_id: str) -> dict:
    normalized_order_id = str(prodigi_order_id or "").strip()
    if not normalized_order_id:
        raise RuntimeError("Prodigi callback did not include an order id.")

    url = f"{_prodigi_base_url()}orders/{normalized_order_id}"

    try:
        response = requests.get(
            url,
            headers=_prodigi_headers(),
            timeout=_request_timeout(),
        )
    except requests.Timeout:
        logger.error("Prodigi order lookup timed out (prodigi_order_id=%s)", normalized_order_id)
        raise RuntimeError("Prodigi order lookup timed out.") from None
    except requests.RequestException:
        logger.exception("Prodigi order lookup failed (prodigi_order_id=%s)", normalized_order_id)
        raise RuntimeError("Prodigi order lookup failed.") from None

    if 200 <= response.status_code < 300:
        try:
            data = response.json()
        except ValueError:
            logger.warning(
                "Prodigi order lookup returned non-JSON success response (prodigi_order_id=%s, status=%s)",
                normalized_order_id,
                response.status_code,
            )
            raise RuntimeError("Prodigi order lookup returned an invalid response.") from None

        if isinstance(data, dict):
            order_payload = data.get("order")
            if isinstance(order_payload, dict):
                return order_payload
            return data

        raise RuntimeError("Prodigi order lookup returned an invalid response.")

    outcome, trace_parent, failure_codes = _parse_prodigi_error(response)
    logger.warning(
        "Prodigi order lookup failed (prodigi_order_id=%s, status=%s, outcome=%s, trace_parent=%s, failure_codes=%s)",
        normalized_order_id,
        response.status_code,
        outcome or "unknown",
        trace_parent or "n/a",
        ",".join(failure_codes) if failure_codes else "none",
    )
    raise RuntimeError(
        f"Prodigi order lookup failed (status={response.status_code}, outcome={outcome or 'unknown'})."
    )



class ProdigiQuoteError(RuntimeError):
    def __init__(self, message, *, status_code=None, outcome=None, trace_parent=None):
        self.status_code = status_code
        self.outcome = outcome or "unknown"
        self.trace_parent = trace_parent
        super().__init__(message)


def create_prodigi_shipping_quote(*, line_items, destination_country_code, shipping_method):
    """Return Prodigi's live shipping total for a physical basket."""
    normalized_country = str(destination_country_code or "").strip().upper()
    normalized_method = str(shipping_method or "budget").strip().lower()
    if not normalized_country:
        raise ProdigiQuoteError("A destination country is required for shipping.")

    items_payload = []
    for product, quantity in line_items:
        copies = int(quantity or 0)
        if copies <= 0:
            continue
        sku = str(getattr(product, "prodigi_sku", "") or "").strip()
        if not sku:
            raise ProdigiQuoteError("A physical product is missing its Prodigi SKU.")

        item_payload = {
            "sku": sku,
            "copies": copies,
            "assets": [{"printArea": "default"}],
        }
        if "canvas" in str(getattr(product, "material", "") or "").lower():
            item_payload["attributes"] = {"wrap": "MirrorWrap"}
        items_payload.append(item_payload)

    if not items_payload:
        return {"amount": "0.00", "currency": "EUR", "trace_parent": None}

    payload = {
        "shippingMethod": normalized_method,
        "destinationCountryCode": normalized_country,
        "currencyCode": "EUR",
        "items": items_payload,
    }
    url = f"{_prodigi_base_url()}quotes"

    try:
        response = requests.post(
            url,
            json=payload,
            headers=_prodigi_headers(),
            timeout=_request_timeout(),
        )
    except requests.Timeout:
        logger.error(
            "Prodigi quote timed out (country=%s, method=%s, items=%s)",
            normalized_country,
            normalized_method,
            len(items_payload),
        )
        raise ProdigiQuoteError("Prodigi shipping quote timed out.") from None
    except requests.RequestException:
        logger.exception(
            "Prodigi quote request failed (country=%s, method=%s, items=%s)",
            normalized_country,
            normalized_method,
            len(items_payload),
        )
        raise ProdigiQuoteError("Prodigi shipping quote request failed.") from None

    if not 200 <= response.status_code < 300:
        outcome, trace_parent, failure_codes = _parse_prodigi_error(response)
        logger.warning(
            "Prodigi quote rejected (country=%s, method=%s, status=%s, outcome=%s, trace_parent=%s, failure_codes=%s)",
            normalized_country,
            normalized_method,
            response.status_code,
            outcome or "unknown",
            trace_parent or "n/a",
            ",".join(failure_codes) if failure_codes else "none",
        )
        raise ProdigiQuoteError(
            "Prodigi could not calculate shipping.",
            status_code=response.status_code,
            outcome=outcome,
            trace_parent=trace_parent,
        )

    try:
        data = response.json()
    except ValueError:
        raise ProdigiQuoteError("Prodigi returned an invalid shipping quote.") from None

    if not isinstance(data, dict):
        raise ProdigiQuoteError("Prodigi returned an invalid shipping quote.")

    quotes = data.get("quotes")
    if not isinstance(quotes, list) or not quotes:
        raise ProdigiQuoteError(
            "Prodigi did not return a shipping quote.",
            outcome=data.get("outcome"),
            trace_parent=data.get("traceParent"),
        )

    selected_quote = None
    for quote in quotes:
        if not isinstance(quote, dict):
            continue
        returned_method = str(
            quote.get("shipmentMethod") or quote.get("shippingMethod") or ""
        ).strip().lower()
        if returned_method == normalized_method:
            selected_quote = quote
            break
    if selected_quote is None and len(quotes) == 1 and isinstance(quotes[0], dict):
        selected_quote = quotes[0]
    if selected_quote is None:
        raise ProdigiQuoteError(
            "Prodigi did not return the requested shipping method.",
            outcome=data.get("outcome"),
            trace_parent=data.get("traceParent"),
        )

    shipping = (selected_quote.get("costSummary") or {}).get("shipping")
    if not isinstance(shipping, dict):
        raise ProdigiQuoteError("Prodigi shipping quote did not include a shipping total.")

    amount = shipping.get("amount")
    currency = str(shipping.get("currency") or "").strip().upper()
    if amount is None or currency != "EUR":
        raise ProdigiQuoteError("Prodigi shipping quote returned an unexpected currency or amount.")

    return {
        "amount": str(amount),
        "currency": currency,
        "trace_parent": data.get("traceParent"),
    }

def create_prodigi_order(order):
    """
    Formats an OpenEire order and sends it to the Prodigi API.
    """
    url = f"{_prodigi_base_url()}orders"
    site_url = os.environ.get("SITE_URL", "http://127.0.0.1:8000")
    headers = _prodigi_headers(order.order_number)

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

                if _is_non_public_prodigi_asset_url(image_url):
                    logger.warning(
                        "Prodigi cannot access non-public asset URL; rejecting fulfillment "
                        "(order=%s, sku=%s)",
                        order.order_number,
                        product.prodigi_sku,
                    )
                    raise RuntimeError("Prodigi fulfillment asset URL is not publicly reachable.")

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
    prodigi_mode = "sandbox" if "sandbox" in url.lower() else "live"
    redacted_callback_url = _redact_callback_url(callback_url)
    if callback_url:
        payload["callbackUrl"] = callback_url
        logger.info(
            "Creating Prodigi order with callback URL (order=%s, mode=%s, callback_url=%s)",
            order.order_number,
            prodigi_mode,
            redacted_callback_url,
        )
    else:
        callback_base_url = str(getattr(settings, "PRODIGI_CALLBACK_BASE_URL", "") or "").strip()
        omission_reason = (
            "PRODIGI_CALLBACK_BASE_URL is not configured"
            if not callback_base_url
            else "callback URL could not be generated"
        )
        logger.warning(
            "Prodigi callback URL omitted from Prodigi order payload (order=%s, mode=%s, reason=%s)",
            order.order_number,
            prodigi_mode,
            omission_reason,
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
    raise ProdigiFulfillmentError(
        status_code=response.status_code,
        outcome=outcome,
        trace_parent=trace_parent,
    )
