from django.conf import settings
from django.db import migrations


OLD_INDEX_NAME = "auth_user_email_ci_non_empty_uniq_idx"
NEW_INDEX_NAME = "auth_user_email_ci_trim_non_empty_uniq_idx"


def _table_name(apps):
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    user_model = apps.get_model(app_label, model_name)
    return user_model._meta.db_table


def _supports_ci_expression_index(schema_editor):
    return schema_editor.connection.vendor in {"postgresql", "sqlite"}


def upgrade_email_index(apps, schema_editor):
    if not _supports_ci_expression_index(schema_editor):
        return

    table_name = _table_name(apps)
    qn = schema_editor.quote_name
    schema_editor.execute(f"DROP INDEX IF EXISTS {qn(OLD_INDEX_NAME)}")
    schema_editor.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {qn(NEW_INDEX_NAME)} "
        f"ON {qn(table_name)} (LOWER(TRIM(email))) WHERE TRIM(email) <> ''"
    )


def downgrade_email_index(apps, schema_editor):
    if not _supports_ci_expression_index(schema_editor):
        return

    table_name = _table_name(apps)
    qn = schema_editor.quote_name
    schema_editor.execute(f"DROP INDEX IF EXISTS {qn(NEW_INDEX_NAME)}")
    schema_editor.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {qn(OLD_INDEX_NAME)} "
        f"ON {qn(table_name)} (LOWER(email)) WHERE email <> ''"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("userprofiles", "0004_auth_user_email_ci_unique_index"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(upgrade_email_index, downgrade_email_index),
    ]
