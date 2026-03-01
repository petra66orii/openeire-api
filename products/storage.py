from storages.backends.s3boto3 import S3Boto3Storage
from django.conf import settings
from django.core.files.storage import Storage, FileSystemStorage
from django.utils.deconstruct import deconstructible

class PrivateR2Storage(S3Boto3Storage):
    """
    Custom storage backend that explicitly routes files to the PRIVATE R2 bucket.
    """
    bucket_name = getattr(settings, "R2_PRIVATE_BUCKET_NAME", None)
    access_key = getattr(settings, "R2_PRIVATE_ACCESS_KEY_ID", None)
    secret_key = getattr(settings, "R2_PRIVATE_SECRET_ACCESS_KEY", None)
    endpoint_url = getattr(settings, "R2_ENDPOINT_URL", None)
    region_name = getattr(settings, "AWS_S3_REGION_NAME", "auto")
    signature_version = getattr(settings, "AWS_S3_SIGNATURE_VERSION", "s3v4")
    file_overwrite = False
    default_acl = 'private'
    custom_domain = False # Forces boto3 to generate direct S3 links, not public URLs


@deconstructible
class PrivateAssetStorage(Storage):
    """
    Routes private assets to R2 in production, but uses local storage for
    dev/tests. This avoids baking machine-specific paths into migrations.
    """
    def _select_storage(self):
        if settings.DEBUG or getattr(settings, "RUNNING_TESTS", False):
            return FileSystemStorage()
        return PrivateR2Storage()

    def _open(self, name, mode='rb'):
        return self._select_storage()._open(name, mode)

    def _save(self, name, content):
        return self._select_storage()._save(name, content)

    def delete(self, name):
        return self._select_storage().delete(name)

    def exists(self, name):
        return self._select_storage().exists(name)

    def size(self, name):
        return self._select_storage().size(name)

    def url(self, name):
        return self._select_storage().url(name)

    def get_available_name(self, name, max_length=None):
        return self._select_storage().get_available_name(name, max_length=max_length)

    def generate_filename(self, filename):
        return self._select_storage().generate_filename(filename)

    def __getattr__(self, name):
        return getattr(self._select_storage(), name)
