from io import BytesIO
from decimal import Decimal
from pathlib import Path
import hashlib
import re
from difflib import SequenceMatcher
from xml.sax.saxutils import escape as xml_escape

from django.conf import settings
from django.utils import timezone

from .file_access import get_asset_file_name, open_asset_file

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from dateutil.relativedelta import relativedelta


DEFAULT_TERMS_VERSION = getattr(settings, "LICENCE_TERMS_VERSION", "RM-1.0")
DEFAULT_MASTER_AGREEMENT = getattr(settings, "LICENCE_MASTER_AGREEMENT", None)
DEFAULT_HASH_MAX_BYTES = int(getattr(settings, "LICENCE_HASH_MAX_BYTES", 50 * 1024 * 1024))
DEFAULT_SIGNATURE_NAME = getattr(settings, "LICENCE_SIGNATURE_NAME", "Gerard Deely")
DEFAULT_SIGNATURE_TITLE = getattr(settings, "LICENCE_SIGNATURE_TITLE", "Licensing Officer")
DEFAULT_SIGNATURE_TEXT = getattr(
    settings,
    "LICENCE_SIGNATURE_TEXT",
    "Digitally issued and authorised by OpenÉire Studios. "
    "This Licence Certificate is valid and enforceable without a handwritten signature.",
)


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


def _resolve_template_path(filename):
    roots = _template_roots()
    for root in roots:
        candidate = root / filename
        if candidate.exists():
            return candidate
    # Keep a deterministic not-found path in the primary configured directory.
    return roots[0] / filename


COMMERCIAL_TEMPLATE_PATH = _resolve_template_path("COMMERCIAL RIGHTS-MANAGED LICENCE CERTIFICATE.md")
PERSONAL_TEMPLATE_PATH = _resolve_template_path("PERSONAL USE LICENSE CERTIFICATE.md")

BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def _load_template(path):
    if not path.exists():
        raise FileNotFoundError(f"Licence template not found: {path}")
    return path.read_text(encoding="utf-8")


def _safe_value(value):
    if value is None:
        return "Not specified"
    return xml_escape(str(value))


def _optional_safe_value(value):
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if text.lower() in {"not specified", "none", "n/a", "na"}:
        return ""
    return xml_escape(text)


def _looks_like_same_text(left, right, threshold=0.9):
    left_text = (left or "").strip().casefold()
    right_text = (right or "").strip().casefold()
    if not left_text or not right_text:
        return False
    if left_text == right_text:
        return True
    return SequenceMatcher(None, left_text, right_text).ratio() >= threshold


def _format_money(value):
    if value is None:
        return "Not specified"
    if isinstance(value, Decimal):
        return f"EUR {value:.2f}"
    return f"EUR {value}"


def _asset_details(license_request):
    asset = license_request.asset
    asset_type = license_request.content_type.model if license_request.content_type_id else "asset"
    asset_id = license_request.object_id
    asset_label = str(asset) if asset else "Unknown asset"
    return asset_type, asset_id, asset_label


def _compute_asset_sha256(asset):
    filename = get_asset_file_name(asset)
    if not filename:
        return "Unavailable"
    file_field = open_asset_file(asset, "rb")
    if not file_field:
        return "Unavailable"
    try:
        if getattr(file_field, "size", None) and file_field.size > DEFAULT_HASH_MAX_BYTES:
            return "Skipped (file too large)"
    except Exception:
        pass
    sha256 = hashlib.sha256()
    try:
        chunk_iter = file_field.chunks() if hasattr(file_field, "chunks") else None
        if chunk_iter is not None:
            for chunk in chunk_iter:
                sha256.update(chunk)
        else:
            while True:
                chunk = file_field.read(1024 * 1024)
                if not chunk:
                    break
                sha256.update(chunk)
        return sha256.hexdigest()
    except Exception:
        return "Unavailable"
    finally:
        try:
            file_field.close()
        except Exception:
            pass


def _apply_metadata(canvas, doc, title, license_request, terms_version):
    asset_type, asset_id, asset_label = _asset_details(license_request)
    territory = license_request.get_territory_display() if license_request.territory else "Not specified"
    duration = license_request.get_duration_display()
    keywords = (
        f"license_id={license_request.id}; "
        f"asset_type={asset_type}; "
        f"asset_id={asset_id}; "
        f"asset_label={asset_label}; "
        f"territory={territory}; "
        f"duration={duration}; "
        f"terms_version={terms_version}"
    )

    canvas.setTitle(title)
    canvas.setAuthor("OpenÉire Studios")
    canvas.setCreator("OpenÉire Studios Licensing System")
    canvas.setSubject("Rights-Managed Licence")
    canvas.setKeywords(keywords)


def _format_date(value):
    return value.strftime("%d/%m/%Y")


def _compute_expiry_date(license_request, issued_at):
    duration = license_request.duration
    if duration == "PERPETUAL":
        return "Perpetual"
    if duration == "OTHER":
        return "As agreed"
    delta_map = {
        "1_MONTH": relativedelta(months=1),
        "3_MONTHS": relativedelta(months=3),
        "6_MONTHS": relativedelta(months=6),
        "1_YEAR": relativedelta(years=1),
        "2_YEARS": relativedelta(years=2),
        "5_YEARS": relativedelta(years=5),
    }
    delta = delta_map.get(duration)
    if not delta:
        return _format_date(issued_at)
    return _format_date(issued_at + delta)


def _build_asset_table(license_request):
    asset = license_request.asset
    filename = Path(get_asset_file_name(asset) or "").name or "Not specified"
    file_format = filename.split(".")[-1].upper() if "." in filename else "Not specified"
    resolution = getattr(asset, "resolution", None) or "Not specified"
    asset_hash = _compute_asset_sha256(asset)

    return [
        [
            _safe_value(license_request.object_id),
            _safe_value(getattr(asset, "title", str(asset))),
            _safe_value(filename),
            _safe_value(resolution),
            _safe_value(file_format),
            _safe_value(asset_hash),
        ]
    ]


def _build_scope_table(license_request):
    permitted_media = license_request.get_permitted_media_display() if license_request.permitted_media else "Not specified"
    media_value = (license_request.permitted_media or "").upper()

    paid_ads = "Permitted (as defined)" if media_value in {"PAID_DIGITAL", "ALL_MEDIA"} else "Not permitted"
    print_usage = "Permitted (as defined)" if media_value in {"PRINT_BROCHURE", "ALL_MEDIA"} else "Not permitted"
    broadcast = "Permitted (as defined)" if media_value in {"BROADCAST", "ALL_MEDIA"} else "Not permitted"

    return [
        [_safe_value("Permitted Media"), _safe_value(permitted_media)],
        [_safe_value("Paid Advertising"), _safe_value(paid_ads)],
        [_safe_value("Print Usage"), _safe_value(print_usage)],
        [_safe_value("Broadcast"), _safe_value(broadcast)],
        [_safe_value("Territory"), _safe_value(license_request.get_territory_display() if license_request.territory else "Not specified")],
        [_safe_value("Duration"), _safe_value(license_request.get_duration_display())],
        [_safe_value("Exclusivity"), _safe_value(license_request.get_exclusivity_display() if license_request.exclusivity else "Not specified")],
        [_safe_value("Campaign / Project"), _safe_value(license_request.get_project_type_display())],
        [_safe_value("Reach Cap"), _safe_value(license_request.reach_caps or "None")],
        [_safe_value("Ad Spend Cap"), _safe_value("Not specified")],
        [_safe_value("Modifications Allowed"), _safe_value("As agreed in writing")],
    ]


def _replace_table(markdown_text, new_table_markdown, table_index=0):
    lines = markdown_text.splitlines()
    tables = []
    i = 0
    while i < len(lines):
        if lines[i].strip().startswith("|") and i + 1 < len(lines) and lines[i + 1].strip().startswith("|"):
            start = i
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                i += 1
            tables.append((start, i))
        else:
            i += 1
    if table_index >= len(tables):
        return markdown_text
    start, end = tables[table_index]
    return "\n".join(lines[:start] + [new_table_markdown] + lines[end:])


def _apply_basic_markdown(text):
    return BOLD_RE.sub(r"<b>\1</b>", text)


def _markdown_table_from_rows(headers, rows):
    header_line = "| " + " | ".join(headers) + " |"
    separator = "| " + " | ".join(["---"] * len(headers)) + " |"
    body = ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join([header_line, separator] + body)


def _render_markdown_to_flowables(markdown_text):
    styles = getSampleStyleSheet()
    title_style = styles["Title"]
    h1 = styles["Heading1"]
    h2 = styles["Heading2"]
    h3 = styles["Heading3"]
    normal = styles["BodyText"]
    normal.leading = 14

    elements = []
    lines = markdown_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip():
            i += 1
            continue

        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            content = line[level:].strip()
            content = _apply_basic_markdown(content)
            if level == 1:
                style = title_style
            elif level == 2:
                style = h2
            else:
                style = h3
            elements.append(Paragraph(content, style))
            elements.append(Spacer(1, 6))
            i += 1
            continue

        if line.strip() == "---":
            elements.append(Spacer(1, 12))
            i += 1
            continue

        if line.lstrip().startswith("- "):
            bullets = []
            while i < len(lines) and lines[i].lstrip().startswith("- "):
                bullets.append(lines[i].lstrip()[2:].strip())
                i += 1
            for bullet in bullets:
                bullet_text = _apply_basic_markdown(bullet)
                elements.append(Paragraph(bullet_text, normal, bulletText="•"))
            elements.append(Spacer(1, 6))
            continue

        if re.match(r"^\s*\d+\.\s+", line):
            numbered = []
            while i < len(lines) and re.match(r"^\s*\d+\.\s+", lines[i]):
                numbered.append(lines[i].strip())
                i += 1
            for item in numbered:
                num, text = item.split(".", 1)
                bullet_text = _apply_basic_markdown(text.strip())
                elements.append(Paragraph(bullet_text, normal, bulletText=f"{num}."))
            elements.append(Spacer(1, 6))
            continue

        if line.strip().startswith("|") and i + 1 < len(lines) and lines[i + 1].strip().startswith("|"):
            header = [cell.strip() for cell in line.strip().strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                row = [cell.strip() for cell in lines[i].strip().strip("|").split("|")]
                rows.append(row)
                i += 1
            table_data = [[Paragraph(_apply_basic_markdown(cell), normal) for cell in header]]
            table_data.extend(
                [[Paragraph(_apply_basic_markdown(cell), normal) for cell in row] for row in rows]
            )
            table = Table(table_data, hAlign="LEFT")
            table.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            elements.append(table)
            elements.append(Spacer(1, 12))
            continue

        paragraph_lines = [line]
        i += 1
        while i < len(lines):
            next_line = lines[i]
            if (
                not next_line.strip()
                or next_line.strip() == "---"
                or next_line.startswith("#")
                or next_line.lstrip().startswith("- ")
                or (next_line.strip().startswith("|") and i + 1 < len(lines) and lines[i + 1].strip().startswith("|"))
            ):
                break
            paragraph_lines.append(next_line.rstrip())
            i += 1
        paragraph_text = " ".join(part.strip() for part in paragraph_lines)
        paragraph_text = _apply_basic_markdown(paragraph_text)
        elements.append(Paragraph(paragraph_text, normal))
        elements.append(Spacer(1, 6))

    return elements


def generate_licence_schedule_pdf(license_request, issued_at=None, terms_version=None):
    issued_at = issued_at or timezone.now()
    terms_version = terms_version or DEFAULT_TERMS_VERSION
    buffer = BytesIO()

    styles = getSampleStyleSheet()
    title_style = styles["Title"]
    normal = styles["BodyText"]
    normal.leading = 14

    elements = []
    elements.append(Paragraph("Appendix A - Licence Schedule", title_style))
    elements.append(Spacer(1, 12))

    asset_type, asset_id, asset_label = _asset_details(license_request)

    rows = [
        ("Licence ID", _safe_value(license_request.id)),
        ("Issued On", _safe_value(issued_at.strftime("%d %B %Y"))),
        ("Client Name", _safe_value(license_request.client_name)),
        ("Company", _safe_value(license_request.company or "Not specified")),
        ("Email", _safe_value(license_request.email)),
        ("Asset", _safe_value(asset_label)),
        ("Asset Type", _safe_value(asset_type)),
        ("Asset ID", _safe_value(asset_id)),
        ("Project Type", _safe_value(license_request.get_project_type_display())),
        ("Permitted Media", _safe_value(license_request.get_permitted_media_display() if license_request.permitted_media else "Not specified")),
        ("Territory", _safe_value(license_request.get_territory_display() if license_request.territory else "Not specified")),
        ("Duration", _safe_value(license_request.get_duration_display())),
        ("Exclusivity", _safe_value(license_request.get_exclusivity_display() if license_request.exclusivity else "Not specified")),
        ("Reach Caps", _safe_value(license_request.reach_caps or "Not specified")),
        ("Quoted Fee", _safe_value(_format_money(license_request.quoted_price))),
        ("Terms Version", _safe_value(terms_version)),
    ]

    table_data = [
        [Paragraph(f"<b>{label}</b>", normal), Paragraph(value, normal)]
        for label, value in rows
    ]

    table = Table(table_data, colWidths=[160, 360])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    elements.append(table)

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        title="Appendix A - Licence Schedule",
        author="OpenÉire Studios",
    )
    doc.build(
        elements,
        onFirstPage=lambda canvas, doc: _apply_metadata(
            canvas, doc, "Appendix A - Licence Schedule", license_request, terms_version
        ),
        onLaterPages=lambda canvas, doc: _apply_metadata(
            canvas, doc, "Appendix A - Licence Schedule", license_request, terms_version
        ),
    )

    return buffer.getvalue()


def generate_licence_certificate_pdf(license_request, issued_at=None, terms_version=None):
    issued_at = issued_at or timezone.now()
    terms_version = terms_version or DEFAULT_TERMS_VERSION
    buffer = BytesIO()

    template_text = _load_template(COMMERCIAL_TEMPLATE_PATH)
    issue_date = _format_date(issued_at)
    activation_date = issue_date
    expiry_date = _compute_expiry_date(license_request, issued_at)

    licensor_name = _safe_value(getattr(settings, "LICENSOR_NAME", "OpenÉire Studios"))
    licensor_registered_name = _optional_safe_value(getattr(settings, "LICENSOR_REGISTERED_NAME", licensor_name)) or licensor_name
    licensor_address = _optional_safe_value(getattr(settings, "LICENSOR_ADDRESS", ""))
    licensor_registration = _optional_safe_value(getattr(settings, "LICENSOR_REGISTRATION_NUMBER", ""))
    licensor_email = _optional_safe_value(getattr(settings, "LICENSOR_CONTACT_EMAIL", settings.DEFAULT_FROM_EMAIL or ""))
    licensor_website = _optional_safe_value(getattr(settings, "LICENSOR_WEBSITE", ""))

    licensee_name = _safe_value(license_request.company or license_request.client_name)
    licensee_registration = _optional_safe_value("")
    licensee_address = _optional_safe_value("")
    licensee_email = _optional_safe_value(license_request.email)

    signature_name = _safe_value(DEFAULT_SIGNATURE_NAME)
    signature_title = _safe_value(DEFAULT_SIGNATURE_TITLE)
    signature_text = _safe_value(DEFAULT_SIGNATURE_TEXT)

    lines = []
    seen_licensor_name = False
    current_party = None
    in_parties_section = False
    last_nonempty_line = None
    for raw_line in template_text.splitlines():
        line = raw_line
        stripped = line.strip()

        if stripped.startswith("## 1. Parties"):
            in_parties_section = True
        elif in_parties_section and stripped == "---":
            in_parties_section = False

        if line.strip() == licensor_name:
            if seen_licensor_name and current_party == "licensor":
                continue
            seen_licensor_name = True
        if line.strip() == "Licensor":
            current_party = "licensor"
        elif line.strip() == "Licensee":
            current_party = "licensee"

        if line.startswith("Licence ID:"):
            line = f"Licence ID: {_safe_value(license_request.id)}"
        elif line.startswith("Master Agreement Version:") and DEFAULT_MASTER_AGREEMENT:
            line = f"Master Agreement Version: {DEFAULT_MASTER_AGREEMENT}"
        elif line.startswith("Issue Date:"):
            line = f"Issue Date: {_safe_value(issue_date)}"
        elif line.startswith("Activation Date:"):
            line = f"Activation Date: {_safe_value(activation_date)}"
        elif line.startswith("Expiry Date:"):
            line = f"Expiry Date: {_safe_value(expiry_date)}"
        elif line.strip() == "[Registered Business Name]":
            line = licensor_registered_name
        elif line.strip() == "[Registered Address]" and current_party == "licensor":
            line = licensor_address
        elif line.strip() == "[Business Registration Number, if applicable]" and current_party == "licensor":
            line = licensor_registration
        elif line.strip() == "[Contact Email]" and current_party == "licensor":
            line = licensor_email
        elif line.strip() == "[Website]" and current_party == "licensor":
            line = licensor_website
        elif line.strip() == "[Legal Entity Name]":
            line = licensee_name
        elif line.strip() == "[Company Registration Number, if applicable]" and current_party == "licensee":
            line = licensee_registration
        elif line.strip() == "[Registered Address]" and current_party == "licensee":
            line = licensee_address
        elif line.strip() == "[Contact Email]" and current_party == "licensee":
            line = licensee_email
        elif line.startswith("Name:"):
            line = f"Name: {signature_name}"
        elif line.startswith("Title:"):
            line = f"Title: {signature_title}"
        elif line.startswith("Date:"):
            line = f"Date: {_safe_value(issue_date)}"
        elif line.startswith("Digital Signature:"):
            line = f"Digital Signature: {signature_text}"

        if (
            in_parties_section
            and current_party == "licensor"
            and line.strip()
            and last_nonempty_line
            and _looks_like_same_text(line, last_nonempty_line)
        ):
            continue

        lines.append(line)
        if line.strip():
            last_nonempty_line = line

    template_text = "\n".join(lines)

    asset_table = _markdown_table_from_rows(
        ["Asset ID", "Title", "File Name", "Resolution", "File Format", "SHA-256 File Hash"],
        _build_asset_table(license_request),
    )
    scope_table = _markdown_table_from_rows(
        ["Variable", "Defined Scope"],
        _build_scope_table(license_request),
    )

    template_text = _replace_table(template_text, asset_table, table_index=0)
    template_text = _replace_table(template_text, scope_table, table_index=1)

    elements = _render_markdown_to_flowables(template_text)

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        title="Appendix B - Licence Certificate",
        author="OpenÉire Studios",
    )
    doc.build(
        elements,
        onFirstPage=lambda canvas, doc: _apply_metadata(
            canvas, doc, "Appendix B - Licence Certificate", license_request, terms_version
        ),
        onLaterPages=lambda canvas, doc: _apply_metadata(
            canvas, doc, "Appendix B - Licence Certificate", license_request, terms_version
        ),
    )

    return buffer.getvalue()
