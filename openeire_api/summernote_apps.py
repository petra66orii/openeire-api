from django_summernote.apps import DjangoSummernoteConfig


class LegacySummernoteConfig(DjangoSummernoteConfig):
    """Keep Summernote's existing AutoField migration without changing our IDs."""

    default_auto_field = "django.db.models.AutoField"
