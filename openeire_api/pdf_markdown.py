import re

from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, Spacer, Table, TableStyle


BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
NUMBERED_RE = re.compile(r"^\s*\d+\.\s+")


def apply_basic_markdown(text):
    text = BOLD_RE.sub(r"<b>\1</b>", str(text))
    return ITALIC_RE.sub(r"<i>\1</i>", text)


def render_markdown_to_flowables(
    markdown_text,
    *,
    table_width=None,
    keep_headings_with_next=False,
):
    styles = getSampleStyleSheet()
    title_style = styles["Title"]
    h2 = styles["Heading2"]
    h3 = styles["Heading3"]
    normal = styles["BodyText"]
    normal.leading = 14
    normal.splitLongWords = True
    normal.allowWidows = False
    normal.allowOrphans = False
    if keep_headings_with_next:
        h2.keepWithNext = True
        h3.keepWithNext = True

    elements = []
    lines = str(markdown_text or "").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip("\r")
        if not line.strip():
            i += 1
            continue

        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            content = apply_basic_markdown(line[level:].strip())
            style = title_style if level == 1 else h2 if level == 2 else h3
            elements.append(Paragraph(content, style))
            if not (keep_headings_with_next and level > 1):
                elements.append(Spacer(1, 6))
            i += 1
            continue

        if line.strip() == "---":
            elements.append(Spacer(1, 12))
            i += 1
            continue

        if line.lstrip().startswith("- "):
            while i < len(lines) and lines[i].lstrip().startswith("- "):
                bullet_text = apply_basic_markdown(lines[i].lstrip()[2:].strip())
                elements.append(Paragraph(bullet_text, normal, bulletText="\u2022"))
                i += 1
            elements.append(Spacer(1, 6))
            continue

        if NUMBERED_RE.match(line):
            while i < len(lines) and NUMBERED_RE.match(lines[i]):
                item = lines[i].strip()
                num, text = item.split(".", 1)
                bullet_text = apply_basic_markdown(text.strip())
                elements.append(Paragraph(bullet_text, normal, bulletText=f"{num}."))
                i += 1
            elements.append(Spacer(1, 6))
            continue

        if line.strip().startswith("|") and i + 1 < len(lines) and lines[i + 1].strip().startswith("|"):
            header = [cell.strip() for cell in line.strip().strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([cell.strip() for cell in lines[i].strip().strip("|").split("|")])
                i += 1

            table_data = [[Paragraph(apply_basic_markdown(cell), normal) for cell in header]]
            table_data.extend(
                [[Paragraph(apply_basic_markdown(cell), normal) for cell in row] for row in rows]
            )
            col_widths = None
            if table_width and header:
                if len(header) == 2:
                    col_widths = [table_width * 0.34, table_width * 0.66]
                else:
                    col_widths = [table_width / len(header)] * len(header)
            table = Table(
                table_data,
                colWidths=col_widths,
                hAlign="LEFT",
                repeatRows=1,
                splitByRow=1,
            )
            table.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#262626")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EDEDED")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8F8F8")]),
                ("LINEBELOW", (0, 0), (-1, 0), 0.75, colors.HexColor("#8C8C8C")),
                ("LINEBELOW", (0, 1), (-1, -2), 0.25, colors.HexColor("#D9D9D9")),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
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
                or NUMBERED_RE.match(next_line)
                or (
                    next_line.strip().startswith("|")
                    and i + 1 < len(lines)
                    and lines[i + 1].strip().startswith("|")
                )
            ):
                break
            paragraph_lines.append(next_line.rstrip("\r"))
            i += 1

        paragraph_text = ""
        for part in paragraph_lines:
            stripped = part.strip()
            if not paragraph_text:
                paragraph_text = stripped
            elif paragraph_text.endswith("<br/>"):
                paragraph_text += stripped
            else:
                paragraph_text += f" {stripped}"
            if part.endswith("  "):
                paragraph_text += "<br/>"
        elements.append(Paragraph(apply_basic_markdown(paragraph_text), normal))
        elements.append(Spacer(1, 6))

    return elements
