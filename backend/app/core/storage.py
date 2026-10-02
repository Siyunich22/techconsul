"""Файловое хранилище (S3/MinIO). Ключи уникальны — загруженные файлы никогда не перезаписываются."""

import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
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


_UNSAFE = re.compile(r"[^\w.\-]+", re.UNICODE)


def safe_filename(name: str) -> str:
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    return _UNSAFE.sub("_", name).strip("._")[:200] or "file"


def make_key(*parts: str, filename: str) -> str:
    """Например: orgs/<org>/experts/<id>/cv/<uuid>_<имя>."""
    return "/".join([*parts, f"{uuid.uuid4().hex}_{safe_filename(filename)}"])


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

    def put(self, key: str, data: BinaryIO, content_type: str) -> None:
        self.client.upload_fileobj(data, self.bucket, key, ExtraArgs={"ContentType": content_type})

    def get(self, key: str) -> StoredObject:
        obj = self.client.get_object(Bucket=self.bucket, Key=key)
        return StoredObject(
            body=obj["Body"].iter_chunks(64 * 1024),
            content_type=obj.get("ContentType", "application/octet-stream"),
            size=obj["ContentLength"],
        )


@lru_cache
def _s3() -> S3Storage:
    return S3Storage()


def get_storage() -> Storage:
    return _s3()
