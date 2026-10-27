"""The per-job vision-result cache: a cache miss when unconfigured (today's exact behaviour, unchanged),
a correct round-trip when configured, and that different (prompt, images) pairs never collide. A fake GCS
backend stands in throughout - no emulator, no network, no paid calls.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tenderlens import storage, vision_cache
from tests.test_storage import FakeBackend


@pytest.fixture
def backend(monkeypatch):
    monkeypatch.setenv("GCS_BUCKET", "fake-test-bucket")
    b = FakeBackend()
    storage.set_backend_for_testing(b)
    yield b
    storage.set_backend_for_testing(None)


def make_image(tmp_path, name, content):
    p = tmp_path / name
    p.write_bytes(content)
    return str(p)


# --------------------------------------------------------------------------- no-op without a job_id / bucket

def test_get_is_none_without_a_job_id(backend, tmp_path):
    img = make_image(tmp_path, "p1.png", b"page bytes")
    assert vision_cache.get(None, "a prompt", [img]) is None


def test_set_is_a_no_op_without_a_job_id(backend, tmp_path):
    img = make_image(tmp_path, "p1.png", b"page bytes")
    vision_cache.set(None, "a prompt", [img], {"answer": "yes"})
    assert backend.objects == {}  # nothing written


def test_get_is_none_without_gcs_bucket_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("GCS_BUCKET", "")
    storage.set_backend_for_testing(FakeBackend())
    img = make_image(tmp_path, "p1.png", b"page bytes")
    assert vision_cache.get("job-1", "a prompt", [img]) is None
    storage.set_backend_for_testing(None)


# --------------------------------------------------------------------------- round-trip

def test_set_then_get_round_trips(backend, tmp_path):
    img = make_image(tmp_path, "p1.png", b"page bytes")
    vision_cache.set("job-1", "read the tick box", [img], {"answer": "yes", "confidence": "high"})
    assert vision_cache.get("job-1", "read the tick box", [img]) == {"answer": "yes", "confidence": "high"}


def test_a_miss_before_anything_is_cached_returns_none(backend, tmp_path):
    img = make_image(tmp_path, "p1.png", b"page bytes")
    assert vision_cache.get("job-1", "never asked before", [img]) is None


def test_different_prompts_for_the_same_image_do_not_collide(backend, tmp_path):
    img = make_image(tmp_path, "p1.png", b"page bytes")
    vision_cache.set("job-1", "prompt A", [img], {"answer": "A"})
    vision_cache.set("job-1", "prompt B", [img], {"answer": "B"})
    assert vision_cache.get("job-1", "prompt A", [img]) == {"answer": "A"}
    assert vision_cache.get("job-1", "prompt B", [img]) == {"answer": "B"}


def test_different_images_for_the_same_prompt_do_not_collide(backend, tmp_path):
    img1 = make_image(tmp_path, "p1.png", b"page one bytes")
    img2 = make_image(tmp_path, "p2.png", b"page two bytes")
    vision_cache.set("job-1", "same question", [img1], {"answer": "from page 1"})
    vision_cache.set("job-1", "same question", [img2], {"answer": "from page 2"})
    assert vision_cache.get("job-1", "same question", [img1]) == {"answer": "from page 1"}
    assert vision_cache.get("job-1", "same question", [img2]) == {"answer": "from page 2"}


def test_different_job_ids_are_isolated_from_each_other(backend, tmp_path):
    img = make_image(tmp_path, "p1.png", b"page bytes")
    vision_cache.set("job-1", "same question", [img], {"answer": "job 1's answer"})
    assert vision_cache.get("job-2", "same question", [img]) is None


def test_a_changed_image_is_not_a_cache_hit(backend, tmp_path):
    """If the same page were re-rendered with different bytes (a different crop, a re-render at a different
    DPI), that must not accidentally reuse a stale answer for what the cache treats as a different image."""
    img = make_image(tmp_path, "p1.png", b"original bytes")
    vision_cache.set("job-1", "same question", [img], {"answer": "original"})
    make_image(tmp_path, "p1.png", b"different bytes now")  # overwrite the same path with new content
    assert vision_cache.get("job-1", "same question", [img]) is None


# --------------------------------------------------------------------------- failure tolerance

def test_set_never_raises_even_if_the_backend_write_fails(backend, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network blip")
    monkeypatch.setattr(backend, "write_bytes", boom)
    img = make_image(tmp_path, "p1.png", b"page bytes")
    vision_cache.set("job-1", "a prompt", [img], {"answer": "yes"})  # must not raise


def test_get_never_raises_even_if_the_backend_read_fails(backend, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network blip")
    monkeypatch.setattr(backend, "read_bytes", boom)
    img = make_image(tmp_path, "p1.png", b"page bytes")
    assert vision_cache.get("job-1", "a prompt", [img]) is None  # a miss, not a crash
