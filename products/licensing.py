import hashlib
from datetime import timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.mail import EmailMessage
from django.urls import reverse
from django.utils import timezone

from .models import LicenceDocument, LicenceDeliveryToken, LicenceOffer
from .file_access import get_asset_file_name, open_asset_file
from .pdf_generator import generate_licence_schedule_pdf, generate_licence_certificate_pdf


DEFAULT_TOKEN_DAYS = int(getattr(settings, "LICENCE_DELIVERY_TOKEN_DAYS", 7))


def get_licensing_from_email():
    return getattr(settings, "LICENSING_FROM_EMAIL", settings.DEFAULT_FROM_EMAIL)


def get_latest_offer(license_request):
    return (
        LicenceOffer.objects
        .filter(license_request=license_request)
        .order_by("-version")
        .first()
    )


def get_active_offer(license_request):
    return (
        LicenceOffer.objects
        .filter(license_request=license_request, status="ACTIVE")
        .order_by("-version")
        .first()
    )


def get_current_offer(license_request):
    prefetched_offers = getattr(license_request, "prefetched_active_offers", None)
    if prefetched_offers is not None:
        offer = prefetched_offers[0] if prefetched_offers else None
    else:
        offer = get_active_offer(license_request)
    if offer and offer.is_expired:
        return None
    return offer


def has_valid_current_offer(license_request):
    return get_current_offer(license_request) is not None


def build_offer_expires_at(now=None):
    issued_at = now or timezone.now()
    expiry_days = int(getattr(settings, "LICENCE_OFFER_EXPIRY_DAYS", 7))
    return issued_at + timedelta(days=expiry_days)


def _scope_summary_lines(license_request, snapshot=None):
    if snapshot:
        lines = [
            f"- Asset: {snapshot.get('asset') or snapshot.get('asset_label') or license_request.asset}",
            f"- Project Type: {snapshot.get('project_type_display') or license_request.get_project_type_display()}",
            f"- Permitted Media: {snapshot.get('permitted_media_display') or 'Not specified'}",
            f"- Territory: {snapshot.get('territory_display') or 'Not specified'}",
            f"- Duration: {snapshot.get('duration_display') or license_request.get_duration_display()}",
            f"- Exclusivity: {snapshot.get('exclusivity_display') or 'Not specified'}",
            f"- Reach Caps: {snapshot.get('reach_caps') or 'None'}",
        ]
        quoted_price = snapshot.get('quoted_price')
        if quoted_price not in (None, ""):
            lines.append(f"- Quoted Fee: EUR {_format_currency_value(quoted_price)}")
        return lines

    territory = (
        license_request.get_territory_display()
        if license_request.territory
        else "Not specified"
    )
    permitted_media = (
        license_request.get_permitted_media_display()
        if license_request.permitted_media
        else "Not specified"
    )
    exclusivity = (
        license_request.get_exclusivity_display()
        if license_request.exclusivity
        else "Not specified"
    )
    lines = [
        f"- Asset: {license_request.asset}",
        f"- Project Type: {license_request.get_project_type_display()}",
        f"- Permitted Media: {permitted_media}",
        f"- Territory: {territory}",
        f"- Duration: {license_request.get_duration_display()}",
        f"- Exclusivity: {exclusivity}",
        f"- Reach Caps: {license_request.reach_caps or 'None'}",
    ]
    if license_request.quoted_price:
        lines.append(f"- Quoted Fee: EUR {_format_currency_value(license_request.quoted_price)}")
    return lines


def _format_currency_value(value):
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return str(value)
    return f"{amount:.2f}"


def _draft_text(value):
    return (value or "").strip()


def _build_negotiation_email_body(license_request):
    draft_text = _draft_text(license_request.ai_draft_response)
    scope_summary = "\n".join(_scope_summary_lines(license_request))

    if draft_text:
        return (
            f"Hi {license_request.client_name},\n\n"
            f"{draft_text}\n\n"
            "Current Scope Summary:\n"
            f"{scope_summary}\n\n"
            "If you would like to proceed or need any refinements, reply to this email.\n\n"
            "Kind regards,\n"
            "OpenEire Studios\n"
        )

    return (
        f"Hi {license_request.client_name},\n\n"
        "Thank you for your Rights-Managed licence enquiry.\n\n"
        "Current Scope Summary:\n"
        f"{scope_summary}\n\n"
        "If you would like to proceed or need any refinements, reply to this email.\n\n"
        "Kind regards,\n"
        "OpenEire Studios\n"
    )


def _build_payment_email_body(license_request, offer):
    draft_text = _draft_text(license_request.ai_payment_draft_response)
    scope_summary = "\n".join(
        _scope_summary_lines(
            license_request,
            snapshot=(offer.scope_snapshot if offer and offer.scope_snapshot else license_request.agreed_scope_snapshot),
        )
    )
    payment_link = (
        offer.stripe_payment_link_url
        if offer and offer.stripe_payment_link_url
        else license_request.stripe_payment_link
    )

    if not payment_link:
        raise ValueError("License request does not have a Stripe payment link.")
    if offer and offer.is_expired:
        raise ValueError("The current payment offer has expired. Generate a fresh offer before sending.")

    intro = (
        f"Hi {license_request.client_name},\n\n"
        f"{draft_text}\n\n"
        if draft_text
        else
        f"Hi {license_request.client_name},\n\n"
        "Your Rights-Managed licence request has been confirmed by our team.\n\n"
    )

    version_label = f"v{offer.version}" if offer else "current version"
    return (
        f"{intro}"
        "Agreed Scope Summary:\n"
        f"{scope_summary}\n\n"
        f"Offer Version: {version_label}\n"
        f"Offer Expiry: {_format_offer_expiry(offer)}\n"
        "To accept and pay, please use this secure payment link:\n"
        f"{payment_link}\n\n"
        "If you need any final amendments before payment, reply to this email.\n\n"
        "Kind regards,\n"
        "OpenEire Studios\n"
    )


def _format_offer_expiry(offer):
    if not offer or not offer.expires_at:
        return "No expiry"
    return timezone.localtime(offer.expires_at).strftime("%Y-%m-%d %H:%M %Z")


def get_asset_file_field(asset):
    if hasattr(asset, "high_res_file") and asset.high_res_file:
        return asset.high_res_file
    if hasattr(asset, "video_file") and asset.video_file:
        return asset.video_file
    return None


def _build_doc_filename(license_request_id, doc_type, issued_at):
    date_stamp = issued_at.strftime("%Y%m%d")
    safe_type = doc_type.lower()
    return f"licences/{license_request_id}/{safe_type}-{date_stamp}.pdf"


def _save_document(license_request, doc_type, pdf_bytes, issued_at):
    sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    filename = _build_doc_filename(license_request.id, doc_type, issued_at)
    document = LicenceDocument(
        license_request=license_request,
        doc_type=doc_type,
        sha256=sha256,
    )
    document.file.save(filename, ContentFile(pdf_bytes), save=True)
    return document


def ensure_licence_documents(license_request, issued_at=None, terms_version=None):
    issued_at = issued_at or timezone.now()
    existing = {
        doc.doc_type: doc
        for doc in LicenceDocument.objects.filter(license_request=license_request)
    }
    documents = []
    pending = {}

    if "SCHEDULE" not in existing:
        pending["SCHEDULE"] = generate_licence_schedule_pdf(
            license_request,
            issued_at=issued_at,
            terms_version=terms_version,
        )
    else:
        documents.append(existing["SCHEDULE"])

    if "CERTIFICATE" not in existing:
        pending["CERTIFICATE"] = generate_licence_certificate_pdf(
            license_request,
            issued_at=issued_at,
            terms_version=terms_version,
        )
    else:
        documents.append(existing["CERTIFICATE"])

    for doc_type in ("SCHEDULE", "CERTIFICATE"):
        if doc_type in pending:
            documents.append(_save_document(license_request, doc_type, pending[doc_type], issued_at))

    return documents


def ensure_delivery_token(license_request, days=None):
    days = days or DEFAULT_TOKEN_DAYS
    now = timezone.now()
    existing = LicenceDeliveryToken.objects.filter(
        license_request=license_request,
        used_at__isnull=True,
        expires_at__gt=now,
    ).order_by("-expires_at").first()
    if existing:
        return existing

    expires_at = now + timedelta(days=days)
    return LicenceDeliveryToken.objects.create(
        license_request=license_request,
        expires_at=expires_at,
    )


def send_licence_delivery_email(license_request, documents, download_url, token_obj):
    asset = license_request.asset
    subject = f"Your Rights-Managed Licence and Download Link: {asset}"
    remaining_days = max(1, (token_obj.expires_at - timezone.now()).days)

    body = (
        f"Hi {license_request.client_name},\n\n"
        "Thank you for your payment. Your Rights-Managed licence is now active.\n\n"
        f"Secure download link (expires in {remaining_days} days):\n"
        f"{download_url}\n\n"
        "Your signed licence documents are attached:\n"
        "- Appendix A - Licence Schedule\n"
        "- Appendix B - Licence Certificate\n\n"
        "Please retain these documents for your records.\n\n"
        "If you have any questions, reply to this email.\n\n"
        "Kind regards,\n"
        "OpenEire Studios\n"
    )

    email = EmailMessage(
        subject=subject,
        body=body,
        from_email=get_licensing_from_email(),
        to=[license_request.email],
    )

    for doc in documents:
        filename = f"{doc.doc_type.lower()}-licence-{license_request.id}.pdf"
        with doc.file.open("rb") as handle:
            email.attach(filename, handle.read(), "application/pdf")

    email.send(fail_silently=False)


def send_licence_quote_email(license_request):
    asset = license_request.asset
    if not license_request.quoted_price:
        raise ValueError("License request does not have a quoted price.")

    offer = get_current_offer(license_request) or get_latest_offer(license_request)
    body = _build_payment_email_body(license_request, offer)

    email = EmailMessage(
        subject=f"Your Rights-Managed Licence Quote and Payment Link: {asset}",
        body=body,
        from_email=get_licensing_from_email(),
        to=[license_request.email],
    )
    email.send(fail_silently=False)
    return body


def send_licence_negotiation_email(license_request):
    if not _draft_text(license_request.ai_draft_response):
        raise ValueError("License request does not have a negotiation draft response.")

    body = _build_negotiation_email_body(license_request)
    email = EmailMessage(
        subject=f"Rights-Managed Licence Negotiation: {license_request.asset}",
        body=body,
        from_email=get_licensing_from_email(),
        to=[license_request.email],
    )
    email.send(fail_silently=False)
    return body


def send_licence_initial_draft_email(license_request):
    if not license_request.ai_draft_response:
        raise ValueError("License request does not have an AI draft response.")

    send_licence_negotiation_email(license_request)


def get_licence_admin_notification_recipients():
    configured = list(getattr(settings, "LICENCE_ADMIN_NOTIFICATION_RECIPIENTS", []) or [])
    if configured:
        return configured

    admins = getattr(settings, "ADMINS", ()) or ()
    return [email for _, email in admins if email]


def send_licence_admin_notification_email(license_request):
    recipients = get_licence_admin_notification_recipients()
    if not recipients:
        return False

    asset = license_request.asset
    territory = (
        license_request.get_territory_display()
        if license_request.territory
        else "Not specified"
    )
    permitted_media = (
        license_request.get_permitted_media_display()
        if license_request.permitted_media
        else "Not specified"
    )
    exclusivity = (
        license_request.get_exclusivity_display()
        if license_request.exclusivity
        else "Not specified"
    )
    admin_path = reverse("customadmin:products_licenserequest_change", args=[license_request.id])
    admin_base_url = getattr(settings, "LICENCE_ADMIN_BASE_URL", None) or getattr(settings, "SITE_URL", None)
    admin_url = f"{str(admin_base_url).rstrip('/')}{admin_path}" if admin_base_url else admin_path

    subject = f"New licence request: {asset}"
    body = (
        "A new commercial licence request has been submitted.\n\n"
        f"Request ID: {license_request.id}\n"
        f"Submitted: {timezone.localtime(license_request.created_at).strftime('%Y-%m-%d %H:%M %Z')}\n"
        f"Asset: {asset}\n"
        f"Asset Type: {license_request.content_type.model if license_request.content_type_id else 'unknown'}\n"
        f"Client Name: {license_request.client_name}\n"
        f"Company: {license_request.company or 'Not provided'}\n"
        f"Email: {license_request.email}\n"
        f"Project Type: {license_request.get_project_type_display()}\n"
        f"Duration: {license_request.get_duration_display()}\n"
        f"Territory: {territory}\n"
        f"Permitted Media: {permitted_media}\n"
        f"Exclusivity: {exclusivity}\n"
        f"Reach Caps: {license_request.reach_caps or 'None'}\n"
        f"Message: {license_request.message or 'No message provided.'}\n\n"
        f"Review in admin: {admin_url}\n"
    )

    email = EmailMessage(
        subject=subject,
        body=body,
        from_email=get_licensing_from_email(),
        to=recipients,
    )
    email.send(fail_silently=False)
    return True
