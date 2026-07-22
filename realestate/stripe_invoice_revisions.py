import logging
from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

import stripe
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import RealEstateInvoice, RealEstatePayment, RealEstateTimelineEvent
from .timeline import record_timeline_event


logger = logging.getLogger(__name__)
MAX_REVISION_DEPTH = 8


class StripeInvoiceRevisionError(RuntimeError):
    pass


@dataclass(frozen=True)
class RevisionResolution:
    current: object
    relation: str
    chain_ids: tuple[str, ...]


def stripe_value(obj, key, default=None):
    if hasattr(obj, "get"):
        return obj.get(key, default)
    return getattr(obj, key, default)


def stripe_reference_id(value):
    if isinstance(value, str):
        return value.strip()
    return str(stripe_value(value, "id", "") or "").strip()


def configure_stripe():
    stripe.api_key = settings.STRIPE_SECRET_KEY
    stripe.max_network_retries = getattr(settings, "STRIPE_MAX_NETWORK_RETRIES", 2)


def configured_stripe_livemode():
    key = str(getattr(settings, "STRIPE_SECRET_KEY", "") or "").strip()
    if key.startswith(("sk_live_", "rk_live_")):
        return True
    if key.startswith(("sk_test_", "rk_test_")):
        return False
    raise StripeInvoiceRevisionError(
        "The configured Stripe key does not identify a test or live environment."
    )


def _integer_value(obj, key):
    value = stripe_value(obj, key)
    try:
        return int(value)
    except (TypeError, ValueError):
        raise StripeInvoiceRevisionError(
            f"Stripe invoice has an invalid {key}."
        ) from None


def validate_stripe_invoice(local_invoice, stripe_invoice):
    stripe_id = stripe_reference_id(stripe_invoice)
    if not stripe_id.startswith("in_"):
        raise StripeInvoiceRevisionError("Stripe invoice has an invalid invoice ID.")

    metadata = stripe_value(stripe_invoice, "metadata", {}) or {}
    expected = {
        "realestate_invoice_number": local_invoice.invoice_number,
        "realestate_enquiry_id": str(local_invoice.enquiry_id),
        "payment_purpose": f"realestate_{local_invoice.invoice_type}",
    }
    for key, expected_value in expected.items():
        if str(metadata.get(key) or "").strip() != expected_value:
            raise StripeInvoiceRevisionError(
                f"Stripe invoice {key} did not match the local invoice."
            )

    currency = str(stripe_value(stripe_invoice, "currency", "") or "").upper()
    if currency != local_invoice.currency:
        raise StripeInvoiceRevisionError(
            "Stripe invoice currency did not match the local invoice."
        )
    expected_total = int(Decimal(local_invoice.total) * Decimal("100"))
    if _integer_value(stripe_invoice, "total") != expected_total:
        raise StripeInvoiceRevisionError(
            "Stripe invoice total did not match the local invoice."
        )
    amount_due = _integer_value(stripe_invoice, "amount_due")
    stripe_status = str(stripe_value(stripe_invoice, "status", "") or "").lower()
    permitted_amounts_due = {expected_total}
    if stripe_status == "void":
        permitted_amounts_due.add(0)
    if amount_due not in permitted_amounts_due:
        raise StripeInvoiceRevisionError(
            "Stripe invoice amount due did not match the local invoice."
        )
    livemode = stripe_value(stripe_invoice, "livemode")
    if not isinstance(livemode, bool) or livemode != configured_stripe_livemode():
        raise StripeInvoiceRevisionError(
            "Stripe invoice environment did not match the configured environment."
        )
    return stripe_id


def _retrieve_invoice(stripe_id):
    if not stripe_id.startswith("in_"):
        raise StripeInvoiceRevisionError("Stripe revision contains an invalid invoice ID.")
    return stripe.Invoice.retrieve(stripe_id)


def _load_reference(value):
    stripe_id = stripe_reference_id(value)
    if not stripe_id:
        raise StripeInvoiceRevisionError("Stripe revision contains a missing invoice reference.")
    if isinstance(value, str):
        return _retrieve_invoice(stripe_id)
    return value


def _from_invoice(stripe_invoice):
    from_invoice = stripe_value(stripe_invoice, "from_invoice", {}) or {}
    return (
        str(stripe_value(from_invoice, "action", "") or "").strip(),
        stripe_value(from_invoice, "invoice"),
    )


def _assert_revision_parent(local_invoice, parent, child):
    parent_id = validate_stripe_invoice(local_invoice, parent)
    validate_stripe_invoice(local_invoice, child)
    action, parent_reference = _from_invoice(child)
    if action != "revision" or stripe_reference_id(parent_reference) != parent_id:
        raise StripeInvoiceRevisionError(
            "Stripe invoice did not identify the expected revision parent."
        )


def _backward_chain_to_id(local_invoice, child, target_id, *, max_depth):
    child_id = validate_stripe_invoice(local_invoice, child)
    visited = {child_id}
    reverse_chain = [child]
    for _ in range(max_depth):
        action, parent_reference = _from_invoice(child)
        parent_id = stripe_reference_id(parent_reference)
        if action != "revision" or not parent_id:
            return None
        if parent_id in visited:
            raise StripeInvoiceRevisionError("Stripe invoice revision chain contains a cycle.")
        parent = _load_reference(parent_reference)
        _assert_revision_parent(local_invoice, parent, child)
        reverse_chain.append(parent)
        visited.add(parent_id)
        if parent_id == target_id:
            return list(reversed(reverse_chain))
        child = parent
    raise StripeInvoiceRevisionError(
        f"Stripe invoice revision chain exceeds {max_depth} revisions."
    )


def follow_latest_revisions(local_invoice, start, *, max_depth=MAX_REVISION_DEPTH):
    current = start
    current_id = validate_stripe_invoice(local_invoice, current)
    chain = [current]
    visited = {current_id}
    traversed = 0
    while traversed < max_depth:
        latest_reference = stripe_value(current, "latest_revision")
        latest_id = stripe_reference_id(latest_reference)
        if not latest_id:
            return chain
        if latest_id in visited:
            raise StripeInvoiceRevisionError("Stripe invoice revision chain contains a cycle.")
        latest = _load_reference(latest_reference)
        segment = _backward_chain_to_id(
            local_invoice,
            latest,
            current_id,
            max_depth=max_depth - traversed,
        )
        if not segment:
            raise StripeInvoiceRevisionError(
                "Stripe latest revision was not descended from the expected invoice."
            )
        for revision in segment[1:]:
            revision_id = validate_stripe_invoice(local_invoice, revision)
            if revision_id in visited:
                raise StripeInvoiceRevisionError(
                    "Stripe invoice revision chain contains a cycle."
                )
            chain.append(revision)
            visited.add(revision_id)
            traversed += 1
            if traversed > max_depth:
                raise StripeInvoiceRevisionError(
                    f"Stripe invoice revision chain exceeds {max_depth} revisions."
                )
        current = latest
        current_id = latest_id
    if stripe_reference_id(stripe_value(current, "latest_revision")):
        raise StripeInvoiceRevisionError(
            f"Stripe invoice revision chain exceeds {max_depth} revisions."
        )
    return chain


def _backward_chain_to_stored(local_invoice, incoming, stored_id):
    return _backward_chain_to_id(
        local_invoice,
        incoming,
        stored_id,
        max_depth=MAX_REVISION_DEPTH,
    )


def resolve_invoice_event(local_invoice, incoming):
    stored_id = str(local_invoice.stripe_invoice_id or "").strip()
    if not stored_id:
        raise StripeInvoiceRevisionError(
            "The local invoice has no stored Stripe invoice to anchor a revision."
        )
    incoming_id = validate_stripe_invoice(local_invoice, incoming)

    if incoming_id == stored_id:
        forward = follow_latest_revisions(local_invoice, incoming)
        relation = "descendant" if len(forward) > 1 else "same"
        return RevisionResolution(
            current=forward[-1],
            relation=relation,
            chain_ids=tuple(validate_stripe_invoice(local_invoice, item) for item in forward),
        )

    backward = _backward_chain_to_stored(local_invoice, incoming, stored_id)
    if backward:
        forward_from_incoming = follow_latest_revisions(local_invoice, incoming)
        combined = backward[:-1] + forward_from_incoming
        return RevisionResolution(
            current=combined[-1],
            relation="descendant",
            chain_ids=tuple(validate_stripe_invoice(local_invoice, item) for item in combined),
        )

    forward = follow_latest_revisions(local_invoice, incoming)
    forward_ids = [validate_stripe_invoice(local_invoice, item) for item in forward]
    if stored_id in forward_ids:
        return RevisionResolution(
            current=forward[-1],
            relation="ancestor",
            chain_ids=tuple(forward_ids),
        )
    raise StripeInvoiceRevisionError(
        "Stripe invoice was unrelated to the stored local Stripe invoice."
    )


def stripe_datetime(timestamp):
    try:
        timestamp = int(timestamp)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(timestamp, tz=dt_timezone.utc) if timestamp else None


def was_manually_voided(local_invoice):
    return local_invoice.enquiry.timeline_events.filter(
        title="Invoice voided",
        notes__contains=local_invoice.invoice_number,
    ).exists()


def apply_revision_snapshot(local_invoice, stripe_invoice, *, record_audit=True):
    new_id = validate_stripe_invoice(local_invoice, stripe_invoice)
    old_id = str(local_invoice.stripe_invoice_id or "").strip()
    old_status = str(local_invoice.stripe_invoice_status or "").strip()
    fields = []
    values = {
        "stripe_invoice_id": new_id,
        "stripe_invoice_number": str(stripe_value(stripe_invoice, "number", "") or ""),
        "stripe_hosted_invoice_url": str(
            stripe_value(stripe_invoice, "hosted_invoice_url", "") or ""
        ),
        "stripe_invoice_pdf_url": str(stripe_value(stripe_invoice, "invoice_pdf", "") or ""),
        "stripe_invoice_status": str(stripe_value(stripe_invoice, "status", "") or ""),
    }
    created_at = stripe_datetime(stripe_value(stripe_invoice, "created"))
    if created_at:
        values["stripe_invoice_created_at"] = created_at
    transitions = stripe_value(stripe_invoice, "status_transitions", {}) or {}
    finalized_at = stripe_datetime(stripe_value(transitions, "finalized_at"))
    if finalized_at:
        values["stripe_invoice_finalized_at"] = finalized_at
    for field, value in values.items():
        if getattr(local_invoice, field) != value:
            setattr(local_invoice, field, value)
            fields.append(field)

    restored = False
    if (
        values["stripe_invoice_status"] == "open"
        and local_invoice.status == RealEstateInvoice.Status.VOID
        and not local_invoice.payments.filter(status=RealEstatePayment.Status.SUCCEEDED).exists()
        and not was_manually_voided(local_invoice)
    ):
        local_invoice.status = RealEstateInvoice.Status.ISSUED
        fields.append("status")
        restored = True

    if fields:
        local_invoice.save(update_fields=tuple(dict.fromkeys(fields + ["updated_at"])))
    revision_changed = bool(old_id and old_id != new_id)
    if record_audit and (revision_changed or restored):
        record_timeline_event(
            local_invoice.enquiry,
            RealEstateTimelineEvent.EventType.NOTE,
            title="Stripe invoice revision reconciled",
            notes=(
                f"Stripe invoice {old_id or 'missing'} ({old_status or 'unknown'}) was "
                f"superseded by {new_id} ({values['stripe_invoice_status'] or 'unknown'})."
            ),
        )
        logger.info(
            "Reconciled real estate Stripe invoice revision. invoice_id=%s "
            "enquiry_id=%s old_stripe_invoice_id=%s new_stripe_invoice_id=%s",
            local_invoice.pk,
            local_invoice.enquiry_id,
            old_id or "missing",
            new_id,
        )
    return bool(fields), revision_changed, restored


@transaction.atomic
def reconcile_stored_invoice_revision(local_invoice, *, record_audit=True):
    local_invoice = (
        RealEstateInvoice.objects.select_for_update()
        .select_related("enquiry")
        .get(pk=local_invoice.pk)
    )
    chain = inspect_stored_invoice_revision(local_invoice)
    apply_revision_snapshot(local_invoice, chain[-1], record_audit=record_audit)
    return local_invoice, chain


def inspect_stored_invoice_revision(local_invoice):
    configure_stripe()
    stored_id = str(local_invoice.stripe_invoice_id or "").strip()
    if not stored_id:
        raise StripeInvoiceRevisionError("The local invoice has no stored Stripe invoice.")
    stored = _retrieve_invoice(stored_id)
    return follow_latest_revisions(local_invoice, stored)
