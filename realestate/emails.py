import logging
import os
from urllib.parse import urljoin

from django.conf import settings
from django.core.mail import EmailMessage
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
    subject = "Property Shoot Request Received - Open\u00C9ire Studios"
    body = (
        f"Hi {enquiry.name},\n\n"
        "Thanks for getting in touch with Open\u00C9ire Studios.\n\n"
        f"We've received your property shoot request for {enquiry.property_address} "
        "and will review the details, requested package and preferred date.\n\n"
        "We'll come back to you within 24 hours to confirm the next steps.\n\n"
        "Request summary:\n"
        f"Package: {enquiry.get_preferred_package_summary()}\n"
        f"Preferred date: {_format_date(enquiry.preferred_date)}\n"
        f"Property address: {enquiry.property_address}, {enquiry.county}\n\n"
        "If you have any questions in the meantime, you can simply reply to this email.\n\n"
        "Open\u00C9ire Studios\n"
        "shoots@openeire.ie\n"
    )
    email = EmailMessage(
        subject=subject,
        body=body,
        from_email=get_default_from_email(),
        to=[enquiry.email],
        reply_to=[get_realestate_reply_to_email()],
    )
    email.send(fail_silently=False)
