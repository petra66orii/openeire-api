from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table

from openeire_api.business_identity import get_business_identity


VAT_NOTICE = "VAT not applicable — supplier not VAT registered."
RELEASE_NOTICE = (
    "Final high-resolution media and usage rights are released once payment "
    "has been received in full."
)


def _pdf(title, rows, notices):
    identity = get_business_identity()
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer, pagesize=A4, title=title, author=identity.display_name, pageCompression=0
    )
    styles = getSampleStyleSheet()
    story = [Paragraph(identity.display_name, styles["Title"])]
    for value in (identity.address, identity.email, identity.phone):
        if value:
            story.append(Paragraph(value, styles["Normal"]))
    story.extend((Spacer(1, 16), Paragraph(title, styles["Heading1"]), Table(rows, hAlign="LEFT")))
    for notice in notices:
        story.extend((Spacer(1, 10), Paragraph(notice, styles["Normal"])))
    document.build(story)
    return buffer.getvalue()


def build_invoice_filename(invoice):
    return f"{invoice.invoice_number}.pdf"


def generate_invoice_pdf(invoice):
    enquiry = invoice.enquiry
    deliverables = {
        "pro": "25 edited interior/exterior photographs; 5–8 aerial stills; ground/aerial video; social cuts; commercial marketing licence",
    }.get(enquiry.preferred_package, enquiry.get_preferred_package_summary())
    payment_refs = ", ".join(
        filter(None, invoice.payments.filter(status="succeeded").values_list("external_reference", flat=True))
    ) or "—"
    rows = (
        ("Invoice number", invoice.invoice_number),
        ("Customer", invoice.customer_name_snapshot),
        ("Company", invoice.company_name_snapshot or "—"),
        ("Job/property", invoice.property_reference_snapshot),
        ("Job reference", invoice.job_reference_snapshot),
        ("Issue date", invoice.issued_at.date().isoformat() if invoice.issued_at else "Draft"),
        ("Due date", invoice.due_at.date().isoformat() if invoice.due_at else "—"),
        ("Description", invoice.description),
        ("Payment stage", invoice.get_invoice_type_display()),
        ("Full package value", f"EUR {enquiry.quoted_total:.2f}"),
        ("Package deliverables", deliverables),
        ("Subtotal", f"EUR {invoice.subtotal:.2f}"),
        ("VAT", f"EUR {invoice.vat_amount:.2f}"),
        ("Total", f"EUR {invoice.total:.2f}"),
        ("Paid", f"EUR {invoice.amount_paid:.2f}"),
        ("Outstanding", f"EUR {invoice.amount_outstanding:.2f}"),
        ("Status", invoice.get_status_display()),
        ("Payment references", payment_refs),
    )
    notices = [RELEASE_NOTICE]
    if not invoice.vat_rate:
        notices.insert(0, VAT_NOTICE)
    return _pdf(f"Invoice {invoice.invoice_number}", rows, notices)


def build_receipt_filename(payment):
    return f"{payment.cash_receipt_number}.pdf"


def generate_cash_receipt_pdf(payment):
    if not payment.cash_receipt_number:
        raise ValueError("Only receipted cash payments can generate a cash receipt.")
    invoice = payment.invoice
    rows = (
        ("Receipt number", payment.cash_receipt_number),
        ("Date", payment.paid_at.date().isoformat()),
        ("Payer", payment.external_reference),
        ("Invoice", invoice.invoice_number),
        ("Job/property", invoice.property_reference_snapshot),
        ("Job reference", invoice.job_reference_snapshot),
        ("Amount", f"EUR {payment.amount:.2f}"),
        ("Method", "Cash"),
        ("Remaining balance", f"EUR {invoice.amount_outstanding:.2f}"),
    )
    notices = [VAT_NOTICE] if not invoice.vat_rate else []
    return _pdf(f"Cash receipt {payment.cash_receipt_number}", rows, notices)
