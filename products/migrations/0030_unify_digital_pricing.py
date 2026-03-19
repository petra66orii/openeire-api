from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0029_licenceoffer_licenserequestauditlog_and_more"),
    ]

    operations = [
        migrations.RenameField(
            model_name="photo",
            old_name="price_4k",
            new_name="price",
        ),
        migrations.RemoveField(
            model_name="photo",
            name="price_hd",
        ),
        migrations.RenameField(
            model_name="video",
            old_name="price_4k",
            new_name="price",
        ),
        migrations.RemoveField(
            model_name="video",
            name="price_hd",
        ),
    ]
