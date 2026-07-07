from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from django.conf import settings
from django.template import Context, Template
from django.utils import timezone
from django.utils.text import slugify

from openeire_api.pdf_markdown import render_markdown_to_flowables

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate


BOOKING_AGREEMENT_TEMPLATE_PATH = (
    Path(__file__).resolve().parent / "docs" / "booking_agreement.md"
)


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
    property_address = _safe_value(getattr(enquiry, "property_address", ""))
    county = _safe_value(getattr(enquiry, "county", ""), default="")
    if county:
        property_address = f"{property_address}, {county}"

    return {
        "booking_reference": _booking_reference(enquiry),
        "issued_on": timezone.localdate().strftime("%d %B %Y"),
        "client_name": _safe_value(getattr(enquiry, "name", "")),
        "company_name": _safe_value(getattr(enquiry, "company_name", ""), default="Not provided"),
        "email": _safe_value(getattr(enquiry, "email", "")),
        "phone": _safe_value(getattr(enquiry, "phone", "")),
        "property_address": property_address,
        "property_type": _safe_value(getattr(enquiry, "property_type", "")),
        "package_name": _safe_value(
            enquiry.get_preferred_package_summary()
            if hasattr(enquiry, "get_preferred_package_summary")
            else getattr(enquiry, "preferred_package", "")
        ),
        "add_ons_summary": _safe_value(
            enquiry.get_add_ons_summary()
            if hasattr(enquiry, "get_add_ons_summary")
            else "",
            default="None",
        ),
        "shoot_date": _format_date(
            getattr(enquiry, "shoot_date", None)
            or getattr(enquiry, "preferred_date", None)
            or getattr(enquiry, "proposed_shoot_date", None)
        ),
        "quote_total": _format_money(getattr(enquiry, "quoted_price", None)),
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
