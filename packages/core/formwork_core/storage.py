"""Object store: raw files, page images, crops, outputs. S3-compatible in prod (MinIO locally), filesystem in dev/tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Protocol

from formwork_core.config import Settings
from formwork_core.errors import NotFound


class ObjectStore(Protocol):
    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...
    async def get(self, key: str) -> bytes: ...
    async def exists(self, key: str) -> bool: ...
    async def delete_prefix(self, prefix: str) -> int: ...


def _safe_key(key: str) -> str:
    parts = [p for p in key.split("/") if p]
    if not parts or any(p in (".", "..") or "\x00" in p for p in parts):
        raise ValueError(f"unsafe object key: {key!r}")
    return "/".join(parts)


class LocalObjectStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / _safe_key(key)).resolve()
        if not p.is_relative_to(self.root):
            raise ValueError("object key escapes the store root")
        return p

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)

    async def get(self, key: str) -> bytes:
        p = self._path(key)
        if not p.is_file():
            raise NotFound(f"object not found: {key}")
        return p.read_bytes()

    async def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    async def delete_prefix(self, prefix: str) -> int:
        base = self._path(prefix)
        n = 0
        if base.is_dir():
            for f in sorted(base.rglob("*"), reverse=True):
                if f.is_file():
                    f.unlink()
                    n += 1
                else:
                    f.rmdir()
        return n


class MemoryObjectStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.objects[_safe_key(key)] = data

    async def get(self, key: str) -> bytes:
        try:
            return self.objects[_safe_key(key)]
        except KeyError:
            raise NotFound(f"object not found: {key}") from None

    async def exists(self, key: str) -> bool:
        return _safe_key(key) in self.objects

    async def delete_prefix(self, prefix: str) -> int:
        keys = [k for k in self.objects if k.startswith(_safe_key(prefix))]
        for k in keys:
            del self.objects[k]
        return len(keys)


class S3ObjectStore:
    """boto3 is synchronous; calls run in a worker thread."""

    def __init__(self, settings: Settings) -> None:
        import boto3

        self.bucket = settings.s3_bucket
        self._client: Any = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key.get_secret_value() if settings.s3_access_key else None,
            aws_secret_access_key=settings.s3_secret_key.get_secret_value()
            if settings.s3_secret_key
            else None,
        )

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self.bucket,
            Key=_safe_key(key),
            Body=data,
            ContentType=content_type,
        )

    async def get(self, key: str) -> bytes:
        def _get() -> bytes:
            try:
                body: bytes = self._client.get_object(Bucket=self.bucket, Key=_safe_key(key))["Body"].read()
                return body
            except self._client.exceptions.NoSuchKey:
                raise NotFound(f"object not found: {key}") from None

        return await asyncio.to_thread(_get)

    async def exists(self, key: str) -> bool:
        def _head() -> bool:
            try:
                self._client.head_object(Bucket=self.bucket, Key=_safe_key(key))
                return True
            except Exception:
                return False

        return await asyncio.to_thread(_head)

    async def delete_prefix(self, prefix: str) -> int:
        def _delete() -> int:
            n = 0
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=_safe_key(prefix)):
                objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
                if objs:
                    self._client.delete_objects(Bucket=self.bucket, Delete={"Objects": objs})
                    n += len(objs)
            return n

        return await asyncio.to_thread(_delete)


def build_object_store(settings: Settings) -> ObjectStore:
    if settings.object_store == "s3":
        return S3ObjectStore(settings)
    return LocalObjectStore(settings.local_store_path)


# ---- key layout (docs/lld.md, "Object store layout") ----
def file_key(tenant: str, sha256: str, ext: str) -> str:
    return f"{tenant}/files/{sha256}/original.{ext.lstrip('.') or 'bin'}"


def page_key(tenant: str, document_id: str, page: int) -> str:
    return f"{tenant}/documents/{document_id}/pages/{page}.png"


def crop_key(tenant: str, document_id: str, block_id: str) -> str:
    return f"{tenant}/documents/{document_id}/crops/{block_id}.png"


def output_key(tenant: str, job_id: str, fmt: str) -> str:
    return f"{tenant}/jobs/{job_id}/output.{fmt}"


def lineage_key(tenant: str, job_id: str) -> str:
    return f"{tenant}/jobs/{job_id}/lineage.json"
