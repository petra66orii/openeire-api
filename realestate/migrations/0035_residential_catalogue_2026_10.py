from django.conf import settings
from django.db import migrations, models
import django.core.validators
import django.db.models.deletion


LEGACY_CATALOGUE_VERSION = "residential_2026_09"
CURRENT_CATALOGUE_VERSION = "residential_2026_10"


def mark_existing_enquiries_as_legacy(apps, schema_editor):
    enquiry = apps.get_model("realestate", "RealEstateEnquiry")
    enquiry.objects.filter(catalogue_version__isnull=True).update(
        catalogue_version=LEGACY_CATALOGUE_VERSION
    )


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("realestate", "0034_enquiry_notification_recovery"),
    ]

    operations = [
        migrations.AddField(
            model_name="realestateenquiry",
            name="catalogue_version",
            field=models.CharField(
                blank=True,
                choices=[
                    (LEGACY_CATALOGUE_VERSION, "Residential catalogue — September 2026 (legacy)"),
                    (CURRENT_CATALOGUE_VERSION, "Residential catalogue — October 2026"),
                ],
                max_length=32,
                null=True,
            ),
        ),
        migrations.RunPython(mark_existing_enquiries_as_legacy, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="realestateenquiry",
            name="catalogue_version",
            field=models.CharField(
                choices=[
                    (LEGACY_CATALOGUE_VERSION, "Residential catalogue — September 2026 (legacy)"),
                    (CURRENT_CATALOGUE_VERSION, "Residential catalogue — October 2026"),
                ],
                default=CURRENT_CATALOGUE_VERSION,
                help_text=(
                    "Package catalogue used for this selection. Issued quotation and agreement "
                    "snapshots retain their own immutable version and wording."
                ),
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="realestateenquiry",
            name="agreed_photograph_count",
            field=models.PositiveSmallIntegerField(
                blank=True,
                help_text=(
                    "Explicit fixed photograph quantity agreed for this booking. Leave blank to "
                    "use the catalogue's indicative range."
                ),
                null=True,
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(500),
                ],
            ),
        ),
        migrations.AddField(
            model_name="realestateenquiry",
            name="custom_review_notes",
            field=models.TextField(
                blank=True,
                help_text="Additional detail for Custom / POA scope review.",
            ),
        ),
        migrations.AddField(
            model_name="realestateenquiry",
            name="custom_review_reasons",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Explicit scope signals that require Custom / POA review.",
            ),
        ),
        migrations.AlterField(
            model_name="realestateenquiry",
            name="form_schema_version",
            field=models.PositiveSmallIntegerField(
                blank=True,
                help_text=(
                    "Public enquiry payload schema. Blank indicates the legacy form; version 2 "
                    "uses the original structured form and version 3 uses the October 2026 "
                    "public catalogue."
                ),
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="realestatebookingagreementsnapshot",
            name="template_version",
            field=models.CharField(default="2.1", max_length=16),
        ),
        migrations.CreateModel(
            name="RealEstateQuotationSnapshot",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("catalogue_version", models.CharField(max_length=32)),
                ("subject", models.CharField(max_length=255)),
                ("recipient_email", models.EmailField(max_length=254)),
                ("context", models.JSONField(default=dict)),
                ("rendered_text", models.TextField()),
                ("rendered_html", models.TextField()),
                ("issued_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="realestate_quotation_snapshots_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "enquiry",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="quotation_snapshots",
                        to="realestate.realestateenquiry",
                    ),
                ),
            ],
            options={
                "verbose_name": "Real estate quotation snapshot",
                "verbose_name_plural": "Real estate quotation snapshots",
                "ordering": ("-issued_at",),
            },
        ),
    ]
