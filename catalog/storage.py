"""S3 client for product images, pointed at the SeaweedFS bucket via swbucket-link env vars."""
import boto3
from django.conf import settings


def s3_client():
    """Return a boto3 S3 client, or None when storage isn't linked (local dev)."""
    if not settings.S3_ENDPOINT_URL:
        return None
    return boto3.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT_URL,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_DEFAULT_REGION,
    )


def presigned_image_url(image_key, expires=3600):
    """Presigned GET URL for a stored image, or empty string if no storage/key."""
    client = s3_client()
    if not client or not image_key:
        return ""
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.S3_BUCKET, "Key": image_key},
        ExpiresIn=expires,
    )
