from io import BytesIO

from reportlab.platypus import Spacer

from openeire_api.business_identity import get_business_identity
from openeire_api.pdf_branding import OpenEirePDFTheme, branded_document, page_decoration


VAT_NOTICE = "VAT not applicable — supplier not VAT registered."
RELEASE_NOTICE = (
    "Final high-resolution media and usage rights are released once payment "
    "has been received in full."
)


def _pdf(title, rows, notices):
    identity = get_business_identity()
    buffer = BytesIO()
    document = branded_document(buffer, title=title, identity=identity, page_compression=0)
    theme = OpenEirePDFTheme()
    story = [theme.heading(title, level=1)]
    story.append(theme.paragraph(" | ".join(value for value in (identity.address, identity.email, identity.phone) if value), "BrandMeta"))
    metadata_labels = {"Invoice number", "Receipt number", "Issue date", "Due date", "Status", "Date"}
    job_labels = {"Customer", "Company", "Job/property", "Job reference", "Payer", "Invoice"}
    description_labels = {"Description", "Package deliverables", "Payment references"}
    metadata = [(label, value) for label, value in rows if label in metadata_labels]
    job = [(label, value) for label, value in rows if label in job_labels]
    descriptions = [(label, value) for label, value in rows if label in description_labels]
    financial = [(label, value) for label, value in rows if label not in metadata_labels | job_labels | description_labels]
    story.extend(theme.paired_information(metadata, job, emphasize=("Status",) if ("Status", "Paid") in metadata else ()))
    for label, value in descriptions:
        if label == "Payment references":
            story.append(theme.paragraph(f"{label}: {value}", "BrandMeta"))
        else:
            story.append(theme.heading(label, level=3))
            story.append(theme.paragraph(value))
    story.append(theme.heading("Payment details"))
    story.append(theme.information_table(financial, financial=True))
    for notice in notices:
        story.extend((Spacer(1, 5), theme.notice(notice)))
    decoration = page_decoration(document_type="CASH RECEIPT" if title.startswith("Cash receipt") else "INVOICE", identity=identity)
    document.build(story, onFirstPage=decoration, onLaterPages=decoration)
    return buffer.getvalue()


def build_invoice_filename(invoice):
    return f"{invoice.invoice_number}.pdf"


def generate_invoice_pdf(invoice):
    enquiry = invoice.enquiry
    deliverables = enquiry.get_preferred_package_summary()
    active_adjustments = list(
        enquiry.financial_adjustments.filter(reversed_at__isnull=True).order_by("created_at")
    )
    deposit_received = sum(
        (
            item.amount_paid
            for item in enquiry.invoices.filter(
                invoice_type="deposit",
            )
        ),
        0,
    )
    payment_refs = ", ".join(
        filter(None, invoice.payments.filter(status="succeeded").values_list("external_reference", flat=True))
    ) or "—"
    rows = [
        ("Invoice number", invoice.invoice_number),
        ("Customer", invoice.customer_name_snapshot),
        ("Company", invoice.company_name_snapshot or "—"),
        ("Job/property", invoice.property_reference_snapshot),
        ("Job reference", invoice.job_reference_snapshot),
        ("Issue date", invoice.issued_at.date().isoformat() if invoice.issued_at else "Draft"),
        ("Due date", invoice.due_at.date().isoformat() if invoice.due_at else "—"),
        ("Description", invoice.description),
        ("Payment stage", invoice.get_invoice_type_display()),
        ("Original booking total", f"EUR {enquiry.original_required_total:.2f}"),
        ("Deposit received", f"EUR {deposit_received:.2f}"),
        ("Package deliverables", deliverables),
        ("Subtotal", f"EUR {invoice.subtotal:.2f}"),
        ("VAT", f"EUR {invoice.vat_amount:.2f}"),
        ("Total", f"EUR {invoice.total:.2f}"),
        ("Paid", f"EUR {invoice.amount_paid:.2f}"),
        ("Outstanding", f"EUR {invoice.amount_outstanding:.2f}"),
        ("Status", invoice.get_status_display()),
        ("Payment references", payment_refs),
    ]
    adjustment_rows = [
        (adjustment.customer_description, f"- EUR {adjustment.amount:.2f}")
        for adjustment in active_adjustments
    ]
    rows[11:11] = adjustment_rows
    rows.insert(
        11 + len(adjustment_rows),
        ("Adjusted booking total", f"EUR {enquiry.adjusted_required_total:.2f}"),
    )
    rows.insert(
        12 + len(adjustment_rows),
        ("Balance due", f"EUR {enquiry.adjusted_balance_due:.2f}"),
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
