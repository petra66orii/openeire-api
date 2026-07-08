from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("checkout", "0011_checkoutattempt_order_checkout_attempt_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="productshipping",
            name="country",
            field=models.CharField(
                choices=[
                    ("IE", "Ireland"),
                    ("US", "United States"),
                    ("AU", "Australia"),
                    ("RO", "Romania"),
                ],
                max_length=2,
            ),
        ),
    ]
