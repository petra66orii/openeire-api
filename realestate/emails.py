import logging
import os
from decimal import Decimal
from decimal import InvalidOperation
from decimal import ROUND_HALF_UP
from urllib.parse import urljoin
from urllib.parse import urlparse

from django.conf import settings
from django.core.mail import EmailMessage
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.urls import reverse

from openeire_api.mail_utils import get_default_from_email


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
    return value.isoformat() if value else "Not specified"


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

    context = {
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
        "quote_total": getattr(enquiry, "quoted_price", None) or "",
        "vat_total": "",
        "total_including_vat": "",
        "deposit_amount": "",
        "balance_due": "",
        "vat_registered": False,
        "vat_rate_percent": Decimal("0.00"),
        "price_input_is_gross": True,
        "vat_notice": "VAT not applicable — supplier not VAT registered",
        "shoot_date": _format_date(getattr(enquiry, "shoot_date", None)),
        "shoot_time": "",
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
        "deposit_amount",
        "balance_due",
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
    subject = "Property shoot request received - OpenEire Studios"
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
