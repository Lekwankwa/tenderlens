"""Packs of hundreds of pages: slicing, the smaller model copy of a page, and the tender-text cap. No network."""
from __future__ import annotations

import random
import sys
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tenderlens import llm as L
from tenderlens.requirements import prompt_excerpt


def test_a_pack_up_to_the_threshold_is_one_slice_and_a_longer_one_goes_in_slices_of_25(monkeypatch):
    monkeypatch.delenv("TENDERLENS_BATCH_OVER", raising=False)
    monkeypatch.delenv("TENDERLENS_BATCH_PAGES", raising=False)
    assert [len(c) for c in L.slices(list(range(50)))] == [50]
    assert [len(c) for c in L.slices(list(range(51)))] == [25, 25, 1]
    assert [len(c) for c in L.slices(list(range(120)))] == [25, 25, 25, 25, 20]


def test_slice_sizes_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("TENDERLENS_BATCH_OVER", "10")
    monkeypatch.setenv("TENDERLENS_BATCH_PAGES", "4")
    assert [len(c) for c in L.slices(list(range(11)))] == [4, 4, 3]


def test_run_in_slices_keeps_task_order_and_finishes_a_slice_before_starting_the_next(monkeypatch):
    monkeypatch.setenv("TENDERLENS_BATCH_OVER", "6")
    monkeypatch.setenv("TENDERLENS_BATCH_PAGES", "4")
    started, lock = [], threading.Lock()
    rng = random.Random(1)

    def task(i):
        def run():
            with lock:
                started.append(i)
            time.sleep(rng.random() * 0.03)  # finish in a different order than they were started
            return i
        return run

    results = L.run_in_slices([task(i) for i in range(10)], max_workers=10)
    assert results == list(range(10))
    # tasks 4..7 may not begin before every task 0..3 has begun AND finished: the first 4 started are 0-3
    assert set(started[:4]) == {0, 1, 2, 3} and set(started[4:8]) == {4, 5, 6, 7}


def _png(tmp_path, w=600, h=800) -> Path:
    p = tmp_path / "page.png"
    img = Image.effect_noise((w, h), 60).convert("RGB")
    img.save(p)
    return p


def test_the_model_copy_is_shrunk_but_the_file_on_disk_is_not(tmp_path):
    import io
    p = _png(tmp_path)
    before = p.read_bytes()
    mime, raw = L.encode_image(p, 70, scale=100 / 150)
    assert mime == "image/jpeg"
    assert Image.open(io.BytesIO(raw)).size == (400, 533)
    assert p.read_bytes() == before  # OCR and the local image checks still see the full render


def test_large_pack_profile_only_switches_on_above_the_threshold(monkeypatch):
    monkeypatch.delenv("TENDERLENS_LARGE_PACK_PROFILE", raising=False)
    monkeypatch.delenv("TENDERLENS_BATCH_OVER", raising=False)
    c = L.ModelClient(base_url="http://x", model="m", api_key="k")
    assert c.use_large_pack_profile(50) is False and c.image_scale == 1.0
    assert c.use_large_pack_profile(51) is True
    assert round(c.image_scale, 3) == round(100 / 150, 3) and c.jpeg_quality == 85
    monkeypatch.setenv("TENDERLENS_LARGE_PACK_PROFILE", "0")
    d = L.ModelClient(base_url="http://x", model="m", api_key="k")
    assert d.use_large_pack_profile(400) is False and d.image_scale == 1.0


def test_a_short_tender_goes_to_the_model_whole():
    text = "Closing date: 1 Nov\nSubmit to a@b.c\n"
    assert prompt_excerpt(text, 10_000) == (text, False)


def test_a_long_tender_is_cut_to_the_limit_but_keeps_the_requirement_lines():
    filler = "\n".join(f"clause {i}: general conditions of contract apply here and nowhere else" for i in range(4000))
    text = "Header line\n" + filler + "\nThe closing date is 23 November 2026 at 10:00\nCIDB grading 6CE or higher\n"
    shown, cut = prompt_excerpt(text, 20_000)
    assert cut and len(shown) < 26_000
    assert "closing date is 23 November 2026" in shown and "CIDB grading 6CE or higher" in shown
    assert shown.startswith("Header line")


def test_a_model_answer_with_null_or_odd_fields_does_not_stop_the_requirements_step():
    from tenderlens.requirements import Requirements, build_requirements, llm_extract

    class Odd:
        configured = True

        def complete_json(self, system, user):
            return {"closing": {"source_quote": "Closing 1 Nov", "date": None, "time": 10},
                    "submit_to_email": {"value": None, "source_quote": "send to a@b.c"},
                    "min_quote_validity_days": {"value": None, "source_quote": "valid 30 days"},
                    "required_documents": [None, "text", {"id": None, "label": 5, "source_quote": "valid 30 days"}],
                    "conditions": "none", "specification": ["not", "a", "dict"]}

    r = llm_extract(Odd(), "Closing 1 Nov. send to a@b.c, valid 30 days", "t.pdf", "", "")
    assert isinstance(r, Requirements) and r.submit_to_email == "" and r.min_quote_validity_days == 0
    assert r.closing_date == "" and r.closing_time == "" and len(r.documents) == 1


def test_a_failure_inside_the_model_step_falls_back_to_the_rules_instead_of_raising(monkeypatch, tmp_path):
    from tenderlens import requirements as R
    monkeypatch.setattr(R, "pdf_text", lambda p, w=None: "Closing date: 15 October 2021 at 14:00")
    monkeypatch.setattr(R, "llm_extract", lambda *a, **k: (_ for _ in ()).throw(TypeError("odd answer")))

    class C:
        configured = True

    req = R.build_requirements(Path("x.pdf"), "", "", C())
    assert any("used rules only" in n for n in req.notes)


def test_the_model_copy_gets_a_white_border_all_round(tmp_path):
    import io
    p = tmp_path / "full.png"
    Image.new("RGB", (300, 400), (0, 0, 0)).save(p)            # ink right up to every edge
    mime, raw = L.encode_image(p, 85, scale=100 / 150, pad=15)
    im = Image.open(io.BytesIO(raw)).convert("L")
    assert mime == "image/jpeg" and im.size == (200 + 30, 267 + 30)
    assert min(im.getpixel((x, 5)) for x in range(im.width)) > 240      # top margin is white
    assert min(im.getpixel((5, y)) for y in range(im.height)) > 240     # left margin is white
    assert im.getpixel((im.width // 2, im.height // 2)) < 40            # the page itself is still there
    assert (tmp_path / "full.png").read_bytes()[:4] == b"\x89PNG"      # the file on disk is untouched


def test_the_large_pack_profile_turns_on_the_border_and_cached_tokens_are_counted(monkeypatch):
    monkeypatch.delenv("TENDERLENS_IMAGE_PAD", raising=False)
    c = L.ModelClient(base_url="http://x", model="m", api_key="k")
    assert c.image_pad == 0
    c.use_large_pack_profile(200)
    assert c.image_pad == 15
    monkeypatch.setenv("TENDERLENS_IMAGE_PAD", "0")
    d = L.ModelClient(base_url="http://x", model="m", api_key="k")
    d.use_large_pack_profile(200)
    assert d.image_pad == 0


def _slice_pages(tmp_path, n):
    from tenderlens.ingest import Page
    out = []
    for i in range(1, n + 1):
        img = tmp_path / f"s{i}.png"
        Image.new("RGB", (100, 100), "white").save(img)
        out.append(Page(i, str(img), doc_type="sbd4"))
    return out


def test_a_slice_whose_pages_cannot_be_read_is_recorded_and_the_other_slices_carry_on(tmp_path, monkeypatch):
    from test_concurrency import _Scripted
    from tenderlens.facts import Facts, combined_first_pass
    monkeypatch.setenv("TENDERLENS_BATCH_OVER", "6")
    monkeypatch.setenv("TENDERLENS_BATCH_PAGES", "4")
    pages = _slice_pages(tmp_path, 10)                          # slices: pages 1-4, 5-8, 9-10
    answer = {"sbd4.name_field": "Excel Supply", "crossed_out": "no", "overwritten": "no", "annotation": "no",
              "rotated": "no", "cut_off": "no", "illegible": "no"}
    llm = _Scripted({p.image_path: answer for p in pages}, fail_for={pages[5].image_path, pages[6].image_path})  # pages 6, 7
    facts = Facts()
    got = combined_first_pass(llm, pages, facts)
    assert [n for n, d in got.items() if d is None] == [6, 7]
    assert [n for n, d in got.items() if isinstance(d, dict)] == [1, 2, 3, 4, 5, 8, 9, 10]    # reassembled in order
    errs = facts.vision_log["slice_errors"]
    assert len(errs) == 1 and errs[0]["slice"] == [5, 8] and errs[0]["pages"] == [6, 7]
    assert errs[0]["message"].startswith("Error: Slice [5-8]") and "after maximum retries" in errs[0]["message"]


def test_the_report_lists_a_slice_that_could_not_be_read_as_an_unresolved_exception_area():
    from pathlib import Path
    from tenderlens.audit import Auditor
    from tenderlens.facts import Facts
    from tenderlens.ingest import Page
    from tenderlens.requirements import Requirements
    facts = Facts()
    facts.vision_log["slice_errors"].append({"slice": [26, 50], "pages": [30, 31],
                                            "message": "Error: Slice [26-50] timed out after maximum retries"})
    pages = [Page(n, "x", doc_type="unknown") for n in range(1, 60)]
    a = Auditor(Requirements(), pages, facts, Path("samples/blank"))
    a.unresolved_areas()
    f = a.res.findings[0]
    assert f.title == "Unresolved exception area: pages 26-50" and "timed out after maximum retries" in f.detail
    assert [e.page for e in f.evidence] == [30, 31] and f.category == "unresolved"
    assert any("unresolved exception area" in h for h in a.res.human_only)


def test_facts_merge_carries_slice_errors_and_pricing_constants_are_unchanged():
    from tenderlens.facts import Facts
    a, b = Facts(), Facts()
    b.vision_log["slice_errors"].append({"slice": [1, 25], "pages": [3], "message": "m"})
    a.merge(b)
    assert a.vision_log["slice_errors"] == b.vision_log["slice_errors"]
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import tools.ce_eval as E
    assert (E.INPUT_TOKEN_PRICE, E.CACHED_INPUT_TOKEN_PRICE, E.OUTPUT_TOKEN_PRICE) == (1.25, 0.15, 4.25)
