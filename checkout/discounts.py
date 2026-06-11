from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from django.conf import settings
from django.db import IntegrityError

from .models import DiscountRedemption


ZERO = Decimal("0.00")


def normalize_discount_code(value) -> str:
    return str(value or "").strip().upper()


def normalize_discount_email(value) -> str:
    return str(value or "").strip().lower()


@dataclass
class DiscountEvaluation:
    code: str = ""
    normalized_code: str = ""
    amount: Decimal = ZERO
    percent: Decimal = ZERO
    label: str = ""
    message: str = ""
    valid: bool = False


def welcome_discount_enabled() -> bool:
    return bool(getattr(settings, "WELCOME_DISCOUNT_ENABLED", False))


def welcome_discount_code() -> str:
    return normalize_discount_code(getattr(settings, "WELCOME_DISCOUNT_CODE", "WELCOME10"))


def welcome_discount_percent() -> Decimal:
    raw_value = str(getattr(settings, "WELCOME_DISCOUNT_PERCENT", "10") or "10").strip()
    return Decimal(raw_value)


def welcome_discount_label() -> str:
    return f"{welcome_discount_code()} ({welcome_discount_percent():.0f}% off art prints)"


def has_email_used_discount(email: str, code: str) -> bool:
    normalized_email = normalize_discount_email(email)
    normalized_code = normalize_discount_code(code)
    if not normalized_email or not normalized_code:
        return False
    return DiscountRedemption.objects.filter(
        normalized_email=normalized_email,
        code=normalized_code,
    ).exists()


def evaluate_discount(*, code, customer_email: Optional[str], eligible_physical_subtotal: Decimal) -> DiscountEvaluation:
    normalized_code = normalize_discount_code(code)
    if not normalized_code:
        return DiscountEvaluation()

    if not welcome_discount_enabled():
        return DiscountEvaluation(
            code=normalized_code,
            normalized_code=normalized_code,
            message="This discount code is not available right now.",
        )

    configured_code = welcome_discount_code()
    if normalized_code != configured_code:
        return DiscountEvaluation(
            code=normalized_code,
            normalized_code=normalized_code,
            message="Invalid discount code.",
        )

    if eligible_physical_subtotal <= ZERO:
        return DiscountEvaluation(
            code=normalized_code,
            normalized_code=normalized_code,
            message="This code applies to art prints only.",
        )

    normalized_email = normalize_discount_email(customer_email)
    if normalized_email and has_email_used_discount(normalized_email, configured_code):
        return DiscountEvaluation(
            code=configured_code,
            normalized_code=configured_code,
            message="This welcome code has already been used for this email address.",
        )

    percent = welcome_discount_percent()
    amount = (
        eligible_physical_subtotal * percent / Decimal("100")
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return DiscountEvaluation(
        code=configured_code,
        normalized_code=configured_code,
        amount=amount,
        percent=percent,
        label=welcome_discount_label(),
        valid=True,
    )


def record_discount_redemption(order):
    normalized_code = normalize_discount_code(order.discount_code)
    normalized_email = normalize_discount_email(order.email)
    if not normalized_code or order.discount_amount <= ZERO or not normalized_email:
        return None

    existing = DiscountRedemption.objects.filter(order=order).first()
    if existing:
        return existing

    try:
        return DiscountRedemption.objects.create(
            email=order.email,
            normalized_email=normalized_email,
            code=normalized_code,
            order=order,
        )
    except IntegrityError:
        existing = DiscountRedemption.objects.filter(
            normalized_email=normalized_email,
            code=normalized_code,
        ).first()
        if existing:
            return existing
        raise
