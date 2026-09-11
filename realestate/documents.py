from decimal import Decimal, ROUND_HALF_UP
import re
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
from .turnaround import TURNAROUND_CONTEXT
from .payment_terms import booking_payment_copy
from .package_catalogue import get_package

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Table, TableStyle


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


def _format_agreement_time(value):
    if not value:
        return BOOKING_AGREEMENT_BLANK
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return BOOKING_AGREEMENT_BLANK
        return cleaned[:5] if len(cleaned) >= 5 else blank_if_missing(cleaned)
    return value.strftime("%H:%M")


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


def _scope_items(values):
    from html import unescape

    placeholders = {"included photographs as specifically agreed", "scope not yet agreed (preview only)."}
    items = [unescape(value).strip().removeprefix("- ").strip() for value in values]
    return [item for item in items if item and item.casefold() not in placeholders]


def _agreed_deliverables(enquiry):
    persisted = enquiry._get_persisted_package_scope() or {}
    explicit = str(getattr(enquiry, "agreed_scope", "") or "").strip()
    package = get_package(enquiry.preferred_package)
    # Older snapshots stored the name (sometimes with price/scope), not the code.
    previous_package = persisted.get("package_code")
    package_matches = (
        previous_package == enquiry.preferred_package if previous_package else
        str(persisted.get("package_name") or "").split(" - ", 1)[0] == (package.name if package else "")
    )
    if persisted and not package_matches:
        previous_override = str(persisted.get("agreed_scope_source") or "").strip()
        if not explicit or explicit == previous_override:
            return []  # A package change requires newly reconciled, explicit scope.
    if explicit:
        return _scope_items(explicit.splitlines())
    if persisted.get("agreed_deliverables"):
        # Stored values are escaped for Markdown; unescape before building new context.
        return _scope_items(persisted["agreed_deliverables"])
    legacy_summary = str(persisted.get("package_name") or "")
    if package and package.included_photographs is not None and " - " in legacy_summary:
        parts = legacy_summary.split(" - ", 2)
        if len(parts) == 3:
            return [parts[2]]
    if persisted or enquiry._requires_historical_scope_review():
        return []  # Staff must recover written scope, not substitute today's catalogue.
    if not package or package.included_photographs is None:
        return []
    return [package.included_photographs_label] + (
        re.split(r" \+ (?!60)", package.other_deliverables) if package.other_deliverables else []
    )


def booking_agreement_missing_requirements(enquiry, *, for_customer=False):
    missing = []
    if _has_travel_supplement(enquiry):
        travel_amount = _decimal_or_none(getattr(enquiry, "travel_supplement_amount", None))
        if travel_amount is None or travel_amount <= 0:
            missing.append("travel supplement amount")
        if not str(getattr(enquiry, "travel_details", "") or "").strip():
            missing.append("travel details")
    if not for_customer:
        return missing
    if not enquiry.pk or enquiry._state.adding or not RealEstateEnquiry.objects.filter(pk=enquiry.pk).exists():
        missing.append("persisted booking reference")
    for field, label in (("name", "client name"), ("email", "client email"),
                         ("property_address", "property address"), ("shoot_date", "agreed shoot date")):
        if not str(getattr(enquiry, field, "") or "").strip():
            missing.append(label)
    if not get_package(enquiry.preferred_package) or enquiry.preferred_package == "not_sure":
        missing.append("selected package")
    if not _agreed_deliverables(enquiry):
        missing.append("approved deliverables / agreed scope")
    if enquiry.payment_arrangement not in RealEstateEnquiry.PaymentArrangement.values:
        missing.append("valid payment arrangement")
        return missing
    if enquiry.payment_arrangement == RealEstateEnquiry.PaymentArrangement.CUSTOM and not str(enquiry.custom_payment_terms or "").strip():
        missing.append("approved custom payment terms")
        return missing
    terms = _payment_terms(enquiry)
    if terms["original_total"] is None or terms["original_total"] <= 0:
        missing.append("agreed enquiry price")
    if terms["arrangement"] == RealEstateEnquiry.PaymentArrangement.DEPOSIT_THEN_BALANCE:
        if terms["deposit_amount"] is None or terms["balance_due"] is None:
            missing.append("agreed deposit and balance")
        elif terms["total_required"] is not None and terms["deposit_amount"] + terms["balance_due"] != terms["total_required"]:
            missing.append("deposit and balance matching the agreed fee")
        if not terms["payment_due_date"]:
            missing.append("balance payment due date")
    if terms["arrangement"] == RealEstateEnquiry.PaymentArrangement.FULL_ON_SHOOT_DAY and (
        enquiry.payment_due_date and enquiry.payment_due_date != enquiry.shoot_date
    ):
        missing.append("payment due date matching the shoot date")
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
            if enquiry.pk and not enquiry._state.adding:
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
    payment_copy = booking_payment_copy(enquiry)
    invoices = _active_invoices(enquiry)
    amounts = _snapshot_amounts(enquiry)
    deposit_invoice = _invoice_by_type(invoices, RealEstateInvoice.InvoiceType.DEPOSIT)
    balance_invoice = _invoice_by_type(invoices, RealEstateInvoice.InvoiceType.BALANCE)
    full_invoice = _invoice_by_type(invoices, RealEstateInvoice.InvoiceType.FULL)

    original_total = (
        getattr(enquiry, "custom_required_total", None)
        if arrangement == RealEstateEnquiry.PaymentArrangement.CUSTOM
        else None
    ) or amounts["total"] or (full_invoice.total if full_invoice else None)

    if original_total is None and deposit_invoice and balance_invoice:
        original_total = deposit_invoice.total + balance_invoice.total
    adjustments = enquiry.total_active_adjustments
    total_required = max(original_total - adjustments, Decimal("0.00")) if original_total is not None else None
    if arrangement == RealEstateEnquiry.PaymentArrangement.CUSTOM:
        amounts["quote_total"] = original_total
    elif amounts["quote_total"] is None:
        amounts["quote_total"] = original_total

    deposit_amount = deposit_invoice.total if deposit_invoice else amounts["deposit"]
    balance_due = balance_invoice.total if balance_invoice else amounts["balance"]
    if adjustments and total_required is not None and deposit_amount is not None:
        deposit_amount = min(deposit_amount, total_required)
        balance_due = max(total_required - deposit_amount, Decimal("0.00"))
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

    expected_method = (
        enquiry.get_expected_payment_method_display()
        if hasattr(enquiry, "get_expected_payment_method_display")
        else ""
    )
    return {
        **amounts,
        "arrangement": arrangement,
        "original_total": _decimal_or_none(original_total),
        "adjustment_total": adjustments,
        "total_required": _decimal_or_none(total_required),
        "deposit_amount": _decimal_or_none(deposit_amount),
        "balance_due": _decimal_or_none(balance_due),
        "payment_due_date": due_date,
        "expected_payment_method": expected_method,
        **payment_copy,
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
        "shoot_date": _format_agreement_date(
            getattr(enquiry, "shoot_date", None)
            or getattr(enquiry, "preferred_date", None)
            or getattr(enquiry, "proposed_shoot_date", None)
        ),
        "shoot_time": _format_agreement_time(getattr(enquiry, "shoot_time", None)),
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
        "package_name": blank_if_missing(
            get_package(enquiry.preferred_package).name if get_package(enquiry.preferred_package) else ""
        ),
        "package_code": enquiry.preferred_package,
        "agreed_scope_source": str(enquiry.agreed_scope or "").strip(),
        "scope_is_override": bool(str(enquiry.agreed_scope or "").strip()) or bool(
            (enquiry._get_persisted_package_scope() or {}).get("scope_is_override")
        ),
        "agreed_deliverables": [blank_if_missing(item) for item in _agreed_deliverables(enquiry)],
        "is_preview": bool(booking_agreement_missing_requirements(enquiry, for_customer=True)),
        "has_adjustments": bool(terms["adjustment_total"]),
        "adjustment_total": _format_agreement_money(terms["adjustment_total"]),
        "included_photographs_label": enquiry.get_included_photographs_label(),
        "included_photograph_count": enquiry.get_included_photograph_count(),
        "additional_photograph_copy": _agreement_currency_text(
            enquiry.ADDITIONAL_PHOTOGRAPH_COPY
        ),
        "turnaround_label": enquiry.get_preferred_package_turnaround_label(),
        "turnaround_detail": enquiry.get_preferred_package_turnaround_detail(),
        "turnaround_context": TURNAROUND_CONTEXT,
        "add_ons_summary": blank_if_missing(
            ", ".join(re.split(r" - (?:EUR |\u20ac)", label, maxsplit=1)[0] for label in enquiry.get_add_on_labels()) or "None"
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
    for_customer=False,
):
    if for_customer:
        missing = booking_agreement_missing_requirements(enquiry, for_customer=True)
        if missing:
            raise ValueError("Booking Agreement cannot be sent until provided: " + ", ".join(missing) + ".")
    if use_snapshot and not create_new_version and getattr(enquiry, "pk", None):
        existing = enquiry.booking_agreement_snapshots.first()
        if existing:
            if for_customer and (
                existing.context.get("is_preview") or "RE-DRAFT" in existing.rendered_markdown
                or existing.context.get("booking_reference") != _booking_reference(enquiry)
                or any(existing.context.get(key) in (None, "", BOOKING_AGREEMENT_BLANK) for key in (
                    "client_name", "email", "property_address", "shoot_date", "total_required", "package_name",
                ))
            ):
                raise ValueError("This is a draft snapshot. Issue a new validated Booking Agreement.")
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
    for_customer=False,
):
    rendered_markdown = render_booking_agreement_markdown(
        enquiry,
        use_snapshot=use_snapshot,
        create_new_version=create_new_version,
        created_by=created_by,
        for_customer=for_customer,
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
    flowables = render_markdown_to_flowables(
        rendered_markdown, table_width=document.width, keep_headings_with_next=True,
    )
    # Keep compact tables, short clauses, and headings with their first content.
    # Oversized content still uses ReportLab's normal splitting fallback.
    for flowable in flowables:
        if isinstance(flowable, Table):
            flowable.setStyle(TableStyle([
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            if flowable.wrap(document.width, document.height)[1] <= document.height - 12:
                flowable.splitByRow = 0
    arranged = []
    index = 0
    while index < len(flowables):
        flowable = flowables[index]
        if isinstance(flowable, Paragraph) and flowable.getPlainText().startswith("9. Signatures and Acceptance"):
            arranged.append(KeepTogether(flowables[index:]))
            break
        if isinstance(flowable, Paragraph) and (
            getattr(flowable.style, "keepWithNext", False) or flowable.getPlainText().endswith(":")
        ):
            group = [flowable]
            index += 1
            while index < len(flowables):
                next_flowable = flowables[index]
                group.append(next_flowable)
                index += 1
                if isinstance(next_flowable, Table):
                    break
                if isinstance(next_flowable, Paragraph) and not next_flowable.getPlainText().endswith(":"):
                    break
            arranged.append(KeepTogether(group))
            continue
        arranged.append(KeepTogether([flowable]) if isinstance(flowable, Paragraph) else flowable)
        index += 1
    document.build(arranged)
    return buffer.getvalue()
