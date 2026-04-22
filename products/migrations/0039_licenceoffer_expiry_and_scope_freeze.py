from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0038_licenserequest_negotiation_and_payment_drafts"),
    ]

    operations = [
        migrations.AddField(
            model_name="licenserequest",
            name="agreed_scope_snapshot",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="licenserequest",
            name="last_negotiation_email_body",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="licenserequest",
            name="last_payment_email_body",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="licenceoffer",
            name="expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
