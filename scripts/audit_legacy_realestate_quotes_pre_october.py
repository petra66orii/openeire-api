"""Read-only pre-October production audit for issued real-estate quotations.

Run with the currently deployed Django application, for example:

    python manage.py shell -c "import sys; exec(sys.stdin.read())" \
        < scripts/audit_legacy_realestate_quotes_pre_october.py

The script intentionally avoids October-only models and performs no writes.
"""

import json
from collections import defaultdict
from decimal import Decimal

from django.apps import apps
from django.utils import timezone


Enquiry = apps.get_model("realestate", "RealEstateEnquiry")
TimelineEvent = apps.get_model("realestate", "RealEstateTimelineEvent")

SUCCESSFUL_QUOTE_STATUSES = ("sent", "completed")
CLOSED_ENQUIRY_STATUSES = {"completed", "closed", "spam"}
POTENTIALLY_ACTIONABLE_STATUSES = {"new", "reviewing", "quoted"}
PAID_INVOICE_STATUSES = {"partially_paid", "paid"}

enquiry_fields = {field.name for field in Enquiry._meta.get_fields()}


def field_value(instance, field_name, default=None):
    if field_name not in enquiry_fields:
        return default
    return getattr(instance, field_name, default)


def money(value):
    if value is None:
        return None
    return f"{Decimal(value):.2f}"


def serialise(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return money(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def issued_at(value):
    if value is None:
        return None
    if timezone.is_aware(value):
        value = timezone.localtime(value)
    return value.isoformat()


def package_details(enquiry):
    package_code = field_value(enquiry, "preferred_package", "")
    package_name = (
        enquiry.get_preferred_package_display()
        if hasattr(enquiry, "get_preferred_package_display")
        else package_code
    )
    package_base_price = None
    try:
        from realestate.package_catalogue import get_package

        catalogue_version = field_value(enquiry, "catalogue_version")
        try:
            package = (
                get_package(package_code, catalogue_version)
                if catalogue_version
                else get_package(package_code)
            )
        except TypeError:
            # The pre-October helper accepts only the package code.
            package = get_package(package_code)
        if package is not None:
            package_name = package.name
            package_base_price = money(package.price_eur)
    except (ImportError, AttributeError):
        # The stored code/display value remains available if catalogue helpers differ.
        pass
    return package_code, package_name, package_base_price


events = list(
    TimelineEvent.objects.filter(
        event_type="quote_sent",
        status__in=SUCCESSFUL_QUOTE_STATUSES,
    )
    .select_related("enquiry")
    .prefetch_related("enquiry__invoices")
    .order_by("created_at", "pk")
)

events_by_enquiry = defaultdict(list)
for event in events:
    events_by_enquiry[event.enquiry_id].append(event)

rows = []
for event in events:
    enquiry = event.enquiry
    enquiry_status = field_value(enquiry, "status", "")
    booking_agreement_received = bool(
        field_value(enquiry, "booking_agreement_received", False)
    )
    deposit_paid = bool(field_value(enquiry, "deposit_paid", False))
    invoices = list(enquiry.invoices.all())
    invoice_evidence = [
        {
            "invoice_id": invoice.pk,
            "invoice_number": getattr(invoice, "invoice_number", ""),
            "invoice_type": getattr(invoice, "invoice_type", ""),
            "status": getattr(invoice, "status", ""),
            "total": money(getattr(invoice, "total", None)),
            "paid_at": serialise(getattr(invoice, "paid_at", None)),
            "stripe_invoice_status": getattr(invoice, "stripe_invoice_status", ""),
        }
        for invoice in invoices
        if getattr(invoice, "status", "") != "void"
    ]
    has_paid_invoice = any(
        getattr(invoice, "status", "") in PAID_INVOICE_STATUSES
        or getattr(invoice, "paid_at", None) is not None
        or getattr(invoice, "stripe_invoice_status", "") == "paid"
        for invoice in invoices
    )
    payment_evidence = []
    if deposit_paid:
        payment_evidence.append("deposit_paid flag is true")
    if has_paid_invoice:
        payment_evidence.append("paid or part-paid invoice evidence exists")

    strong_conversion = booking_agreement_received and (
        deposit_paid or has_paid_invoice
    )
    appears_converted = bool(
        enquiry_status in {"booked", "completed"}
        or booking_agreement_received
        or payment_evidence
    )
    clearly_closed = bool(
        enquiry_status in CLOSED_ENQUIRY_STATUSES or strong_conversion
    )

    enquiry_events = events_by_enquiry[event.enquiry_id]
    later_quote_ids = [
        other.pk
        for other in enquiry_events
        if (other.created_at, other.pk) > (event.created_at, event.pk)
    ]
    multiple_quote_events = len(enquiry_events) > 1

    package_code, package_name, package_base_price = package_details(enquiry)
    stored_add_ons = list(field_value(enquiry, "add_ons", []) or [])
    add_on_labels = getattr(enquiry, "ADD_ON_LABELS", {})
    add_ons = [
        {"code": str(code), "label": add_on_labels.get(str(code), str(code))}
        for code in stored_add_ons
    ]
    agreed_scope = str(field_value(enquiry, "agreed_scope", "") or "").strip()
    quoted_price = field_value(enquiry, "quoted_price")

    review_reasons = []
    if clearly_closed:
        if enquiry_status in CLOSED_ENQUIRY_STATUSES:
            review_reasons.append(
                f"historical only: enquiry status is {enquiry_status}"
            )
        if strong_conversion:
            review_reasons.append(
                "historical only: booking agreement plus payment evidence exists"
            )
    else:
        review_reasons.append(
            "no quote-specific expiry, acceptance or cancellation state is stored"
        )
        if enquiry_status in POTENTIALLY_ACTIONABLE_STATUSES:
            review_reasons.append(
                f"enquiry status {enquiry_status} remains potentially actionable"
            )
        else:
            review_reasons.append(
                f"enquiry status {enquiry_status!r} is not clearly closed"
            )
        if appears_converted:
            review_reasons.append(
                "some conversion evidence exists, but it is not strong enough to close automatically"
            )
        if multiple_quote_events:
            review_reasons.append(
                "multiple successful quote events exist without explicit supersession state"
            )

    if agreed_scope:
        review_reasons.append("manually customised agreed scope is stored")
    if package_code in {"custom", "not_sure"}:
        review_reasons.append("package scope or price requires manual reconstruction")
    if quoted_price is None and field_value(enquiry, "quoted_total") is None:
        review_reasons.append("no stored quoted price or total is available")
    if (
        package_base_price is not None
        and quoted_price is not None
        and not stored_add_ons
        and not agreed_scope
        and Decimal(quoted_price) != Decimal(package_base_price)
    ):
        review_reasons.append(
            "stored quoted price differs from package base without stored add-ons or scope override"
        )

    classification = "CLOSED" if clearly_closed else "REVIEW"
    rows.append(
        {
            "classification": classification,
            "timeline_event_id": event.pk,
            "enquiry_id": enquiry.pk,
            "contact": {
                "name": field_value(enquiry, "name", ""),
                "company": field_value(enquiry, "company_name", ""),
                "email": field_value(enquiry, "email", ""),
                "phone": field_value(enquiry, "phone", ""),
            },
            "property_reference": field_value(enquiry, "property_address", ""),
            "eircode": field_value(enquiry, "eircode", ""),
            "quote_issued_at": issued_at(event.created_at),
            "quote_event_status": event.status,
            "enquiry_status": enquiry_status,
            "catalogue_version": field_value(enquiry, "catalogue_version"),
            "selected_package": {
                "code": package_code,
                "name": package_name,
                "catalogue_base_price": package_base_price,
            },
            "stored_pricing": {
                field_name: serialise(field_value(enquiry, field_name))
                for field_name in (
                    "quoted_price",
                    "quoted_subtotal",
                    "quoted_vat_rate",
                    "quoted_vat_amount",
                    "quoted_total",
                    "quoted_deposit_amount",
                    "quoted_balance_due",
                    "travel_supplement_amount",
                    "additional_stills_quantity",
                )
                if field_name in enquiry_fields
            },
            "add_ons": add_ons,
            "agreed_scope": agreed_scope or None,
            "booking_agreement_received": booking_agreement_received,
            "deposit_paid": deposit_paid,
            "deposit_paid_at": serialise(field_value(enquiry, "deposit_paid_at")),
            "payment_evidence": payment_evidence,
            "invoice_evidence": invoice_evidence,
            "appears_converted": appears_converted,
            "clearly_historical_or_closed": clearly_closed,
            "multiple_quote_events": multiple_quote_events,
            "later_quote_event_ids": later_quote_ids,
            "manual_review_required": not clearly_closed,
            "review_reasons": review_reasons,
        }
    )

payload = {
    "read_only": True,
    "schema": {
        "catalogue_version_field_present": "catalogue_version" in enquiry_fields,
        "quotation_snapshot_model_used": False,
    },
    "summary": {
        "total_issued_quote_events": len(rows),
        "CLOSED": sum(row["classification"] == "CLOSED" for row in rows),
        "REVIEW": sum(row["classification"] == "REVIEW" for row in rows),
        "ambiguous_or_multiple_quote_events": sum(
            row["classification"] == "REVIEW" or row["multiple_quote_events"]
            for row in rows
        ),
        "multiple_quote_enquiries": sum(
            len(enquiry_events) > 1
            for enquiry_events in events_by_enquiry.values()
        ),
    },
    "results": rows,
    "limitations": [
        "The pre-October schema has no quote-specific expiry, acceptance, cancellation or supersession state.",
        "Quote events do not freeze package, price or scope; reported enquiry values may have changed after issue.",
        "No quotation body is regenerated and no October quotation snapshot model is queried.",
    ],
}

print(json.dumps(payload, indent=2, ensure_ascii=False))
