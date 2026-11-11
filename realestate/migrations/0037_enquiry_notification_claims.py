from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("realestate", "0036_realestateenquiry_print_source")]

    operations = [
        migrations.AddField(
            model_name="realestateenquiry",
            name="internal_notification_started_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="realestateenquiry",
            name="client_confirmation_started_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
    ]
