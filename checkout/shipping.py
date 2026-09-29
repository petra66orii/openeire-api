import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.conf import settings

from .alerts import send_shipping_quote_failure_alert
from .prodigi import ProdigiQuoteError, create_prodigi_shipping_quote

logger = logging.getLogger(__name__)

DEFAULT_FREE_SHIPPING_THRESHOLD = Decimal("150.00")


class ShippingConfigurationError(Exception):
    """Raised when a trustworthy physical shipping quote cannot be obtained."""


@dataclass(frozen=True)
class ShippingQuote:
    delivery_cost: Decimal
    physical_subtotal: Decimal
    free_shipping_applied: bool


def _decimal_or_default(value, *, default):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def get_free_shipping_threshold():
    threshold = _decimal_or_default(
        getattr(settings, "FREE_SHIPPING_THRESHOLD", DEFAULT_FREE_SHIPPING_THRESHOLD),
        default=DEFAULT_FREE_SHIPPING_THRESHOLD,
    )
    return max(threshold, Decimal("0.00"))


def get_free_shipping_eligible_countries():
    configured = getattr(settings, "FREE_SHIPPING_ELIGIBLE_COUNTRIES", None)
    if not configured:
        return set()
    normalized = {
        str(country).strip().upper()
        for country in configured
        if str(country).strip()
    }
    if "*" in normalized:
        return {"*"}
    return normalized


def free_shipping_applies(*, physical_subtotal, shipping_country):
    if not getattr(settings, "FREE_SHIPPING_ENABLED", True):
        return False
    if physical_subtotal <= Decimal("0.00"):
        return False
    if physical_subtotal < get_free_shipping_threshold():
        return False

    eligible_countries = get_free_shipping_eligible_countries()
    if not eligible_countries:
        return False
    if "*" in eligible_countries:
        return True

    return str(shipping_country or "").strip().upper() in eligible_countries


def calculate_physical_shipping_quote(*, line_items, shipping_country, shipping_method):
    physical_subtotal = Decimal("0.00")
    normalized_items = []

    for product_instance, quantity in line_items:
        line_quantity = int(quantity or 0)
        if line_quantity <= 0:
            continue
        physical_subtotal += Decimal(str(product_instance.price)) * line_quantity
        normalized_items.append((product_instance, line_quantity))

    if not normalized_items:
        return ShippingQuote(
            delivery_cost=Decimal("0.00"),
            physical_subtotal=physical_subtotal,
            free_shipping_applied=False,
        )

    try:
        prodigi_quote = create_prodigi_shipping_quote(
            line_items=normalized_items,
            destination_country_code=shipping_country,
            shipping_method=shipping_method,
        )
        delivery_cost = Decimal(str(prodigi_quote["amount"])).quantize(Decimal("0.01"))
        if delivery_cost < Decimal("0.00"):
            raise ValueError("negative shipping amount")
    except (ProdigiQuoteError, InvalidOperation, KeyError, TypeError, ValueError) as exc:
        logger.warning(
            "Blocking checkout because a trustworthy Prodigi shipping quote could not be obtained "
            "(country=%s, method=%s, cart_items=%s, error_type=%s)",
            str(shipping_country or "").strip().upper(),
            str(shipping_method or "").strip().lower(),
            len(normalized_items),
            exc.__class__.__name__,
        )
        try:
            send_shipping_quote_failure_alert(
                line_items=normalized_items,
                shipping_country=shipping_country,
                shipping_method=shipping_method,
                error=exc,
            )
        except Exception:
            logger.exception("Could not send shipping quote failure alert.")
        raise ShippingConfigurationError(
            "We couldn't calculate shipping at the moment. Please try again shortly."
        ) from None

    free_shipping = free_shipping_applies(
        physical_subtotal=physical_subtotal,
        shipping_country=shipping_country,
    )
    if free_shipping:
        delivery_cost = Decimal("0.00")

    return ShippingQuote(
        delivery_cost=delivery_cost,
        physical_subtotal=physical_subtotal,
        free_shipping_applied=free_shipping,
    )
