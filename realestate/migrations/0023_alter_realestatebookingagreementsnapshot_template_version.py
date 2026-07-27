from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        (
            "realestate",
            "0022_alter_realestatebookingagreementsnapshot_template_version",
        ),
    ]

    operations = [
        migrations.AlterField(
            model_name="realestatebookingagreementsnapshot",
            name="template_version",
            field=models.CharField(default="1.8", max_length=16),
        ),
    ]
