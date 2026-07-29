import math
import uuid
from pathlib import Path

import boto3
from botocore.config import Config
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError

from .delivery import content_disposition, safe_download_filename

DEFAULT_MAX_SIZE = 50 * 1024 * 1024 * 1024
DEFAULT_PART_SIZE = 10 * 1024 * 1024
DELIVERY_PREFIX = "real-estate-deliveries"
ALLOWED_TYPES = {
    "application/pdf",
    "application/zip",
    "image/jpeg",
    "image/webp",
    "video/mp4",
}


def get_allowed_types():
    configured = str(
        getattr(settings, "REAL_ESTATE_DELIVERY_ALLOWED_MIME_TYPES", "") or ""
    )
    if not configured:
        return ALLOWED_TYPES
    return {item.strip().lower() for item in configured.split(",") if item.strip()}


def get_max_size():
    return int(
        getattr(settings, "REAL_ESTATE_DELIVERY_MAX_FILE_SIZE", DEFAULT_MAX_SIZE)
    )


def validate_upload(filename, content_type, file_size):
    normalized_type = str(content_type or "").strip().lower()
    if normalized_type not in get_allowed_types():
        raise ValidationError("Unsupported delivery file type.")
    if int(file_size) <= 0 or int(file_size) > get_max_size():
        raise ValidationError("Delivery file size is outside the allowed range.")
    return safe_download_filename(filename), normalized_type


def delivery_object_key(delivery, filename):
    suffix = Path(safe_download_filename(filename)).suffix.lower()
    prefix = str(
        getattr(settings, "REAL_ESTATE_DELIVERY_R2_PREFIX", DELIVERY_PREFIX)
    ).strip("/")
    if not prefix or ".." in prefix:
        raise ImproperlyConfigured("REAL_ESTATE_DELIVERY_R2_PREFIX is invalid.")
    return f"{prefix}/{delivery.pk}/{uuid.uuid4().hex}{suffix}"


def _client():
    required = (
        getattr(settings, "R2_ENDPOINT_URL", None),
        getattr(settings, "R2_PRIVATE_ACCESS_KEY_ID", None),
        getattr(settings, "R2_PRIVATE_SECRET_ACCESS_KEY", None),
        getattr(settings, "R2_PRIVATE_BUCKET_NAME", None),
    )
    if not all(required):
        raise ImproperlyConfigured("Private R2 delivery configuration is incomplete.")
    return boto3.client(
        "s3",
        endpoint_url=required[0],
        aws_access_key_id=required[1],
        aws_secret_access_key=required[2],
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def _bucket():
    return settings.R2_PRIVATE_BUCKET_NAME


def start_upload(delivery, filename, content_type, file_size):
    safe_name, normalized_type = validate_upload(filename, content_type, file_size)
    object_key = delivery_object_key(delivery, safe_name)
    response = _client().create_multipart_upload(
        Bucket=_bucket(),
        Key=object_key,
        ContentType=normalized_type,
        Metadata={"filename": safe_name},
    )
    part_size = max(
        DEFAULT_PART_SIZE,
        math.ceil(int(file_size) / 10_000 / (1024 * 1024)) * 1024 * 1024,
    )
    return response["UploadId"], object_key, part_size, safe_name, normalized_type


def part_url(session, part_number):
    return _client().generate_presigned_url(
        "upload_part",
        Params={
            "Bucket": _bucket(),
            "Key": session.object_key,
            "UploadId": session.upload_id,
            "PartNumber": part_number,
        },
        ExpiresIn=int(
            getattr(settings, "R2_MULTIPART_PART_URL_EXPIRY_SECONDS", 3600)
        ),
    )


def complete_upload(session, parts):
    normalized = [
        {"ETag": item["etag"], "PartNumber": item["part_number"]}
        for item in sorted(parts, key=lambda item: item["part_number"])
    ]
    client = _client()
    client.complete_multipart_upload(
        Bucket=_bucket(),
        Key=session.object_key,
        UploadId=session.upload_id,
        MultipartUpload={"Parts": normalized},
    )
    head = client.head_object(Bucket=_bucket(), Key=session.object_key)
    if int(head.get("ContentLength", -1)) != session.expected_size:
        raise ValidationError("Uploaded object size verification failed.")
    actual_type = str(head.get("ContentType", "")).split(";")[0].strip().lower()
    if actual_type != session.expected_mime_type:
        raise ValidationError("Uploaded object type verification failed.")
    return head


def abort_upload(session):
    _client().abort_multipart_upload(
        Bucket=_bucket(),
        Key=session.object_key,
        UploadId=session.upload_id,
    )


def download_url(deliverable):
    return _client().generate_presigned_url(
        "get_object",
        Params={
            "Bucket": _bucket(),
            "Key": deliverable.object_key,
            "ResponseContentType": deliverable.mime_type,
            "ResponseContentDisposition": content_disposition(
                deliverable.original_filename
            ),
        },
        ExpiresIn=300,
    )


def delete_validated_object(object_key):
    prefix = str(
        getattr(settings, "REAL_ESTATE_DELIVERY_R2_PREFIX", DELIVERY_PREFIX)
    ).strip("/")
    expected = f"{prefix}/"
    if not object_key.startswith(expected) or ".." in object_key:
        raise ValidationError("Refusing to delete an object outside the delivery prefix.")
    _client().delete_object(Bucket=_bucket(), Key=object_key)
