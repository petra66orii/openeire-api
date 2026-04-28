from functools import lru_cache
from io import BytesIO
from pathlib import Path
from datetime import timedelta
from urllib.parse import urljoin
from xml.sax.saxutils import escape as xml_escape

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from openeire_api.mail_utils import BRAND_DISPLAY_NAME, get_contact_email_address, resolve_order_display_name

from .models import PersonalLicenceToken


PERSONAL_LICENCE_FILENAME = "PERSONAL USE LICENSE CERTIFICATE.md"
DEFAULT_PERSONAL_TERMS_VERSION = "PERSONAL v1.1 - March 2026"
DEFAULT_PERSONAL_TERMS_SUMMARY = (
    "Personal use only (no business, marketing, or client use).",
    "You may store the file on your personal devices and keep personal backups.",
    "You may not resell, redistribute, or upload source files to stock/POD/marketplace platforms.",
    "AI training, dataset creation, and generative model use are prohibited.",
)
DEFAULT_TOKEN_DAYS = int(getattr(settings, "PERSONAL_LICENCE_TOKEN_DAYS", 7))


def get_personal_terms_version():
    return getattr(settings, "PERSONAL_TERMS_VERSION", DEFAULT_PERSONAL_TERMS_VERSION)


def _template_roots():
    roots = []
    configured_dir = getattr(settings, "LICENCE_TEMPLATE_DIR", None)
    if configured_dir:
        roots.append(Path(configured_dir))

    base_dir = Path(getattr(settings, "BASE_DIR", Path(__file__).resolve().parents[1]))
    roots.extend([base_dir, base_dir.parent, Path(__file__).resolve().parents[2]])

    unique_roots = []
    seen = set()
    for root in roots:
        root_str = str(root)
        if root_str in seen:
            continue
        seen.add(root_str)
        unique_roots.append(root)
    return unique_roots


@lru_cache(maxsize=1)
def resolve_personal_licence_path():
    roots = _template_roots()
    for root in roots:
        candidate = root / PERSONAL_LICENCE_FILENAME
        if candidate.exists():
            return candidate
    return roots[0] / PERSONAL_LICENCE_FILENAME


@lru_cache(maxsize=1)
def get_personal_licence_text():
    path = resolve_personal_licence_path()
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def get_personal_licence_summary():
    summary = getattr(settings, "PERSONAL_TERMS_SUMMARY", DEFAULT_PERSONAL_TERMS_SUMMARY)
    return list(summary)


def get_personal_licence_url(request=None):
    configured = getattr(settings, "PERSONAL_TERMS_PUBLIC_URL", None)
    if configured:
        return configured

    path = reverse("personal-licence-text")
    if request is not None:
        return request.build_absolute_uri(path)
    return path


def ensure_personal_licence_token(order, days=None):
    if days is None:
        days = DEFAULT_TOKEN_DAYS
    now = timezone.now()
    existing = (
        PersonalLicenceToken.objects.filter(
            order=order,
            expires_at__gt=now,
            used_at__isnull=True,
        )
        .order_by("-expires_at")
        .first()
    )
    if existing:
        return existing

    expires_at = now + timedelta(days=days)
    return PersonalLicenceToken.objects.create(order=order, expires_at=expires_at)


def build_personal_licence_download_url(order, request=None):
    token_obj = ensure_personal_licence_token(order)
    path = reverse("personal-licence-download", args=[str(token_obj.token)])
    base_url = getattr(settings, "PERSONAL_DOWNLOAD_BASE_URL", None)
    if base_url:
        return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    if request is not None:
        return request.build_absolute_uri(path)
    raise RuntimeError(
        "PERSONAL_DOWNLOAD_BASE_URL must be configured when generating personal licence links without a request context."
    )


def build_personal_licence_filename(order):
    return f"openeire-personal-licence-{order.id}.pdf"


def generate_personal_licence_pdf(order):
    buffer = BytesIO()
    styles = getSampleStyleSheet()
    title_style = styles["Title"]
    heading_style = styles["Heading2"]
    body_style = styles["BodyText"]
    body_style.leading = 14

    display_name = xml_escape(resolve_order_display_name(order))
    contact_email = xml_escape(get_contact_email_address())
    terms_version = xml_escape(order.personal_terms_version or get_personal_terms_version())
    order_reference = xml_escape(order.order_number or str(order.id))
    issued_at = order.date or timezone.now()

    elements = [
        Paragraph(f"{BRAND_DISPLAY_NAME} Personal Use Licence", title_style),
        Spacer(1, 12),
        Paragraph(f"<b>Order reference:</b> {order_reference}", body_style),
        Paragraph(
            f"<b>Issued to:</b> {display_name} ({xml_escape(order.email)})",
            body_style,
        ),
        Paragraph(
            f"<b>Issued on:</b> {issued_at.strftime('%d %B %Y')}",
            body_style,
        ),
        Paragraph(f"<b>Terms version:</b> {terms_version}", body_style),
        Spacer(1, 12),
        Paragraph("Summary", heading_style),
        Spacer(1, 6),
    ]

    for line in get_personal_licence_summary():
        elements.append(Paragraph(xml_escape(f"- {line}"), body_style))
        elements.append(Spacer(1, 4))

    elements.extend(
        [
            Spacer(1, 12),
            Paragraph("Full Personal Use Licence", heading_style),
            Spacer(1, 6),
        ]
    )

    licence_text = get_personal_licence_text() or "The full personal licence text is currently unavailable."
    for block in licence_text.split("\n\n"):
        cleaned = block.strip()
        if not cleaned:
            continue
        paragraph = xml_escape(cleaned).replace("\n", "<br/>")
        elements.append(Paragraph(paragraph, body_style))
        elements.append(Spacer(1, 8))

    elements.extend(
        [
            Spacer(1, 8),
            Paragraph(
                f"For support, contact {contact_email}.",
                body_style,
            ),
        ]
    )

    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        title=f"{BRAND_DISPLAY_NAME} Personal Use Licence",
        author=BRAND_DISPLAY_NAME,
    )
    document.build(elements)
    return buffer.getvalue()
