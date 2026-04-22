from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0039_licenceoffer_expiry_and_scope_freeze"),
    ]

    operations = [
        migrations.AlterField(
            model_name="licenserequestauditlog",
            name="from_status",
            field=models.CharField(blank=True, max_length=32, null=True),
        ),
        migrations.AlterField(
            model_name="licenserequestauditlog",
            name="to_status",
            field=models.CharField(blank=True, max_length=32, null=True),
        ),
    ]
