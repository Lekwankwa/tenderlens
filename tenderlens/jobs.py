"""Job state for a large-pack audit, persisted as one small JSON object per job in the same bucket the
upload itself used - see Step 4 of the GCS task. Chosen over Firestore: this project already has GCS wired
up for uploads (same IAM role, no new API to enable), runs as a single Cloud Run instance
(--max-instances=1 - see deploy.sh), and already keeps its other small shared state as a committed SQLite
file rather than a hosted database (see pilot.py) - Firestore is the right call at real multi-instance
scale, which this prototype isn't at yet.

Honest scope: this is NOT a standing background worker. TenderLens has no separate worker process - the
audit still runs synchronously inside the same Streamlit script run that was triggered by pressing "Audit
the pack" (Cloud Run's 60-minute request timeout already comfortably covers a run - see README "Known
limits"). What this module actually buys you:
  - the page can be refreshed mid-run and still show real progress (state.json is read fresh each time);
  - if the Cloud Run instance restarts mid-run, the NEXT visit to this job_id (not an autonomous resume -
    nobody is watching while the tab is closed) picks the job back up, and thanks to vision_cache.py does
    not repeat the vision calls already answered before the restart.
"""
from __future__ import annotations

import time

from . import storage

STATUSES = ("queued", "running", "done", "failed")


def _object_name(job_id: str) -> str:
    return f"jobs/{job_id}/state.json"


def save(job_id: str, status: str, stage: str = "", done: int = 0, total: int = 0, error: str = "") -> None:
    """Best-effort, like vision_cache.set(): a failed write must never be why an audit fails. No-op without
    a job_id or GCS_BUCKET configured - the small-file upload path has no job to track."""
    if not job_id or not storage.gcs_bucket():
        return
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}, got {status!r}")
    try:
        storage.write_json(_object_name(job_id), {
            "status": status, "stage": stage, "done": done, "total": total, "error": error,
            "updated_at": time.time(),
        })
    except Exception:  # noqa: BLE001 - see module docstring; a lost progress update is not a reason to fail
        pass


def load(job_id: str) -> dict | None:
    """None if there's no job_id, GCS isn't configured, or nothing has been saved for this job yet."""
    if not job_id or not storage.gcs_bucket():
        return None
    return storage.read_json(_object_name(job_id))


def clear(job_id: str) -> None:
    """Called once a job's result has been shown to the user - job state should not outlive the job any
    more than the uploaded files should (see Step 5's retention rules)."""
    if not job_id or not storage.gcs_bucket():
        return
    try:
        storage.delete(_object_name(job_id))
    except storage.StorageError:
        pass  # already gone, or the bucket's own lifecycle rule will catch it regardless
