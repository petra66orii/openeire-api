from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, time

import stripe
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from openeire_api.business_identity import get_business_identity

from .emails import get_realestate_site_url
from .models import (
    RealEstateDeliveryOverride,
    RealEstateDocumentSequence,
    RealEstateEnquiry,
    RealEstateInvoice,
    RealEstatePayment,
    RealEstateTimelineEvent,
)
from .payments import calculate_realestate_deposit_amounts
from .timeline import record_timeline_event


MONEY = Decimal("0.01")


def money(value):
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def allocate_document_number(kind, *, at=None):
    year = (at or timezone.now()).year
    prefix = "OE-RE" if kind == RealEstateDocumentSequence.Kind.INVOICE else "OE-RC"
    with transaction.atomic():
        sequence, _ = RealEstateDocumentSequence.objects.select_for_update().get_or_create(
            kind=kind, year=year, defaults={"next_value": 1}
        )
        value = sequence.next_value
        sequence.next_value += 1
        sequence.save(update_fields=("next_value",))
    return f"{prefix}-{year}-{value:04d}"


def _invoice_amounts(enquiry, invoice_type):
    snapshot = calculate_realestate_deposit_amounts(enquiry)
    if invoice_type == RealEstateInvoice.InvoiceType.DEPOSIT:
        total = money(snapshot["deposit_amount"])
    elif invoice_type == RealEstateInvoice.InvoiceType.BALANCE:
        total = money(snapshot["balance_due"])
    else:
        total = money(snapshot["total_including_vat"])

    vat_rate = Decimal(snapshot["vat_rate"])
    if snapshot["vat_registered"] and vat_rate:
        subtotal = money(total / (Decimal("1") + vat_rate))
        vat_amount = money(total - subtotal)
    else:
        subtotal = total
        vat_amount = Decimal("0.00")
    return subtotal, vat_rate, vat_amount, total


@transaction.atomic
def ensure_realestate_invoice(enquiry, invoice_type, *, issue=True):
    enquiry = RealEstateEnquiry.objects.select_for_update().get(pk=enquiry.pk)
    existing = RealEstateInvoice.objects.filter(
        enquiry=enquiry, invoice_type=invoice_type
    ).exclude(status=RealEstateInvoice.Status.VOID).first()
    if existing:
        return existing, False

    subtotal, vat_rate, vat_amount, total = _invoice_amounts(enquiry, invoice_type)
    now = timezone.now()
    package = enquiry.get_preferred_package_display()
    descriptions = {
        RealEstateInvoice.InvoiceType.DEPOSIT: f"30% booking deposit — {package} Real Estate Media Package",
        RealEstateInvoice.InvoiceType.BALANCE: f"Final balance — {package} Real Estate Media Package",
        RealEstateInvoice.InvoiceType.FULL: f"Full payment — {package} Real Estate Media Package",
    }
    due_date = enquiry.payment_due_date
    if (
        invoice_type == RealEstateInvoice.InvoiceType.FULL
        and enquiry.payment_arrangement == RealEstateEnquiry.PaymentArrangement.FULL_ON_SHOOT_DAY
        and not due_date
    ):
        due_date = enquiry.shoot_date
    due_at = (
        timezone.make_aware(datetime.combine(due_date, time.min))
        if due_date else now
    )
    invoice = RealEstateInvoice.objects.create(
        enquiry=enquiry,
        invoice_type=invoice_type,
        invoice_number=allocate_document_number(RealEstateDocumentSequence.Kind.INVOICE, at=now),
        status=RealEstateInvoice.Status.ISSUED if issue else RealEstateInvoice.Status.DRAFT,
        currency="EUR",
        description=descriptions.get(invoice_type, enquiry.custom_payment_terms or "Real estate adjustment"),
        subtotal=subtotal,
        vat_rate=vat_rate,
        vat_amount=vat_amount,
        total=total,
        customer_name_snapshot=enquiry.name,
        company_name_snapshot=enquiry.company_name,
        customer_email_snapshot=enquiry.email,
        customer_phone_snapshot=enquiry.phone,
        property_reference_snapshot=enquiry.property_address,
        job_reference_snapshot=f"RE-{enquiry.pk}",
        issued_at=now if issue else None,
        due_at=due_at if issue else None,
    )
    if issue:
        record_timeline_event(
            enquiry,
            RealEstateTimelineEvent.EventType.INVOICE_ISSUED,
            title=f"{invoice.get_invoice_type_display()} invoice issued",
            notes=f"Invoice {invoice.invoice_number} issued for EUR {invoice.total}.",
        )
    return invoice, True


def ensure_standard_realestate_invoices(enquiry):
    if enquiry.payment_arrangement != RealEstateEnquiry.PaymentArrangement.DEPOSIT_THEN_BALANCE:
        raise ValidationError("Deposit and balance invoices are not valid for this payment arrangement.")
    deposit, _ = ensure_realestate_invoice(enquiry, RealEstateInvoice.InvoiceType.DEPOSIT)
    balance, _ = ensure_realestate_invoice(enquiry, RealEstateInvoice.InvoiceType.BALANCE)
    return deposit, balance


def ensure_invoices_for_arrangement(enquiry):
    if enquiry.payment_arrangement == RealEstateEnquiry.PaymentArrangement.DEPOSIT_THEN_BALANCE:
        return list(ensure_standard_realestate_invoices(enquiry))
    if enquiry.payment_arrangement in {
        RealEstateEnquiry.PaymentArrangement.FULL_UPFRONT,
        RealEstateEnquiry.PaymentArrangement.FULL_ON_SHOOT_DAY,
    }:
        invoice, _ = ensure_realestate_invoice(enquiry, RealEstateInvoice.InvoiceType.FULL)
        return [invoice]
    if enquiry.payment_arrangement == RealEstateEnquiry.PaymentArrangement.CUSTOM:
        if not enquiry.custom_payment_terms or not enquiry.custom_required_total:
            raise ValidationError("Custom terms and required total must be configured first.")
        return []
    raise ValidationError("Unsupported payment arrangement.")


def _successful_total(invoice):
    return invoice.payments.filter(status=RealEstatePayment.Status.SUCCEEDED).aggregate(
        value=Sum("amount")
    )["value"] or Decimal("0.00")


@transaction.atomic
def record_realestate_payment(
    *, invoice, amount, method, paid_at, recorded_by=None, status=RealEstatePayment.Status.SUCCEEDED,
    stripe_checkout_session_id="", stripe_payment_intent_id="", stripe_charge_id="",
    external_reference="", bank_lodgement_reference="", notes="",
):
    invoice = RealEstateInvoice.objects.select_for_update().select_related("enquiry").get(pk=invoice.pk)
    amount = money(amount)
    if amount <= 0:
        raise ValidationError("Payment amount must be greater than zero.")
    if invoice.status == RealEstateInvoice.Status.VOID:
        raise ValidationError("Payments cannot be recorded against a void invoice.")

    if stripe_checkout_session_id:
        existing = RealEstatePayment.objects.filter(
            stripe_checkout_session_id=stripe_checkout_session_id
        ).first()
        if existing:
            return existing, False
    if stripe_payment_intent_id:
        existing = RealEstatePayment.objects.filter(
            stripe_payment_intent_id=stripe_payment_intent_id
        ).first()
        if existing:
            return existing, False

    if status == RealEstatePayment.Status.SUCCEEDED:
        outstanding = money(invoice.total - _successful_total(invoice))
        if amount > outstanding:
            raise ValidationError("Payment exceeds the invoice amount outstanding.")
        was_release_ready = can_release_realestate_delivery(invoice.enquiry)
    else:
        was_release_ready = None

    receipt_number = ""
    if method == RealEstatePayment.Method.CASH and status == RealEstatePayment.Status.SUCCEEDED:
        receipt_number = allocate_document_number(
            RealEstateDocumentSequence.Kind.RECEIPT, at=paid_at
        )

    payment = RealEstatePayment.objects.create(
        invoice=invoice,
        amount=amount,
        currency=invoice.currency,
        method=method,
        status=status,
        paid_at=paid_at,
        stripe_checkout_session_id=stripe_checkout_session_id,
        stripe_payment_intent_id=stripe_payment_intent_id,
        stripe_charge_id=stripe_charge_id,
        external_reference=external_reference,
        cash_receipt_number=receipt_number,
        bank_lodgement_reference=bank_lodgement_reference,
        recorded_by=recorded_by,
        notes=notes,
    )
    if status == RealEstatePayment.Status.SUCCEEDED:
        record_timeline_event(
            invoice.enquiry,
            RealEstateTimelineEvent.EventType.PAYMENT_RECORDED,
            actor_type=(RealEstateTimelineEvent.ActorType.ADMIN if recorded_by else RealEstateTimelineEvent.ActorType.SYSTEM),
            title="Payment recorded",
            notes=f"EUR {payment.amount} recorded against {invoice.invoice_number}.",
            created_by=recorded_by,
            stripe_session_id=stripe_checkout_session_id,
        )
        _refresh_invoice_and_compatibility(
            invoice,
            paid_at=paid_at,
            actor=recorded_by,
            was_release_ready=was_release_ready,
        )
    return payment, True


def _refresh_invoice_and_compatibility(invoice, *, paid_at, actor=None, was_release_ready=None):
    paid = money(_successful_total(invoice))
    if was_release_ready is None:
        was_release_ready = can_release_realestate_delivery(invoice.enquiry)
    if paid >= invoice.total:
        was_paid = (
            RealEstateInvoice.objects.filter(pk=invoice.pk)
            .values_list("status", flat=True)
            .first()
            == RealEstateInvoice.Status.PAID
        )
        invoice.status = RealEstateInvoice.Status.PAID
        invoice.paid_at = invoice.paid_at or paid_at
        invoice.save(update_fields=("status", "paid_at", "updated_at"))
        if not was_paid:
            record_timeline_event(
                invoice.enquiry,
                RealEstateTimelineEvent.EventType.INVOICE_PAID,
                title="Invoice paid in full",
                notes=f"Invoice {invoice.invoice_number} paid in full.",
                created_by=actor,
            )
        if invoice.invoice_type == RealEstateInvoice.InvoiceType.DEPOSIT:
            enquiry = invoice.enquiry
            enquiry.deposit_paid = True
            enquiry.deposit_paid_at = enquiry.deposit_paid_at or paid_at
            stripe_payment = invoice.payments.filter(
                status=RealEstatePayment.Status.SUCCEEDED
            ).exclude(stripe_checkout_session_id="").first()
            fields = ["deposit_paid", "deposit_paid_at", "updated_at"]
            if stripe_payment and not enquiry.stripe_deposit_session_id:
                enquiry.stripe_deposit_session_id = stripe_payment.stripe_checkout_session_id
                fields.append("stripe_deposit_session_id")
            if enquiry.status not in {
                RealEstateEnquiry.Status.BOOKED, RealEstateEnquiry.Status.COMPLETED,
                RealEstateEnquiry.Status.CLOSED, RealEstateEnquiry.Status.SPAM,
            }:
                enquiry.status = RealEstateEnquiry.Status.BOOKED
                fields.append("status")
            enquiry.save(update_fields=fields)
        elif (
            invoice.invoice_type == RealEstateInvoice.InvoiceType.FULL
            and invoice.enquiry.payment_arrangement == RealEstateEnquiry.PaymentArrangement.FULL_UPFRONT
        ):
            enquiry = invoice.enquiry
            if enquiry.status not in {
                RealEstateEnquiry.Status.BOOKED, RealEstateEnquiry.Status.COMPLETED,
                RealEstateEnquiry.Status.CLOSED, RealEstateEnquiry.Status.SPAM,
            }:
                enquiry.status = RealEstateEnquiry.Status.BOOKED
                enquiry.save(update_fields=("status", "updated_at"))
    elif paid > 0:
        invoice.status = RealEstateInvoice.Status.PARTIALLY_PAID
        invoice.save(update_fields=("status", "updated_at"))

    if not was_release_ready and can_release_realestate_delivery(invoice.enquiry):
        record_timeline_event(
            invoice.enquiry,
            RealEstateTimelineEvent.EventType.DELIVERY_READY,
            title="Delivery ready",
            notes="All required issued invoices are paid in full.",
        )


def can_release_realestate_delivery(enquiry):
    invoices = enquiry.invoices.exclude(status=RealEstateInvoice.Status.VOID).filter(
        status__in=(
            RealEstateInvoice.Status.ISSUED,
            RealEstateInvoice.Status.PARTIALLY_PAID,
            RealEstateInvoice.Status.PAID,
            RealEstateInvoice.Status.OVERDUE,
        )
    )
    has_final_invoice = invoices.filter(
        invoice_type__in=(RealEstateInvoice.InvoiceType.BALANCE, RealEstateInvoice.InvoiceType.FULL)
    ).exists()
    if enquiry.payment_arrangement == RealEstateEnquiry.PaymentArrangement.CUSTOM:
        required = enquiry.custom_required_total or Decimal("0")
        invoiced = sum((invoice.total for invoice in invoices), Decimal("0"))
        has_final_invoice = bool(invoices.exists() and invoiced >= required)
    if has_final_invoice and invoices.exists() and all(invoice.amount_outstanding == 0 for invoice in invoices):
        return True
    return enquiry.delivery_overrides.filter(revoked_at__isnull=True).exists()


@transaction.atomic
def grant_delivery_override(enquiry, *, user, reason):
    if not user or not user.is_staff:
        raise PermissionDenied("Staff permission is required.")
    reason = str(reason or "").strip()
    if not reason:
        raise ValidationError("An override reason is required.")
    override = RealEstateDeliveryOverride.objects.create(
        enquiry=enquiry, reason=reason, created_by=user
    )
    record_timeline_event(
        enquiry, RealEstateTimelineEvent.EventType.DELIVERY_OVERRIDE_GRANTED,
        actor_type=RealEstateTimelineEvent.ActorType.ADMIN,
        title="Delivery override granted", notes=reason, created_by=user,
    )
    return override


@transaction.atomic
def revoke_delivery_override(override, *, user, reason):
    if not user or not user.is_staff:
        raise PermissionDenied("Staff permission is required.")
    reason = str(reason or "").strip()
    if not reason:
        raise ValidationError("A revocation reason is required.")
    override = RealEstateDeliveryOverride.objects.select_for_update().get(pk=override.pk)
    if override.revoked_at:
        return override
    override.revoked_by = user
    override.revoked_at = timezone.now()
    override.revocation_reason = reason
    override.save(update_fields=("revoked_by", "revoked_at", "revocation_reason"))
    record_timeline_event(
        override.enquiry, RealEstateTimelineEvent.EventType.DELIVERY_OVERRIDE_REVOKED,
        actor_type=RealEstateTimelineEvent.ActorType.ADMIN,
        title="Delivery override revoked", notes=reason, created_by=user,
    )
    return override


def create_realestate_balance_checkout_session(enquiry):
    _, invoice = ensure_standard_realestate_invoices(enquiry)
    outstanding = money(invoice.amount_outstanding)
    if outstanding <= 0:
        raise ValidationError("The balance invoice is already paid.")
    amount_cents = int(outstanding * 100)
    identity = get_business_identity()
    metadata = {
        "realestate_enquiry_id": str(enquiry.pk),
        "realestate_invoice_number": invoice.invoice_number,
        "job_reference": invoice.job_reference_snapshot,
        "package_reference": enquiry.preferred_package,
        "purpose": "realestate_balance",
        "brand": identity.display_name,
    }
    stripe.api_key = settings.STRIPE_SECRET_KEY
    session = stripe.checkout.Session.create(
        mode="payment",
        payment_method_types=getattr(settings, "STRIPE_PAYMENT_METHOD_TYPES", ["card"]),
        line_items=[{"price_data": {"currency": "eur", "unit_amount": amount_cents, "product_data": {"name": f"{identity.display_name} real estate balance - {invoice.invoice_number}", "metadata": metadata}}, "quantity": 1}],
        metadata=metadata,
        payment_intent_data={"metadata": metadata},
        customer_email=enquiry.email,
        success_url=f"{get_realestate_site_url().rstrip('/')}/real-estate/balance/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{get_realestate_site_url().rstrip('/')}/real-estate/balance/cancelled",
        idempotency_key=f"realestate-balance-{invoice.invoice_number}-{amount_cents}",
    )
    invoice.stripe_checkout_session_id = str(getattr(session, "id", "") or session.get("id", ""))
    invoice.stripe_checkout_url = str(getattr(session, "url", "") or session.get("url", ""))
    invoice.save(update_fields=("stripe_checkout_session_id", "stripe_checkout_url", "updated_at"))
    return invoice.stripe_checkout_url
