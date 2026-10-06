import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Prefetch
from django.utils import timezone

from realestate.models import (
    RealEstateEnquiry,
    RealEstateInvoice,
    RealEstateQuotationSnapshot,
    RealEstateTimelineEvent,
)
from realestate.package_catalogue import (
    CURRENT_CATALOGUE_VERSION,
    LEGACY_CATALOGUE_VERSION,
    PACKAGE_CATALOGUES,
)


SNAPSHOT_MATCH_WINDOW = timedelta(minutes=10)
CLOSED_ENQUIRY_STATUSES = {
    RealEstateEnquiry.Status.BOOKED,
    RealEstateEnquiry.Status.COMPLETED,
    RealEstateEnquiry.Status.CLOSED,
    RealEstateEnquiry.Status.SPAM,
}
ISSUED_EVENT_STATUSES = {
    RealEstateTimelineEvent.EventStatus.SENT,
    RealEstateTimelineEvent.EventStatus.COMPLETED,
}


@dataclass(frozen=True)
class QuotationAuditRow:
    classification: str
    quote_id: str
    timeline_event_id: int
    snapshot_id: int | None
    enquiry_id: int
    client_name: str
    contact_email: str
    property_reference: str
    issued_at: str
    expiry_date: None
    validity_period: str
    quote_event_status: str
    enquiry_status: str
    catalogue_version: str
    package_code: str
    package_name: str
    package_base_price: str | None
    add_ons: list[dict[str, str]]
    total_quoted_amount: str | None
    stored_scope: str
    agreed_photograph_count: int | None
    quotation_body_snapshot_exists: bool
    accepted_or_converted: bool
    conversion_evidence: list[str]
    invoice_statuses: list[str]
    appears_superseded: bool
    later_quote_event_ids: list[int]
    manual_review_required: bool
    review_reasons: list[str]


def _money(value):
    if value is None:
        return None
    return f"{Decimal(value):.2f}"


def _match_snapshots(events, snapshots_by_enquiry):
    matches = {}
    used_snapshot_ids = set()
    for event in sorted(events, key=lambda item: (item.created_at, item.pk)):
        candidates = []
        for snapshot in snapshots_by_enquiry.get(event.enquiry_id, []):
            if snapshot.pk in used_snapshot_ids:
                continue
            if event.recipient_email and snapshot.recipient_email != event.recipient_email:
                continue
            distance = abs(snapshot.issued_at - event.created_at)
            if distance <= SNAPSHOT_MATCH_WINDOW:
                candidates.append((distance, snapshot.pk, snapshot))
        if not candidates:
            continue
        snapshot = min(candidates, key=lambda item: (item[0], item[1]))[2]
        matches[event.pk] = snapshot
        used_snapshot_ids.add(snapshot.pk)
    return matches


def _conversion_evidence(enquiry):
    evidence = []
    if enquiry.status in {
        RealEstateEnquiry.Status.BOOKED,
        RealEstateEnquiry.Status.COMPLETED,
    }:
        evidence.append(f"enquiry status is {enquiry.status}")
    if enquiry.booking_agreement_received:
        evidence.append("booking agreement recorded as received")
    if enquiry.deposit_paid:
        evidence.append("deposit recorded as paid")
    paid_invoices = [
        invoice
        for invoice in enquiry.invoices.all()
        if invoice.status
        in {
            RealEstateInvoice.Status.PARTIALLY_PAID,
            RealEstateInvoice.Status.PAID,
        }
    ]
    if paid_invoices:
        invoice_states = ", ".join(
            sorted({invoice.status for invoice in paid_invoices})
        )
        evidence.append(f"partially or fully paid invoice exists ({invoice_states})")
    return evidence


def _stored_add_ons(enquiry):
    return [
        {
            "code": str(code),
            "label": enquiry.ADD_ON_LABELS.get(str(code), str(code)),
        }
        for code in (enquiry.add_ons or [])
    ]


def _build_row(event, snapshot, later_quote_event_ids):
    enquiry = event.enquiry
    catalogue = PACKAGE_CATALOGUES.get(enquiry.catalogue_version)
    package = catalogue.get(enquiry.preferred_package) if catalogue else None
    conversion_evidence = _conversion_evidence(enquiry)
    accepted_or_converted = bool(conversion_evidence)
    closed = enquiry.status in CLOSED_ENQUIRY_STATUSES or accepted_or_converted
    invoice_statuses = sorted(
        {
            invoice.status
            for invoice in enquiry.invoices.all()
            if invoice.status != RealEstateInvoice.Status.VOID
        }
    )
    review_reasons = []

    if not snapshot:
        review_reasons.append("no immutable quotation-body snapshot matches this issue event")
    if not closed:
        review_reasons.append("no quotation-specific expiry or validity period is stored")
        if enquiry.status in {
            RealEstateEnquiry.Status.NEW,
            RealEstateEnquiry.Status.REVIEWING,
            RealEstateEnquiry.Status.QUOTED,
        }:
            review_reasons.append(
                f"legacy quote remains potentially actionable with enquiry status {enquiry.status}"
            )
        else:
            review_reasons.append(f"enquiry status {enquiry.status!r} is ambiguous")
        if later_quote_event_ids:
            review_reasons.append(
                "later quote issue event(s) exist but the earlier quote is not explicitly superseded"
            )
        if invoice_statuses and not accepted_or_converted:
            review_reasons.append(
                "unpaid non-void invoice exists but legacy invoice backfill does not reliably prove conversion"
            )

    if catalogue is None:
        review_reasons.append("stored catalogue version is not defined by this release")
    elif package is None:
        review_reasons.append("stored package is not present in the stored catalogue version")
    if (
        package
        and package.price_eur is not None
        and enquiry.quoted_price is not None
        and not enquiry.add_ons
        and not str(enquiry.agreed_scope or "").strip()
        and not enquiry.agreed_photograph_count
        and Decimal(enquiry.quoted_price) != Decimal(package.price_eur)
    ):
        review_reasons.append(
            "stored quoted price differs from the catalogue base price without stored add-ons or scope override"
        )
    if str(enquiry.agreed_scope or "").strip():
        review_reasons.append("manually customised agreed scope is stored")
    if enquiry.agreed_photograph_count:
        review_reasons.append("explicit agreed photograph count is stored")
    if enquiry.preferred_package == RealEstateEnquiry.PreferredPackage.CUSTOM:
        review_reasons.append("Custom / POA package requires manual scope verification")
    if enquiry.custom_review_reasons or str(enquiry.custom_review_notes or "").strip():
        review_reasons.append("custom-review signals or notes are stored")

    if closed:
        classification = "CLOSED"
        manual_review_required = False
        close_reason = (
            "; ".join(conversion_evidence)
            if conversion_evidence
            else f"enquiry status is {enquiry.status}"
        )
        review_reasons.insert(0, f"historical only: {close_reason}")
    elif review_reasons:
        classification = "REVIEW"
        manual_review_required = True
    else:
        classification = "SAFE"
        manual_review_required = False

    return QuotationAuditRow(
        classification=classification,
        quote_id=f"timeline:{event.pk}",
        timeline_event_id=event.pk,
        snapshot_id=snapshot.pk if snapshot else None,
        enquiry_id=enquiry.pk,
        client_name=enquiry.name,
        contact_email=enquiry.email,
        property_reference=enquiry.property_address,
        issued_at=timezone.localtime(event.created_at).isoformat(),
        expiry_date=None,
        validity_period="Not stored",
        quote_event_status=event.status,
        enquiry_status=enquiry.status,
        catalogue_version=enquiry.catalogue_version or "",
        package_code=enquiry.preferred_package,
        package_name=(package.name if package else enquiry.get_preferred_package_display()),
        package_base_price=(
            _money(package.price_eur) if package and package.price_eur is not None else None
        ),
        add_ons=_stored_add_ons(enquiry),
        total_quoted_amount=_money(enquiry.quoted_total or enquiry.quoted_price),
        stored_scope=str(enquiry.agreed_scope or "").strip(),
        agreed_photograph_count=enquiry.agreed_photograph_count,
        quotation_body_snapshot_exists=bool(snapshot),
        accepted_or_converted=accepted_or_converted,
        conversion_evidence=conversion_evidence,
        invoice_statuses=invoice_statuses,
        appears_superseded=bool(later_quote_event_ids),
        later_quote_event_ids=later_quote_event_ids,
        manual_review_required=manual_review_required,
        review_reasons=review_reasons,
    )


def collect_quotation_audit_rows(*, database="default", legacy_versions=None):
    event_queryset = (
        RealEstateTimelineEvent.objects.using(database)
        .filter(
            event_type=RealEstateTimelineEvent.EventType.QUOTE_SENT,
            status__in=ISSUED_EVENT_STATUSES,
        )
        .select_related("enquiry")
        .prefetch_related(
            Prefetch(
                "enquiry__invoices",
                queryset=RealEstateInvoice.objects.using(database).only("id", "status", "enquiry_id"),
            )
        )
        .order_by("created_at", "pk")
    )
    events = list(event_queryset)
    if legacy_versions is None:
        discovered = {
            event.enquiry.catalogue_version
            for event in events
            if event.enquiry.catalogue_version != CURRENT_CATALOGUE_VERSION
        }
        legacy_versions = discovered | {LEGACY_CATALOGUE_VERSION}
    else:
        legacy_versions = set(legacy_versions)

    legacy_events = [
        event for event in events if event.enquiry.catalogue_version in legacy_versions
    ]
    enquiry_ids = {event.enquiry_id for event in legacy_events}
    snapshots_by_enquiry = defaultdict(list)
    for snapshot in (
        RealEstateQuotationSnapshot.objects.using(database)
        .filter(enquiry_id__in=enquiry_ids)
        .order_by("issued_at", "pk")
    ):
        snapshots_by_enquiry[snapshot.enquiry_id].append(snapshot)

    snapshot_matches = _match_snapshots(legacy_events, snapshots_by_enquiry)
    events_by_enquiry = defaultdict(list)
    for event in legacy_events:
        events_by_enquiry[event.enquiry_id].append(event)
    later_event_ids = {}
    for enquiry_events in events_by_enquiry.values():
        ordered = sorted(enquiry_events, key=lambda item: (item.created_at, item.pk))
        for index, event in enumerate(ordered):
            later_event_ids[event.pk] = [item.pk for item in ordered[index + 1 :]]
    return [
        _build_row(
            event,
            snapshot_matches.get(event.pk),
            later_event_ids.get(event.pk, []),
        )
        for event in legacy_events
    ], sorted(legacy_versions)


class Command(BaseCommand):
    help = (
        "Read-only audit of issued legacy residential quotations and immutable "
        "quotation-body snapshot coverage."
    )

    def add_arguments(self, parser):
        parser.add_argument("--database", default="default")
        parser.add_argument(
            "--format",
            choices=("text", "json"),
            default="text",
            dest="output_format",
        )
        parser.add_argument(
            "--legacy-catalogue-version",
            action="append",
            dest="legacy_versions",
            help=(
                "Catalogue identifier to audit. Repeat for multiple identifiers. "
                "By default every stored non-current identifier is included."
            ),
        )

    def handle(self, *args, **options):
        rows, legacy_versions = collect_quotation_audit_rows(
            database=options["database"],
            legacy_versions=options["legacy_versions"],
        )
        counts = {
            classification: sum(
                row.classification == classification for row in rows
            )
            for classification in ("SAFE", "REVIEW", "CLOSED")
        }
        payload = {
            "read_only": True,
            "current_catalogue_version": CURRENT_CATALOGUE_VERSION,
            "audited_legacy_catalogue_versions": legacy_versions,
            "counts": counts,
            "results": [asdict(row) for row in rows],
            "limitations": [
                "Historical quotations have no quotation-specific expiry or validity field.",
                "Before immutable snapshots, issued quote bodies were represented only by quote_sent timeline events and mutable enquiry fields.",
                "Legacy event rows do not freeze the catalogue version, price, package or scope at the event timestamp.",
            ],
        }

        if options["output_format"] == "json":
            self.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False))
            return

        self.stdout.write("READ-ONLY LEGACY RESIDENTIAL QUOTATION AUDIT")
        self.stdout.write(
            f"Current catalogue: {CURRENT_CATALOGUE_VERSION} | "
            f"Legacy identifiers: {', '.join(legacy_versions) or '(none)'}"
        )
        self.stdout.write(
            "Counts: " + ", ".join(f"{key}={value}" for key, value in counts.items())
        )
        for row in rows:
            self.stdout.write("")
            self.stdout.write(
                f"[{row.classification}] {row.quote_id} | enquiry={row.enquiry_id} | "
                f"issued={row.issued_at}"
            )
            self.stdout.write(
                f"  client={row.client_name} <{row.contact_email}> | "
                f"property={row.property_reference}"
            )
            self.stdout.write(
                f"  status={row.enquiry_status} | event_status={row.quote_event_status} | "
                f"catalogue={row.catalogue_version}"
            )
            self.stdout.write(
                f"  package={row.package_name} ({row.package_code}) | "
                f"base={row.package_base_price or 'POA'} | "
                f"total={row.total_quoted_amount or 'Not stored'}"
            )
            self.stdout.write(
                f"  add_ons={json.dumps(row.add_ons, ensure_ascii=False)} | "
                f"agreed_photos={row.agreed_photograph_count or 'Not stored'}"
            )
            self.stdout.write(
                f"  scope={row.stored_scope or 'Not stored'} | "
                f"expiry={row.validity_period} | snapshot={row.quotation_body_snapshot_exists}"
            )
            self.stdout.write(
                f"  accepted_or_converted={row.accepted_or_converted} | "
                f"invoices={row.invoice_statuses or ['None']} | "
                f"appears_superseded={row.appears_superseded} | "
                f"manual_review={row.manual_review_required}"
            )
            self.stdout.write(
                "  reasons=" + ("; ".join(row.review_reasons) or "No review indicators")
            )
