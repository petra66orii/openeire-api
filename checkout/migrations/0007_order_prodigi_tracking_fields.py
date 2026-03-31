from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("checkout", "0006_order_personal_terms_version"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="prodigi_last_callback_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="order",
            name="prodigi_order_id",
            field=models.CharField(blank=True, db_index=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="order",
            name="prodigi_shipments",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="order",
            name="prodigi_status",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="order",
            name="tracking_email_sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="order",
            name="tracking_email_signature",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
    ]
