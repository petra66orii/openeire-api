from django.db import migrations, models


def backfill_printable_photos(apps, schema_editor):
    Photo = apps.get_model("products", "Photo")
    ProductVariant = apps.get_model("products", "ProductVariant")

    printable_photo_ids = ProductVariant.objects.values_list("photo_id", flat=True).distinct()
    Photo.objects.filter(id__in=printable_photo_ids).update(is_printable=True)


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0030_unify_digital_pricing"),
    ]

    operations = [
        migrations.AddField(
            model_name="photo",
            name="is_printable",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(backfill_printable_photos, migrations.RunPython.noop),
    ]
