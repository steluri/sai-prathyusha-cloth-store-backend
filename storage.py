from __future__ import annotations

import mimetypes
import os
import uuid
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from flask import redirect, send_from_directory
from werkzeug.utils import secure_filename


ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}


class StorageError(RuntimeError):
    pass


def _extension(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _object_key(file, slot: str) -> str:
    extension = _extension(file.filename or "")
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError(f"Unsupported file type for '{slot}' image.")
    safe_slot = secure_filename(slot) or "image"
    return f"products/{safe_slot}-{uuid.uuid4().hex}.{extension}"


def _upload_path(key: str) -> str:
    return f"/uploads/{quote(key, safe='/')}"


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, file, slot: str) -> str:
        key = _object_key(file, slot)
        destination = self.root / Path(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        file.save(destination)
        return _upload_path(key)

    def delete(self, path: str | None) -> None:
        key = key_from_upload_path(path)
        if not key:
            return
        target = (self.root / Path(key)).resolve()
        root = self.root.resolve()
        if root not in target.parents:
            return
        target.unlink(missing_ok=True)

    def response(self, key: str):
        return send_from_directory(self.root, key)


class S3Storage:
    def __init__(self):
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise StorageError(
                "S3 storage requires boto3. Run: pip install -r backend/requirements.txt"
            ) from exc

        self.bucket = os.environ.get("OBJECT_STORAGE_BUCKET", "").strip()
        if not self.bucket:
            raise StorageError("OBJECT_STORAGE_BUCKET is required when STORAGE_BACKEND=s3")

        self.region = os.environ.get("OBJECT_STORAGE_REGION", "us-east-1").strip()
        self.endpoint_url = os.environ.get("OBJECT_STORAGE_ENDPOINT_URL", "").strip() or None
        self.public_url = os.environ.get("OBJECT_STORAGE_PUBLIC_URL", "").strip().rstrip("/")
        addressing_style = os.environ.get("OBJECT_STORAGE_ADDRESSING_STYLE", "auto").strip()
        config = Config(s3={"addressing_style": addressing_style})
        options = {
            "service_name": "s3",
            "region_name": self.region,
            "endpoint_url": self.endpoint_url,
            "config": config,
        }
        access_key = os.environ.get("OBJECT_STORAGE_ACCESS_KEY_ID", "").strip()
        secret_key = os.environ.get("OBJECT_STORAGE_SECRET_ACCESS_KEY", "").strip()
        if access_key:
            options["aws_access_key_id"] = access_key
        if secret_key:
            options["aws_secret_access_key"] = secret_key
        self.client = boto3.client(**options)

    def save(self, file, slot: str) -> str:
        key = _object_key(file, slot)
        content_type = file.mimetype or mimetypes.guess_type(file.filename or "")[0]
        extra_args = {"ContentType": content_type} if content_type else None
        try:
            if extra_args:
                self.client.upload_fileobj(file.stream, self.bucket, key, ExtraArgs=extra_args)
            else:
                self.client.upload_fileobj(file.stream, self.bucket, key)
        except Exception as exc:
            raise StorageError(f"Could not upload image to object storage: {exc}") from exc
        return _upload_path(key)

    def delete(self, path: str | None) -> None:
        key = key_from_upload_path(path)
        if not key:
            return
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            raise StorageError(f"Could not delete image from object storage: {exc}") from exc

    def response(self, key: str):
        if self.public_url:
            return redirect(f"{self.public_url}/{quote(key, safe='/')}", code=302)
        try:
            url = self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=3600,
            )
        except Exception as exc:
            raise StorageError(f"Could not create image URL: {exc}") from exc
        return redirect(url, code=302)

    def create_bucket(self) -> bool:
        try:
            self.client.head_bucket(Bucket=self.bucket)
            return False
        except Exception:
            pass

        args = {"Bucket": self.bucket}
        if not self.endpoint_url and self.region != "us-east-1":
            args["CreateBucketConfiguration"] = {"LocationConstraint": self.region}
        self.client.create_bucket(**args)
        return True


def key_from_upload_path(path: str | None) -> str | None:
    prefix = "/uploads/"
    if not path or not path.startswith(prefix):
        return None
    key = path[len(prefix):]
    normalized = str(PurePosixPath(key))
    if not key or normalized.startswith("../") or normalized == "..":
        return None
    return normalized


def build_storage(base_dir: Path):
    backend = os.environ.get("STORAGE_BACKEND", "local").strip().lower()
    if backend == "local":
        return LocalStorage(base_dir / "uploads")
    if backend == "s3":
        return S3Storage()
    raise StorageError("STORAGE_BACKEND must be either 'local' or 's3'")
