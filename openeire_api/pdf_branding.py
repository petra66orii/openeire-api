"""Opt-in OpenÉire document styling, aligned with emails/base_email.html.

Only presentation belongs here: callers own wording, identity policy, snapshots
and financial calculations. No network or custom-font dependency is required.
"""
from functools import lru_cache
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

from .business_identity import get_business_identity


CHARCOAL = colors.HexColor("#1A1A1A")
GREEN = colors.HexColor("#16A34A")
YELLOW = colors.HexColor("#FFC400")
WARM_WHITE = colors.HexColor("#FAFAF9")
BORDER = colors.HexColor("#E5E7EB")
MUTED = colors.HexColor("#5F6673")
PALE_GREEN = colors.HexColor("#EDF7F0")
MARGIN = 18 * mm
CONTENT_WIDTH = A4[0] - 2 * MARGIN
LOGO_PATH = Path(__file__).resolve().parents[1] / "static" / "emails" / "openeire-studios-logo.png"


@lru_cache(maxsize=1)
def local_logo():
    """Trim transparent padding for layout; never alter the canonical asset."""
    try:
        with Image.open(LOGO_PATH) as source:
            logo = source.convert("RGBA")
            bounds = logo.getbbox()
            if not bounds:
                return None
            logo = logo.crop(bounds)
            return ImageReader(logo), logo.width / logo.height
    except (OSError, ValueError):
        return None


def plain(value):
    return escape(str(value)).replace("\n", "<br/>")


class SectionHeading(Paragraph):
    def draw(self):
        self.canv.saveState()
        self.canv.setStrokeColor(GREEN)
        self.canv.setLineWidth(2)
        self.canv.line(-6, 2, -6, min(self.height, 13))
        self.canv.restoreState()
        super().draw()


class OpenEirePDFTheme:
    def __init__(self):
        self.styles = getSampleStyleSheet()
        for name in ("Normal", "BodyText"):
            self.styles[name].fontName = "Helvetica"
            self.styles[name].fontSize = 10
            self.styles[name].leading = 14
            self.styles[name].textColor = CHARCOAL
            self.styles[name].splitLongWords = True
            self.styles[name].allowWidows = False
            self.styles[name].allowOrphans = False
        title = self.styles["Title"]
        title.fontName = "Helvetica-Bold"
        title.fontSize = 18
        title.leading = 23
        title.alignment = 0
        title.textColor = CHARCOAL
        title.spaceAfter = 14
        title.keepWithNext = True
        for name, size in (("Heading1", 16), ("Heading2", 12), ("Heading3", 11)):
            style = self.styles[name]
            style.fontName = "Helvetica-Bold"
            style.fontSize = size
            style.leading = size + 4
            style.textColor = CHARCOAL
            style.spaceBefore = 14
            style.spaceAfter = 8
            style.keepWithNext = True
        for name, properties in {
            "BrandMeta": dict(fontSize=9, leading=12, textColor=MUTED, spaceAfter=7),
            "BrandCell": dict(fontSize=9.5, leading=13, spaceBefore=0, spaceAfter=0),
            "BrandLabel": dict(fontSize=9.5, leading=13, fontName="Helvetica-Bold", spaceBefore=0, spaceAfter=0),
            "BrandTotal": dict(fontSize=12, leading=16, fontName="Helvetica-Bold", spaceBefore=0, spaceAfter=0),
            "BrandNotice": dict(fontSize=9.5, leading=13, backColor=WARM_WHITE, borderColor=BORDER, borderWidth=.5, borderPadding=9, spaceBefore=12, spaceAfter=12),
            "BrandChoice": dict(fontSize=10, leading=14, backColor=PALE_GREEN, borderPadding=9, spaceBefore=12, spaceAfter=12),
            "BrandHeader": dict(fontSize=9, leading=12, textColor=colors.white, fontName="Helvetica-Bold", alignment=2),
        }.items():
            self.styles.add(ParagraphStyle(name, parent=self.styles["BodyText"], **properties))

    def paragraph(self, value, style="BodyText"):
        return Paragraph(plain(value), self.styles[style])

    def heading(self, text, level=2, *, markup=False):
        style = self.styles["Title" if level == 1 else "Heading2" if level == 2 else "Heading3"]
        cls = Paragraph if level == 1 else SectionHeading
        return cls(text if markup else plain(text), style)

    def table_style(self, *, header=False):
        commands = [
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 9),
            ("RIGHTPADDING", (0, 0), (-1, -1), 9),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LINEBELOW", (0, 0), (-1, -1), .35, BORDER),
        ]
        if header:
            commands += [("BACKGROUND", (0, 0), (-1, 0), PALE_GREEN), ("LINEBELOW", (0, 0), (-1, 0), .7, GREEN)]
        return TableStyle(commands)

    def information_table(self, rows, *, width=CONTENT_WIDTH, financial=False, emphasize=()):
        data = []
        highlights = []
        for index, (label, value) in enumerate(rows):
            prominent = label in emphasize or (financial and label in ("Total", "Paid", "Outstanding", "Amount", "Remaining balance"))
            style = "BrandTotal" if prominent else "BrandCell"
            data.append([self.paragraph(label, "BrandLabel"), self.paragraph(value, style)])
            if prominent:
                highlights.append(("BACKGROUND", (0, index), (-1, index), PALE_GREEN))
        table = Table(data, colWidths=[width * .34, width * .66], hAlign="LEFT", splitByRow=1, splitInRow=1)
        table.setStyle(self.table_style())
        table.setStyle(TableStyle(highlights))
        return table

    def paired_information(self, left_rows, right_rows, *, width=CONTENT_WIDTH, emphasize=()):
        column_width = (width - 16) / 2
        left = self.information_table(left_rows, width=column_width, emphasize=emphasize)
        right = self.information_table(right_rows, width=column_width)
        if max(block.wrap(column_width, 1000)[1] for block in (left, right)) > 225:
            # Long job/location data needs full-width, independently splittable rows.
            return [self.information_table(left_rows, width=width, emphasize=emphasize), self.information_table(right_rows, width=width)]
        table = Table([[left, right]], colWidths=[width / 2, width / 2], hAlign="LEFT")
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        return [table]

    def notice(self, value, *, choice=False):
        return self.paragraph(value, "BrandChoice" if choice else "BrandNotice")

    def signature_fields(self, labels, *, width=CONTENT_WIDTH):
        # Each row can move intact to the next page; ample writing space is kept.
        rows = [[self.paragraph(label, "BrandLabel")] for label in labels]
        table = Table(rows, colWidths=[width], hAlign="LEFT", splitByRow=1)
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 9),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 20),
            ("LINEBELOW", (0, 0), (-1, -1), .5, BORDER),
        ]))
        return table


def branded_document(buffer, *, title, identity=None, page_compression=1):
    identity = identity or get_business_identity()
    return SimpleDocTemplate(buffer, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=36 * mm, bottomMargin=23 * mm, title=title, author=identity.display_name, pageCompression=page_compression)


def page_decoration(*, document_type, identity=None, reference=""):
    identity = identity or get_business_identity()
    theme = OpenEirePDFTheme()

    def draw(canvas, document):
        width, height = document.pagesize
        canvas.saveState()
        x, y = document.leftMargin, height - 29 * mm
        canvas.setFillColor(CHARCOAL)
        canvas.roundRect(x, y, document.width, 16 * mm, 4, fill=1, stroke=0)
        logo = local_logo()
        if logo and identity.display_name == get_business_identity().display_name:
            reader, aspect = logo
            logo_width = 105
            canvas.drawImage(reader, x + 12, y + (16 * mm - logo_width / aspect) / 2, width=logo_width, height=logo_width / aspect, mask="auto")
        else:
            canvas.setFont("Helvetica-Bold", 14)
            canvas.setFillColor(colors.white)
            canvas.drawString(x + 12, y + 17, identity.display_name)
        label = theme.paragraph(document_type, "BrandHeader")
        _, label_height = label.wrap(document.width * .55, 40)
        label.drawOn(canvas, x + document.width * .45 - 12, y + (16 * mm - label_height) / 2)
        canvas.setStrokeColor(GREEN)
        canvas.setLineWidth(2)
        canvas.line(x, y - 4, x + 58, y - 4)
        canvas.setStrokeColor(YELLOW)
        canvas.line(x + 61, y - 4, x + 70, y - 4)
        footer_y = 14 * mm
        canvas.setStrokeColor(BORDER)
        canvas.setLineWidth(.5)
        canvas.line(x, footer_y + 8, x + document.width, footer_y + 8)
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 8)
        footer = " | ".join(filter(None, (identity.display_name, identity.email, "openeire.ie")))
        canvas.drawString(x, footer_y - 4, footer)
        canvas.drawRightString(x + document.width, footer_y - 4, f"Page {document.page}")
        if reference:
            canvas.setFont("Helvetica", 7.5)
            canvas.drawString(x, footer_y - 15, reference)
        canvas.restoreState()

    return draw
