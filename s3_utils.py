import os
import hashlib
import logging
import boto3
from botocore.exceptions import ClientError
from fastapi import UploadFile, HTTPException

logger    = logging.getLogger(__name__)
S3_BUCKET = os.environ.get("S3_BUCKET", "legaldrop-documents")
AWS_REGION= os.environ.get("AWS_REGION", "us-east-1")

s3 = boto3.client(
    "s3",
    region_name=AWS_REGION,
    aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
)


async def store_document(file: UploadFile, delivery_id: str) -> dict:
    file_bytes = await file.read()
    if len(file_bytes) > 500 * 1024 * 1024:
        raise HTTPException(413, "File exceeds 500 MB limit.")

    sha256   = hashlib.sha256(file_bytes).hexdigest()
    filename = file.filename or "document"
    s3_key   = f"deliveries/{delivery_id}/{filename}"
    ct       = file.content_type or "application/octet-stream"

    try:
        s3.put_object(
            Bucket=S3_BUCKET, Key=s3_key,
            Body=file_bytes,
            ContentType=ct,
            ServerSideEncryption="AES256",
        )
    except ClientError as e:
        logger.error("S3 upload failed: %s", e)
        raise HTTPException(502, "Document storage error. Please try again.")

    return {
        "s3_key":           s3_key,
        "filename":         filename,
        "file_size_bytes":  len(file_bytes),
        "content_type":     ct,
        "local_sha256":     sha256,
    }


def presigned_url(s3_key: str, expiry: int = 900) -> str:
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": S3_BUCKET, "Key": s3_key},
        ExpiresIn=expiry,
    )


def presigned_download_url(s3_key: str, filename: str, expiry: int = 3600) -> str:
    return s3.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": S3_BUCKET,
            "Key": s3_key,
            "ResponseContentDisposition": f'attachment; filename="{filename}"',
        },
        ExpiresIn=expiry,
    )