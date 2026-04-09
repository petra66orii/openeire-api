import hashlib
from datetime import timedelta

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.mail import EmailMessage
from django.urls import reverse
from django.utils import timezone

from .models import LicenceDocument, LicenceDeliveryToken
from .pdf_generator import generate_licence_schedule_pdf, generate_licence_certificate_pdf


DEFAULT_TOKEN_DAYS = int(getattr(settings, "LICENCE_DELIVERY_TOKEN_DAYS", 7))


def get_licensing_from_email():
    return getattr(settings, "LICENSING_FROM_EMAIL", settings.DEFAULT_FROM_EMAIL)


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
    if not license_request.stripe_payment_link:
        raise ValueError("License request does not have a Stripe payment link.")
    if not license_request.quoted_price:
        raise ValueError("License request does not have a quoted price.")

    subject = f"Your Rights-Managed Licence Quote and Payment Link: {asset}"
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
    ai_note = ""
    if license_request.ai_draft_response:
        ai_note = (
            "\nLicensing Note (reviewed by our team):\n"
            f"{license_request.ai_draft_response}\n"
        )

    body = (
        f"Hi {license_request.client_name},\n\n"
        "Your Rights-Managed licence request has been reviewed by our licensing team.\n\n"
        "Quote Summary:\n"
        f"- Asset: {asset}\n"
        f"- Fee: EUR {license_request.quoted_price:.2f}\n"
        f"- Project Type: {license_request.get_project_type_display()}\n"
        f"- Permitted Media: {permitted_media}\n"
        f"- Territory: {territory}\n"
        f"- Duration: {license_request.get_duration_display()}\n"
        f"- Exclusivity: {exclusivity}\n"
        f"- Reach Caps: {license_request.reach_caps or 'None'}\n"
        f"{ai_note}\n"
        "To accept and pay, please use this secure payment link:\n"
        f"{license_request.stripe_payment_link}\n\n"
        "If you need amendments before payment, reply to this email and we can revise the quote.\n\n"
        "Kind regards,\n"
        "OpenEire Studios\n"
    )

    email = EmailMessage(
        subject=subject,
        body=body,
        from_email=get_licensing_from_email(),
        to=[license_request.email],
    )
    email.send(fail_silently=False)


def send_licence_initial_draft_email(license_request):
    if not license_request.ai_draft_response:
        raise ValueError("License request does not have an AI draft response.")

    asset = license_request.asset
    subject = f"Initial Rights-Managed Licence Draft: {asset}"
    body = (
        f"Hi {license_request.client_name},\n\n"
        "Thank you for your Rights-Managed licence request.\n\n"
        "Please find our initial draft response below:\n\n"
        f"{license_request.ai_draft_response}\n\n"
        "This is an initial draft only. Final quote, scope confirmation, and payment link "
        "will be issued after internal review.\n\n"
        "Kind regards,\n"
        "OpenEire Studios\n"
    )

    email = EmailMessage(
        subject=subject,
        body=body,
        from_email=get_licensing_from_email(),
        to=[license_request.email],
    )
    email.send(fail_silently=False)


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
