"""Discover the CE (civil engineering) training set without assuming any path or name.

    python tools/ce_discover.py [--root "~/Downloads/CE "] [--out evaluation/ce_baseline]

Read-only on the dataset: nothing is renamed, moved, deleted or regenerated. It walks the root, works out each file's
role from its name AND its content (page count, CSV header, key title), groups the files into one scenario per grade
folder, and writes manifest.json / manifest.md (and the anomaly list) inside the repo.

Labels: the buyer's tender is the "authentic_source" (a public procurement document); clean and tampered packs are
"synthetic_test_document" (the answer keys state the bidder is fictional) - never genuine submissions.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROLES = ("tender", "clean_pack", "tampered_pack", "requirements", "answer_key", "supporting_annex", "ignored", "unknown")
LABEL = {"tender": "authentic_source", "supporting_annex": "authentic_source",
         "clean_pack": "synthetic_test_document", "tampered_pack": "synthetic_test_document"}
REQUIRED_ROLES = ("tender", "clean_pack", "tampered_pack", "requirements", "answer_key")
DEFAULT_ROOT = Path.home() / "Downloads" / "CE "


@dataclass
class FileInfo:
    path: str
    role: str
    size: int
    sha256: str = ""
    pages: int | None = None
    text_layer: bool | None = None
    label: str = ""
    problems: list[str] = field(default_factory=list)


@dataclass
class Scenario:
    folder: str
    grade: int | None
    tender_number: int | None
    files: list[FileInfo] = field(default_factory=list)
    complete: bool = False
    missing_roles: list[str] = field(default_factory=list)
    anomalies: list[dict] = field(default_factory=list)


def find_root(explicit: Path | None) -> Path:
    """The dataset root. The folder on disk is called 'CE ' (trailing space); a plain 'CE' is accepted too, but
    nothing is guessed beyond those two spellings."""
    for cand in ([explicit] if explicit else [DEFAULT_ROOT, DEFAULT_ROOT.with_name("CE")]):
        if cand and cand.expanduser().is_dir():
            return cand.expanduser()
    raise SystemExit(f"CE dataset folder not found (looked for {explicit or DEFAULT_ROOT}).")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pdf_pages(path: Path) -> int | None:
    out = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True).stdout
    m = re.search(r"^Pages:\s+(\d+)", out, re.M)
    return int(m.group(1)) if m else None


def has_text_layer(path: Path, pages: int = 3) -> bool:
    out = subprocess.run(["pdftotext", "-l", str(pages), str(path), "-"], capture_output=True, text=True).stdout
    return len(out.strip()) > 40


def classify(path: Path, in_annex_tree: bool) -> str:
    """Role from the name first, confirmed by content where the name is not enough."""
    name = path.name.lower()
    if name == ".ds_store":
        return "ignored"
    if in_annex_tree:
        return "supporting_annex"
    if name.endswith(".md"):
        head = path.read_text(errors="replace")[:600].lower()
        return "answer_key" if "answer key" in head or "answer_key" in name else "unknown"
    if name.endswith(".csv"):
        with path.open(newline="", errors="replace") as fh:
            header = next(csv.reader(fh), [])
        return "requirements" if "requirement" in [h.lower() for h in header] else "unknown"
    if name.endswith(".pdf") or name.endswith(".pdf.pdf"):
        if "tampered" in name:
            return "tampered_pack"
        if "clean_pack" in name or ("clean" in name and "pack" in name):
            return "clean_pack"
        return "tender"
    return "unknown"


def grade_of(folder: str) -> int | None:
    m = re.match(r"\s*(\d)\s*CE", folder, re.I)
    return int(m.group(1)) if m else None


def tender_number_of(files: list[FileInfo]) -> int | None:
    for f in files:
        m = re.search(r"Tender(\d+)_", Path(f.path).name)
        if m:
            return int(m.group(1))
    return None


def scan_folder(root: Path, folder: Path) -> Scenario:
    sc = Scenario(folder=folder.name, grade=grade_of(folder.name), tender_number=None)
    for p in sorted(folder.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        in_annex = "Supporting Annexures" in rel.parts
        fi = FileInfo(path=str(rel), role=classify(p, in_annex), size=p.stat().st_size)
        fi.label = LABEL.get(fi.role, "")
        if fi.role != "ignored":
            fi.sha256 = sha256_file(p)
        if p.suffix.lower() == ".pdf" and fi.role != "ignored":
            fi.pages = pdf_pages(p)
            if fi.pages is None:
                fi.problems.append("pdfinfo could not read this PDF (corrupt or unreadable)")
            elif fi.role != "supporting_annex":
                fi.text_layer = has_text_layer(p)
        if fi.size == 0:
            fi.problems.append("zero-byte file")
        sc.files.append(fi)
    sc.tender_number = tender_number_of(sc.files)
    roles = {f.role for f in sc.files}
    sc.missing_roles = [r for r in REQUIRED_ROLES if r not in roles]
    sc.complete = not sc.missing_roles and not any(f.problems for f in sc.files)
    sc.anomalies = anomalies_for(sc)
    return sc


def anomalies_for(sc: Scenario) -> list[dict]:
    """Layout oddities, each with a proposed correction. Reported only; nothing on disk is touched."""
    out: list[dict] = []

    def add(what: str, fix: str) -> None:
        out.append({"anomaly": what, "proposed_correction": fix})

    if sc.grade is not None and sc.tender_number is not None and sc.grade != sc.tender_number:
        add(f"Folder is grade {sc.grade} but its files are named Tender{sc.tender_number}",
            "Keep as is; use the manifest's folder-to-tender map. Optionally name folders by tender number.")
    if not re.fullmatch(r"\dCE", sc.folder):
        add(f"Folder name '{sc.folder}' does not follow one pattern (spaces, case, suffixes)",
            f"Rename to '{sc.grade}CE' in a copy of the dataset if scripts need a uniform name.")
    tenders = [f for f in sc.files if f.role == "tender"]
    if len(tenders) > 1:
        add(f"The tender is split over {len(tenders)} PDFs ({sum(f.pages or 0 for f in tenders)} pages together)",
            "Pass all of them to the engine in the order given by the key; do not merge on disk.")
    for f in sc.files:
        n = Path(f.path).name
        if n.lower().endswith(".pdf.pdf"):
            add(f"Double extension: {n}", "Rename to a single .pdf in a copy.")
        if f.role == "tender" and re.search(r"\bgb\b|5GB|and gb", n, re.I):
            add(f"Tender '{n}' covers GB (general building) as well as CE; GB is out of scope",
                "Evaluate the CE parts only; the key's requirements decide what counts.")
        if f.role in ("clean_pack", "tampered_pack") and f.text_layer:
            add(f"{n} has a text layer, although the packs are described as image-only scans",
                "Check the pack was produced as intended.")
    if any(f.role == "supporting_annex" for f in sc.files):
        n = sum(1 for f in sc.files if f.role == "supporting_annex")
        add(f"{n} supporting annexure files (specs and drawings, reference only)",
            "Not passed to the engine; the key marks them as reference only.")
    for r in sc.missing_roles:
        add(f"Missing required part: {r}", "Obtain the file; the scenario stays Incomplete until then.")
    dup = {}
    for f in sc.files:
        if f.sha256:
            dup.setdefault(f.sha256, []).append(f.path)
    for paths in dup.values():
        if len(paths) > 1:
            add(f"Identical files (same SHA-256): {paths}", "Keep one; report which is canonical.")
    return out


def write_reports(root: Path, scenarios: list[Scenario], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps(
        {"root": str(root), "scenarios": [asdict(s) for s in scenarios]}, indent=1))
    lines = [f"# CE dataset manifest", "", f"Root: `{root}` (note the trailing space in the folder name).", "",
             "Labels: the tender is the *authentic source* (public procurement document). Clean and tampered packs are "
             "*synthetic test documents* (fictional bidder), never genuine submissions.", "",
             f"Grade folders found: {len(scenarios)} of 9. Complete: {sum(s.complete for s in scenarios)}.", "",
             "| Folder | Tender | Tender file(s) (pages) | Clean pack (pages) | Tampered pack (pages) | Requirements | Key | Complete |",
             "|---|---|---|---|---|---|---|---|"]

    def pick(sc: Scenario, role: str) -> str:
        fs = [f for f in sc.files if f.role == role]
        return "; ".join(f"{Path(f.path).name}" + (f" ({f.pages})" if f.pages else "") for f in fs) or "MISSING"

    for s in scenarios:
        lines.append(f"| {s.folder} | Tender{s.tender_number} | {pick(s, 'tender')} | {pick(s, 'clean_pack')} | "
                     f"{pick(s, 'tampered_pack')} | {pick(s, 'requirements')} | {pick(s, 'answer_key')} | "
                     f"{'yes' if s.complete else 'NO: ' + ', '.join(s.missing_roles)} |")
    lines += ["", "## Anomalies (reported only; nothing on disk was changed)", ""]
    for s in scenarios:
        for a in s.anomalies:
            lines.append(f"- **{s.folder}**: {a['anomaly']}. Proposed: {a['proposed_correction']}")
    (out / "manifest.md").write_text("\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=Path("evaluation/ce_baseline"))
    a = ap.parse_args()
    root = find_root(a.root)
    folders = sorted((d for d in root.iterdir() if d.is_dir()), key=lambda d: (grade_of(d.name) or 99, d.name))
    scenarios = [scan_folder(root, d) for d in folders]
    found = sorted(s.grade for s in scenarios if s.grade)
    missing = sorted(set(range(1, 10)) - set(found))
    write_reports(root, scenarios, a.out)
    print(f"root: {root!r}")
    print(f"grade folders: {found}  missing: {missing or 'none'}")
    for s in scenarios:
        print(f"  {s.folder!r:16} Tender{s.tender_number}  complete={s.complete}  anomalies={len(s.anomalies)}")
    print(f"wrote {a.out}/manifest.json and manifest.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
