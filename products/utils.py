import boto3
from botocore.config import Config
from django.conf import settings

def generate_r2_presigned_url(file_key, expiration=172800):
    """
    Generates a pre-signed URL for a Cloudflare R2 object.
    Defaults to 48 hours (172800 seconds) expiration.
    """
    if not all([
        settings.R2_ENDPOINT_URL,
        settings.R2_PRIVATE_BUCKET_NAME,
        settings.R2_PRIVATE_ACCESS_KEY_ID,
        settings.R2_PRIVATE_SECRET_ACCESS_KEY,
    ]):
        print("Missing R2 private bucket settings; cannot generate presigned URL.")
        return None

    s3_client = boto3.client(
        's3',
        endpoint_url=settings.R2_ENDPOINT_URL,
        aws_access_key_id=settings.R2_PRIVATE_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_PRIVATE_SECRET_ACCESS_KEY,
        config=Config(signature_version='s3v4'),
        region_name='auto' # R2 requires this
    )

    try:
        response = s3_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': settings.R2_PRIVATE_BUCKET_NAME,
                'Key': file_key
            },
            ExpiresIn=expiration
        )
        return response
    except Exception as e:
        print(f"Error generating presigned URL: {e}")
        return None
