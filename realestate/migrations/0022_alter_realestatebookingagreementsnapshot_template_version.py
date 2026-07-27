from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("realestate", "0021_realestateenquiry_form_schema_version"),
    ]

    operations = [
        migrations.AlterField(
            model_name="realestatebookingagreementsnapshot",
            name="template_version",
            field=models.CharField(default="1.7", max_length=16),
        ),
    ]
