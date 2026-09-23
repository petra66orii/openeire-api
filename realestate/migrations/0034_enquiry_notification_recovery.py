from django.db import migrations, models
from django.db.models import F


def mark_historical_notifications_unknown(apps, schema_editor):
    # Historic delivery cannot be established; never resend old client emails blindly.
    Enquiry = apps.get_model("realestate", "RealEstateEnquiry")
    Enquiry.objects.using(schema_editor.connection.alias).update(
        internal_notification_sent_at=F("created_at"),
        client_confirmation_sent_at=F("created_at"),
    )


class Migration(migrations.Migration):
    dependencies = [("realestate", "0033_merge_20260922_0000")]

    operations = [
        migrations.AddField(
            model_name="realestateenquiry",
            name="internal_notification_sent_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="realestateenquiry",
            name="client_confirmation_sent_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="realestateenquiry",
            name="enquiry_email_last_error",
            field=models.TextField(blank=True, editable=False),
        ),
        migrations.AddField(
            model_name="realestateenquiry",
            name="enquiry_email_last_attempt_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.RunPython(mark_historical_notifications_unknown, migrations.RunPython.noop),
    ]
