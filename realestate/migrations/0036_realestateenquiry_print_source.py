from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("realestate", "0035_residential_catalogue_2026_10")]

    operations = [
        migrations.AddField(
            model_name="realestateenquiry",
            name="print_source",
            field=models.CharField(
                blank=True,
                choices=[("flyer", "Flyer"), ("portfolio-card", "Portfolio card"),
                         ("office-drop", "Office drop"), ("qr-sticker", "QR sticker")],
                help_text="Submitted print campaign source; separate from the customer's how-heard answer.",
                max_length=20,
            ),
        ),
    ]
