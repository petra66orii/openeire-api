from django.db import migrations, models
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("checkout", "0006_order_personal_terms_version"),
        ("products", "0032_backfill_productvariant_prodigi_sku"),
    ]

    operations = [
        migrations.CreateModel(
            name="PersonalDownloadToken",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("expires_at", models.DateTimeField()),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "order_item",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="personal_download_tokens",
                        to="checkout.orderitem",
                    ),
                ),
            ],
            options={
                "verbose_name": "Personal Download Token",
                "verbose_name_plural": "Personal Download Tokens",
                "ordering": ["-created_at"],
            },
        ),
    ]
