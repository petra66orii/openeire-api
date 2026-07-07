from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from django.conf import settings
from django.template import Context, Template
from django.utils import timezone
from django.utils.text import slugify

from openeire_api.pdf_markdown import render_markdown_to_flowables
from .payments import calculate_realestate_deposit_amounts

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate


BOOKING_AGREEMENT_TEMPLATE_PATH = (
    Path(__file__).resolve().parent / "docs" / "booking_agreement.md"
)
BOOKING_AGREEMENT_BLANK = "______________________________"


def blank_if_missing(value, blank=BOOKING_AGREEMENT_BLANK):
    if value is None:
        return blank
    text = str(value).strip()
    if not text:
        return blank
    return xml_escape(text)


def _safe_value(value, default="Not specified"):
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    return xml_escape(text)


def _format_date(value, default="To be confirmed"):
    if not value:
        return default
    if isinstance(value, str):
        return _safe_value(value)
    return value.strftime("%d %B %Y")


def _format_money(value):
    if value is None:
        return "To be confirmed"
    return f"EUR {value}"


def _format_agreement_date(value):
    if not value:
        return BOOKING_AGREEMENT_BLANK
    if isinstance(value, str):
        return blank_if_missing(value)
    return value.strftime("%d %B %Y")


def _format_agreement_money(value):
    if value is None:
        return BOOKING_AGREEMENT_BLANK
    return f"EUR {value}"


def _booking_reference(enquiry):
    return f"RE-{enquiry.id}" if getattr(enquiry, "id", None) else "RE-DRAFT"


def build_booking_agreement_filename(enquiry):
    reference = _booking_reference(enquiry).lower()
    name_part = slugify(getattr(enquiry, "name", "") or "client") or "client"
    return f"openeire-booking-agreement-{reference}-{name_part}.pdf"


def _load_booking_agreement_template():
    path = Path(
        getattr(settings, "REALESTATE_BOOKING_AGREEMENT_TEMPLATE", "")
        or BOOKING_AGREEMENT_TEMPLATE_PATH
    )
    if not path.exists():
        raise FileNotFoundError(f"Booking agreement template not found: {path}")
    return path.read_text(encoding="utf-8")


def _build_booking_agreement_context(enquiry):
    property_address = blank_if_missing(getattr(enquiry, "property_address", ""))
    county = blank_if_missing(getattr(enquiry, "county", ""), blank="")
    if county:
        property_address = f"{property_address}, {county}"

    quote_amounts = {}
    try:
        quote_amounts = calculate_realestate_deposit_amounts(enquiry)
    except ValueError:
        quote_amounts = {}

    quote_total = quote_amounts.get("quote_total", getattr(enquiry, "quoted_price", None))

    return {
        "booking_reference": _booking_reference(enquiry),
        "issued_on": timezone.localdate().strftime("%d %B %Y"),
        "client_name": blank_if_missing(getattr(enquiry, "name", "")),
        "company_name": blank_if_missing(getattr(enquiry, "company_name", "")),
        "client_contact_name": blank_if_missing(getattr(enquiry, "name", "")),
        "email": blank_if_missing(getattr(enquiry, "email", "")),
        "phone": blank_if_missing(getattr(enquiry, "phone", "")),
        "registered_business_address": BOOKING_AGREEMENT_BLANK,
        "property_address": property_address,
        "property_type": blank_if_missing(getattr(enquiry, "property_type", "")),
        "listing_type": BOOKING_AGREEMENT_BLANK,
        "shoot_date": _format_agreement_date(
            getattr(enquiry, "shoot_date", None)
            or getattr(enquiry, "preferred_date", None)
            or getattr(enquiry, "proposed_shoot_date", None)
        ),
        "shoot_time": BOOKING_AGREEMENT_BLANK,
        "access_contact": BOOKING_AGREEMENT_BLANK,
        "access_notes": BOOKING_AGREEMENT_BLANK,
        "travel_details": BOOKING_AGREEMENT_BLANK,
        "package_name": blank_if_missing(
            enquiry.get_preferred_package_summary()
            if hasattr(enquiry, "get_preferred_package_summary")
            else getattr(enquiry, "preferred_package", "")
        ),
        "add_ons_summary": blank_if_missing(
            enquiry.get_add_ons_summary()
            if hasattr(enquiry, "get_add_ons_summary")
            else ""
        ),
        "quote_total": _format_agreement_money(quote_total),
        "vat_total": _format_agreement_money(quote_amounts.get("vat_total")),
        "total_including_vat": _format_agreement_money(
            quote_amounts.get("total_including_vat")
        ),
        "deposit_amount": _format_agreement_money(quote_amounts.get("deposit_amount")),
        "balance_due": _format_agreement_money(quote_amounts.get("balance_due")),
    }


def generate_booking_agreement_pdf(enquiry):
    template_text = _load_booking_agreement_template()
    rendered_markdown = Template(template_text).render(
        Context(_build_booking_agreement_context(enquiry), autoescape=False)
    )

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        title="OpenEire Studios Real Estate Booking Agreement",
        author="OpenEire Studios",
    )
    document.build(render_markdown_to_flowables(rendered_markdown))
    return buffer.getvalue()
