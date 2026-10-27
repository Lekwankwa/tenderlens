"""Per-job cache of vision call results, keyed by (page image bytes, prompt). This is what makes a resumed
job actually skip pages already answered - not just skip OCR/requirements the way cli.py's existing
rerun_vision() does (see that function's own docstring: it predates this and only avoids re-reading
documents, not re-asking vision questions).

GCS-only, and only when a job_id is given: a Cloud Run instance restart loses local disk entirely, so only
a job uploaded through tenderlens/storage.py (GCS_BUCKET configured) can lose in-progress vision work to a
restart in the first place. Without a job_id (every caller outside the GCS-upload path - the CLI, a local
Streamlit Community Cloud run), every lookup misses and nothing is written: today's exact behaviour, not
something this changes.

A cache entry holds the same per-page vision answer that would otherwise live only in memory during one
run - no more sensitive than that, but it rides on the same retention lifecycle rule as the uploads
themselves (see Step 5) and is deleted the same way once a job no longer needs it.
"""
from __future__ import annotations

import hashlib

from . import storage


def _key(prompt: str, images: list[str]) -> str:
    h = hashlib.sha256(prompt.encode())
    for path in images:
        with open(path, "rb") as f:
            h.update(f.read())
    return h.hexdigest()


def _object_name(job_id: str, key: str) -> str:
    return f"jobs/{job_id}/vision_cache/{key}.json"


def get(job_id: str | None, prompt: str, images: list[str]) -> dict | None:
    if not job_id or not storage.gcs_bucket():
        return None
    return storage.read_json(_object_name(job_id, _key(prompt, images)))


def set(job_id: str | None, prompt: str, images: list[str], value: dict) -> None:
    if not job_id or not storage.gcs_bucket():
        return
    try:
        storage.write_json(_object_name(job_id, _key(prompt, images)), value)
    except Exception:  # noqa: BLE001 - caching is an optimization; losing one write must never break a run
        pass
