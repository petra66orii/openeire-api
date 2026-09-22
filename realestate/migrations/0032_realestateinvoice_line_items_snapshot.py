from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("realestate", "0031_propertyscopecheck_propertyauthorisation"),
    ]

    operations = [
        migrations.AddField(
            model_name="realestateinvoice",
            name="line_items_snapshot",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
