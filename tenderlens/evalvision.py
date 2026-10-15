"""How well does THIS vision model read THESE pages?  (python -m tenderlens eval-vision ...)

The winning pack is the ideal test: a person has already transcribed its handwriting, and the pack has no
real problems, so (a) every entry the model reads differently from the transcript is a misreading, and (b) every
'crossed out', 'note in the margin', 'blank' or 'Yes' it reports is a false alarm. Run this once after connecting
a model, and read the numbers before trusting its findings.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from . import forms as F
from .facts import ENTRY, Facts, inspect_pages, vision_extract
from .ingest import classify_pages, load_pdf, load_templates


def _norm(v) -> str:
    if v is None:
        return "blank"
    return re.sub(r"[^a-z0-9]", "", str(v).lower())


def _mask(v) -> str:
    s = str(v)
    return re.sub(r"\d{8,}", lambda m: m.group(0)[:3] + "•" * (len(m.group(0)) - 5) + m.group(0)[-2:], s)


def eval_vision(vision, pack: Path, blank: Path, transcript: Path, work: Path, out: Path, passes: int = 2) -> str:
    pages = load_pdf(pack, work)
    classify_pages(pages, load_templates(blank))
    by_sha = {p.sha: p for p in pages}
    truth = {}
    for it in json.loads(Path(transcript).read_text())["fields"]:
        if it["field"] in ENTRY and it.get("page_sha") in by_sha:
            truth[it["field"]] = it["value"]

    facts = Facts()
    vision_extract(vision, pages, facts, local_content=True, verify=False)   # the model's FIRST look, no second chance
    first_calls = facts.vision_log["calls"]
    per_form = defaultdict(lambda: [0, 0])
    wrong, unread_n, missing = [], 0, []
    deviations = []
    for key, want in truth.items():
        e = ENTRY[key]
        form = key.split(".")[0]
        want_c = F.clean(e.kind, want)
        got = facts.best(key)
        per_form[form][1] += 1
        if got is None:
            missing.append(key)
            continue
        if _norm(got.value) == _norm(want_c):
            per_form[form][0] += 1
        else:
            wrong.append((key, want_c, got.value))
        if F.is_deviation(e, got.value) and not F.is_deviation(e, want_c):
            deviations.append((key, got.value))
    unread_n = len(facts.unread)

    insp = Facts()
    inspect_pages(vision, pages, insp, passes=passes)
    leads = [f for f in insp.items if f.field.startswith("inspect.")]
    confirmed = [f for f in leads if f.verified]

    total = sum(v[1] for v in per_form.values())
    right = sum(v[0] for v in per_form.values())
    L = ["# TenderLens: vision model check", "",
         f"Model: `{vision.model}` at `{vision.base_url}`. Pack: `{Path(pack).name}`. "
         f"Ground truth: {len(truth)} handwritten entries transcribed by a person.", "",
         f"- **Entries read the same as the transcript: {right} of {total} ({100 * right // max(1, total)}%)**",
         f"- Entries the model did not return at all: {len(missing)}; could not read: {unread_n}",
         f"- **False alarms on handwriting** (a Yes, blank, double tick or bad date that the transcript does not have): "
         f"{len(deviations)} before the second look",
         f"- Page inspection on a clean pack: {len(leads)} leads, **{len(confirmed)} confirmed by a second look "
         "(each one is a false alarm that would become an issue)**",
         f"- Calls: {vision.calls}, failed: {facts.vision_log['failed'] + insp.vision_log['failed']}", "",
         "## By form", "", "| Form | Correct | Of |", "|---|---|---|"]
    for form, (r, n) in sorted(per_form.items()):
        L.append(f"| {form} | {r} | {n} |")
    if wrong:
        L += ["", "## Misreadings", "", "| Entry | Transcript | Model |", "|---|---|---|"]
        L += [f"| {k} | {_mask(w)} | {_mask(g)} |" for k, w, g in wrong]
    if deviations:
        L += ["", "## False alarms before the second look", ""] + [f"- {k}: model said {_mask(v)}" for k, v in deviations]
    if leads:
        L += ["", "## Page-inspection leads on the clean pack", ""]
        c = Counter((f.field, f.page, f.verified) for f in leads)
        L += [f"- {k[0]} on page {k[1]}" + (" (confirmed)" if k[2] else " (not confirmed)") for k in c]
    L += ["", "## How to read this", "",
          "A model that gets under about 90% of the entries right should not be trusted for ticks and dates: use a "
          "transcript for those, or a larger vision model. Any confirmed false alarm on this clean pack means the "
          "model will invent issues on real packs: leave 'Vision: look for crossed-out text' off."]
    text = "\n".join(L) + "\n"
    out.mkdir(parents=True, exist_ok=True)
    (out / "eval_vision.md").write_text(text)
    return text
