import math
import re
import uuid
from pathlib import Path

import boto3
from botocore.config import Config
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .models import VideoUploadSession

FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
DEFAULT_ALLOWED_VIDEO_TYPES = (
    "video/mp4",
    "video/quicktime",
    "video/webm",
    "video/x-m4v",
)
S3_MIN_MULTIPART_SIZE = 5 * 1024 * 1024
DEFAULT_MIN_PART_SIZE = 10 * 1024 * 1024
DEFAULT_MAX_FILE_SIZE = 50 * 1024 * 1024 * 1024
DEFAULT_PART_URL_EXPIRY = 3600
DEFAULT_MAX_CONCURRENCY = 4
DEFAULT_MASTER_PREFIX = "digital_products/videos/"
DEFAULT_PREVIEW_PREFIX = "previews/videos/"


def get_allowed_video_content_types():
    raw = getattr(settings, "R2_MULTIPART_ALLOWED_VIDEO_TYPES", "")
    if not raw:
        return set(DEFAULT_ALLOWED_VIDEO_TYPES)
    return {value.strip().lower() for value in str(raw).split(",") if value.strip()}


def get_max_video_upload_size():
    value = getattr(settings, "R2_MULTIPART_MAX_FILE_SIZE", DEFAULT_MAX_FILE_SIZE)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_FILE_SIZE
    return parsed if parsed > 0 else DEFAULT_MAX_FILE_SIZE


def get_default_multipart_concurrency():
    value = getattr(settings, "R2_MULTIPART_DEFAULT_CONCURRENCY", DEFAULT_MAX_CONCURRENCY)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_CONCURRENCY
    return max(1, min(parsed, 8))


def get_part_url_expiry_seconds():
    value = getattr(settings, "R2_MULTIPART_PART_URL_EXPIRY_SECONDS", DEFAULT_PART_URL_EXPIRY)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_PART_URL_EXPIRY
    return max(300, parsed)


def get_min_part_size_bytes():
    value = getattr(settings, "R2_MULTIPART_MIN_PART_SIZE", DEFAULT_MIN_PART_SIZE)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MIN_PART_SIZE
    return max(S3_MIN_MULTIPART_SIZE, parsed)


def get_object_prefix(purpose):
    if purpose == VideoUploadSession.PURPOSE_PREVIEW:
        return getattr(settings, "R2_VIDEO_PREVIEW_PREFIX", DEFAULT_PREVIEW_PREFIX)
    return getattr(settings, "R2_VIDEO_MASTER_PREFIX", DEFAULT_MASTER_PREFIX)


def sanitize_upload_filename(filename):
    candidate = (filename or "").strip()
    if not candidate:
        candidate = "video-upload"
    basename = candidate.replace("\\", "/").split("/")[-1]
    parsed = Path(basename)
    stem = FILENAME_SAFE_RE.sub("-", parsed.stem).strip(".-_").lower()
    suffix = FILENAME_SAFE_RE.sub("", parsed.suffix).lower()

    safe_stem = stem or "video-upload"
    safe_suffix = suffix if suffix.startswith(".") else ""
    return f"{safe_stem}{safe_suffix}"


def build_object_key(*, filename, purpose):
    prefix = get_object_prefix(purpose).rstrip("/")
    safe_name = sanitize_upload_filename(filename)
    stem, suffix = Path(safe_name).stem, Path(safe_name).suffix
    unique_suffix = uuid.uuid4().hex[:8]
    return f"{prefix}/{stem}-{unique_suffix}{suffix}"


def recommend_part_size(file_size):
    min_part_size = get_min_part_size_bytes()
    max_parts_size = max(min_part_size, math.ceil(file_size / 10_000))
    rounded = math.ceil(max_parts_size / (1024 * 1024)) * 1024 * 1024
    return max(min_part_size, rounded)


def _bucket_name_for_purpose(purpose):
    if purpose == VideoUploadSession.PURPOSE_PREVIEW:
        return getattr(settings, "R2_BUCKET_NAME", None)
    return getattr(settings, "R2_PRIVATE_BUCKET_NAME", None)


def _credentials_for_purpose(purpose):
    if purpose == VideoUploadSession.PURPOSE_PREVIEW:
        return (
            getattr(settings, "R2_ACCESS_KEY_ID", None),
            getattr(settings, "R2_SECRET_ACCESS_KEY", None),
        )
    return (
        getattr(settings, "R2_PRIVATE_ACCESS_KEY_ID", None),
        getattr(settings, "R2_PRIVATE_SECRET_ACCESS_KEY", None),
    )


def get_bucket_name_for_purpose(purpose):
    bucket = _bucket_name_for_purpose(purpose)
    if not bucket:
        raise ImproperlyConfigured("R2 bucket configuration is incomplete for video uploads.")
    return bucket


def get_r2_client_for_purpose(purpose):
    endpoint_url = getattr(settings, "R2_ENDPOINT_URL", None)
    access_key, secret_key = _credentials_for_purpose(purpose)
    bucket = _bucket_name_for_purpose(purpose)

    if not all([endpoint_url, access_key, secret_key, bucket]):
        raise ImproperlyConfigured("R2 multipart upload configuration is incomplete.")

    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def start_multipart_upload(*, filename, content_type, file_size, purpose):
    part_size = recommend_part_size(file_size)
    object_key = build_object_key(filename=filename, purpose=purpose)
    client = get_r2_client_for_purpose(purpose)
    bucket_name = get_bucket_name_for_purpose(purpose)
    response = client.create_multipart_upload(
        Bucket=bucket_name,
        Key=object_key,
        ContentType=content_type,
    )
    return {
        "upload_id": response["UploadId"],
        "object_key": object_key,
        "bucket_name": bucket_name,
        "part_size": part_size,
        "max_concurrency": get_default_multipart_concurrency(),
    }


def generate_part_upload_url(*, purpose, upload_id, object_key, part_number):
    client = get_r2_client_for_purpose(purpose)
    bucket_name = get_bucket_name_for_purpose(purpose)
    return client.generate_presigned_url(
        "upload_part",
        Params={
            "Bucket": bucket_name,
            "Key": object_key,
            "UploadId": upload_id,
            "PartNumber": part_number,
        },
        ExpiresIn=get_part_url_expiry_seconds(),
    )


def complete_multipart_upload(*, purpose, upload_id, object_key, parts):
    client = get_r2_client_for_purpose(purpose)
    bucket_name = get_bucket_name_for_purpose(purpose)
    normalized_parts = [
        {"ETag": part["etag"], "PartNumber": part["part_number"]}
        for part in sorted(parts, key=lambda item: item["part_number"])
    ]
    client.complete_multipart_upload(
        Bucket=bucket_name,
        Key=object_key,
        UploadId=upload_id,
        MultipartUpload={"Parts": normalized_parts},
    )


def abort_multipart_upload(*, purpose, upload_id, object_key):
    client = get_r2_client_for_purpose(purpose)
    bucket_name = get_bucket_name_for_purpose(purpose)
    client.abort_multipart_upload(
        Bucket=bucket_name,
        Key=object_key,
        UploadId=upload_id,
    )
