import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from decimal import ROUND_HALF_UP
from urllib.parse import urljoin

import stripe
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from openeire_api.business_identity import get_business_identity

from .emails import get_realestate_api_url


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
DEPOSIT_SESSION_LIFETIME_SECONDS = 24 * 60 * 60
DEPOSIT_SESSION_MIN_REMAINING_SECONDS = 30 * 60


@dataclass(frozen=True)
class DepositCheckoutSessionResult:
    checkout_url: str = ""
    session_id: str = ""
    reused: bool = False
    payment_already_exists: bool = False


class DepositSessionStateChanged(Exception):
    """The saved deposit Session changed while a replacement was being prepared."""


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


def _deposit_cancellation_url():
    return urljoin(
        get_realestate_api_url().rstrip("/") + "/",
        reverse("real-estate-deposit-cancelled").lstrip("/"),
    )


def _deposit_success_url():
    success_url = urljoin(
        get_realestate_api_url().rstrip("/") + "/",
        reverse("real-estate-deposit-success").lstrip("/"),
    )
    return f"{success_url}?session_id={{CHECKOUT_SESSION_ID}}"


def _stripe_metadata(enquiry, invoice=None):
    return {
        "realestate_enquiry_id": str(enquiry.pk),
        "job_reference": f"RE-{enquiry.pk}",
        "package_reference": str(getattr(enquiry, "preferred_package", "") or "")[:100],
        "purpose": "realestate_deposit",
        "brand": get_business_identity().display_name,
        **({"realestate_invoice_number": invoice.invoice_number} if invoice else {}),
    }


def _stripe_value(obj, key, default=None):
    if hasattr(obj, "get"):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _configure_stripe():
    stripe.api_key = settings.STRIPE_SECRET_KEY
    stripe.max_network_retries = getattr(settings, "STRIPE_MAX_NETWORK_RETRIES", 2)
    stripe.default_http_client = stripe.RequestsClient(
        timeout=getattr(settings, "STRIPE_TIMEOUT_SECONDS", 10)
    )


def _configured_stripe_livemode():
    secret_key = str(getattr(settings, "STRIPE_SECRET_KEY", "") or "")
    return secret_key.startswith(("sk_live_", "rk_live_"))


def _is_missing_stripe_session_error(exc):
    return isinstance(exc, stripe.error.InvalidRequestError) and (
        getattr(exc, "code", None) == "resource_missing"
        or getattr(exc, "http_status", None) == 404
    )


def _is_retriable_stripe_creation_error(exc):
    if isinstance(
        exc,
        (
            stripe.error.APIConnectionError,
            stripe.error.APIError,
            stripe.error.RateLimitError,
        ),
    ):
        return True
    status = getattr(exc, "http_status", None)
    return status == 429 or (isinstance(status, int) and status >= 500)


def _clear_deposit_creation_key(enquiry, key):
    from .models import RealEstateEnquiry

    cleared = RealEstateEnquiry.objects.filter(
        pk=enquiry.pk,
        stripe_deposit_creation_key=key,
    ).update(stripe_deposit_creation_key="", updated_at=timezone.now())
    if cleared:
        enquiry.stripe_deposit_creation_key = ""


@transaction.atomic
def _get_or_create_deposit_creation_key(
    enquiry,
    invoice,
    *,
    expected_session_id=None,
):
    from .models import RealEstateEnquiry

    locked = RealEstateEnquiry.objects.select_for_update().get(pk=enquiry.pk)
    if expected_session_id is not None and str(
        locked.stripe_deposit_session_id or ""
    ).strip() != str(expected_session_id or "").strip():
        raise DepositSessionStateChanged
    key = str(locked.stripe_deposit_creation_key or "").strip()
    try:
        expires_at = int(key.rsplit("-", 2)[-2])
        now_timestamp = int(timezone.now().timestamp())
        if expires_at <= now_timestamp:
            raise ValueError("creation attempt has expired")
        if expires_at <= now_timestamp + DEPOSIT_SESSION_MIN_REMAINING_SECONDS:
            raise ValidationError(
                "The previous Stripe creation attempt is too close to expiry to retry "
                "safely. Retry after its expiry or review it in Stripe."
            )
    except (IndexError, TypeError, ValueError):
        expires_at = int(timezone.now().timestamp()) + DEPOSIT_SESSION_LIFETIME_SECONDS
        key = (
            f"realestate-deposit-{invoice.invoice_number}-{expires_at}-"
            f"{uuid.uuid4().hex}"
        )
        locked.stripe_deposit_creation_key = key
        locked.save(update_fields=("stripe_deposit_creation_key", "updated_at"))
    enquiry.stripe_deposit_creation_key = key
    return key, expires_at


@transaction.atomic
def _store_created_deposit_session(enquiry, key, checkout_url, session_id):
    from .models import RealEstateEnquiry

    locked = RealEstateEnquiry.objects.select_for_update().get(pk=enquiry.pk)
    current_key = str(locked.stripe_deposit_creation_key or "").strip()
    current_session_id = str(locked.stripe_deposit_session_id or "").strip()
    if current_key != key:
        if current_session_id != session_id:
            raise DepositSessionStateChanged
    else:
        locked.deposit_payment_link = checkout_url
        locked.stripe_deposit_session_id = session_id
        locked.stripe_deposit_creation_key = ""
        locked.save(
            update_fields=(
                "deposit_payment_link",
                "stripe_deposit_session_id",
                "stripe_deposit_creation_key",
                "updated_at",
            )
        )
    enquiry.deposit_payment_link = locked.deposit_payment_link
    enquiry.stripe_deposit_session_id = locked.stripe_deposit_session_id
    enquiry.stripe_deposit_creation_key = locked.stripe_deposit_creation_key


def _session_mismatch_reason(session, enquiry, invoice, amount_in_cents):
    metadata = _stripe_value(session, "metadata", {}) or {}
    checks = (
        (
            str(_stripe_value(session, "id", "") or "")
            == str(enquiry.stripe_deposit_session_id or ""),
            "session_id",
        ),
        (int(_stripe_value(session, "amount_total", 0) or 0) == amount_in_cents, "amount"),
        (str(_stripe_value(session, "currency", "") or "").lower() == "eur", "currency"),
        (str(metadata.get("purpose") or "") == "realestate_deposit", "purpose"),
        (
            str(metadata.get("realestate_enquiry_id") or "") == str(enquiry.pk),
            "enquiry_metadata",
        ),
        (
            str(metadata.get("realestate_invoice_number") or "") == invoice.invoice_number,
            "invoice_metadata",
        ),
        (
            bool(_stripe_value(session, "livemode", False))
            == _configured_stripe_livemode(),
            "livemode",
        ),
    )
    return next((reason for matches, reason in checks if not matches), "")


def _reconcile_paid_deposit_session(enquiry, invoice, session):
    from .finance import record_realestate_payment
    from .models import RealEstatePayment, RealEstateTimelineEvent
    from .timeline import record_timeline_event

    session_id = str(_stripe_value(session, "id", "") or "").strip()
    existing = RealEstatePayment.objects.filter(
        stripe_checkout_session_id=session_id,
        status=RealEstatePayment.Status.SUCCEEDED,
    ).first()
    if existing:
        return existing
    if invoice.amount_outstanding <= 0:
        return invoice.payments.filter(status=RealEstatePayment.Status.SUCCEEDED).first()

    amount_total = int(_stripe_value(session, "amount_total", 0) or 0)
    payment, created = record_realestate_payment(
        invoice=invoice,
        amount=Decimal(amount_total) / Decimal("100"),
        method=RealEstatePayment.Method.STRIPE_DEPOSIT_CHECKOUT,
        paid_at=timezone.now(),
        stripe_checkout_session_id=session_id,
        stripe_payment_intent_id=str(_stripe_value(session, "payment_intent", "") or ""),
        notes="Reconciled from Stripe before sending a deposit request email.",
    )
    if created:
        try:
            record_timeline_event(
                enquiry,
                RealEstateTimelineEvent.EventType.DEPOSIT_PAID,
                status=RealEstateTimelineEvent.EventStatus.COMPLETED,
                actor_type=RealEstateTimelineEvent.ActorType.SYSTEM,
                title="Deposit paid",
                notes="Stripe confirmed payment for the real estate booking deposit.",
                reference_url=enquiry.deposit_payment_link,
                stripe_session_id=session_id,
            )
        except Exception:
            logger.exception(
                "Failed to record reconciled deposit timeline event. enquiry_id=%s session_id=%s",
                enquiry.pk,
                session_id or "unknown",
            )
    return payment


def create_realestate_deposit_checkout_session(
    enquiry,
    *,
    invoice=None,
    amount_in_cents=None,
    expected_session_id=None,
):
    if not getattr(enquiry, "pk", None):
        raise ValueError("Enquiry must be saved before creating a deposit checkout.")

    from .finance import ensure_standard_realestate_invoices

    if invoice is None:
        invoice, _ = ensure_standard_realestate_invoices(enquiry)
    if amount_in_cents is None:
        amounts = calculate_realestate_deposit_amounts(enquiry)
        amount_in_cents = int(
            (amounts["deposit_amount"] * Decimal("100")).quantize(
                Decimal("1"),
                rounding=ROUND_HALF_UP,
            )
        )
    if amount_in_cents <= 0:
        raise ValueError("Deposit amount must be greater than zero.")

    _configure_stripe()

    metadata = _stripe_metadata(enquiry, invoice)
    idempotency_key, expires_at = _get_or_create_deposit_creation_key(
        enquiry,
        invoice,
        expected_session_id=expected_session_id,
    )
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
        "expires_at": expires_at,
        "after_expiration": {"recovery": {"enabled": True}},
        "success_url": _deposit_success_url(),
        "cancel_url": _deposit_cancellation_url(),
    }
    customer_email = str(getattr(enquiry, "email", "") or "").strip()
    if customer_email:
        session_kwargs["customer_email"] = customer_email

    try:
        session = stripe.checkout.Session.create(
            **session_kwargs,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        if not _is_retriable_stripe_creation_error(exc) and not isinstance(
            exc, stripe.error.IdempotencyError
        ):
            _clear_deposit_creation_key(enquiry, idempotency_key)
        raise

    checkout_url = str(_stripe_value(session, "url", "") or "").strip()
    session_id = str(_stripe_value(session, "id", "") or "").strip()
    if not checkout_url or not session_id:
        raise RuntimeError("Stripe did not return a complete Checkout Session.")

    _store_created_deposit_session(
        enquiry,
        idempotency_key,
        checkout_url,
        session_id,
    )

    logger.info(
        "Created real estate deposit checkout session. enquiry_id=%s session_id=%s amount_cents=%s",
        enquiry.pk,
        session_id or "unknown",
        amount_in_cents,
    )
    return checkout_url


def prepare_realestate_deposit_checkout_session(enquiry, *, _state_retry_count=0):
    from .finance import ensure_standard_realestate_invoices

    invoice, _ = ensure_standard_realestate_invoices(enquiry)
    amounts = calculate_realestate_deposit_amounts(enquiry)
    amount_in_cents = int(
        (amounts["deposit_amount"] * Decimal("100")).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )
    if enquiry.deposit_paid or invoice.amount_outstanding <= 0:
        return DepositCheckoutSessionResult(payment_already_exists=True)

    _configure_stripe()
    session_id = str(enquiry.stripe_deposit_session_id or "").strip()
    if session_id:
        try:
            session = stripe.checkout.Session.retrieve(session_id)
        except Exception as exc:
            if _is_missing_stripe_session_error(exc):
                logger.warning(
                    "Stored real estate deposit session is missing; replacing. "
                    "enquiry_id=%s session_id=%s",
                    enquiry.pk,
                    session_id,
                )
            else:
                logger.exception(
                    "Could not retrieve real estate deposit session. enquiry_id=%s session_id=%s",
                    enquiry.pk,
                    session_id,
                )
                raise
        else:
            mismatch_reason = _session_mismatch_reason(
                session, enquiry, invoice, amount_in_cents
            )
            status = str(_stripe_value(session, "status", "") or "").lower()
            payment_status = str(
                _stripe_value(session, "payment_status", "") or ""
            ).lower()
            if status == "complete" or payment_status == "paid":
                if mismatch_reason:
                    logger.error(
                        "Terminal real estate deposit session failed validation; manual review required. "
                        "enquiry_id=%s session_id=%s reason=%s",
                        enquiry.pk,
                        session_id,
                        mismatch_reason,
                    )
                    raise ValidationError(
                        "Stripe reports a terminal deposit Session that does not match "
                        "this enquiry. No replacement was created; manual review is required."
                    )
                if payment_status != "paid":
                    raise ValidationError(
                        "Stripe reports the deposit Session complete without a paid payment."
                    )
                _reconcile_paid_deposit_session(enquiry, invoice, session)
                logger.info(
                    "Reconciled existing paid real estate deposit session. "
                    "enquiry_id=%s session_id=%s",
                    enquiry.pk,
                    session_id,
                )
                return DepositCheckoutSessionResult(
                    session_id=session_id,
                    payment_already_exists=True,
                )

            expires_at = int(_stripe_value(session, "expires_at", 0) or 0)
            safe_until = (
                int(timezone.now().timestamp())
                + DEPOSIT_SESSION_MIN_REMAINING_SECONDS
            )
            checkout_url = str(_stripe_value(session, "url", "") or "").strip()
            reusable = (
                not mismatch_reason
                and status == "open"
                and payment_status == "unpaid"
                and expires_at > safe_until
                and bool(checkout_url)
            )
            if reusable:
                if enquiry.deposit_payment_link != checkout_url:
                    enquiry.deposit_payment_link = checkout_url
                    enquiry.save(update_fields=("deposit_payment_link", "updated_at"))
                logger.info(
                    "Reusing valid real estate deposit session. enquiry_id=%s session_id=%s",
                    enquiry.pk,
                    session_id,
                )
                return DepositCheckoutSessionResult(
                    checkout_url=checkout_url,
                    session_id=session_id,
                    reused=True,
                )

            reason = mismatch_reason or (
                "near_expiry" if expires_at <= safe_until else status or payment_status or "invalid"
            )
            if status == "open" and payment_status == "unpaid":
                try:
                    stripe.checkout.Session.expire(session_id)
                except Exception:
                    logger.exception(
                        "Could not expire non-reusable real estate deposit session. "
                        "enquiry_id=%s session_id=%s reason=%s",
                        enquiry.pk,
                        session_id,
                        reason,
                    )
                    raise
            logger.warning(
                "Stored real estate deposit session is not reusable; replacing. "
                "enquiry_id=%s session_id=%s reason=%s",
                enquiry.pk,
                session_id,
                reason,
            )
    elif enquiry.deposit_payment_link:
        logger.warning(
            "Stored real estate deposit URL has no Session ID; replacing. enquiry_id=%s",
            enquiry.pk,
        )

    try:
        checkout_url = create_realestate_deposit_checkout_session(
            enquiry,
            invoice=invoice,
            amount_in_cents=amount_in_cents,
            expected_session_id=session_id,
        )
    except DepositSessionStateChanged:
        if _state_retry_count >= 2:
            raise ValidationError(
                "The deposit Session changed repeatedly while preparing the email. "
                "Please retry the action."
            )
        enquiry.refresh_from_db()
        return prepare_realestate_deposit_checkout_session(
            enquiry,
            _state_retry_count=_state_retry_count + 1,
        )
    return DepositCheckoutSessionResult(
        checkout_url=checkout_url,
        session_id=enquiry.stripe_deposit_session_id,
    )
