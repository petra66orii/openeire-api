from django.conf import settings
from django.db import migrations
from django.db.models import Count


INDEX_NAME = "auth_user_email_ci_non_empty_uniq_idx"


def _normalize_email(value):
    return str(value or "").strip().lower()


def _build_dedup_email(user_id, suffix=0):
    if suffix == 0:
        return f"dedup-user-{user_id}@invalid.local"
    return f"dedup-user-{user_id}-{suffix}@invalid.local"


def normalize_and_resolve_duplicate_emails(apps, schema_editor):
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    user_model = apps.get_model(app_label, model_name)

    # Step 1: Normalize all non-empty emails (trim + lowercase) in-place.
    users = (
        user_model.objects
        .exclude(email__isnull=True)
        .exclude(email="")
        .order_by("id")
        .only("id", "email")
    )

    for user in users.iterator():
        normalized = _normalize_email(user.email)
        if normalized != user.email:
            user_model.objects.filter(pk=user.pk).update(email=normalized)

    # Step 2: Resolve duplicates without building an in-memory set of all emails.
    duplicate_groups = (
        user_model.objects
        .exclude(email__isnull=True)
        .exclude(email="")
        .values("email")
        .annotate(row_count=Count("id"))
        .filter(row_count__gt=1)
        .order_by("email")
    )

    for group in duplicate_groups.iterator():
        duplicate_email = group["email"]
        duplicate_user_ids = list(
            user_model.objects
            .filter(email=duplicate_email)
            .order_by("id")
            .values_list("id", flat=True)
        )
        # Keep the first account's normalized email unchanged.
        for user_id in duplicate_user_ids[1:]:
            suffix = 0
            while True:
                candidate = _build_dedup_email(user_id, suffix)
                # Check against the full table to avoid collisions with
                # unprocessed rows or pre-existing placeholder-like emails.
                exists = user_model.objects.filter(email__iexact=candidate).exclude(pk=user_id).exists()
                if not exists:
                    break
                suffix += 1
            user_model.objects.filter(pk=user_id).update(email=candidate)


def create_email_ci_unique_index(apps, schema_editor):
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    user_model = apps.get_model(app_label, model_name)
    table_name = user_model._meta.db_table
    vendor = schema_editor.connection.vendor

    if vendor in {"postgresql", "sqlite"}:
        qn = schema_editor.quote_name
        schema_editor.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {qn(INDEX_NAME)} "
            f"ON {qn(table_name)} (LOWER(email)) WHERE email <> ''"
        )
        return
    # Non-supported vendors keep application-level validation only.
    return


def drop_email_ci_unique_index(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    if vendor in {"postgresql", "sqlite"}:
        qn = schema_editor.quote_name
        schema_editor.execute(f"DROP INDEX IF EXISTS {qn(INDEX_NAME)}")


class Migration(migrations.Migration):

    dependencies = [
        ("userprofiles", "0003_userprofile_can_access_gallery"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(normalize_and_resolve_duplicate_emails, migrations.RunPython.noop),
        migrations.RunPython(create_email_ci_unique_index, drop_email_ci_unique_index),
    ]
