from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("realestate", "0004_realestateenquiry_deposit_payment_state"),
    ]

    operations = [
        migrations.AddField(
            model_name="realestateenquiry",
            name="delivery_provider",
            field=models.CharField(
                choices=[
                    ("myairbridge", "MyAirBridge"),
                    ("google_drive", "Google Drive"),
                    ("dropbox", "Dropbox"),
                    ("onedrive", "OneDrive"),
                    ("portal", "OpenEire Client Portal"),
                    ("other", "Other"),
                ],
                default="myairbridge",
                max_length=20,
            ),
        ),
    ]
