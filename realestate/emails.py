import logging
import os
from decimal import Decimal
from decimal import InvalidOperation
from decimal import ROUND_HALF_UP
from urllib.parse import urljoin
from urllib.parse import urlparse

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from openeire_api.business_identity import get_business_identity, public_business_context
from django.core.mail import EmailMessage
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.urls import reverse

from openeire_api.mail_utils import get_default_from_email
from .models import RealEstateEnquiry, RealEstateInvoice, RealEstatePayment


logger = logging.getLogger(__name__)


def get_realestate_notification_email():
    return str(
        getattr(settings, "REALESTATE_NOTIFICATION_EMAIL", "shoots@openeire.ie")
        or "shoots@openeire.ie"
    ).strip()


def get_realestate_reply_to_email():
    return str(
        getattr(settings, "REALESTATE_REPLY_TO_EMAIL", "shoots@openeire.ie")
        or "shoots@openeire.ie"
    ).strip()


def get_realestate_site_url():
    return str(
        getattr(settings, "REALESTATE_SITE_URL", None)
        or getattr(settings, "SITE_URL", None)
        or os.getenv("SITE_URL")
        or "https://api.openeire.ie"
    ).strip()


def get_realestate_api_url():
    configured = str(
        getattr(settings, "REALESTATE_API_URL", None)
        or os.getenv("REALESTATE_API_URL")
        or getattr(settings, "SITE_URL", None)
        or os.getenv("SITE_URL")
        or ""
    ).strip()
    if not configured and (
        getattr(settings, "DEBUG", False) or getattr(settings, "IS_TEST_ENV", False)
    ):
        configured = "http://localhost:8000"
    if not _is_public_absolute_url(configured):
        raise ImproperlyConfigured(
            "REALESTATE_API_URL or SITE_URL must be configured as an absolute "
            "public API URL."
        )
    return configured.rstrip("/")


def build_absolute_site_url(path):
    return urljoin(get_realestate_site_url().rstrip("/") + "/", str(path).lstrip("/"))


def _is_public_absolute_url(value):
    parsed = urlparse(str(value or "").strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def get_realestate_email_logo_url():
    configured_url = str(
        getattr(settings, "REALESTATE_EMAIL_LOGO_URL", "")
        or getattr(settings, "EMAIL_LOGO_URL", "")
        or ""
    ).strip()
    if _is_public_absolute_url(configured_url):
        return configured_url

    site_url = get_realestate_site_url()
    if _is_public_absolute_url(site_url):
        return build_absolute_site_url(
            static("emails/openeire-studios-logo.png")
        )
    return ""


def format_money(value):
    if value is None:
        return ""

    if isinstance(value, str):
        cleaned_value = value.strip()
        if not cleaned_value or cleaned_value.lower() in {"none", "null"}:
            return ""
        numeric_value = cleaned_value.replace("€", "").replace(",", "").strip()
        if numeric_value.upper().startswith("EUR "):
            numeric_value = numeric_value[4:].strip()
    else:
        numeric_value = str(value).strip()

    try:
        amount = Decimal(numeric_value).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
    except (InvalidOperation, ValueError):
        return ""

    return f"€{amount:,.2f}"


def build_realestate_admin_url(enquiry, request=None):
    admin_path = reverse(
        "customadmin:realestate_realestateenquiry_change",
        args=[enquiry.id],
    )
    admin_base_url = (
        getattr(settings, "REALESTATE_ADMIN_BASE_URL", None)
        or getattr(settings, "SITE_URL", None)
        or os.getenv("SITE_URL")
    )
    if admin_base_url:
        return urljoin(str(admin_base_url).rstrip("/") + "/", admin_path.lstrip("/"))
    if request is not None:
        return request.build_absolute_uri(admin_path)
    return admin_path


def _format_date(value):
    if not value:
        return "Not specified"
    return value if isinstance(value, str) else value.isoformat()


def _format_display_date(value):
    if not value:
        return ""
    if isinstance(value, str):
        return value
    return value.strftime("%d %B %Y")


def _format_display_time(value):
    if not value:
        return ""
    if isinstance(value, str):
        return value
    return value.strftime("%H:%M")


def _active_invoices(enquiry):
    if not getattr(enquiry, "pk", None):
        return []
    return list(
        enquiry.invoices.exclude(status=RealEstateInvoice.Status.VOID)
        .prefetch_related("payments")
        .order_by("created_at")
    )


def _invoice_by_type(invoices, invoice_type):
    return next((invoice for invoice in invoices if invoice.invoice_type == invoice_type), None)


def _email_financial_context(enquiry):
    arrangement = getattr(
        enquiry,
        "payment_arrangement",
        RealEstateEnquiry.PaymentArrangement.DEPOSIT_THEN_BALANCE,
    )
    invoices = _active_invoices(enquiry)
    deposit_invoice = _invoice_by_type(invoices, RealEstateInvoice.InvoiceType.DEPOSIT)
    balance_invoice = _invoice_by_type(invoices, RealEstateInvoice.InvoiceType.BALANCE)
    full_invoice = _invoice_by_type(invoices, RealEstateInvoice.InvoiceType.FULL)
    invoice = full_invoice or balance_invoice or deposit_invoice or (invoices[0] if invoices else None)

    total_required = (
        getattr(enquiry, "custom_required_total", None)
        if arrangement == RealEstateEnquiry.PaymentArrangement.CUSTOM
        else None
    ) or (full_invoice.total if full_invoice else None) or getattr(enquiry, "quoted_total", None) or getattr(enquiry, "quoted_price", None)
    deposit_amount = deposit_invoice.total if deposit_invoice else getattr(enquiry, "quoted_deposit_amount", None)
    balance_due = balance_invoice.total if balance_invoice else getattr(enquiry, "quoted_balance_due", None)
    if arrangement != RealEstateEnquiry.PaymentArrangement.DEPOSIT_THEN_BALANCE:
        deposit_amount = None
        balance_due = None

    due_date = getattr(enquiry, "payment_due_date", None)
    if not due_date and arrangement == RealEstateEnquiry.PaymentArrangement.FULL_ON_SHOOT_DAY:
        due_date = getattr(enquiry, "shoot_date", None)
    if not due_date and invoice and invoice.due_at:
        due_date = invoice.due_at.date()

    latest_cash_receipt = ""
    if invoice:
        cash_payment = (
            RealEstatePayment.objects.filter(
                invoice__enquiry=enquiry,
                cash_receipt_number__gt="",
            )
            .order_by("-paid_at", "-created_at")
            .first()
        )
        latest_cash_receipt = cash_payment.cash_receipt_number if cash_payment else ""

    if arrangement == RealEstateEnquiry.PaymentArrangement.DEPOSIT_THEN_BALANCE:
        booking_confirmation_rule = "Booking is confirmed after the signed agreement and cleared deposit are received."
        quote_payment_rule = "A 30% deposit secures the booking, with the remaining balance due under the agreed terms."
    elif arrangement == RealEstateEnquiry.PaymentArrangement.FULL_UPFRONT:
        booking_confirmation_rule = "Booking is confirmed after the signed agreement and full payment are received."
        quote_payment_rule = "Full payment is required before booking confirmation; no deposit or balance split applies."
    elif arrangement == RealEstateEnquiry.PaymentArrangement.FULL_ON_SHOOT_DAY:
        booking_confirmation_rule = "Booking may be confirmed while unpaid under the approved full-payment-on-shoot-day arrangement."
        quote_payment_rule = "The full amount is due on the shoot date; final delivery remains locked until full payment is recorded."
    else:
        booking_confirmation_rule = str(getattr(enquiry, "custom_payment_terms", "") or "Approved custom terms apply.")
        quote_payment_rule = booking_confirmation_rule

    return {
        "payment_arrangement": arrangement,
        "payment_arrangement_label": (
            enquiry.get_payment_arrangement_display()
            if hasattr(enquiry, "get_payment_arrangement_display")
            else arrangement
        ),
        "is_deposit_then_balance": arrangement == RealEstateEnquiry.PaymentArrangement.DEPOSIT_THEN_BALANCE,
        "is_full_upfront": arrangement == RealEstateEnquiry.PaymentArrangement.FULL_UPFRONT,
        "is_full_on_shoot_day": arrangement == RealEstateEnquiry.PaymentArrangement.FULL_ON_SHOOT_DAY,
        "is_custom_payment": arrangement == RealEstateEnquiry.PaymentArrangement.CUSTOM,
        "total_required": total_required or "",
        "deposit_amount": deposit_amount or "",
        "balance_due": balance_due or "",
        "payment_due_date": _format_display_date(due_date),
        "expected_payment_method": (
            enquiry.get_expected_payment_method_display()
            if hasattr(enquiry, "get_expected_payment_method_display")
            else ""
        ),
        "custom_payment_terms": getattr(enquiry, "custom_payment_terms", "") or "",
        "booking_confirmation_rule": booking_confirmation_rule,
        "quote_payment_rule": quote_payment_rule,
        "invoice_number": invoice.invoice_number if invoice else "",
        "invoice_type": invoice.get_invoice_type_display() if invoice else "",
        "stripe_hosted_invoice_url": invoice.stripe_hosted_invoice_url if invoice else "",
        "outstanding_amount": invoice.amount_outstanding if invoice else total_required or "",
        "cash_receipt_number": latest_cash_receipt,
    }


def _as_recipient_list(value):
    if isinstance(value, str):
        value = [value]
    return [str(item).strip() for item in (value or []) if str(item).strip()]


def send_templated_email(
    subject,
    to,
    template_base,
    context,
    reply_to=None,
    attachments=None,
):
    template_name = str(template_base or "").strip().strip("/")
    if not template_name:
        raise ValueError("template_base is required.")
    if "/" not in template_name:
        template_name = f"emails/real_estate/{template_name}"

    logo_url = get_realestate_email_logo_url()
    email_context = {
        "brand_logo_url": logo_url,
        "email_logo_url": logo_url,
        "cta_url": "",
        "cta_label": "",
    }
    email_context.update(context or {})

    text_body = render_to_string(f"{template_name}.txt", email_context)
    html_body = render_to_string(f"{template_name}.html", email_context)

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=get_default_from_email(),
        to=_as_recipient_list(to),
        reply_to=_as_recipient_list(reply_to),
    )
    email.attach_alternative(html_body, "text/html")
    for attachment in attachments or []:
        if isinstance(attachment, tuple):
            email.attach(*attachment)
        else:
            email.attach(attachment)
    return email.send(fail_silently=False)


def build_realestate_email_context(enquiry, **overrides):
    name = str(getattr(enquiry, "name", "") or "").strip()
    first_name = name.split()[0] if name else "there"
    property_address = str(getattr(enquiry, "property_address", "") or "").strip()
    county = str(getattr(enquiry, "county", "") or "").strip()
    if property_address and county:
        property_address = f"{property_address}, {county}"

    reply_to_email = get_realestate_reply_to_email()
    logo_url = get_realestate_email_logo_url()
    quote_reply_mailto = f"mailto:{reply_to_email}" if reply_to_email else ""

    financial_context = _email_financial_context(enquiry)
    context = {
        **public_business_context(),
        **financial_context,
        "brand_logo_url": logo_url,
        "email_logo_url": logo_url,
        "cta_url": "",
        "cta_label": "",
        "first_name": first_name,
        "agency_name": getattr(enquiry, "company_name", "") or "",
        "company_name": getattr(enquiry, "company_name", "") or "",
        "property_address": property_address or "the property",
        "package_name": (
            enquiry.get_preferred_package_summary()
            if hasattr(enquiry, "get_preferred_package_summary")
            else ""
        ),
        "addons": enquiry.get_add_on_labels()
        if hasattr(enquiry, "get_add_on_labels")
        else [],
        "quote_total": getattr(enquiry, "quoted_subtotal", None) or getattr(enquiry, "quoted_price", None) or "",
        "vat_total": "",
        "total_including_vat": financial_context["total_required"],
        "vat_registered": False,
        "vat_rate_percent": Decimal("0.00"),
        "price_input_is_gross": True,
        "vat_notice": "VAT not applicable — supplier not VAT registered",
        "shoot_date": _format_date(getattr(enquiry, "shoot_date", None)),
        "shoot_time": _format_display_time(getattr(enquiry, "shoot_time", None)),
        "booking_reference": (
            f"RE-{getattr(enquiry, 'id', '')}"
            if getattr(enquiry, "id", None)
            else ""
        ),
        "delivery_link": "",
        "review_link": "",
        "new_date": "",
        "deposit_payment_link": "",
        "booking_agreement_link": "",
        "reply_to_email": reply_to_email,
        "quote_reply_email": reply_to_email,
        "quote_reply_mailto": quote_reply_mailto,
        "quote_reply_url": quote_reply_mailto,
    }
    context.update(overrides)

    for money_field in (
        "quote_total",
        "vat_total",
        "total_including_vat",
        "total_required",
        "deposit_amount",
        "balance_due",
        "outstanding_amount",
    ):
        context[money_field] = format_money(context.get(money_field))

    return context


def send_realestate_internal_notification_email(enquiry, request=None):
    subject = (
        f"New Property Shoot Enquiry - {enquiry.county} - "
        f"{enquiry.get_preferred_package_display()}"
    )
    body = (
        "New property shoot enquiry received.\n\n"
        "Contact\n"
        f"Name: {enquiry.name}\n"
        f"Email: {enquiry.email}\n"
        f"Phone: {enquiry.phone}\n"
        f"Agency / company: {enquiry.company_name or 'Not provided'}\n"
        f"Client type: {enquiry.get_client_type_display()}\n"
        f"How heard: {enquiry.get_how_heard_display() or 'Not provided'}\n\n"
        "Shoot request\n"
        f"Package: {enquiry.get_preferred_package_summary()}\n"
        f"Add-ons: {enquiry.get_add_ons_summary()}\n"
        f"Property type: {enquiry.property_type}\n"
        f"Address: {enquiry.property_address}, {enquiry.county}\n"
        f"Eircode: {enquiry.eircode or 'Not provided'}\n"
        f"Preferred date: {_format_date(enquiry.preferred_date)}\n\n"
        "Message / notes\n"
        f"{enquiry.message or 'No message provided.'}\n\n"
        "Pipeline\n"
        f"Status: {enquiry.get_status_display()}\n"
        f"Quoted price: {enquiry.quoted_price if enquiry.quoted_price is not None else 'Not quoted'}\n"
        f"Confirmed shoot date: {_format_date(enquiry.shoot_date)}\n"
        f"Submitted at: {enquiry.created_at.isoformat()}\n\n"
        "View in admin:\n"
        f"{build_realestate_admin_url(enquiry, request=request)}\n"
    )
    email = EmailMessage(
        subject=subject,
        body=body,
        from_email=get_default_from_email(),
        to=[get_realestate_notification_email()],
        reply_to=[get_realestate_reply_to_email()],
    )
    email.send(fail_silently=False)


def send_realestate_client_confirmation_email(enquiry):
    subject = f"Property shoot request received - {get_business_identity().display_name}"
    return send_templated_email(
        subject=subject,
        to=[enquiry.email],
        template_base="enquiry_reply",
        context=build_realestate_email_context(
            enquiry,
            shoot_date=_format_date(enquiry.preferred_date),
        ),
        reply_to=[get_realestate_reply_to_email()],
    )
