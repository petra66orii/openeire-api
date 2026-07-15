import logging
from decimal import Decimal
from decimal import ROUND_HALF_UP
from urllib.parse import urljoin

import stripe
from django.conf import settings

from openeire_api.business_identity import get_business_identity

from .emails import get_realestate_site_url


logger = logging.getLogger(__name__)

DEPOSIT_RATE = Decimal("0.30")
PRICING_SNAPSHOT_VERSION = 1
PRICING_SNAPSHOT_FIELDS = (
    "pricing_snapshot_version",
    "price_input_is_gross",
    "vat_registered_at_quote",
    "quoted_vat_rate",
    "quoted_subtotal",
    "quoted_vat_amount",
    "quoted_total",
    "quoted_deposit_amount",
    "quoted_balance_due",
)


def _money(value):
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _snapshot_is_complete(enquiry):
    return all(getattr(enquiry, field, None) is not None for field in PRICING_SNAPSHOT_FIELDS)


def _amounts_from_snapshot(enquiry):
    quote_total = (
        enquiry.quoted_total
        if enquiry.price_input_is_gross
        else enquiry.quoted_subtotal
    )
    return {
        "quote_total": _money(quote_total),
        "quote_subtotal": _money(enquiry.quoted_subtotal),
        "vat_total": _money(enquiry.quoted_vat_amount),
        "total_including_vat": _money(enquiry.quoted_total),
        "deposit_amount": _money(enquiry.quoted_deposit_amount),
        "balance_due": _money(enquiry.quoted_balance_due),
        "vat_rate": Decimal(enquiry.quoted_vat_rate),
        "vat_rate_percent": _money(Decimal(enquiry.quoted_vat_rate) * Decimal("100")),
        "vat_registered": bool(enquiry.vat_registered_at_quote),
        "price_input_is_gross": bool(enquiry.price_input_is_gross),
    }


def calculate_realestate_deposit_amounts(enquiry, *, persist_snapshot=True):
    if getattr(enquiry, "quoted_price", None) is None:
        raise ValueError("Quoted price is required before creating a deposit checkout.")

    quote_total = _money(enquiry.quoted_price)
    if quote_total <= 0:
        raise ValueError("Quoted price must be greater than zero.")

    if _snapshot_is_complete(enquiry):
        return _amounts_from_snapshot(enquiry)

    vat_registered = bool(getattr(settings, "VAT_REGISTERED", False))
    vat_rate = Decimal(str(getattr(settings, "VAT_RATE", Decimal("0.23"))))
    price_input_is_gross = bool(
        getattr(settings, "REALESTATE_PRICE_INPUT_IS_GROSS", True)
    )

    if not vat_registered:
        quote_subtotal = quote_total
        vat_total = Decimal("0.00")
        total_including_vat = quote_total
    elif price_input_is_gross:
        quote_subtotal = _money(quote_total / (Decimal("1.00") + vat_rate))
        vat_total = _money(quote_total - quote_subtotal)
        total_including_vat = quote_total
    else:
        quote_subtotal = quote_total
        vat_total = _money(quote_subtotal * vat_rate)
        total_including_vat = _money(quote_subtotal + vat_total)

    deposit_amount = _money(total_including_vat * DEPOSIT_RATE)
    balance_due = _money(total_including_vat - deposit_amount)

    snapshot_values = {
        "pricing_snapshot_version": PRICING_SNAPSHOT_VERSION,
        "price_input_is_gross": price_input_is_gross,
        "vat_registered_at_quote": vat_registered,
        "quoted_vat_rate": vat_rate if vat_registered else Decimal("0.00"),
        "quoted_subtotal": quote_subtotal,
        "quoted_vat_amount": vat_total,
        "quoted_total": total_including_vat,
        "quoted_deposit_amount": deposit_amount,
        "quoted_balance_due": balance_due,
    }
    for field, value in snapshot_values.items():
        setattr(enquiry, field, value)
    if persist_snapshot and getattr(enquiry, "pk", None):
        enquiry.save(update_fields=[*PRICING_SNAPSHOT_FIELDS, "updated_at"])

    return {
        "quote_total": quote_total,
        "quote_subtotal": quote_subtotal,
        "vat_total": vat_total,
        "total_including_vat": total_including_vat,
        "deposit_amount": deposit_amount,
        "balance_due": balance_due,
        "vat_rate": vat_rate if vat_registered else Decimal("0.00"),
        "vat_rate_percent": _money(
            (vat_rate if vat_registered else Decimal("0.00")) * Decimal("100")
        ),
        "vat_registered": vat_registered,
        "price_input_is_gross": price_input_is_gross,
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


def _stripe_metadata(enquiry, invoice=None):
    return {
        "realestate_enquiry_id": str(enquiry.pk),
        "job_reference": f"RE-{enquiry.pk}",
        "package_reference": str(getattr(enquiry, "preferred_package", "") or "")[:100],
        "purpose": "realestate_deposit",
        "brand": get_business_identity().display_name,
        **({"realestate_invoice_number": invoice.invoice_number} if invoice else {}),
    }


def create_realestate_deposit_checkout_session(enquiry):
    if not getattr(enquiry, "pk", None):
        raise ValueError("Enquiry must be saved before creating a deposit checkout.")

    from .finance import ensure_standard_realestate_invoices

    deposit_invoice, _ = ensure_standard_realestate_invoices(enquiry)
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

    metadata = _stripe_metadata(enquiry, deposit_invoice)
    session_kwargs = {
        "mode": "payment",
        "payment_method_types": _configured_stripe_payment_method_types(),
        "line_items": [
            {
                "price_data": {
                    "currency": "eur",
                    "unit_amount": amount_in_cents,
                    "product_data": {
                        "name": (
                            f"{get_business_identity().display_name} "
                            f"real estate booking deposit - RE-{enquiry.pk}"
                        ),
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
