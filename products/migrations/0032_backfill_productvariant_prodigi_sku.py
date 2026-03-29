from django.db import migrations


def backfill_productvariant_prodigi_sku(apps, schema_editor):
    batch_size = 500
    ProductVariant = apps.get_model("products", "ProductVariant")
    PrintTemplate = apps.get_model("products", "PrintTemplate")

    template_skus = {
        (template.material, template.size): template.prodigi_sku
        for template in PrintTemplate.objects.exclude(prodigi_sku__isnull=True)
        .exclude(prodigi_sku__exact="")
        .iterator()
    }

    variants_to_update = []
    queryset = ProductVariant.objects.filter(prodigi_sku__isnull=True) | ProductVariant.objects.filter(prodigi_sku__exact="")
    for variant in queryset.iterator():
        prodigi_sku = template_skus.get((variant.material, variant.size))
        if not prodigi_sku:
            continue
        variant.prodigi_sku = prodigi_sku
        variants_to_update.append(variant)
        if len(variants_to_update) >= batch_size:
            ProductVariant.objects.bulk_update(
                variants_to_update,
                ["prodigi_sku"],
                batch_size=batch_size,
            )
            variants_to_update = []

    if variants_to_update:
        ProductVariant.objects.bulk_update(
            variants_to_update,
            ["prodigi_sku"],
            batch_size=batch_size,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("products", "0031_photo_is_printable"),
    ]

    operations = [
        migrations.RunPython(
            backfill_productvariant_prodigi_sku,
            migrations.RunPython.noop,
        ),
    ]
