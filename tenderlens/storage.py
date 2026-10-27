"""Browser-to-bucket uploads for large tender packs (the tender document and the response pack can each be
up to MAX_UPLOAD_MB). The browser uploads chunks straight to Google Cloud Storage over a resumable session;
nothing large ever passes through Streamlit or Cloud Run's own request path (Cloud Run caps an inbound
HTTP/1 request at 32 MiB - see README).

Design: the server initiates the resumable session with its own IAM credentials (Storage Object Admin on
this one bucket - no signBlob, no Service Account Token Creator role needed) and hands the browser the
resulting session URI. That URI works as a bearer credential for the rest of the upload (valid 7 days, per
Google's docs) - treat it exactly like a signed URL or an API key: never written, printed, logged or put in
an error message. This is Google's own recommended pattern over a fully-signed URL for the initiating
request; see the Step 1 docs findings for why.

Config:
    GCS_BUCKET               the one private bucket uploads go to
    MAX_UPLOAD_MB             default 300. Decimal MB (1 MB = 1,000,000 bytes), applied to EACH file
                              separately (the tender document and the response pack each get this limit).
    UPLOAD_RETENTION_DAYS     default 7. A bucket lifecycle rule (see deploy notes) deletes objects after
                              this many days; this module does not enforce it itself.

What GCS does and does NOT enforce for you: there is no documented header on a resumable session's
initiating request that GCS itself uses to reject an oversized upload - that header
(x-goog-content-length-range) exists only for signed POST Policy documents, a different, non-resumable
upload mechanism unsuitable for a 300 MB file on an unstable connection. So the real size enforcement here
happens in exactly one place that matters: finalize_upload() reads the object's REAL size from GCS metadata
after the browser says it's done, and deletes + refuses the job if it's over the limit. The browser-side
check and the claimed size sent at session creation are both real UX (an honest client won't upload 300 MB
just to be told no), but neither is a security boundary - do not treat them as one.
"""
from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

ALLOWED_KINDS = ("tender", "pack")
PDF_MAGIC = b"%PDF-"


# Config is read fresh on every call, not frozen as a module constant at import time - same reason
# llm.py's LlamaClient reads its env vars in __init__ rather than at import, and pilot.pilot_enabled() is a
# function rather than a constant: a real deployment's env vars don't change mid-process, so this makes no
# difference there, but a test process imports this module once and runs many tests with different env
# vars against it, which a frozen constant would never see.
def gcs_bucket() -> str:
    return os.environ.get("GCS_BUCKET", "").strip()


def max_upload_mb() -> int:
    return int(os.environ.get("MAX_UPLOAD_MB", "300"))


def max_upload_bytes() -> int:
    return max_upload_mb() * 1_000_000


def upload_retention_days() -> int:
    return int(os.environ.get("UPLOAD_RETENTION_DAYS", "7"))


class StorageError(RuntimeError):
    """Base class. str(err) is always safe to show a user or log - never a session URL or any credential."""


class StorageUnavailable(StorageError):
    """GCS_BUCKET isn't configured, or the bucket/API couldn't be reached."""


class UploadTooLarge(StorageError):
    def __init__(self, actual_bytes: int, max_bytes: int | None = None):
        max_bytes = max_upload_bytes() if max_bytes is None else max_bytes
        super().__init__(f"{actual_bytes:,} bytes is over the {max_bytes:,}-byte ({max_bytes // 1_000_000} MB) limit")
        self.actual_bytes = actual_bytes
        self.max_bytes = max_bytes


class NotAPdf(StorageError):
    def __init__(self, reason: str):
        super().__init__(f"Not a PDF: {reason}")


@dataclass
class UploadSession:
    """Everything the browser needs to do the chunked PUTs itself. session_url is a bearer credential -
    see the module docstring: never logged, never put in an error message, never written to a test
    fixture. object_name is safe to log (it's server-generated, not the user's filename)."""
    object_name: str
    session_url: str
    max_bytes: int


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _clean_filename(name: str) -> str:
    """Original filename, kept only as metadata (never as the object name - see module docstring): strip
    any path, control characters and anything that isn't a reasonably plain filename character, and cap
    the length. An empty or fully-stripped result falls back to a generic name rather than an empty string.
    Splits on both "/" and "\\" (not just the host OS's separator via Path().name) - a browser upload can
    carry a Windows-style path even when this runs on Linux."""
    name = re.split(r"[\\/]", name)[-1]  # drop any directory component (., .., /, \, a full path)
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)  # control characters
    name = re.sub(r"[^A-Za-z0-9 ._-]", "_", name).strip()
    name = name[:200]
    return name or "upload.pdf"


def _object_name(kind: str, job_id: str) -> str:
    """Server-generated: a random id plus the job id, never the user's filename - so a hostile filename
    can't be used to overwrite another job's object or escape the uploads/ prefix."""
    if kind not in ALLOWED_KINDS:
        raise ValueError(f"kind must be one of {ALLOWED_KINDS}, got {kind!r}")
    safe_job_id = re.sub(r"[^A-Za-z0-9_-]", "", job_id)[:64] or "job"
    return f"uploads/{safe_job_id}/{kind}-{secrets.token_hex(16)}.pdf"


class GCSBackend:
    """Thin wrapper around google.cloud.storage so storage.py's functions don't import it directly - a
    test passes a fake with the same four methods instead (see tests/test_storage.py), no emulator needed
    for the offline suite. A real backend is only constructed when GCS_BUCKET is set and storage is
    actually used, so importing this module never requires google-cloud-storage to be installed unless
    you're actually using the feature."""

    def __init__(self, bucket_name: str):
        from google.cloud import storage
        self._client = storage.Client()
        self._bucket = self._client.bucket(bucket_name)

    def create_resumable_session(self, object_name: str, content_type: str, size: int, metadata: dict,
                                 origin: str) -> str:
        blob = self._bucket.blob(object_name)
        blob.metadata = metadata
        return blob.create_resumable_upload_session(content_type=content_type, size=size, origin=origin or None)

    def stat(self, object_name: str) -> dict | None:
        """{"size": int, "content_type": str} or None if the object doesn't exist (upload never finished,
        or finalize_upload() is being called too early)."""
        blob = self._bucket.get_blob(object_name)
        if blob is None:
            return None
        return {"size": blob.size, "content_type": blob.content_type}

    def read_prefix(self, object_name: str, n: int) -> bytes:
        blob = self._bucket.blob(object_name)
        return blob.download_as_bytes(start=0, end=max(0, n - 1))

    def download_to_file(self, object_name: str, dest: Path) -> None:
        blob = self._bucket.blob(object_name)
        blob.download_to_filename(str(dest))

    def delete(self, object_name: str) -> None:
        self._bucket.blob(object_name).delete()

    def read_bytes(self, object_name: str) -> bytes | None:
        """None if the object doesn't exist - a cache/state miss, not an error."""
        blob = self._bucket.blob(object_name)
        if not blob.exists():
            return None
        return blob.download_as_bytes()

    def write_bytes(self, object_name: str, data: bytes, content_type: str) -> None:
        self._bucket.blob(object_name).upload_from_string(data, content_type=content_type)


_backend: GCSBackend | None = None
_backend_bucket: str | None = None  # which bucket _backend was built for - rebuilt if GCS_BUCKET changes
_backend_override: GCSBackend | None = None  # tests set this instead of constructing a real GCSBackend


def set_backend_for_testing(backend) -> None:
    """A fake implementing GCSBackend's four methods. Call with None to go back to the real backend."""
    global _backend_override
    _backend_override = backend


def _get_backend() -> GCSBackend:
    global _backend
    if _backend_override is not None:
        return _backend_override
    bucket = gcs_bucket()
    if not bucket:
        raise StorageUnavailable("GCS_BUCKET is not configured.")
    if _backend is None or _backend_bucket != bucket:
        try:
            _backend = GCSBackend(bucket)
        except Exception as err:  # noqa: BLE001 - never let a credential-bearing exception string through unfiltered
            raise StorageUnavailable(f"Could not reach Cloud Storage: {type(err).__name__}") from None
        globals()["_backend_bucket"] = bucket
    return _backend


def create_upload_session(kind: str, filename: str, size: int, content_type: str, job_id: str,
                          origin: str = "") -> UploadSession:
    """Enforcement point 1 of 3 (see module docstring): rejects an oversized or non-PDF request before a
    session is even created - the honest-client, good-UX path, not a security boundary on its own."""
    max_bytes = max_upload_bytes()
    if size > max_bytes:
        raise UploadTooLarge(size, max_bytes)
    if content_type != "application/pdf":
        raise NotAPdf(f"content type {content_type!r}, expected application/pdf")
    object_name = _object_name(kind, job_id)
    metadata = {"original_filename": _clean_filename(filename), "kind": kind, "job_id": job_id}
    backend = _get_backend()
    session_url = backend.create_resumable_session(object_name, content_type, size, metadata, origin)
    return UploadSession(object_name=object_name, session_url=session_url, max_bytes=max_bytes)


def finalize_upload(object_name: str) -> dict:
    """Enforcement point 3 of 3, the one that's actually real (see module docstring): reads the object's
    true size and the first few bytes from GCS itself - not anything the client claimed - after the
    browser reports the upload finished. Deletes the object and raises if it's oversized or not a PDF.
    Returns {"object_name", "size", "original_filename"} on success."""
    backend = _get_backend()
    info = backend.stat(object_name)
    if info is None:
        raise StorageError(f"Object {object_name} not found - the upload may not have finished.")
    if info["size"] > max_upload_bytes():
        backend.delete(object_name)
        raise UploadTooLarge(info["size"])
    prefix = backend.read_prefix(object_name, len(PDF_MAGIC))
    if not prefix.startswith(PDF_MAGIC):
        backend.delete(object_name)
        raise NotAPdf("the file's own bytes don't start with the PDF magic number - a renamed non-PDF, "
                      "or a corrupted upload")
    return {"object_name": object_name, "size": info["size"]}


def open_for_processing(object_name: str, dest_dir: Path) -> Path:
    """Streams the object from the bucket into the local work area, under its own server-generated name
    (never the original filename - see module docstring). Returns the local path. Page-by-page processing
    of what lands here, without loading the whole pack into memory at once, is the ingest pipeline's job
    (see Step 4) - this function's only responsibility is getting the bytes from the bucket to local disk."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / Path(object_name).name
    _get_backend().download_to_file(object_name, dest)
    return dest


def delete(object_name: str) -> None:
    """Best-effort: a job's work files should not outlive the job (see Step 5's retention rules) - a
    failure here is logged by the caller, not raised, since it must never be the reason an audit fails."""
    _get_backend().delete(object_name)


# -- small JSON objects: job state (see jobs.py) and the per-page vision cache (see vision_cache.py) -----
# Neither holds document contents or personal data on its own (job state is progress counters; the vision
# cache's values are the same per-page answers already held in memory during a normal run) - but both ride
# on the same retention lifecycle rule as the uploads themselves (see Step 5), and both are deleted by
# delete() like any other object once the job no longer needs them.

def read_json(object_name: str) -> dict | None:
    """None if the object doesn't exist - a cache/state miss, not an error worth raising."""
    try:
        raw = _get_backend().read_bytes(object_name)
    except Exception:  # noqa: BLE001 - a transient read failure is a miss, not a reason to crash the job
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def write_json(object_name: str, value: dict) -> None:
    _get_backend().write_bytes(object_name, json.dumps(value).encode(), "application/json")
