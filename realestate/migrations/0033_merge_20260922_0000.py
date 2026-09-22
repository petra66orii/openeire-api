from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("realestate", "0032_realestateinvoice_line_items_snapshot"),
        ("realestate", "0032_stripe_invoice_recovery"),
    ]

    operations = []
