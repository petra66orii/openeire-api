from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("realestate", "0026_realestatedelivery_public_id"),
    ]

    operations = [
        migrations.AlterField(
            model_name="realestateenquiry",
            name="deposit_payment_link",
            field=models.URLField(blank=True, max_length=2048),
        ),
        migrations.AlterField(
            model_name="realestateinvoice",
            name="stripe_checkout_url",
            field=models.URLField(blank=True, max_length=2048),
        ),
        migrations.AlterField(
            model_name="realestateinvoice",
            name="stripe_hosted_invoice_url",
            field=models.URLField(blank=True, max_length=2048),
        ),
        migrations.AlterField(
            model_name="realestateinvoice",
            name="stripe_invoice_pdf_url",
            field=models.URLField(blank=True, max_length=2048),
        ),
    ]
