from email.utils import formataddr, parseaddr

from django.conf import settings


BRAND_DISPLAY_NAME = "Open\u00c9ire Studios"
BRAND_ASCII_NAME = "OpenEire Studios"
DEFAULT_STUDIO_EMAIL = "studio@openeire.ie"


def extract_email_address(value, fallback_address=DEFAULT_STUDIO_EMAIL):
    _, address = parseaddr(str(value or "").strip())
    if address:
        return address

    text = str(value or "").strip()
    return text or fallback_address


def format_branded_sender(value, fallback_address=DEFAULT_STUDIO_EMAIL):
    address = extract_email_address(value, fallback_address=fallback_address)
    return formataddr((BRAND_DISPLAY_NAME, address))


def get_default_from_email():
    return format_branded_sender(getattr(settings, "DEFAULT_FROM_EMAIL", None))


def get_server_from_email():
    return format_branded_sender(
        getattr(settings, "SERVER_EMAIL", None),
        fallback_address=extract_email_address(
            getattr(settings, "DEFAULT_FROM_EMAIL", None),
        ),
    )


def get_licensing_from_email():
    return format_branded_sender(
        getattr(settings, "LICENSING_FROM_EMAIL", None),
        fallback_address=extract_email_address(
            getattr(settings, "DEFAULT_FROM_EMAIL", None),
        ),
    )


def get_contact_email_address():
    configured = (
        getattr(settings, "LICENSOR_CONTACT_EMAIL", None)
        or getattr(settings, "DEFAULT_FROM_EMAIL", None)
    )
    return extract_email_address(configured)


def _clean_text(value):
    text = str(value or "").strip()
    return text or None


def resolve_order_display_name(order):
    for attr_name in ("full_name", "customer_name"):
        resolved = _clean_text(getattr(order, attr_name, None))
        if resolved:
            return resolved

    for attr_name in ("first_name",):
        resolved = _clean_text(getattr(order, attr_name, None))
        if resolved:
            return resolved

    user_profile = getattr(order, "user_profile", None)
    user = getattr(user_profile, "user", None) or getattr(order, "user", None)
    if user is not None:
        full_name = _clean_text(user.get_full_name()) if hasattr(user, "get_full_name") else None
        if full_name:
            return full_name

        first_name = _clean_text(getattr(user, "first_name", None))
        if first_name:
            return first_name

        email = _clean_text(getattr(user, "email", None))
        if email:
            return email

    order_email = _clean_text(getattr(order, "email", None))
    if order_email:
        return order_email

    return "there"
