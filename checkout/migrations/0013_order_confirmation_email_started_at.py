from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("checkout", "0012_alter_productshipping_country")]

    operations = [
        migrations.AddField(
            model_name="order",
            name="confirmation_email_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
