from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

import stripe
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from openeire_api.business_identity import get_business_identity

from .models import RealEstateInvoice


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


@transaction.atomic
def create_stripe_invoice(local_invoice, *, send=False):
    _configure_stripe()
    local_invoice = RealEstateInvoice.objects.select_for_update().select_related("enquiry").get(
        pk=local_invoice.pk
    )
    if local_invoice.status not in {
        RealEstateInvoice.Status.ISSUED,
        RealEstateInvoice.Status.PARTIALLY_PAID,
    }:
        raise ValidationError("Only issued unpaid invoices can be created in Stripe.")
    if local_invoice.stripe_invoice_id:
        return local_invoice, False

    enquiry = local_invoice.enquiry
    customer_id = enquiry.stripe_customer_id
    if not customer_id:
        customer = stripe.Customer.create(
            email=local_invoice.customer_email_snapshot,
            name=local_invoice.company_name_snapshot or local_invoice.customer_name_snapshot,
            metadata={
                "realestate_enquiry_id": str(enquiry.pk),
                "brand": get_business_identity().display_name,
            },
            idempotency_key=f"realestate-customer-{enquiry.pk}",
        )
        customer_id = str(_value(customer, "id"))
        enquiry.stripe_customer_id = customer_id
        enquiry.save(update_fields=("stripe_customer_id", "updated_at"))

    due_days = int(getattr(settings, "REALESTATE_STRIPE_INVOICE_DUE_DAYS", 7))
    if local_invoice.due_at:
        due_days = max(1, (local_invoice.due_at.date() - timezone.localdate()).days)
    metadata = _stripe_metadata(local_invoice)
    stripe_invoice = stripe.Invoice.create(
        customer=customer_id,
        collection_method="send_invoice",
        days_until_due=due_days,
        auto_advance=False,
        automatic_tax={"enabled": False},
        metadata=metadata,
        custom_fields=[{"name": "OpenÉire invoice", "value": local_invoice.invoice_number}],
        description=(
            f"{local_invoice.description}. Property/job: {local_invoice.job_reference_snapshot}. "
            f"Full package value: EUR {local_invoice.enquiry.quoted_total}. "
            "VAT not applicable — supplier not VAT registered."
        ),
        idempotency_key=f"realestate-stripe-invoice-{local_invoice.invoice_number}",
    )
    stripe_invoice_id = str(_value(stripe_invoice, "id"))
    stripe.InvoiceItem.create(
        customer=customer_id,
        invoice=stripe_invoice_id,
        amount=int(local_invoice.total * Decimal("100")),
        currency=local_invoice.currency.lower(),
        description=local_invoice.description,
        metadata=metadata,
        idempotency_key=f"realestate-stripe-item-{local_invoice.invoice_number}",
    )
    finalized = stripe.Invoice.finalize_invoice(
        stripe_invoice_id,
        idempotency_key=f"realestate-stripe-finalize-{local_invoice.invoice_number}",
    )
    result = stripe.Invoice.send_invoice(stripe_invoice_id) if send else finalized
    created_timestamp = int(_value(result, "created", 0) or 0)
    local_invoice.stripe_invoice_id = stripe_invoice_id
    local_invoice.stripe_invoice_number = str(_value(result, "number"))
    local_invoice.stripe_hosted_invoice_url = str(_value(result, "hosted_invoice_url"))
    local_invoice.stripe_invoice_pdf_url = str(_value(result, "invoice_pdf"))
    local_invoice.stripe_invoice_status = str(_value(result, "status", "open"))
    local_invoice.stripe_invoice_created_at = (
        datetime.fromtimestamp(created_timestamp, tz=dt_timezone.utc)
        if created_timestamp else timezone.now()
    )
    local_invoice.stripe_invoice_finalized_at = timezone.now()
    local_invoice.save(update_fields=(
        "stripe_invoice_id", "stripe_invoice_number", "stripe_hosted_invoice_url",
        "stripe_invoice_pdf_url", "stripe_invoice_status", "stripe_invoice_created_at",
        "stripe_invoice_finalized_at", "updated_at",
    ))
    return local_invoice, True


def send_stripe_invoice(local_invoice):
    if not local_invoice.stripe_invoice_id:
        return create_stripe_invoice(local_invoice, send=True)[0]
    _configure_stripe()
    result = stripe.Invoice.send_invoice(local_invoice.stripe_invoice_id)
    local_invoice.stripe_invoice_status = str(_value(result, "status", "open"))
    local_invoice.save(update_fields=("stripe_invoice_status", "updated_at"))
    return local_invoice


@transaction.atomic
def mark_stripe_invoice_paid_out_of_band(local_invoice, *, user):
    if not user or not user.is_staff:
        raise PermissionDenied("Staff permission is required.")
    local_invoice = RealEstateInvoice.objects.select_for_update().get(pk=local_invoice.pk)
    if not local_invoice.stripe_invoice_id:
        raise ValidationError("This invoice has no Stripe invoice.")
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
        "stripe_marked_paid_out_of_band_at", "stripe_marked_paid_out_of_band_by",
        "stripe_invoice_status", "updated_at",
    ))
    return local_invoice
