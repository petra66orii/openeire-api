from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("realestate", "0001_initial"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="realestateenquiry",
            options={
                "ordering": ("-created_at",),
                "verbose_name": "Real estate enquiry",
                "verbose_name_plural": "Real estate enquiries",
            },
        ),
    ]
