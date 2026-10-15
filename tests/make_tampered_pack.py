"""Build ONE tampered copy of the winning pack with many deliberate discrepancies, run TenderLens on it,
and write an answer key that says what was changed and whether each change was caught.

    python tests/make_tampered_pack.py        ->  out/tampered_pack.pdf, out/tampered_answer_key.md
Needs samples/response/pack.pdf (your original). The original is never modified.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tamper as T
from tenderlens import vision as V
from tenderlens.cli import audit_pack
from tenderlens.ingest import classify_pages, load_pdf
from PIL import Image

OUT = T.ROOT / "out"
S = T.S


def typed_edits(pages):
    """The typed, structural and identifier changes (also reused by make_tampered_pack_full.py)."""
    sp, span = pages, T.span

    def sig_zone(n):
        ev = sorted(V.signature_evidence(pages[n - 1], r"^signature", "beside" if n == 1 else "above"),
                    key=lambda e: e["label_box"][1])[-1]
        return ev["zone"]

    z1, z22 = sig_zone(1), sig_zone(22)
    # (number, what was changed, where, text a finding must contain, page to edit, edit function)
    edits = [
        (1, "Quantity changed from 30 to 20", "Quotation, quantity", "Quantity", 1,
         lambda im: T.repaint(im, span(sp, 1, r"^30$"), "20")),
        (2, "Validity changed from 60 days to 20 days", "Quotation, validity line", "validity", 1,
         lambda im: T.repaint(im, span(sp, 1, r"^60$"), "20")),
        (3, "RFQ reference changed to 5/2/1(0311)21/22", "Quotation, reference line", "reference", 1,
         lambda im: T.repaint(im, span(sp, 1, r"^NO:5/2"), "NO:5/2/1(0311)21/22")),
        (4, "Addressed to Tenders@dalrrd.gov.za instead of the RFQ address", "Quotation, 'To' email",
         "different email", 1, lambda im: T.repaint(im, span(sp, 1, r"^Quotations"), "Tenders@dalrrd.gov.za")),
        (5, "Delivery address city changed to Polokwane", "Quotation, delivery address", "Delivery address", 1,
         lambda im: T.repaint(im, span(sp, 1, r"^Pretoria$", nth=1), "Polokwane")),
        (6, "Battery capacity changed from 105AH to 100AH", "Quotation, item line", "description", 1,
         lambda im: T.repaint(im, span(sp, 1, r"^105AH$"), "100AH")),
        (7, "Closing date on the quote changed to 2021/10/22", "Quotation, closing date", "Closing date", 1,
         lambda im: T.repaint(im, span(sp, 1, r"^date_2021"), "date_2021/10/22")),
        (8, "CSD number on the quote changed (…0966851 to …0966951)", "Quotation, header", "CSD supplier number", 1,
         lambda im: T.repaint(im, span(sp, 1, r"^MAAA"), "MAAA0966951")),
        (9, "Signature removed from the quotation", "Quotation, signature line", "Quotation may be unsigned", 1,
         lambda im: T.whiteout(im, z1)),
        (10, "Registration number changed (…534725… to …534752…)", "B-BBEE affidavit", "registration number", 24,
         lambda im: T.repaint(im, span(sp, 24, r"^2020/534725/07$"), "2020/534752/07")),
        (11, "SA ID number changed (…087 to …097)", "B-BBEE affidavit", "SA ID", 24,
         lambda im: T.repaint(im, span(sp, 24, r"^0204145509087$"), "0204145509097")),
        (12, "Company name changed to EXCELLENT SUPPLIES", "B-BBEE affidavit", "Company name", 24,
         lambda im: T.repaint(im, span(sp, 24, r"^EXCEL$", r"^HOLDINGS"), "EXCELLENT SUPPLIES")),
        (13, "Tax reference changed (…1187 to …1178)", "SARS tax compliance page", "Tax reference", 23,
         lambda im: T.repaint(im, span(sp, 23, r"^9628891187$"), "9628891178")),
        (17, "Signature removed from SBD 9", "SBD 9, signing page", "SBD 9 may be unsigned", 22,
         lambda im: T.whiteout(im, z22)),
        (18, "Pen scribble added in the margin", "SBD 4, page 2", "Unexplained mark", 14,
         lambda im: T.scribble(im, im.width - 120, 700)),
    ] + [(19, "Initials removed from original pages 3, 6, 11, 13, 19, 25", "Bottom-right corners", "Initials missing", n,
          lambda im: T.whiteout(im, (int(im.width * 0.68), int(im.height * 0.90), im.width, im.height)))
         for n in (3, 6, 11, 13, 19, 25)]
    removed = {18: (14, "Page removed: SBD 8 certification and signature page", "SBD 8"),
               20: (15, "Page removed: SBD 9, second page", "SBD 9"),
               28: (16, "Page removed: CSD report, B-BBEE and tax section", "CSD summary report is missing")}

    return edits, removed


def main():
    pages = load_pdf(T.PACK, T.WORK)
    classify_pages(pages)
    edits, removed = typed_edits(pages)

    by_page: dict[int, list] = {}
    for _, _, _, _, p, fn in edits:
        by_page.setdefault(p, []).append(fn)
    ops = []
    for n in range(1, 30):
        if n in removed:
            continue
        if n in by_page:
            im = T.img(pages, n)
            for fn in by_page[n]:
                im = fn(im)
            ops.append(("img", im))
        else:
            ops.append(("orig", n))
        if n == 5:
            ops.append(("orig", 5))                                       # a duplicated page
        if n == 9:
            ops.append(("img", Image.new("RGB", T.img(pages, 1).size, (255, 255, 255))))  # a blank sheet slipped in
    OUT.mkdir(exist_ok=True)
    pdf = OUT / "tampered_pack.pdf"
    T.build(T.PACK, ops, pdf)

    req, res, facts, pg = audit_pack(S / "rfq" / "rfq_email.pdf", S / "rfq" / "spec_0310.pdf", pdf, S / "blank",
                                     T.TRANSCRIPT, T.WORK)
    issues = [f for f in res.findings if f.tier == "issue"]
    text = [(f.title + " " + f.detail).lower() for f in issues]
    hit = lambda s: any(s.lower() in t for t in text)
    rows = []
    seen = set()
    for num, what, where, expect, _, _ in sorted(edits, key=lambda e: e[0]):
        if num in seen:
            continue
        seen.add(num)
        rows.append((num, what, where, hit(expect)))
    for p, (num, what, expect) in removed.items():
        rows.append((num, what, f"Original page {p}", hit(expect)))
    rows.append((20, "Blank sheet slipped in after original page 9", "Between SBD 6.2 pages", hit("is blank")))
    rows.append((21, "Original page 5 (SBD 6.1) copied in twice", "SBD 6.1", hit("more than once")))
    rows.sort()
    lines = ["# Tampered pack: answer key", "",
             "`tampered_pack.pdf` is your winning pack with the changes below. Upload it to TenderLens and compare.", "",
             "| # | What was changed | Where | Caught? |", "|---|---|---|---|"]
    lines += [f"| {n} | {w} | {wh} | {'yes' if ok else '**NO**'} |" for n, w, wh, ok in rows]
    lines += ["", f"**{sum(1 for r in rows if r[3])} of {len(rows)} changes caught.** "
              f"TenderLens raised {len(issues)} issue(s):", ""]
    lines += [f"- {f.title}" for f in issues]
    lines += ["", "## Not included, because TenderLens cannot catch these yet",
              "- A handwritten No/Yes tick, date, amount or number changed by pen.",
              "- A scribble in white space inside a form, or a strike-through across printed text.",
              "- Altered printed wording of a standard form.",
              "- A page missing from the middle of the CSD commodity list."]
    (OUT / "tampered_answer_key.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:4 + len(rows) + 4]))


if __name__ == "__main__":
    main()
