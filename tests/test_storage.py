"""Upload session creation, size/type enforcement, filename cleaning, and that a session URL (a bearer
credential - see tenderlens/storage.py's module docstring) never leaks into a log line or an error message.
A fake backend stands in for google.cloud.storage throughout - no emulator, no network, no paid calls.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tenderlens import storage


SECRET_SESSION_URL = "https://storage.googleapis.com/upload/storage/v1/b/my-bucket/o?upload_id=SECRET-TOKEN-ABC123"


class FakeBackend:
    """Implements GCSBackend's four methods against an in-memory dict - the only interface storage.py's
    functions actually depend on."""

    def __init__(self):
        self.objects: dict[str, dict] = {}  # object_name -> {"bytes": b"...", "content_type": str}
        self.sessions_created: list[str] = []

    def create_resumable_session(self, object_name, content_type, size, metadata, origin):
        self.sessions_created.append(object_name)
        # the real upload (the browser PUTting bytes) isn't simulated here - tests that need a finished
        # object call put_object() directly, the way a completed browser upload would leave one behind.
        return SECRET_SESSION_URL

    def stat(self, object_name):
        obj = self.objects.get(object_name)
        if obj is None:
            return None
        return {"size": len(obj["bytes"]), "content_type": obj["content_type"]}

    def read_prefix(self, object_name, n):
        return self.objects[object_name]["bytes"][:n]

    def download_to_file(self, object_name, dest):
        dest.write_bytes(self.objects[object_name]["bytes"])

    def delete(self, object_name):
        self.objects.pop(object_name, None)

    def put_object(self, object_name, data: bytes, content_type="application/pdf"):
        """Test helper: simulates a browser having finished the resumable PUT."""
        self.objects[object_name] = {"bytes": data, "content_type": content_type}


@pytest.fixture
def backend():
    b = FakeBackend()
    storage.set_backend_for_testing(b)
    yield b
    storage.set_backend_for_testing(None)


PDF_BYTES = b"%PDF-1.7\n...minimal fake pdf content..."


# --------------------------------------------------------------------------- session creation

def test_create_upload_session_returns_a_server_generated_object_name(backend):
    session = storage.create_upload_session("pack", "my response.pdf", 1000, "application/pdf", "job-1")
    assert session.object_name.startswith("uploads/job-1/pack-")
    assert session.object_name.endswith(".pdf")
    assert "my response" not in session.object_name  # never the user's filename


def test_create_upload_session_stores_the_cleaned_filename_as_metadata(backend):
    storage.create_upload_session("tender", "../../etc/passwd.pdf", 1000, "application/pdf", "job-1")
    object_name = backend.sessions_created[-1]
    # the object name itself must never contain the path-traversal attempt
    assert "etc" not in object_name and ".." not in object_name


def test_two_sessions_for_the_same_job_get_different_object_names(backend):
    a = storage.create_upload_session("pack", "a.pdf", 1000, "application/pdf", "job-1")
    b = storage.create_upload_session("pack", "a.pdf", 1000, "application/pdf", "job-1")
    assert a.object_name != b.object_name


def test_create_upload_session_rejects_an_oversized_request(backend):
    with pytest.raises(storage.UploadTooLarge):
        storage.create_upload_session("pack", "big.pdf", storage.max_upload_bytes() + 1, "application/pdf", "job-1")
    assert backend.sessions_created == []  # never even asked GCS for a session


def test_create_upload_session_rejects_a_non_pdf_content_type(backend):
    with pytest.raises(storage.NotAPdf):
        storage.create_upload_session("pack", "a.docx", 1000, "application/msword", "job-1")


def test_create_upload_session_rejects_an_unknown_kind(backend):
    with pytest.raises(ValueError):
        storage.create_upload_session("transcript", "a.pdf", 1000, "application/pdf", "job-1")


# --------------------------------------------------------------------------- finalize: the real enforcement

def test_finalize_upload_accepts_a_pdf_within_the_limit(backend):
    session = storage.create_upload_session("pack", "a.pdf", 1000, "application/pdf", "job-1")
    backend.put_object(session.object_name, PDF_BYTES)
    result = storage.finalize_upload(session.object_name)
    assert result["object_name"] == session.object_name
    assert result["size"] == len(PDF_BYTES)


def test_finalize_upload_rejects_and_deletes_a_file_that_lied_about_its_size_at_session_creation(backend):
    """The client claimed a small size to get a session, then the browser actually PUT more bytes than
    that - finalize_upload() must catch this from GCS's own object metadata, not trust the claim."""
    session = storage.create_upload_session("pack", "a.pdf", 1000, "application/pdf", "job-1")
    oversized = b"%PDF-" + b"x" * (storage.max_upload_bytes() + 1)
    backend.put_object(session.object_name, oversized)
    with pytest.raises(storage.UploadTooLarge) as exc:
        storage.finalize_upload(session.object_name)
    assert exc.value.actual_bytes == len(oversized)
    assert session.object_name not in backend.objects  # deleted, not left sitting in the bucket


def test_finalize_upload_rejects_and_deletes_a_renamed_non_pdf(backend):
    """A client can set content_type: application/pdf and name the file *.pdf while uploading something
    else entirely - the magic-bytes check at finalize is what actually catches that, not the declared type."""
    session = storage.create_upload_session("pack", "a.pdf", 1000, "application/pdf", "job-1")
    backend.put_object(session.object_name, b"PK\x03\x04this is actually a zip file")
    with pytest.raises(storage.NotAPdf):
        storage.finalize_upload(session.object_name)
    assert session.object_name not in backend.objects


def test_finalize_upload_on_a_missing_object_raises_cleanly(backend):
    with pytest.raises(storage.StorageError):
        storage.finalize_upload("uploads/job-1/pack-doesnotexist.pdf")


# --------------------------------------------------------------------------- filename cleaning

@pytest.mark.parametrize("raw,expected_safe", [
    ("normal name.pdf", "normal name.pdf"),
    ("../../etc/passwd", "passwd"),
    ("..\\..\\windows\\system32\\evil.pdf", "evil.pdf"),
    ("file\x00withnull.pdf", "filewithnull.pdf"),
    ("semi;colon`backtick$(whoami).pdf", "semi_colon_backtick__whoami_.pdf"),
    ("", "upload.pdf"),
    ("   ", "upload.pdf"),
    ("a" * 500 + ".pdf", "a" * 200),
])
def test_clean_filename(raw, expected_safe):
    assert storage._clean_filename(raw) == expected_safe


# --------------------------------------------------------------------------- the session URL is a secret

def test_the_session_url_never_appears_in_an_upload_too_large_error(backend):
    session = storage.create_upload_session("pack", "a.pdf", 1000, "application/pdf", "job-1")
    oversized = b"%PDF-" + b"x" * (storage.max_upload_bytes() + 1)
    backend.put_object(session.object_name, oversized)
    with pytest.raises(storage.UploadTooLarge) as exc:
        storage.finalize_upload(session.object_name)
    assert SECRET_SESSION_URL not in str(exc.value)


def test_the_session_url_never_appears_in_a_not_a_pdf_error(backend):
    session = storage.create_upload_session("pack", "a.pdf", 1000, "application/pdf", "job-1")
    backend.put_object(session.object_name, b"not a pdf at all")
    with pytest.raises(storage.NotAPdf) as exc:
        storage.finalize_upload(session.object_name)
    assert SECRET_SESSION_URL not in str(exc.value)


def test_upload_session_dataclass_repr_does_not_hide_the_url_but_callers_must_not_log_it_raw():
    """Documents the contract (see module docstring), rather than hiding the field in repr(), which would
    just push the mistake somewhere else - callers must affirmatively choose not to log session.session_url."""
    session = storage.UploadSession(object_name="uploads/job-1/pack-abc.pdf", session_url=SECRET_SESSION_URL,
                                    max_bytes=1000)
    assert session.session_url == SECRET_SESSION_URL  # present on the object...
    assert "object_name" in repr(session)              # ...but every caller must choose what to log


# --------------------------------------------------------------------------- open_for_processing / delete

def test_open_for_processing_streams_the_object_to_a_local_path(backend, tmp_path):
    session = storage.create_upload_session("pack", "a.pdf", 1000, "application/pdf", "job-1")
    backend.put_object(session.object_name, PDF_BYTES)
    local_path = storage.open_for_processing(session.object_name, tmp_path)
    assert local_path.read_bytes() == PDF_BYTES
    assert local_path.parent == tmp_path


def test_delete_removes_the_object(backend):
    session = storage.create_upload_session("pack", "a.pdf", 1000, "application/pdf", "job-1")
    backend.put_object(session.object_name, PDF_BYTES)
    storage.delete(session.object_name)
    assert session.object_name not in backend.objects


# --------------------------------------------------------------------------- unconfigured storage

def test_unconfigured_bucket_raises_storage_unavailable(monkeypatch):
    storage.set_backend_for_testing(None)
    monkeypatch.setenv("GCS_BUCKET", "")
    with pytest.raises(storage.StorageUnavailable):
        storage.create_upload_session("pack", "a.pdf", 1000, "application/pdf", "job-1")
