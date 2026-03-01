from storages.backends.s3boto3 import S3Boto3Storage
from django.conf import settings

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
