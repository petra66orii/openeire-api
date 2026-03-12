import os

os.environ.setdefault("REQUIRE_SHARED_THROTTLE_CACHE", "false")

from .settings import *  # noqa

# Force local file storage for tests (avoid R2/S3 calls)
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

MEDIA_ROOT = BASE_DIR / "test_media"

THROTTLE_CACHE_ALIAS = "throttle"
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "openeire-test-default-cache",
    },
    THROTTLE_CACHE_ALIAS: {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "openeire-test-throttle-cache",
        "TIMEOUT": None,
    },
}
