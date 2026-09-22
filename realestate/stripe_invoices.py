from decimal import Decimal

import stripe
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from openeire_api.business_identity import get_business_identity

from .invoice_line_items import get_invoice_line_items
from .models import RealEstateInvoice
from .stripe_invoice_revisions import (
    StripeInvoiceRevisionError,
    reconcile_stored_invoice_revision,
)


def _value(obj, key, default=""):
    return getattr(obj, key, None) or (obj.get(key, default) if hasattr(obj, "get") else default)


def _stripe_metadata(invoice):
    return {
        "realestate_enquiry_id": str(invoice.enquiry_id),
        "realestate_invoice_number": invoice.invoice_number,
        "job_reference": invoice.job_reference_snapshot,
        "payment_purpose": f"realestate_{invoice.invoice_type}",
        "brand": get_business_identity().display_name,
    }


def _configure_stripe():
    stripe.api_key = settings.STRIPE_SECRET_KEY
    stripe.max_network_retries = getattr(settings, "STRIPE_MAX_NETWORK_RETRIES", 2)


def _frozen_stripe_payloads(local_invoice):
    enquiry = local_invoice.enquiry
    due_days = int(getattr(settings, "REALESTATE_STRIPE_INVOICE_DUE_DAYS", 7))
    if local_invoice.due_at:
        due_days = max(1, (local_invoice.due_at.date() - timezone.localdate()).days)
    metadata = _stripe_metadata(local_invoice)
    adjustment_descriptions = ", ".join(
        f"{item.customer_description}: -EUR {item.amount}"
        for item in enquiry.financial_adjustments.filter(
            reversed_at__isnull=True
        ).order_by("created_at")
    )
    adjustment_text = (
        f" Adjustments: {adjustment_descriptions}."
        if adjustment_descriptions
        else ""
    )
    item_payloads = []
    for item in get_invoice_line_items(local_invoice):
        unit_amount_cents = Decimal(item["unit_amount"]) * Decimal("100")
        item_payloads.append({
            "unit_amount_decimal": format(unit_amount_cents, "f"),
            "quantity": int(item.get("quantity") or 1),
            "currency": local_invoice.currency.lower(),
            "description": item["description"],
            "metadata": metadata,
        })
    return {
        "customer_id": enquiry.stripe_customer_id,
        "customer": {
            "email": local_invoice.customer_email_snapshot,
            "name": local_invoice.company_name_snapshot or local_invoice.customer_name_snapshot,
            "metadata": {
                "realestate_enquiry_id": str(enquiry.pk),
                "brand": get_business_identity().display_name,
            },
        },
        "create": {
            "collection_method": "send_invoice",
            "days_until_due": due_days,
            "auto_advance": False,
            "automatic_tax": {"enabled": False},
            "metadata": metadata,
            "custom_fields": [
                {"name": "OpenÉire invoice", "value": local_invoice.invoice_number}
            ],
            "description": (
                f"{local_invoice.description}. Property/job: {local_invoice.job_reference_snapshot}. "
                f"Original booking total: EUR {enquiry.original_required_total}."
                f"{adjustment_text} Balance requested: EUR {local_invoice.total}. "
                "VAT not applicable — supplier not VAT registered."
            ),
        },
        "items": item_payloads,
    }


def create_stripe_invoice(local_invoice, *, send=False):
    from .stripe_sync import resume

    _configure_stripe()
    return resume(local_invoice, stripe, _frozen_stripe_payloads, send=send)


def send_stripe_invoice(local_invoice):
    from .stripe_sync import resume

    _configure_stripe()
    local_invoice.refresh_from_db()
    if not local_invoice.stripe_invoice_id:
        return create_stripe_invoice(local_invoice, send=True)[0]
    # An ordinary retry resumes the same pending send. A deliberate reminder
    # after confirmed success gets a new send operation/key.
    return resume(
        local_invoice,
        stripe,
        _frozen_stripe_payloads,
        send=True,
        reminder=True,
    )[0]


@transaction.atomic
def mark_stripe_invoice_paid_out_of_band(local_invoice, *, user):
    if not user or not user.is_staff:
        raise PermissionDenied("Staff permission is required.")
    local_invoice = RealEstateInvoice.objects.select_for_update().get(pk=local_invoice.pk)
    if not local_invoice.stripe_invoice_id:
        raise ValidationError("This invoice has no Stripe invoice.")
    try:
        local_invoice, _chain = reconcile_stored_invoice_revision(local_invoice)
    except StripeInvoiceRevisionError as exc:
        raise ValidationError(str(exc)) from exc
    if local_invoice.amount_outstanding:
        raise ValidationError("Record the successful local payment before marking Stripe paid.")
    if local_invoice.stripe_marked_paid_out_of_band_at:
        return local_invoice
    _configure_stripe()
    stripe.Invoice.pay(local_invoice.stripe_invoice_id, paid_out_of_band=True)
    local_invoice.stripe_marked_paid_out_of_band_at = timezone.now()
    local_invoice.stripe_marked_paid_out_of_band_by = user
    local_invoice.stripe_invoice_status = "paid"
    local_invoice.save(update_fields=(
        "stripe_marked_paid_out_of_band_at",
        "stripe_marked_paid_out_of_band_by",
        "stripe_invoice_status",
        "updated_at",
    ))
    return local_invoice
