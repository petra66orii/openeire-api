import logging
from decimal import Decimal
from decimal import ROUND_HALF_UP
from urllib.parse import urljoin

import stripe
from django.conf import settings

from .emails import get_realestate_site_url


logger = logging.getLogger(__name__)

VAT_RATE = Decimal("0.23")
DEPOSIT_RATE = Decimal("0.30")


def _money(value):
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_realestate_deposit_amounts(enquiry):
    if getattr(enquiry, "quoted_price", None) is None:
        raise ValueError("Quoted price is required before creating a deposit checkout.")

    quote_total = _money(enquiry.quoted_price)
    if quote_total <= 0:
        raise ValueError("Quoted price must be greater than zero.")

    vat_total = _money(quote_total * VAT_RATE)
    total_including_vat = _money(quote_total + vat_total)
    deposit_amount = _money(total_including_vat * DEPOSIT_RATE)
    balance_due = _money(total_including_vat - deposit_amount)

    return {
        "quote_total": quote_total,
        "vat_total": vat_total,
        "total_including_vat": total_including_vat,
        "deposit_amount": deposit_amount,
        "balance_due": balance_due,
    }


def _configured_stripe_payment_method_types():
    configured = getattr(settings, "STRIPE_PAYMENT_METHOD_TYPES", None)
    if isinstance(configured, (list, tuple)):
        cleaned = [
            value.strip()
            for value in configured
            if isinstance(value, str) and value.strip()
        ]
        if cleaned:
            return cleaned
    return ["card"]


def _checkout_return_url(path):
    return urljoin(get_realestate_site_url().rstrip("/") + "/", path.lstrip("/"))


def _stripe_metadata(enquiry):
    return {
        "realestate_enquiry_id": str(enquiry.pk),
        "client_name": str(getattr(enquiry, "name", "") or "")[:500],
        "property_address": str(getattr(enquiry, "property_address", "") or "")[:500],
        "package_name": (
            enquiry.get_preferred_package_display()
            if hasattr(enquiry, "get_preferred_package_display")
            else str(getattr(enquiry, "preferred_package", "") or "")
        )[:500],
        "purpose": "realestate_deposit",
    }


def create_realestate_deposit_checkout_session(enquiry):
    if not getattr(enquiry, "pk", None):
        raise ValueError("Enquiry must be saved before creating a deposit checkout.")

    amounts = calculate_realestate_deposit_amounts(enquiry)
    amount_in_cents = int(
        (amounts["deposit_amount"] * Decimal("100")).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )
    if amount_in_cents <= 0:
        raise ValueError("Deposit amount must be greater than zero.")

    stripe.api_key = settings.STRIPE_SECRET_KEY
    stripe.max_network_retries = getattr(settings, "STRIPE_MAX_NETWORK_RETRIES", 2)
    stripe.default_http_client = stripe.RequestsClient(
        timeout=getattr(settings, "STRIPE_TIMEOUT_SECONDS", 10)
    )

    metadata = _stripe_metadata(enquiry)
    session_kwargs = {
        "mode": "payment",
        "payment_method_types": _configured_stripe_payment_method_types(),
        "line_items": [
            {
                "price_data": {
                    "currency": "eur",
                    "unit_amount": amount_in_cents,
                    "product_data": {
                        "name": f"OpenEire real estate booking deposit - RE-{enquiry.pk}",
                        "metadata": metadata,
                    },
                },
                "quantity": 1,
            }
        ],
        "metadata": metadata,
        "payment_intent_data": {"metadata": metadata},
        "success_url": _checkout_return_url(
            "real-estate/deposit/success?session_id={CHECKOUT_SESSION_ID}"
        ),
        "cancel_url": _checkout_return_url("real-estate/deposit/cancelled"),
    }
    customer_email = str(getattr(enquiry, "email", "") or "").strip()
    if customer_email:
        session_kwargs["customer_email"] = customer_email

    session = stripe.checkout.Session.create(
        **session_kwargs,
        idempotency_key=f"realestate-deposit-enquiry-{enquiry.pk}-{amount_in_cents}",
    )

    checkout_url = str(getattr(session, "url", "") or session.get("url", "")).strip()
    session_id = str(getattr(session, "id", "") or session.get("id", "")).strip()
    if not checkout_url:
        raise RuntimeError("Stripe did not return a checkout URL.")

    enquiry.deposit_payment_link = checkout_url
    if session_id:
        enquiry.stripe_deposit_session_id = session_id
    enquiry.save(
        update_fields=[
            "deposit_payment_link",
            "stripe_deposit_session_id",
            "updated_at",
        ]
    )

    logger.info(
        "Created real estate deposit checkout session. enquiry_id=%s session_id=%s amount_cents=%s",
        enquiry.pk,
        session_id or "unknown",
        amount_in_cents,
    )
    return checkout_url
