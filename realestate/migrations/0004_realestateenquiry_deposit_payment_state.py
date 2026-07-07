from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("realestate", "0003_realestateenquiry_client_links_and_booking_state"),
    ]

    operations = [
        migrations.AlterField(
            model_name="realestateenquiry",
            name="deposit_payment_link",
            field=models.URLField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="realestateenquiry",
            name="deposit_paid",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="realestateenquiry",
            name="deposit_paid_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="realestateenquiry",
            name="stripe_deposit_session_id",
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
