from decimal import Decimal, ROUND_HALF_UP

from django.db import migrations


MONEY = Decimal("0.01")


def money(value):
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def allocate_number(Sequence, year):
    sequence, _ = Sequence.objects.get_or_create(
        kind="invoice", year=year, defaults={"next_value": 1}
    )
    value = sequence.next_value
    sequence.next_value += 1
    sequence.save(update_fields=("next_value",))
    return f"OE-RE-{year}-{value:04d}"


def backfill_invoices(apps, schema_editor):
    Enquiry = apps.get_model("realestate", "RealEstateEnquiry")
    Invoice = apps.get_model("realestate", "RealEstateInvoice")
    Sequence = apps.get_model("realestate", "RealEstateDocumentSequence")
    required = (
        "quoted_vat_rate", "quoted_subtotal", "quoted_vat_amount", "quoted_total",
        "quoted_deposit_amount", "quoted_balance_due",
    )
    for enquiry in Enquiry.objects.exclude(quoted_total__isnull=True).iterator():
        if any(getattr(enquiry, field) is None for field in required):
            continue
        issued_at = enquiry.updated_at or enquiry.created_at
        year = issued_at.year
        vat_rate = Decimal(enquiry.quoted_vat_rate)
        for invoice_type, total in (
            ("deposit", enquiry.quoted_deposit_amount),
            ("balance", enquiry.quoted_balance_due),
        ):
            if Invoice.objects.filter(
                enquiry_id=enquiry.pk, invoice_type=invoice_type
            ).exclude(status="void").exists():
                continue
            total = money(total)
            if vat_rate:
                subtotal = money(total / (Decimal("1") + vat_rate))
                vat_amount = money(total - subtotal)
            else:
                subtotal, vat_amount = total, Decimal("0.00")
            Invoice.objects.create(
                enquiry_id=enquiry.pk,
                invoice_type=invoice_type,
                invoice_number=allocate_number(Sequence, year),
                status="issued",
                currency="EUR",
                description=f"Real estate {invoice_type} payment",
                subtotal=subtotal,
                vat_rate=vat_rate,
                vat_amount=vat_amount,
                total=total,
                customer_name_snapshot=enquiry.name,
                company_name_snapshot=enquiry.company_name,
                customer_email_snapshot=enquiry.email,
                customer_phone_snapshot=enquiry.phone,
                property_reference_snapshot=enquiry.property_address,
                job_reference_snapshot=f"RE-{enquiry.pk}",
                issued_at=issued_at,
                due_at=issued_at,
            )


class Migration(migrations.Migration):
    dependencies = [("realestate", "0008_financial_ledger")]
    operations = [
        migrations.RunPython(backfill_invoices, migrations.RunPython.noop),
    ]
