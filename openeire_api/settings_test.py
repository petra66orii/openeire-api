from .settings import *  # noqa

# Force local file storage for tests (avoid R2/S3 calls)
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

MEDIA_ROOT = BASE_DIR / "test_media"