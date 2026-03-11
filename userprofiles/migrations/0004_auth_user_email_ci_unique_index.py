from django.conf import settings
from django.db import migrations


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

    seen = set()
    users = (
        user_model.objects
        .exclude(email__isnull=True)
        .exclude(email="")
        .order_by("id")
        .only("id", "email")
    )

    for user in users.iterator():
        normalized = _normalize_email(user.email)
        if not normalized:
            if user.email != "":
                user_model.objects.filter(pk=user.pk).update(email="")
            continue

        if normalized not in seen:
            seen.add(normalized)
            if user.email != normalized:
                user_model.objects.filter(pk=user.pk).update(email=normalized)
            continue

        suffix = 0
        while True:
            candidate = _build_dedup_email(user.pk, suffix)
            if candidate not in seen:
                break
            suffix += 1

        user_model.objects.filter(pk=user.pk).update(email=candidate)
        seen.add(candidate)


def create_email_ci_unique_index(apps, schema_editor):
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    user_model = apps.get_model(app_label, model_name)
    table_name = user_model._meta.db_table
    vendor = schema_editor.connection.vendor

    if vendor in {"postgresql", "sqlite"}:
        schema_editor.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {INDEX_NAME} "
            f"ON {table_name} (LOWER(email)) WHERE email <> ''"
        )
        return
    # Non-supported vendors keep application-level validation only.
    return


def drop_email_ci_unique_index(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    if vendor in {"postgresql", "sqlite"}:
        schema_editor.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")


class Migration(migrations.Migration):

    dependencies = [
        ("userprofiles", "0003_userprofile_can_access_gallery"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(normalize_and_resolve_duplicate_emails, migrations.RunPython.noop),
        migrations.RunPython(create_email_ci_unique_index, drop_email_ci_unique_index),
    ]
