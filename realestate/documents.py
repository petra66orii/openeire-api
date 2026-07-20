from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from django.conf import settings
from django.template import Context, Template
from django.utils import timezone
from django.utils.text import slugify

from openeire_api.business_identity import get_business_identity
from openeire_api.pdf_markdown import render_markdown_to_flowables

from .models import (
    RealEstateBookingAgreementSnapshot,
    RealEstateEnquiry,
    RealEstateInvoice,
)
from .payments import calculate_realestate_deposit_amounts

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate


BOOKING_AGREEMENT_TEMPLATE_PATH = (
    Path(__file__).resolve().parent / "docs" / "booking_agreement.md"
)
BOOKING_AGREEMENT_BLANK = "Not provided"
BOOKING_AGREEMENT_TEMPLATE_VERSION = RealEstateBookingAgreementSnapshot.TEMPLATE_VERSION
MONEY = Decimal("0.01")


def blank_if_missing(value, blank=BOOKING_AGREEMENT_BLANK):
    if value is None:
        return blank
    text = str(value).strip()
    if not text:
        return blank
    return (
        xml_escape(text)
        .replace("|", "&#124;")
        .replace("\r\n", "<br/>")
        .replace("\r", "<br/>")
        .replace("\n", "<br/>")
    )


def _decimal_or_none(value):
    if value is None or value == "":
        return None
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def _format_agreement_date(value):
    if not value:
        return BOOKING_AGREEMENT_BLANK
    if isinstance(value, str):
        return blank_if_missing(value)
    return value.strftime("%d %B %Y")


def _format_agreement_money(value):
    value = _decimal_or_none(value)
    if value is None:
        return BOOKING_AGREEMENT_BLANK
    return f"€{value}"


def _agreement_currency_text(value):
    return blank_if_missing(value).replace("EUR ", "€")


def _booking_reference(enquiry):
    return f"RE-{enquiry.id}" if getattr(enquiry, "id", None) else "RE-DRAFT"


def _has_travel_supplement(enquiry):
    return "travel_supplement" in (getattr(enquiry, "add_ons", None) or [])


def booking_agreement_missing_requirements(enquiry):
    if not _has_travel_supplement(enquiry):
        return []

    missing = []
    travel_amount = _decimal_or_none(
        getattr(enquiry, "travel_supplement_amount", None)
    )
    if travel_amount is None or travel_amount <= 0:
        missing.append("travel supplement amount")
    if not str(getattr(enquiry, "travel_details", "") or "").strip():
        missing.append("travel details")
    return missing


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


def _active_invoices(enquiry):
    if not getattr(enquiry, "pk", None):
        return []
    return list(
        enquiry.invoices.exclude(status=RealEstateInvoice.Status.VOID).order_by("created_at")
    )


def _invoice_by_type(invoices, invoice_type):
    return next((invoice for invoice in invoices if invoice.invoice_type == invoice_type), None)


def _ensure_pricing_snapshot(enquiry):
    if (
        getattr(enquiry, "quoted_total", None) is None
        and getattr(enquiry, "quoted_price", None) is not None
    ):
        try:
            calculate_realestate_deposit_amounts(enquiry)
            enquiry.refresh_from_db()
        except ValueError:
            pass


def _snapshot_amounts(enquiry):
    _ensure_pricing_snapshot(enquiry)
    return {
        "quote_total": getattr(enquiry, "quoted_subtotal", None)
        or getattr(enquiry, "quoted_price", None),
        "vat_total": getattr(enquiry, "quoted_vat_amount", None),
        "total": getattr(enquiry, "quoted_total", None)
        or getattr(enquiry, "quoted_price", None),
        "deposit": getattr(enquiry, "quoted_deposit_amount", None),
        "balance": getattr(enquiry, "quoted_balance_due", None),
        "vat_registered": bool(getattr(enquiry, "vat_registered_at_quote", False)),
        "vat_rate": getattr(enquiry, "quoted_vat_rate", None) or Decimal("0"),
        "price_input_is_gross": (
            True
            if getattr(enquiry, "price_input_is_gross", None) is None
            else getattr(enquiry, "price_input_is_gross")
        ),
    }


def _payment_terms(enquiry):
    arrangement = getattr(
        enquiry,
        "payment_arrangement",
        RealEstateEnquiry.PaymentArrangement.DEPOSIT_THEN_BALANCE,
    )
    invoices = _active_invoices(enquiry)
    amounts = _snapshot_amounts(enquiry)
    deposit_invoice = _invoice_by_type(invoices, RealEstateInvoice.InvoiceType.DEPOSIT)
    balance_invoice = _invoice_by_type(invoices, RealEstateInvoice.InvoiceType.BALANCE)
    full_invoice = _invoice_by_type(invoices, RealEstateInvoice.InvoiceType.FULL)

    total_required = (
        getattr(enquiry, "custom_required_total", None)
        if arrangement == RealEstateEnquiry.PaymentArrangement.CUSTOM
        else None
    ) or (full_invoice.total if full_invoice else None) or amounts["total"]

    deposit_amount = deposit_invoice.total if deposit_invoice else amounts["deposit"]
    balance_due = balance_invoice.total if balance_invoice else amounts["balance"]
    if arrangement != RealEstateEnquiry.PaymentArrangement.DEPOSIT_THEN_BALANCE:
        deposit_amount = None
        balance_due = None

    due_date = getattr(enquiry, "payment_due_date", None)
    if not due_date and arrangement == RealEstateEnquiry.PaymentArrangement.FULL_ON_SHOOT_DAY:
        due_date = getattr(enquiry, "shoot_date", None)
    if not due_date and balance_invoice and balance_invoice.due_at:
        due_date = balance_invoice.due_at.date()
    if not due_date and full_invoice and full_invoice.due_at:
        due_date = full_invoice.due_at.date()

    custom_terms = str(getattr(enquiry, "custom_payment_terms", "") or "").strip()
    if arrangement == RealEstateEnquiry.PaymentArrangement.CUSTOM and not custom_terms:
        raise ValueError("Custom booking agreements require approved custom payment terms.")

    if arrangement == RealEstateEnquiry.PaymentArrangement.DEPOSIT_THEN_BALANCE:
        booking_confirmation_text = (
            "The booking is not confirmed until OpenEire has received both the signed Booking Agreement "
            "and the booking deposit in cleared funds."
        )
        payment_clause_text = (
            "The booking deposit forms part of the Total Fee. The remaining balance is due on the due date "
            "shown above. Final high-resolution media and usage rights remain withheld until all sums due "
            "have been paid in full."
        )
        acceptance_text = (
            "By signing electronically and by paying the booking deposit after receipt of this Booking "
            "Agreement, the Client confirms that it has read, understood, and agreed to this Booking "
            "Agreement and the OpenEire Property Media Service Terms."
        )
        cancellation_payment_text = (
            "If the Client cancels the booking between 24 and 72 hours before the Shoot Date, 50% of the "
            "Total Fee shall be payable by the Client, less any deposit already paid."
        )
    elif arrangement == RealEstateEnquiry.PaymentArrangement.FULL_UPFRONT:
        booking_confirmation_text = (
            "The booking is not confirmed until OpenEire has received both the signed Booking Agreement "
            "and full payment in cleared funds. No separate deposit or balance split applies."
        )
        payment_clause_text = (
            "The Total Fee is payable in full before booking confirmation. No separate deposit or balance "
            "split applies. Final high-resolution media and usage rights remain withheld until all sums due "
            "have been paid in full."
        )
        acceptance_text = (
            "By signing electronically and paying the Total Fee in full, the Client confirms that it has "
            "read, understood, and agreed to this Booking Agreement and the OpenEire Property Media Service Terms."
        )
        cancellation_payment_text = (
            "If the Client cancels the booking between 24 and 72 hours before the Shoot Date, 50% of the "
            "Total Fee shall be payable by the Client, less any amount already paid."
        )
    elif arrangement == RealEstateEnquiry.PaymentArrangement.FULL_ON_SHOOT_DAY:
        booking_confirmation_text = (
            "Under the approved full-payment-on-shoot-day arrangement, the booking may be confirmed after "
            "the signed Booking Agreement is received, before payment is made."
        )
        payment_clause_text = (
            "The Total Fee is due on the Shoot Date. The fee is payable for services performed and is not "
            "contingent on the property being sold, let, or otherwise completed. Final high-resolution media "
            "and usage rights remain withheld until full payment has been received."
        )
        if getattr(enquiry, "expected_payment_method", "") == RealEstateEnquiry.ExpectedPaymentMethod.CASH:
            payment_clause_text += " Where cash is the expected payment method, a receipt will be issued."
        acceptance_text = (
            "By signing electronically under the approved full-payment-on-shoot-day arrangement, the Client "
            "confirms that it has read, understood, and agreed to this Booking Agreement and the OpenEire "
            "Property Media Service Terms. Full payment remains due on the Shoot Date."
        )
        cancellation_payment_text = (
            "If the Client cancels the booking between 24 and 72 hours before the Shoot Date, 50% of the "
            "Total Fee shall be payable by the Client, less any amount already paid."
        )
    else:
        booking_confirmation_text = custom_terms
        payment_clause_text = custom_terms
        acceptance_text = (
            "By signing electronically under the approved custom payment schedule, the Client confirms that "
            "it has read, understood, and agreed to this Booking Agreement, the custom payment terms shown "
            "above, and the OpenEire Property Media Service Terms."
        )
        cancellation_payment_text = (
            "If the Client cancels the booking between 24 and 72 hours before the Shoot Date, 50% of the "
            "Total Fee shall be payable by the Client, less any amount already paid."
        )

    expected_method = (
        enquiry.get_expected_payment_method_display()
        if hasattr(enquiry, "get_expected_payment_method_display")
        else ""
    )
    return {
        **amounts,
        "arrangement": arrangement,
        "total_required": _decimal_or_none(total_required),
        "deposit_amount": _decimal_or_none(deposit_amount),
        "balance_due": _decimal_or_none(balance_due),
        "payment_due_date": due_date,
        "expected_payment_method": expected_method,
        "custom_payment_terms": custom_terms,
        "booking_confirmation_text": booking_confirmation_text,
        "payment_clause_text": payment_clause_text,
        "acceptance_text": acceptance_text,
        "cancellation_payment_text": cancellation_payment_text,
    }


def _build_booking_agreement_context(enquiry):
    identity = get_business_identity(private_legal_document=True)
    property_address = blank_if_missing(getattr(enquiry, "property_address", ""))
    county = blank_if_missing(getattr(enquiry, "county", ""), blank="")
    if county:
        property_address = f"{property_address}, {county}"
    eircode = blank_if_missing(getattr(enquiry, "eircode", ""), blank="")
    if eircode:
        property_address = f"{property_address}, {eircode}"

    terms = _payment_terms(enquiry)
    arrangement = terms["arrangement"]
    is_split_payment = arrangement == RealEstateEnquiry.PaymentArrangement.DEPOSIT_THEN_BALANCE
    is_full_upfront = arrangement == RealEstateEnquiry.PaymentArrangement.FULL_UPFRONT
    is_full_on_shoot_day = arrangement == RealEstateEnquiry.PaymentArrangement.FULL_ON_SHOOT_DAY
    is_custom_payment = arrangement == RealEstateEnquiry.PaymentArrangement.CUSTOM
    has_travel_supplement = _has_travel_supplement(enquiry)

    return {
        **identity.as_context(),
        "booking_reference": _booking_reference(enquiry),
        "issued_on": timezone.localdate().strftime("%d %B %Y"),
        "agreement_template_version": BOOKING_AGREEMENT_TEMPLATE_VERSION,
        "client_name": blank_if_missing(getattr(enquiry, "name", "")),
        "company_name": blank_if_missing(getattr(enquiry, "company_name", "")),
        "client_contact_name": blank_if_missing(getattr(enquiry, "name", "")),
        "email": blank_if_missing(getattr(enquiry, "email", "")),
        "phone": blank_if_missing(getattr(enquiry, "phone", "")),
        "registered_business_address": blank_if_missing(
            getattr(enquiry, "registered_business_address", "")
        ),
        "property_address": property_address,
        "property_type": blank_if_missing(getattr(enquiry, "property_type", "")),
        "listing_type": blank_if_missing(getattr(enquiry, "listing_type", "")),
        "shoot_date": _format_agreement_date(
            getattr(enquiry, "shoot_date", None)
            or getattr(enquiry, "preferred_date", None)
            or getattr(enquiry, "proposed_shoot_date", None)
        ),
        "shoot_time": blank_if_missing(getattr(enquiry, "shoot_time", "")),
        "access_contact": blank_if_missing(getattr(enquiry, "access_contact", "")),
        "access_notes": blank_if_missing(getattr(enquiry, "access_notes", "")),
        "travel_supplement_applies": (
            "Yes - included in the quoted services total"
            if has_travel_supplement
            else "No"
        ),
        "travel_supplement_amount": (
            _format_agreement_money(
                getattr(enquiry, "travel_supplement_amount", None)
            )
            if has_travel_supplement
            else ""
        ),
        "travel_details": (
            blank_if_missing(getattr(enquiry, "travel_details", ""))
            if has_travel_supplement
            else "Not applicable"
        ),
        "package_name": _agreement_currency_text(
            enquiry.get_preferred_package_summary()
            if hasattr(enquiry, "get_preferred_package_summary")
            else getattr(enquiry, "preferred_package", "")
        ),
        "add_ons_summary": _agreement_currency_text(
            enquiry.get_add_ons_summary()
            if hasattr(enquiry, "get_add_ons_summary")
            else ""
        ),
        "quote_total": _format_agreement_money(terms["quote_total"]),
        "vat_total": _format_agreement_money(terms["vat_total"]),
        "total_including_vat": _format_agreement_money(terms["total_required"]),
        "total_required": _format_agreement_money(terms["total_required"]),
        "deposit_amount": _format_agreement_money(terms["deposit_amount"]) if is_split_payment else "",
        "balance_due": _format_agreement_money(terms["balance_due"]) if is_split_payment else "",
        "payment_arrangement": arrangement,
        "payment_arrangement_label": (
            enquiry.get_payment_arrangement_display()
            if hasattr(enquiry, "get_payment_arrangement_display")
            else "30% deposit then balance"
        ),
        "payment_due_date": _format_agreement_date(terms["payment_due_date"]),
        "expected_payment_method": blank_if_missing(
            terms["expected_payment_method"],
            blank="Not specified",
        ),
        "custom_payment_terms": blank_if_missing(terms["custom_payment_terms"], blank=""),
        "is_split_payment": is_split_payment,
        "is_full_upfront": is_full_upfront,
        "is_full_on_shoot_day": is_full_on_shoot_day,
        "is_custom_payment": is_custom_payment,
        "booking_confirmation_text": xml_escape(terms["booking_confirmation_text"]),
        "payment_clause_text": xml_escape(terms["payment_clause_text"]),
        "acceptance_text": xml_escape(terms["acceptance_text"]),
        "cancellation_payment_text": xml_escape(terms["cancellation_payment_text"]),
        "vat_registered": terms["vat_registered"],
        "price_input_is_gross": terms["price_input_is_gross"],
        "vat_notice": "VAT not applicable - supplier not VAT registered.",
    }


def _snapshot_values(enquiry):
    terms = _payment_terms(enquiry)
    return {
        "payment_arrangement": terms["arrangement"],
        "total_required": terms["total_required"],
        "deposit_amount": terms["deposit_amount"],
        "balance_due": terms["balance_due"],
        "payment_due_date": terms["payment_due_date"],
        "expected_payment_method": getattr(enquiry, "expected_payment_method", "") or "",
        "custom_payment_terms": terms["custom_payment_terms"],
    }


def render_booking_agreement_markdown(
    enquiry,
    *,
    use_snapshot=True,
    create_new_version=False,
    created_by=None,
):
    if use_snapshot and not create_new_version and getattr(enquiry, "pk", None):
        existing = enquiry.booking_agreement_snapshots.first()
        if existing:
            return existing.rendered_markdown

    missing_requirements = booking_agreement_missing_requirements(enquiry)
    if missing_requirements:
        raise ValueError(
            "Booking Agreement cannot be generated until the following travel "
            f"information is provided: {', '.join(missing_requirements)}."
        )

    context = _build_booking_agreement_context(enquiry)
    rendered_markdown = Template(_load_booking_agreement_template()).render(
        Context(context, autoescape=False)
    )
    if use_snapshot and getattr(enquiry, "pk", None):
        RealEstateBookingAgreementSnapshot.objects.create(
            enquiry=enquiry,
            template_version=BOOKING_AGREEMENT_TEMPLATE_VERSION,
            context=context,
            rendered_markdown=rendered_markdown,
            created_by=created_by,
            **_snapshot_values(enquiry),
        )
    return rendered_markdown


def generate_booking_agreement_pdf(
    enquiry,
    *,
    use_snapshot=True,
    create_new_version=False,
    created_by=None,
):
    rendered_markdown = render_booking_agreement_markdown(
        enquiry,
        use_snapshot=use_snapshot,
        create_new_version=create_new_version,
        created_by=created_by,
    )
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=28 * mm,
        bottomMargin=20 * mm,
        title=f"{get_business_identity().display_name} Real Estate Booking Agreement",
        author=get_business_identity().display_name,
    )
    document.build(
        render_markdown_to_flowables(
            rendered_markdown,
            table_width=document.width,
            keep_headings_with_next=True,
        )
    )
    return buffer.getvalue()
