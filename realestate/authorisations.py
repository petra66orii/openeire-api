"""Issue, accept and render private operational documents without financial side effects."""
from copy import deepcopy
from datetime import timedelta
from io import BytesIO
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.template.loader import render_to_string
from reportlab.platypus import PageBreak

from openeire_api.business_identity import BusinessIdentity, get_business_identity, public_business_context
from openeire_api.pdf_branding import OpenEirePDFTheme, branded_document, page_decoration
from .emails import get_realestate_email_logo_url
from .authorisation_forms import AuthorisationForm, CAPACITIES
from .authorisation_terms import TERMS, VERSION
from .documents import _agreed_deliverables
from .models import PropertyAuthorisation, RealEstateEnquiry


def build_snapshot(enquiry, location):
    identity = get_business_identity(private_legal_document=True)
    scope = _agreed_deliverables(enquiry)
    add_ons = enquiry.get_add_on_labels()
    capture_text = " ".join(scope + add_ons).lower()
    details = [
        ("Booking reference", f"RE-{enquiry.pk}"),
        ("Property / locations covered", location),
        ("Enquiry property address", enquiry.property_address),
        ("County / Eircode", f"{enquiry.county} {enquiry.eircode}".strip()),
        ("Property type", enquiry.get_property_type_display() if hasattr(enquiry, "get_property_type_display") else enquiry.property_type),
        ("Shoot date", str(enquiry.shoot_date or enquiry.proposed_shoot_date or enquiry.preferred_date or "Not yet agreed")),
        ("Shoot time (Europe/Dublin)", str(enquiry.shoot_time or enquiry.get_preferred_time_window_display() or "Not yet agreed")),
        ("Client / instructing person", enquiry.name),
        ("Agency / company", enquiry.company_name),
        ("Access contact", enquiry.access_contact or " ".join([enquiry.access_contact_name, enquiry.access_contact_phone]).strip()),
        ("Existing access notes", enquiry.access_notes),
        ("Selected package", enquiry.get_preferred_package_display()),
        ("Agreed scope / deliverables", "\n".join(scope) or "Refer to the agreed Booking Agreement / written scope; no scope is added by this form."),
        ("Agreed add-ons", "\n".join(add_ons) or "None recorded"),
        ("Known video requirement", "Included in recorded scope" if any(x in capture_text for x in ("video", "film")) else "Not identified in recorded scope; refer to Booking Agreement"),
        ("Known drone requirement", "Included in recorded scope" if any(x in capture_text for x in ("drone", "aerial")) else "Not identified in recorded scope; refer to Booking Agreement"),
    ]
    for field in ("location_details", "property_type_details", "bedroom_count", "floor_count", "grounds_size", "internal_floor_area", "internal_floor_area_unit", "secondary_accommodation_details", "outbuildings_details", "property_features", "occupancy_status", "on_camera_people", "audio_requirements"):
        value = getattr(enquiry, field, "")
        if value:
            display = getattr(enquiry, f"get_{field}_display", None)
            details.append((field.replace("_", " ").capitalize(), str(display() if display else value)))
    return {
        "title": "OpenÉire Studios Property Access & Shoot Authorisation",
        "version": VERSION,
        "identity": identity.as_context(),
        "details": [list(row) for row in details],
        "terms": [list(row) for row in TERMS],
        "video_included": any(x in capture_text for x in ("video", "film")),
        "initial": {"full_name": enquiry.name, "email": enquiry.email, "business_name": enquiry.company_name},
    }


def document_sections(snapshot):
    """Shared human-readable contents for HTML and PDF; no client HTML is trusted."""
    if "sections" in snapshot:
        return snapshot["sections"]
    sections = [("Booking and location", snapshot["details"])]
    sections += [(heading, [("", body)]) for heading, body in snapshot["terms"]]
    answers = snapshot.get("answers")
    if answers:
        rows = [("Name", answers["full_name"]), ("Capacity", snapshot["acceptance"]["capacity"]), ("Email", answers["email"]), ("Owner name, if different", answers.get("owner_name", "")), ("Agency / business", answers.get("business_name", ""))]
        for choice, field, label, none in [
            ("restrictions_choice", "restrictions", "Restricted areas", "None"),
            ("hazards_choice", "hazards", "Known hazards / precautions", "No known hazards to disclose"),
            ("capture_choice", "must_capture", "Essential / must-capture features", "No specific requirements - no additional specific capture instructions supplied. OpenÉire will use professional judgement within the agreed package/scope; all agreed deliverables remain intact."),
            ("video_choice", "video_requirements", "Video requirements", "No specific video requirements / N/A - professional judgement within the agreed scope."),
        ]:
            if choice == "video_choice" and not snapshot["video_included"]:
                continue
            rows.append((label, answers.get(field) if answers.get(choice) == "specific" else none))
        sections.append(("Authorising person's responses", rows))
        acceptance = snapshot["acceptance"]
        sections.append(("Accepted electronically" if acceptance["method"] == "electronic" else "Handwritten copy received", [("Name", acceptance["name"]), ("Capacity", acceptance["capacity"]), ("Email", acceptance["email"]), ("Recorded date/time", acceptance["timestamp"]), ("Date received", acceptance.get("received_on", "")), ("Typed signature", answers.get("typed_signature", ""))]))
    return sections


def freeze_sections(snapshot):
    snapshot.pop("sections", None)
    snapshot["sections"] = [[heading, [list(row) for row in rows]] for heading, rows in document_sections(snapshot)]


def generate_pdf(snapshot):
    output = BytesIO()
    theme = OpenEirePDFTheme()
    identity = BusinessIdentity(**{key.removeprefix("business_"): value for key, value in snapshot["identity"].items()})
    story = []

    def paragraph(value, style="BodyText"):
        story.append(theme.heading(value, 1 if style == "Title" else 2) if style in ("Title", "Heading2") else theme.paragraph(value, style))

    paragraph(snapshot["title"], "Title")
    paragraph(f"Document version {snapshot['version']}", "BrandMeta")
    for key, value in snapshot["identity"].items():
        if value:
            paragraph(f"{key.removeprefix('business_').replace('_', ' ').capitalize()}: {value}", "BrandMeta")
    for heading, rows in document_sections(snapshot):
        if heading == "Acceptance - choose either method":
            story.append(PageBreak())
        paragraph(heading, "Heading2")
        if heading == "Booking and location":
            story.append(theme.information_table(rows))
            continue
        if heading in ("Accepted electronically", "Handwritten copy received"):
            story.append(theme.information_table([(label, value) for label, value in rows if value]))
            continue
        for label, value in rows:
            if value:
                text = f"{label}: {value}" if label else value
                if label in ("Essential / must-capture features", "Video requirements"):
                    story.append(theme.notice(text, choice=True))
                else:
                    paragraph(text)
    if not snapshot.get("acceptance"):
        paragraph("Complete for handwritten acceptance", "Heading2")
        paragraph("Either accept using the secure page or complete these fields, sign and return this PDF. Both methods are equally acceptable.")
        fields = ["Name", "Capacity (owner / agent / representative / other - explain)", "Email", "Owner name if different / agency", "Restrictions (None, or details and locations)", "Hazards (No known hazards, or details / precautions)", "Must-capture (No specific requirements / N/A, or details and locations)"]
        if snapshot["video_included"]:
            fields.append("Video (No specific requirements / N/A, or details)")
        fields += ["Date", "Signature"]
        story.append(theme.signature_fields([label + ":" for label in fields]))

    document = branded_document(output, title=snapshot["title"], identity=identity)
    decoration = page_decoration(document_type="PROPERTY ACCESS & SHOOT AUTHORISATION", identity=identity, reference=f"Property Access & Shoot Authorisation | v{snapshot['version']}")
    document.build(story, onFirstPage=decoration, onLaterPages=decoration)
    return output.getvalue()


@transaction.atomic
def issue_authorisation(enquiry, *, location, recipient_email, user, supersedes=None):
    enquiry = RealEstateEnquiry.objects.select_for_update().get(pk=enquiry.pk)
    if not location.strip():
        raise ValueError("Specify the locations covered.")
    old = None
    if supersedes:
        old = PropertyAuthorisation.objects.select_for_update().get(pk=supersedes.pk, enquiry=enquiry)
        if old.superseded_at:
            raise ValueError("This document has already been reissued.")
    snapshot = build_snapshot(enquiry, location.strip())
    snapshot["initial"]["email"] = recipient_email
    if recipient_email.casefold() != enquiry.email.casefold():
        snapshot["initial"]["full_name"] = ""
    freeze_sections(snapshot)
    document = PropertyAuthorisation.objects.create(enquiry=enquiry, location=location.strip(), recipient_email=recipient_email, issued_snapshot=snapshot, issued_pdf=generate_pdf(snapshot), template_version=VERSION, expires_at=timezone.now() + timedelta(days=90), created_by=user, supersedes=old)
    if old:
        old.superseded_at = timezone.now()
        old.save(update_fields=["superseded_at"])
    return document


def is_available(document):
    return not document.revoked_at and not document.superseded_at and document.expires_at > timezone.now()


@transaction.atomic
def accept_authorisation(document, data, *, method="electronic", user=None):
    document = PropertyAuthorisation.objects.select_for_update().get(pk=document.pk)
    if method not in ("electronic", "handwritten"):
        raise ValueError("Invalid acceptance method.")
    if method == "handwritten" and (not user or not user.is_active or not user.is_staff or not user.has_perm("realestate.change_realestateenquiry")):
        raise ValueError("Staff permission is required to record handwritten acceptance.")
    if document.accepted_at or not is_available(document):
        raise ValueError("This authorisation is already accepted, expired, revoked or superseded. Staff must reissue it.")
    form = AuthorisationForm(data, video_included=document.issued_snapshot["video_included"], handwritten=method == "handwritten")
    if not form.is_valid():
        raise ValueError(form.errors.as_text())
    answers = dict(form.cleaned_data)
    received = answers.pop("received_on", None)
    now = timezone.now()
    capacity = dict(CAPACITIES)[answers["capacity"]]
    if answers["capacity"] == "other":
        capacity += ": " + answers["capacity_other"]
    snapshot = deepcopy(document.issued_snapshot)
    snapshot["answers"] = answers
    snapshot["acceptance"] = {"name": answers["full_name"], "email": answers["email"], "capacity": capacity, "method": method, "timestamp": timezone.localtime(now, ZoneInfo("Europe/Dublin")).isoformat(), "received_on": received.isoformat() if received else ""}
    freeze_sections(snapshot)
    document.accepted_name = answers["full_name"]
    document.accepted_email = answers["email"]
    document.accepted_capacity = capacity
    document.acceptance_method = method
    document.accepted_at = now
    document.received_on = received
    document.recorded_by = user if method == "handwritten" else None
    document.accepted_snapshot = snapshot
    document.accepted_pdf = generate_pdf(snapshot)
    document.status = method
    # Conditional write prevents a second acceptance even on databases where
    # select_for_update is unavailable. The content has been validated above.
    values = {field: getattr(document, field) for field in ("accepted_name", "accepted_email", "accepted_capacity", "acceptance_method", "accepted_at", "received_on", "recorded_by", "accepted_snapshot", "accepted_pdf", "status")}
    changed = PropertyAuthorisation.objects.filter(pk=document.pk, accepted_at__isnull=True, revoked_at__isnull=True, superseded_at__isnull=True, expires_at__gt=now).update(**values)
    if changed != 1:
        raise ValueError("This authorisation is no longer available for acceptance.")
    return document


@transaction.atomic
def send_authorisation(document, *, request):
    document = PropertyAuthorisation.objects.select_for_update().get(pk=document.pk)
    if document.accepted_at or not is_available(document):
        raise ValueError("Only an active, unaccepted authorisation can be sent.")
    url = request.build_absolute_uri(reverse("property-authorisation", args=[document.token]))
    context = {**public_business_context(), "email_logo_url": get_realestate_email_logo_url(), "cta_url": url, "cta_label": "Review & complete authorisation", "review_url": url, "pdf_url": url + "?download=1", "location": document.location}
    message = EmailMultiAlternatives(subject="Property Access & Shoot Authorisation - OpenÉire Studios", body=render_to_string("emails/real_estate/property_authorisation.txt", context), from_email=settings.DEFAULT_FROM_EMAIL, to=[document.recipient_email], reply_to=[settings.REALESTATE_REPLY_TO_EMAIL])
    message.attach_alternative(render_to_string("emails/real_estate/property_authorisation.html", context), "text/html")
    message.attach("property-access-authorisation.pdf", bytes(document.issued_pdf), "application/pdf")
    if message.send() != 1:
        raise ValueError("The email backend did not confirm sending. Authorisation remains unsent.")
    document.sent_at = timezone.now()
    document.status = PropertyAuthorisation.Status.SENT
    document.save(update_fields=["sent_at", "status"])
    return document
