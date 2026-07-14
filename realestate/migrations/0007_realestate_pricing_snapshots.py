from decimal import Decimal, ROUND_HALF_UP

from django.db import migrations, models


MONEY = Decimal("0.01")
LEGACY_VAT_RATE = Decimal("0.23")
DEPOSIT_RATE = Decimal("0.30")


def money(value):
    return Decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP)


def backfill_legacy_pricing_snapshots(apps, schema_editor):
    RealEstateEnquiry = apps.get_model("realestate", "RealEstateEnquiry")
    for enquiry in RealEstateEnquiry.objects.exclude(quoted_price__isnull=True).iterator():
        quote_total = money(enquiry.quoted_price)
        vat_amount = money(quote_total * LEGACY_VAT_RATE)
        final_total = money(quote_total + vat_amount)
        deposit_amount = money(final_total * DEPOSIT_RATE)
        balance_due = money(final_total - deposit_amount)

        enquiry.pricing_snapshot_version = 1
        enquiry.price_input_is_gross = False
        enquiry.vat_registered_at_quote = True
        enquiry.quoted_vat_rate = LEGACY_VAT_RATE
        enquiry.quoted_subtotal = quote_total
        enquiry.quoted_vat_amount = vat_amount
        enquiry.quoted_total = final_total
        enquiry.quoted_deposit_amount = deposit_amount
        enquiry.quoted_balance_due = balance_due
        enquiry.save(
            update_fields=[
                "pricing_snapshot_version",
                "price_input_is_gross",
                "vat_registered_at_quote",
                "quoted_vat_rate",
                "quoted_subtotal",
                "quoted_vat_amount",
                "quoted_total",
                "quoted_deposit_amount",
                "quoted_balance_due",
            ]
        )


class Migration(migrations.Migration):
    dependencies = [("realestate", "0006_realestatetimelineevent")]

    operations = [
        migrations.AddField(
            model_name="realestateenquiry",
            name="pricing_snapshot_version",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="realestateenquiry",
            name="price_input_is_gross",
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="realestateenquiry",
            name="vat_registered_at_quote",
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="realestateenquiry",
            name="quoted_vat_rate",
            field=models.DecimalField(blank=True, decimal_places=5, max_digits=6, null=True),
        ),
        migrations.AddField(
            model_name="realestateenquiry",
            name="quoted_subtotal",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name="realestateenquiry",
            name="quoted_vat_amount",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name="realestateenquiry",
            name="quoted_total",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name="realestateenquiry",
            name="quoted_deposit_amount",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name="realestateenquiry",
            name="quoted_balance_due",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.RunPython(backfill_legacy_pricing_snapshots, migrations.RunPython.noop),
    ]
