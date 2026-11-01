"""Score the CE runs against the (verified) answer keys and write the baseline report.

    python tools/ce_score.py [--out evaluation/ce_baseline]

Reads runs/<grade>/<clean|tampered>/{report.json,run.json} written by ce_eval.py, and only now opens the keys. Matching is
deliberately conservative and reported at three levels, because a free-text key cannot be matched by a rule alone:
  detected      a finding cites a page the key names AND shares wording with the key's "expected finding"
  page only     a finding cites the page but shares no wording (weak: counted separately, never as detected)
  wrong page    wording matches (2+ words) but no finding cites the key's page
  missed        neither
False positives on the CLEAN pack are the issue-tier findings (observations are listed but not counted); each one is
listed for a person to judge against the key's "must not flag" list. Nothing here claims the engine was trained.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.ce_verify_keys import page_set, parse_key  # noqa: E402

LEGACY_MATCHING = False   # True (tools/ce_score.py --legacy) reproduces the earlier, more generous matching


STOP = {"the", "and", "for", "with", "that", "this", "from", "page", "pages", "form", "pack", "not", "are", "was",
        "has", "have", "differs", "than", "which", "but", "its", "any", "all", "one", "may", "must", "should", "into"}


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", text.lower()) if w not in STOP}


@dataclass
class Row:
    case: str
    kind: str
    pages: list[int]
    expected: str
    result: str = "missed"
    matched: str = ""


@dataclass
class GradeScore:
    folder: str
    rows: list[Row] = field(default_factory=list)
    false_positives: list[dict] = field(default_factory=list)
    run_clean: dict = field(default_factory=dict)
    run_tampered: dict = field(default_factory=dict)

    def counts(self) -> Counter:
        return Counter(r.result for r in self.rows)


# Findings about the pack as a whole or about a general condition (initials, page counts, stray marks, ...) name many pages, so
# they land on a key row's page by coincidence. A spot-check of 40 "page only" and 25 "detected" rows found most of the
# credit given for them was coincidence (about 5% and 54% real). They only count for a key row whose own expected
# finding is about that same thing (the cue words below).
GENERIC = (
    (r"^Initials missing", r"initial"),
    (r"pages found|not together|out of order|more than once|pages? (are|is) missing", r"page|order|swapped|duplicate|missing|removed|sequence"),
    (r"^Page cut off", r"cut|crop|truncat|edge"),
    (r"^(Small|Unexplained) mark|Handwritten note", r"mark|note|stray|annotation|scribble"),
    (r"Forms are signed on different dates|signed on different", r"date"),
    (r"^Page rotated|^Form struck", r"rotat|upside|struck|voided"),
    (r"^The CSD (summary )?report", r"csd|supplier database|report"),
)


def is_generic_for(finding_title: str, expected: str) -> bool:
    for title_rx, cue_rx in GENERIC:
        if re.search(title_rx, finding_title) and not re.search(cue_rx, expected, re.I):
            return True
    return False


def finding_pages(f: dict) -> set[int]:
    return {e.get("page") for e in f.get("evidence", []) if isinstance(e.get("page"), int)}


def match_defect(d: dict, findings: list[dict]) -> Row:
    pages = page_set(re.sub(r"\(.*?\)", "", d.get("Page", "")))
    knock = page_set(re.sub(r".*knock-on:\s*([\d\-, ]+)\).*", r"\1", d.get("Page", ""))) if "knock-on" in d.get("Page", "") else []
    want = words(d.get("Expected TenderLens finding", "") + " " + d.get("Type", ""))
    row = Row(d["Test case"], d.get("Type", ""), pages, d.get("Expected TenderLens finding", ""))
    best_rank = 0
    for f in findings:
        if not LEGACY_MATCHING and is_generic_for(f.get("title", ""), d.get("Expected TenderLens finding", "") + " " + d.get("Type", "")):
            continue
        shared = len(want & words(f.get("title", "") + " " + f.get("detail", "")))
        on_page = bool(finding_pages(f) & (set(pages) | set(knock)))
        rank = 3 if on_page and shared >= 1 else 2 if shared >= 2 else 1 if on_page else 0
        if rank > best_rank:
            best_rank, row.matched = rank, f.get("title", "")
    row.result = {3: "detected", 2: "wrong page", 1: "page only", 0: "missed"}[best_rank]
    if not pages and best_rank == 2:
        row.result = "detected"  # the defect is something absent (no page to cite): wording is all there is to match
    if best_rank == 0:
        row.matched = ""
    return row


def uncredited_report(base: Path, kind: str, findings: list[dict], credited: set[str]) -> None:
    """Write uncredited.md / uncredited.json next to the run's own files. Nothing is hidden or re-tiered: a CLEAN pack's
    findings are all listed (issue-tier ones are false-positive candidates), and a TAMPERED pack lists every finding the
    key did not credit. The engine's SBD rules firing on non-SBD items (initials, 'N of M pages found', 'page cut off',
    'Missing: ...') show up here under their own headings."""
    rows = [f for f in findings if kind == "clean" or f.get("title") not in credited]
    classes = {"initials": r"^Initials missing", "pages_found": r"pages found", "page_cut_off": r"^Page cut off",
               "missing_document": r"^Missing:", "unsigned": r"may be unsigned|no official stamp|no commissioner|"
               r"Could not locate the signature", "stray_mark": r"^(Unexplained|Small) mark|Handwritten note",
               "cross_document_mismatch": r"differs between documents", "rotated": r"^Page rotated"}
    grouped: dict[str, list[dict]] = defaultdict(list)
    for f in rows:
        cls = next((c for c, rx in classes.items() if re.search(rx, f.get("title", ""))), "other")
        grouped[cls].append({"title": f.get("title"), "tier": f.get("tier"), "severity": f.get("severity"),
                             "pages": sorted(finding_pages(f))})
    (base / kind / "uncredited.json").write_text(json.dumps(grouped, indent=1))
    L = [f"# Uncredited findings ({kind} pack)", "",
         "A clean pack should draw none at issue tier; each of these is a false-positive candidate to judge against the "
         "key's must-not-flag list." if kind == "clean" else
         "Findings the key does not credit (no matching planted defect): false positives or true extra findings; a person "
         "decides.", "", f"{len(rows)} finding(s)."]
    for cls, items in sorted(grouped.items()):
        L += ["", f"## {cls} ({len(items)})"]
        L += [f"- [{i['tier']}] {i['title']} (pages {i['pages']})" for i in items]
    (base / kind / "uncredited.md").write_text("\n".join(L) + "\n")


def score_grade(out: Path, root: Path, sc: dict, tag: str = "") -> GradeScore:
    folder = sc["folder"]
    gs = GradeScore(folder)
    base = out / "runs" / (folder.replace(" ", "_") + (f"__{tag}" if tag else ""))
    key_path = next(root / f["path"] for f in sc["files"] if f["role"] == "answer_key")
    key = parse_key(key_path)
    for kind, attr in (("clean", "run_clean"), ("tampered", "run_tampered")):
        if (base / kind / "run.json").exists():
            setattr(gs, attr, json.loads((base / kind / "run.json").read_text()))
    if (base / "tampered" / "report.json").exists():
        findings = json.loads((base / "tampered" / "report.json").read_text()).get("findings", [])
        gs.rows = [match_defect(d, findings) for d in key.defects]
        uncredited_report(base, "tampered", findings, {r.matched for r in gs.rows if r.result == "detected"})
    if (base / "clean" / "report.json").exists():
        clean_findings = json.loads((base / "clean" / "report.json").read_text()).get("findings", [])
        uncredited_report(base, "clean", clean_findings, set())
        for f in clean_findings:
            if f.get("tier") == "issue":
                gs.false_positives.append({"title": f.get("title", ""), "pages": sorted(finding_pages(f)),
                                           "severity": f.get("severity", "")})
    return gs


def write_report(out: Path, manifest: dict, verification: list[dict], scores: list[GradeScore]) -> None:
    ver = {v["folder"]: v for v in verification}
    L = ["# CE baseline report", "",
         "An honest baseline of the existing engine, not a training result: loading or evaluating this data did not "
         "train or improve the model. The tenders are authentic public documents; the clean and tampered packs are "
         "synthetic test documents (fictional bidders).", "",
         f"Grade folders discovered: {len(manifest['scenarios'])} of 9; Complete: "
         f"{sum(s['complete'] for s in manifest['scenarios'])}. Provider for every request: Fireworks AI (the only "
         "provider in this codebase; no fallback lane exists, so none fired).", "",
         "## Scenarios, key status and files", "",
         "| Folder | Tender | Key status | Tender (pages) | Clean (pages) | Tampered (pages) |", "|---|---|---|---|---|---|"]
    for s in manifest["scenarios"]:
        def one(role):
            fs = [f for f in s["files"] if f["role"] == role]
            return "; ".join(f"{Path(f['path']).name} ({f['pages']})" for f in fs)
        L.append(f"| {s['folder']} | {s['tender_number']} | {ver.get(s['folder'], {}).get('status', 'not checked')} | "
                 f"{one('tender')} | {one('clean_pack')} | {one('tampered_pack')} |")
    L += ["", "See `manifest.md` (anomalies), `key_discrepancies.md` (answer-key checks).", "",
          "## Results by grade (tampered pack against the verified key)", "",
          "| Folder | Defects in key | Detected | Page only | Wrong page | Missed | Clean-pack issue findings | Tampered s | Clean s |",
          "|---|---|---|---|---|---|---|---|---|"]
    by_type: dict[str, Counter] = defaultdict(Counter)
    for g in scores:
        c = g.counts()
        for r in g.rows:
            by_type[r.kind or "unspecified"][r.result] += 1
        L.append(f"| {g.folder} | {len(g.rows) or 'not run'} | {c['detected']} | {c['page only']} | {c['wrong page']} | "
                 f"{c['missed']} | {len(g.false_positives) if g.run_clean else 'not run'} | "
                 f"{g.run_tampered.get('seconds', '-')} | {g.run_clean.get('seconds', '-')} |")
    L += ["", "## Results by defect class", "", "| Class | Detected | Page only | Wrong page | Missed |", "|---|---|---|---|---|"]
    for k, c in sorted(by_type.items()):
        L.append(f"| {k} | {c['detected']} | {c['page only']} | {c['wrong page']} | {c['missed']} |")
    L += ["", "## Performance by grade and provider", "",
          "| Folder | Pack | Pages | Seconds | Vision calls | Text calls | Tokens (in+out) | Retries | Peak MB | Provider |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for g in scores:
        for kind, r in (("clean", g.run_clean), ("tampered", g.run_tampered)):
            if r:
                L.append(f"| {g.folder} | {kind} | {r['pages']} | {r['seconds']} | {r['vision_calls']} | {r['text_calls']} | "
                         f"{r['prompt_tokens']:,}+{r['completion_tokens']:,} | {r['retries']} | {r['peak_rss_mb']:.0f} | "
                         f"{r['provider']} (fallback fired: {r['fallback_fired']}) |")
    L += ["", "## Processing failures and items needing a person", ""]
    for g in scores:
        for kind, r in (("clean", g.run_clean), ("tampered", g.run_tampered)):
            for se in (r or {}).get("slice_errors", []):
                L.append(f"- {g.folder} {kind}: {se['message']} (pages {se['pages']}): unresolved exception area for manual review")
            if r and (r["status"] != "ok" or r["failed_pages"] or r["errors"]):
                L.append(f"- {g.folder} {kind}: status {r['status']} {r['error']}; failed pages {r['failed_pages']}; "
                         f"errors {r['errors']}")
        for fp in g.false_positives:
            L.append(f"- {g.folder} clean pack: issue-tier finding to judge against the key's must-not-flag list: "
                     f"{fp['title']} (pages {fp['pages']})")
    warnings_path = out / "budget_warnings.json"
    if warnings_path.exists():
        L += ["", "## Budget warnings", ""]
        for w in json.loads(warnings_path.read_text()):
            L.append(f"- {w['time']}: {w['message']} ({json.dumps({k: v for k, v in w.items() if k not in ('time', 'message')})})")
    L += ["", "Pricing arithmetic (key section 4) and requirement interpretations are not matched by rule here; they "
          "need a person to compare the report with the key.", ""]
    (out / "baseline_report.md").write_text("\n".join(L))


def clean_counts(base: Path) -> dict:
    f = base / "clean" / "report.json"
    if not f.exists():
        return {}
    fs = json.loads(f.read_text()).get("findings", [])
    cut = sum(1 for x in fs if x.get("title", "").startswith("Page cut off"))
    marks = sum(1 for x in fs if re.search(r"\b(mark|note)\b", x.get("title", "")) and not x.get("title", "").startswith("Page cut off"))
    return {"total": len(fs), "issue": sum(1 for x in fs if x.get("tier") == "issue"), "cut_off": cut, "marks": marks}


def write_comparison(out: Path, manifest: dict, wanted: set[int], old_tag: str, new_tag: str) -> None:
    """Old tag against new tag, grade by grade, on the same key: defect rows and false positives on the clean pack."""
    root = Path(manifest["root"])
    L = [f"# Comparison: `{old_tag}` (before) vs `{new_tag}` (after the SBD gate, the inheritance limit and adaptive initials)", "",
         "Same key, same packs, same image profile (100 DPI, JPEG quality 85). One run each, so differences of one or two "
         "rows are within run-to-run noise. 'Clean-pack findings' counts every finding on the CLEAN pack: a clean pack "
         "should draw none at issue tier.", "",
         "| Grade | Setting | Defects | Detected | Page only | Wrong page | Missed | Clean findings (all) | Clean issue-tier | "
         "Clean 'page cut off' | Tampered findings | Vision calls (c+t) | Tokens in+out (c+t) |", "|" + "---|" * 13]
    tot = {old_tag: Counter(), new_tag: Counter()}
    for sc in manifest["scenarios"]:
        if sc["grade"] not in wanted:
            continue
        for tag in (old_tag, new_tag):
            gs = score_grade(out, root, sc, tag)
            base = out / "runs" / (sc["folder"].replace(" ", "_") + f"__{tag}")
            cc = clean_counts(base)
            c = gs.counts()
            tf = (json.loads((base / "tampered" / "report.json").read_text()).get("findings", [])
                  if (base / "tampered" / "report.json").exists() else [])
            rc, rt = gs.run_clean, gs.run_tampered
            calls = (rc.get("vision_calls", 0) + rt.get("vision_calls", 0))
            toks = sum(r.get("prompt_tokens", 0) + r.get("completion_tokens", 0) for r in (rc, rt))
            L.append(f"| {sc['grade']} ({sc['folder']}) | {tag} | {len(gs.rows)} | {c['detected']} | {c['page only']} | "
                     f"{c['wrong page']} | {c['missed']} | {cc.get('total', '-')} | {cc.get('issue', '-')} | "
                     f"{cc.get('cut_off', '-')} | {len(tf)} | {calls} | {toks:,} |")
            for k, v in (("cut", cc.get("cut_off", 0)), ("marks", cc.get("marks", 0)), ("detected", c["detected"]), ("defects", len(gs.rows)), ("clean", cc.get("total", 0)),
                         ("issue", cc.get("issue", 0)), ("calls", calls), ("tokens", toks), ("missed", c["missed"])):
                tot[tag][k] += v
    L += ["", "## Totals", "", "| Setting | Defects | Detected | Missed | Clean findings | Clean issue-tier | Vision calls | Tokens |",
          "|---|---|---|---|---|---|---|---|"]
    for tag in (old_tag, new_tag):
        t = tot[tag]
        L.append(f"| {tag} | {t['defects']} | {t['detected']} | {t['missed']} | {t['clean']} | {t['issue']} | {t['calls']} | {t['tokens']:,} |")
    L += ["", "## The vision-reading false positives (clean packs): page cut off + stray marks / notes", "",
          "| Setting | Page cut off | Stray marks / notes | Together |", "|---|---|---|---|"]
    for tag in (old_tag, new_tag):
        L.append(f"| {tag} | {tot[tag]['cut']} | {tot[tag]['marks']} | {tot[tag]['cut'] + tot[tag]['marks']} |")
    o, n = tot[old_tag], tot[new_tag]
    if o["clean"]:
        L += ["", f"Clean-pack findings: {o['clean']} -> {n['clean']} ({(n['clean'] - o['clean']) / o['clean']:+.0%}); "
              f"issue-tier: {o['issue']} -> {n['issue']}; detected defects: {o['detected']} -> {n['detected']}; "
              f"tokens: {o['tokens']:,} -> {n['tokens']:,}."]
    (out / "gating_comparison.md").write_text("\n".join(L) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=Path("evaluation/ce_baseline"))
    ap.add_argument("--tag", default="dpi100", help="which tagged runs to score (ce_eval.py --tag)")
    ap.add_argument("--grades", help="comma-separated grade numbers to include (default: those with runs)")
    ap.add_argument("--legacy", action="store_true", help="the earlier matching, which credited pack-wide findings by coincidence")
    ap.add_argument("--compare", help="an earlier tag to compare --tag against; writes gating_comparison.md")
    a = ap.parse_args()
    global LEGACY_MATCHING
    LEGACY_MATCHING = a.legacy
    manifest = json.loads((a.out / "manifest.json").read_text())
    root = Path(manifest["root"])
    vpath = a.out / "key_verification.json"
    verification = json.loads(vpath.read_text()) if vpath.exists() else []
    wanted = {int(x) for x in a.grades.split(",")} if a.grades else None
    scores = [g for g in (score_grade(a.out, root, sc, a.tag) for sc in manifest["scenarios"]
                          if wanted is None or sc["grade"] in wanted) if g.run_clean or g.run_tampered]
    (a.out / "scores.json").write_text(json.dumps(
        [{"folder": g.folder, "rows": [r.__dict__ for r in g.rows], "false_positives": g.false_positives} for g in scores],
        indent=1))
    write_report(a.out, manifest, verification, scores)
    if a.compare:
        write_comparison(a.out, manifest, wanted or {g.folder and int(re.match(r"\s*(\d)", g.folder).group(1)) for g in scores},
                         a.compare, a.tag)
    print(f"wrote {a.out}/baseline_report.md and scores.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
