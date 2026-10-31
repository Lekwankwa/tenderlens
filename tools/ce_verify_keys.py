"""Independently verify each CE answer key against its tender, requirements CSV and both packs. Local only (pdfinfo,
pdftotext, Tesseract) - no model call, no cost. Keys are never edited; contradictions are reported, not fixed.

    python tools/ce_verify_keys.py [--root "~/Downloads/CE "] [--out evaluation/ce_baseline] [--no-ocr]

Checks per grade:
  header     pack page counts and SHA-256 prefixes in the key match the real packs; the source tender's page count
             matches the real tender file(s)
  structure  section-2 requirement ids == requirements.csv ids, with the same clean/tampered expectations and test
             cases; every test case in section 3 belongs to a requirement and vice versa; every referenced pack page
             exists in the pack it refers to; tender page references fit the tender
  content    (OCR of each planted-defect page in the TAMPERED pack) the page mentions the document the key names, and
             a date/amount quoted in the key's "exact modification" really is on the page. Not found is reported as
             "not confirmed" (OCR is imperfect), never as a contradiction.
  tender     dates quoted in a requirement appear on the tender pages it cites (where the tender has a text layer)
A key is "verified" (no flags), "verified with flags" (only not-confirmed items) or "not verified" (a contradiction).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.ce_discover import DEFAULT_ROOT, find_root, pdf_pages, scan_folder, sha256_file  # noqa: E402

DATE = re.compile(r"\b\d{1,2}[/.]\d{1,2}[/.]\d{4}\b|\b\d{4}[/-]\d{2}[/-]\d{2}\b")


@dataclass
class Key:
    title: str = ""
    packs: dict[str, dict] = field(default_factory=dict)       # file name -> {pages, sha_prefix}
    source_pages: int | None = None
    requirements: dict[str, dict] = field(default_factory=dict)  # R-id -> row
    defects: list[dict] = field(default_factory=list)            # section 3 rows
    must_not_flag: list[str] = field(default_factory=list)


@dataclass
class Result:
    grade_folder: str
    contradictions: list[str] = field(default_factory=list)
    csv_defects: list[str] = field(default_factory=list)
    not_confirmed: list[str] = field(default_factory=list)
    checks_passed: int = 0

    @property
    def status(self) -> str:
        flags = self.not_confirmed or self.csv_defects
        return "not verified" if self.contradictions else ("verified with flags" if flags else "verified")


def table_rows(lines: list[str]) -> list[dict]:
    """Rows of the first Markdown table in `lines`, as dicts keyed by the header cells."""
    rows, header = [], None
    for ln in lines:
        if not ln.startswith("|"):
            if header:
                break
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if header is None:
            header = cells
        elif set("".join(cells)) <= set("-: "):
            continue
        else:
            rows.append(dict(zip(header, cells)))
    return rows


def parse_key(path: Path) -> Key:
    text = path.read_text(errors="replace")
    k = Key(title=text.splitlines()[0].lstrip("# ").strip())
    for m in re.finditer(r"^- (\S+\.pdf): (\d+) pages?, [\d.]+ MB, SHA-256 `([0-9a-f]+)", text, re.M):
        k.packs[m.group(1)] = {"pages": int(m.group(2)), "sha_prefix": m.group(3)}
    m = (re.search(r"combined as source pages 1-(\d+)", text)
         or re.search(r"Source (?:tender|documents): .*?\((?:[^()]*?,\s*)?(\d+)\s*pages?\)", text))
    if m:
        k.source_pages = int(m.group(1))
    sections = re.split(r"^## ", text, flags=re.M)
    for sec in sections:
        head = sec.split("\n", 1)[0]
        body = sec.split("\n")[1:]
        if head.startswith("2."):
            for r in table_rows(body):
                if r.get("ID", "").startswith("R-"):
                    k.requirements[r["ID"]] = r
        elif head.startswith("3."):
            k.defects = [r for r in table_rows(body) if r.get("Test case")]
        elif head.startswith("6."):
            k.must_not_flag = [ln.lstrip("-* ").strip() for ln in body if ln.lstrip().startswith(("-", "*", "|"))]
    return k


def page_set(spec: str) -> list[int]:
    """'1-14, 19, 22' -> [1..14, 19, 22]; text that is not a page list gives []."""
    out: list[int] = []
    for part in re.split(r"[,;&]|\band\b", spec):
        m = re.fullmatch(r"\s*p?(\d+)\s*(?:-\s*(\d+))?\s*", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2) or m.group(1))
            out.extend(range(a, b + 1))
    return out


def ocr_page(pdf: Path, number: int, cache: Path) -> str:
    """OCR of one page of `pdf` (150 DPI like the engine); cached so a re-run costs nothing."""
    from tenderlens.ingest import DPI, ocr_image
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdftoppm", "-r", str(DPI), "-png", "-f", str(number), "-l", str(number), str(pdf),
                        str(Path(tmp) / "p")], check=True, capture_output=True)
        png = next(Path(tmp).glob("p-*.png"))
        from tenderlens.ingest import sha1_file
        return ocr_image(png, cache / f"{sha1_file(png)}.json")[0]


def tender_text(files: list[Path], page: int) -> str | None:
    """Text of tender page `page` (numbered across the files in the given order), or None if it has no text layer."""
    for f in files:
        n = pdf_pages(f) or 0
        if page <= n:
            t = subprocess.run(["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(f), "-"],
                               capture_output=True, text=True).stdout
            return t if len(t.strip()) > 40 else None
        page -= n
    return None


def verify(root: Path, folder: Path, ocr: bool, cache: Path) -> Result:
    sc = scan_folder(root, folder)
    res = Result(folder.name)

    def bad(msg: str) -> None:
        res.contradictions.append(msg)

    def flag(msg: str) -> None:
        res.not_confirmed.append(msg)

    def ok() -> None:
        res.checks_passed += 1

    by_role = {r: [f for f in sc.files if f.role == r] for r in ("tender", "clean_pack", "tampered_pack",
                                                                  "requirements", "answer_key")}
    if not all(by_role[r] for r in ("clean_pack", "tampered_pack", "answer_key", "requirements")):
        bad(f"missing parts: {sc.missing_roles}")
        return res
    key = parse_key(root / by_role["answer_key"][0].path)
    packs = {"clean": root / by_role["clean_pack"][0].path, "tampered": root / by_role["tampered_pack"][0].path}
    npages = {k: pdf_pages(p) or 0 for k, p in packs.items()}

    # header: page counts and SHA-256 prefixes
    for kind, path in packs.items():
        entry = key.packs.get(path.name)
        if not entry:
            bad(f"key header does not describe {path.name}")
            continue
        (ok if entry["pages"] == npages[kind] else lambda: bad(
            f"{path.name}: key says {entry['pages']} pages, file has {npages[kind]}"))()
        real = sha256_file(path)
        (ok if real.startswith(entry["sha_prefix"]) else lambda: bad(
            f"{path.name}: SHA-256 {real[:16]}... does not start with the key's {entry['sha_prefix']}"))()
    tender_files = [root / f.path for f in by_role["tender"]]
    total_tender = sum(pdf_pages(f) or 0 for f in tender_files)
    if key.source_pages is not None:
        (ok if key.source_pages == total_tender else lambda: bad(
            f"key says the source tender has {key.source_pages} pages, the file(s) have {total_tender}"))()

    # structure: CSV against the key's section 2
    with (root / by_role["requirements"][0].path).open(newline="", errors="replace") as fh:
        reader = csv.DictReader(fh)
        csv_rows = {r["id"]: r for r in reader}
        csv_cols = set(reader.fieldnames or [])
    if set(csv_rows) != set(key.requirements):
        bad(f"requirement ids differ: only in CSV {sorted(set(csv_rows) - set(key.requirements))}, "
            f"only in key {sorted(set(key.requirements) - set(csv_rows))}")
    else:
        ok()
    wanted = {"clean_expected", "tampered_expected", "tampered_cases", "clean_pack_pages"}
    if wanted - csv_cols:
        res.csv_defects.append(f"requirements.csv has no column(s) {sorted(wanted - csv_cols)} (other grades' CSVs "
                               "do); those comparisons were made against the key's own tables only")
    short = [rid for rid, r in csv_rows.items() if None in r.values()]
    if short:
        res.csv_defects.append(
            f"{len(short)} of {len(csv_rows)} requirements.csv rows have fewer columns than the header "
            f"(no tampered result or test cases), e.g. {short[:4]}; compared against the key where possible")
    for rid in sorted(set(csv_rows) & set(key.requirements)):
        c, k = csv_rows[rid], key.requirements[rid]
        if rid in short:
            continue
        for col, kcol, what in (("clean_expected", "Clean", "clean result"),
                                ("tampered_expected", "Tampered", "tampered result")):
            if col in csv_cols:
                a, b = c.get(col), k.get(kcol)
                (ok if (a or "").strip() == (b or "").strip() else lambda: bad(
                    f"{rid} {what}: CSV '{a}' vs key '{b}'"))()
        if "tampered_cases" in csv_cols:
            ca = {x.strip() for x in re.split(r"[,;]", c.get("tampered_cases") or "") if x.strip() and x.strip() != "-"}
            ka = {x.strip() for x in re.split(r"[,;]", k.get("Test cases", "")) if x.strip() and x.strip() != "-"}
            (ok if ca == ka else lambda: bad(f"{rid} test cases: CSV {sorted(ca)} vs key {sorted(ka)}"))()
        for p in page_set(k.get("Clean pack pages", "")):
            if p > npages["clean"]:
                bad(f"{rid}: clean-pack page {p} does not exist (pack has {npages['clean']})")
        for p in page_set(k.get("Source", "")):
            if p > total_tender:
                bad(f"{rid}: tender page {p} does not exist (tender has {total_tender})")

    # structure: section 3 against section 2 and the tampered pack
    cases_in_defects = {d["Test case"] for d in key.defects}
    cases_in_reqs = {x.strip() for r in key.requirements.values() for x in re.split(r"[,;]", r.get("Test cases", ""))
                     if x.strip() and x.strip() != "-"}
    if cases_in_defects != cases_in_reqs:
        bad(f"test cases listed without a requirement: {sorted(cases_in_defects - cases_in_reqs)}; "
            f"requirements naming test cases that are not planted: {sorted(cases_in_reqs - cases_in_defects)}")
    else:
        ok()
    for d in key.defects:
        for p in page_set(d.get("Page", "")):
            if p > npages["tampered"]:
                bad(f"{d['Test case']}: tampered page {p} does not exist (pack has {npages['tampered']})")
        if d.get("Requirement", "").split(" ")[0] not in key.requirements:
            bad(f"{d['Test case']}: requirement '{d.get('Requirement')}' is not in section 2")
        else:
            ok()

    # content: the planted-defect pages really show what the key says (OCR, local)
    if ocr:
        jobs = []
        for d in key.defects:
            pages = page_set(d.get("Page", ""))
            if pages and pages[0] <= npages["tampered"]:
                jobs.append((d, pages[0]))
        with ThreadPoolExecutor(max_workers=4) as ex:
            texts = list(ex.map(lambda j: ocr_page(packs["tampered"], j[1], cache), jobs))
        for (d, page), text in zip(jobs, texts):
            norm = re.sub(r"[^a-z0-9/]+", " ", text.lower())
            doc = d.get("Document", "")
            # "Source page 4 (rfx.pdf p4)" names where the text came from in the tender, not a document on the pack page
            doc_words = [] if re.match(r"\s*(source\s+)?(tender\s+)?page\b", doc, re.I) else [
                w for w in re.findall(r"[a-z]{4,}", doc.lower())
                if w not in {"tender", "page", "attachment", "document", "form", "source"}]
            if doc_words and not any(w in norm for w in doc_words):
                flag(f"{d['Test case']} (tampered p{page}): OCR of the page does not mention "
                     f"'{d.get('Document')}'")
            else:
                ok()
            dates = {re.sub(r"[.\- ]", "/", x) for x in DATE.findall(d.get("Exact modification", ""))}
            if dates:
                seen = {re.sub(r"[.\- ]", "/", x) for x in DATE.findall(text)}
                if dates & seen:
                    ok()
                else:
                    flag(f"{d['Test case']} (tampered p{page}): date(s) {sorted(dates)} from the key not found in "
                         "the page OCR")

    # tender: dates quoted in a requirement appear on the tender pages it cites (text layer only)
    if tender_files:
        for rid, r in key.requirements.items():
            dates = {re.sub(r"[.\- ]", "/", x) for x in DATE.findall(r.get("Requirement", ""))}
            texts = [tender_text(tender_files, p) for p in page_set(r.get("Source", ""))]
            texts = [t for t in texts if t]
            if dates and texts:
                seen = {re.sub(r"[.\- ]", "/", x) for t in texts for x in DATE.findall(t)}
                ok() if dates & seen else flag(f"{rid}: date(s) {sorted(dates)} not on the tender pages it cites")
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=Path("evaluation/ce_baseline"))
    ap.add_argument("--no-ocr", action="store_true", help="skip the OCR content checks")
    ap.add_argument("--only", help="comma-separated folder names")
    a = ap.parse_args()
    root = find_root(a.root)
    folders = sorted((d for d in root.iterdir() if d.is_dir()),
                     key=lambda d: (int(re.match(r"\s*(\d)", d.name).group(1)) if re.match(r"\s*(\d)", d.name) else 99))
    if a.only:
        folders = [d for d in folders if d.name in a.only.split(",")]
    cache = a.out / ".ocr_cache"
    results = []
    for d in folders:
        r = verify(root, d, not a.no_ocr, cache)
        results.append(r)
        print(f"{d.name!r:16} {r.status:20} passed={r.checks_passed} flags={len(r.not_confirmed)} "
              f"contradictions={len(r.contradictions)}", flush=True)
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "key_verification.json").write_text(json.dumps(
        [{"folder": r.grade_folder, "status": r.status, "passed": r.checks_passed,
          "contradictions": r.contradictions, "csv_defects": r.csv_defects, "not_confirmed": r.not_confirmed} for r in results], indent=1))
    lines = ["# Answer-key verification", "",
             "Each key was checked against the real tender file(s), the requirements CSV and both packs "
             "(local checks only). A key is trusted only when it has no contradictions. Keys were not modified.", "",
             "| Folder | Status | Checks passed | Not confirmed | Contradictions |", "|---|---|---|---|---|"]
    lines += [f"| {r.grade_folder} | {r.status} | {r.checks_passed} | {len(r.not_confirmed)} | "
              f"{len(r.contradictions)} |" for r in results]
    lines += ["", "## Contradictions (the key disagrees with the files)", ""]
    for r in results:
        lines += [f"- **{r.grade_folder}**: {c}" for c in r.contradictions]
    lines += ["", "## requirements.csv defects (the key itself is not contradicted)", ""]
    for r in results:
        lines += [f"- **{r.grade_folder}**: {c}" for c in r.csv_defects]
    lines += ["", "## Not confirmed (OCR could not find it; may be OCR error, check by eye)", ""]
    for r in results:
        lines += [f"- **{r.grade_folder}**: {c}" for c in r.not_confirmed]
    (a.out / "key_discrepancies.md").write_text("\n".join(lines) + "\n")
    print(f"wrote {a.out}/key_verification.json and key_discrepancies.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
