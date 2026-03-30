import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.conf import settings

from products.models import PrintTemplate

from .models import ProductShipping

logger = logging.getLogger(__name__)

DEFAULT_FREE_SHIPPING_THRESHOLD = Decimal("120.00")


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
    delivery_cost = Decimal("0.00")

    for product_instance, quantity in line_items:
        line_quantity = int(quantity or 0)
        if line_quantity <= 0:
            continue

        physical_subtotal += product_instance.price * line_quantity

        try:
            template = PrintTemplate.objects.get(
                material=product_instance.material,
                size=product_instance.size,
            )
            shipping_rule = ProductShipping.objects.get(
                product=template,
                country=shipping_country,
                method=shipping_method,
            )
            delivery_cost += shipping_rule.cost * line_quantity
        except (PrintTemplate.DoesNotExist, ProductShipping.DoesNotExist):
            logger.warning(
                "No shipping rule found for checkout item "
                "(material=%s, size=%s, country=%s, method=%s)",
                product_instance.material,
                product_instance.size,
                shipping_country,
                shipping_method,
            )

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
