from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("realestate", "0002_alter_realestateenquiry_options"),
    ]

    operations = [
        migrations.AddField(
            model_name="realestateenquiry",
            name="booking_agreement_link",
            field=models.URLField(blank=True),
        ),
        migrations.AddField(
            model_name="realestateenquiry",
            name="booking_agreement_received",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="realestateenquiry",
            name="delivery_link",
            field=models.URLField(blank=True),
        ),
        migrations.AddField(
            model_name="realestateenquiry",
            name="deposit_payment_link",
            field=models.URLField(blank=True),
        ),
        migrations.AddField(
            model_name="realestateenquiry",
            name="proposed_shoot_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="realestateenquiry",
            name="review_link",
            field=models.URLField(blank=True),
        ),
    ]
