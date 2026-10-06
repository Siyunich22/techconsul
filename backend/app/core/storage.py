"""Файловое хранилище (S3/MinIO). Ключи уникальны — загруженные файлы никогда не перезаписываются."""

import io
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO, Protocol

import boto3

from app.core.config import get_settings


@dataclass
class StoredObject:
    body: Iterator[bytes]
    content_type: str
    size: int


class Storage(Protocol):
    def put(self, key: str, data: BinaryIO, content_type: str) -> None: ...
    def get(self, key: str) -> StoredObject: ...
    def download(self, key: str, path: Path) -> None: ...
    def create_multipart(self, key: str, content_type: str) -> str: ...
    def upload_part(self, key: str, upload_id: str, part_no: int, data: bytes) -> str: ...
    def complete_multipart(self, key: str, upload_id: str, parts: dict[int, str]) -> None: ...
    def abort_multipart(self, key: str, upload_id: str) -> None: ...


_UNSAFE = re.compile(r"[^\w.\-]+", re.UNICODE)


def safe_filename(name: str) -> str:
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    return _UNSAFE.sub("_", name).strip("._")[:200] or "file"


def make_key(*parts: str, filename: str) -> str:
    """Например: orgs/<org>/experts/<id>/cv/<uuid>_<имя>."""
    return "/".join([*parts, f"{uuid.uuid4().hex}_{safe_filename(filename)}"])


def put_bytes(storage: "Storage", key: str, data: bytes, content_type: str) -> None:
    storage.put(key, io.BytesIO(data), content_type)


class S3Storage:
    def __init__(self) -> None:
        s = get_settings()
        self.bucket = s.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint_url,
            aws_access_key_id=s.s3_access_key,
            aws_secret_access_key=s.s3_secret_key,
            region_name=s.s3_region,
        )

    def ensure_bucket(self) -> None:
        """Создать бакет, если его нет (Railway/облако — без отдельного init-контейнера)."""
        from botocore.exceptions import ClientError

        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in ("404", "NoSuchBucket", "NotFound"):
                raise
            self.client.create_bucket(Bucket=self.bucket)

    def put(self, key: str, data: BinaryIO, content_type: str) -> None:
        self.client.upload_fileobj(data, self.bucket, key, ExtraArgs={"ContentType": content_type})

    def get(self, key: str) -> StoredObject:
        obj = self.client.get_object(Bucket=self.bucket, Key=key)
        return StoredObject(
            body=obj["Body"].iter_chunks(64 * 1024),
            content_type=obj.get("ContentType", "application/octet-stream"),
            size=obj["ContentLength"],
        )

    def download(self, key: str, path: Path) -> None:
        self.client.download_file(self.bucket, key, str(path))

    def create_multipart(self, key: str, content_type: str) -> str:
        return self.client.create_multipart_upload(Bucket=self.bucket, Key=key, ContentType=content_type)[
            "UploadId"
        ]

    def upload_part(self, key: str, upload_id: str, part_no: int, data: bytes) -> str:
        resp = self.client.upload_part(
            Bucket=self.bucket, Key=key, UploadId=upload_id, PartNumber=part_no, Body=data
        )
        return resp["ETag"]

    def complete_multipart(self, key: str, upload_id: str, parts: dict[int, str]) -> None:
        self.client.complete_multipart_upload(
            Bucket=self.bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={"Parts": [{"PartNumber": n, "ETag": parts[n]} for n in sorted(parts)]},
        )

    def abort_multipart(self, key: str, upload_id: str) -> None:
        self.client.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload_id)


@lru_cache
def _s3() -> S3Storage:
    return S3Storage()


def get_storage() -> Storage:
    return _s3()
