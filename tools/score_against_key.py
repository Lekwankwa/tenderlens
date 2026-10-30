"""Score a TenderLens report.json against the tampered-pack answer key, row by row.

    python tools/score_against_key.py out/some_run/report.json [--json]

Each of the key's 62 rows (tests/fixtures/v2/tampered_v2_answer_key.md) is matched to a finding by title and by the
pack page the finding cites. Pages are the pages of tests/fixtures/v2/tampered_pack_v2.pdf as it really is (found by
comparing it page by page with samples/response/pack.pdf), which differs from the key's own page labels in a few
rows - see the notes on rows 16, 57, 60 below. The answer key itself is never edited.

Result per row: "issue" or "observation" (found; the tier it was reported at), "wrong page" (right kind of finding,
but the tampered page is not among the cited pages), "missed", or "n/a" (the key's known-gap row).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# (row, group, what changed, title regex, pages that must be cited (any of), note)
ROWS: list[tuple[int, str, str, str, tuple[int, ...], str]] = [
    (1, "Quotation", "Quantity 30->20 (against the spec)", r"Quantity on the quote", (1,), ""),
    (2, "Quotation", "Validity 60->20 days", r"Quote validity shorter", (1,), ""),
    (3, "Quotation", "RFQ reference ->0311", r"Quote reference differs", (1,), ""),
    (4, "Quotation", "Addressed to Tenders@", r"addressed to a different email", (1,), ""),
    (5, "Quotation", "Delivery city ->Polokwane", r"Delivery address on the quote", (1,), ""),
    (6, "Quotation", "Battery 105AH->100AH", r"Item description on the quote", (1,), ""),
    (7, "Quotation", "Closing date on quote ->2021/10/22", r"Closing date on the quote differs", (1,), ""),
    (8, "Quotation", "CSD number on quote altered", r"CSD supplier number differs", (1,), ""),
    (9, "Quotation", "Signature removed", r"Quotation may be unsigned", (1,), ""),
    (10, "Affidavit", "Registration number altered", r"Company registration number differs", (25,), ""),
    (11, "Affidavit", "SA ID ->...097", r"SA ID number differs", (25,), ""),
    (12, "Affidavit", "Company name ->EXCELLENT SUPPLIES", r"Company name differs", (25,), ""),
    (13, "SARS and CSD", "Tax reference ->...178 (SARS page)", r"Tax reference number differs", (24,), ""),
    (14, "Signatures", "Signature removed, SBD 9", r"SBD 9 may be unsigned", (23,), ""),
    (15, "Signatures", "Pen scribble, SBD 4 page 2", r"Unexplained mark on SBD 4|Handwritten note outside the form fields on SBD 4", (15,), ""),
    (16, "Signatures", "Initials removed from 6 pages", r"Initials missing on", (3, 7, 14, 16, 21, 26),
     "pixel comparison with the clean pack: initials are gone from tampered pages 3, 7, 14, 16, 21, 26 (the key lists 'original pages 3, 6, 11, 13, 19, 25')"),
    (17, "SARS and CSD", "CSD tax status Non-Compliant", r"does not show the supplier as tax compliant", (26,), ""),
    (18, "SARS and CSD", "CSD Restricted Supplier Yes", r"Restricted Supplier", (26,), ""),
    (19, "Affidavit", "Level One->Three", r"affidavit's level does not follow", (25,), ""),
    (20, "SARS and CSD", "CSD report dated 27 Mar", r"CSD report is \d+ days old", (29,), ""),
    (21, "SARS and CSD", "SARS page names Excel Trading Holdings", r"Company name differs", (24,), ""),
    (22, "SARS and CSD", "PIN purpose Tender->Other", r"PIN purpose is not", (24,), ""),
    (23, "Affidavit", "Income exceeded R10 million", r"income exceeded R10 million", (25,), ""),
    (24, "Quotation", "Unit price R2410.8->R2310.8", r"Quote total does not equal", (1,), ""),
    (25, "SBD 4", "q2.7 NO->YES", r"question 2\.7 is answered Yes", (15,), ""),
    (26, "SBD 4", "q2.9 both marked", r"both YES and NO are marked for the question 2\.9", (15,), ""),
    (27, "SBD 8", "q4.3 YES", r"question 4\.3 is answered Yes", (19,), ""),
    (28, "SBD 8", "Date wiped", r"SBD 8: date is blank", (20,), ""),
    (29, "SBD 9", "Dated 2021/10/20, after closing", r"SBD 9 is dated after the closing date", (23,), ""),
    (30, "SBD 6.1", "Years in business 1->6", r"years in business' is more", (7,), ""),
    (31, "Signatory", "Quotation signed by Thabo Mokoena", r"person who signed differs", (1,), ""),
    (32, "Signatory", "Commissioner's stamp and signature removed", r"no official stamp|no commissioner's signature", (25,), ""),
    (33, "SBD 8", "Pen line through printed paragraph", r"Printed text crossed out on SBD 8", (19,), ""),
    (34, "SBD 8", "Correction fluid over 'N/A'", r"Entry overwritten or covered on SBD 8", (19,), ""),
    (35, "SBD 4", "Note 'price to be confirmed later'", r"Handwritten note outside the form fields on SBD 4|Unexplained mark on SBD 4", (15,), ""),
    (36, "SBD 6.1", "Subcontracting ->YES", r"subcontracting answer is answered Yes", (6,), ""),
    (37, "SBD 6.1", "Points claimed 10 vs level", r"Points claimed do not match", (6,), ""),
    (38, "SBD 6.1", "Registration number altered", r"Company registration number differs", (7,), ""),
    (39, "SBD 6.1", "Company type -> CC (known gap)", r"\A\Z", (), "known gap: no rule compares this"),
    (40, "SBD 6.1", "Second witness removed", r"SBD 6\.1: only 1 witness", (8,), ""),
    (41, "SBD 6.1", "Dated 2021/10/01 vs 10/06", r"Forms are signed on different dates", (8,), ""),
    (42, "SBD 6.2", "Imported content YES vs R0 declared", r"imported-content answer is answered Yes|declares imported content, but", (12,), ""),
    (43, "Local content", "Bid price R70324 vs quote R72324", r"Bid price differs between quote and local content", (1, 13), ""),
    (44, "Local content", "40% local content with R0 imported", r"Local content percentage does not follow", (13,), ""),
    (45, "Local content", "Witness 1 removed, witness 2 dated", r"Local content declaration: only 1 witness|Local content declaration.*witness", (14,), ""),
    (46, "SBD 4", "SA ID ...078", r"SA ID number differs", (16,), ""),
    (47, "SBD 4", "Tax reference ...178 (SARS says ...187)", r"Tax reference number differs", (16,), ""),
    (48, "SBD 4", "q2.10 neither marked", r"question 2\.10 is blank", (17,), ""),
    (49, "SBD 4", "q2.11 YES", r"question 2\.11 is answered Yes", (17,), ""),
    (50, "SBD 4", "Dated 2021/10/01 vs 10/06", r"Forms are signed on different dates", (18,), ""),
    (51, "SBD 4", "Signature replaced", r"signature on SBD 4 may be in a different hand", (18,), ""),
    (52, "SBD 8", "q4.1 YES", r"question 4\.1 is answered Yes", (19,), ""),
    (53, "SBD 8", "q4.2 YES", r"question 4\.2 is answered Yes", (19,), ""),
    (54, "SBD 8", "q4.4 YES", r"question 4\.4 is answered Yes", (20,), ""),
    (55, "SBD 8", "Declarant's name wiped", r"SBD 8: declarant is blank", (20,), ""),
    (56, "SBD 6.2", "Page scanned upside down", r"Page rotated on SBD 6\.2", (10,), ""),
    (57, "Local content", "Bottom of a page cut off", r"Page cut off at the edge on Local content declaration", (13, 14),
     "the key says declaration page 2 (pack page 14); in the PDF page 14 is identical to the clean page and it is page 1 (pack page 13) whose last lines are clipped"),
    (58, "Page structure", "SBD 4 first two pages swapped", r"SBD 4: pages are out of order", (15, 16), ""),
    (59, "Page structure", "CSD report middle pages swapped", r"CSD summary report: pages are out of order", (27, 28), ""),
    (60, "Page structure", "Blank sheet inserted", r"Pack page 11 is blank", (11,),
     "in the PDF the blank sheet REPLACES Annexure C (clean page 11); the key calls it an insertion"),
    (61, "Page structure", "Page copied in twice (SBD 6.1)", r"same page appears more than once", (4, 5), ""),
    (62, "Page structure", "SBD 9 second page removed", r"SBD 9: 3 of 4 pages found", (21, 22, 23), ""),
]

# not in the key, but true of the PDF: Annexure C is absent from the tampered pack (clean page 11 became the blank)
UNLISTED_TRUE = [("Annexure C removed (page 11 is blank)", r"Missing: Annexure C")]


def score(report: dict) -> list[dict]:
    findings = report["findings"]
    out = []
    for row, group, what, rx, pages, note in ROWS:
        if row == 39:
            out.append(dict(row=row, group=group, what=what, result="n/a", cited=[], note=note))
            continue
        hits = [f for f in findings if re.search(rx, f["title"] + " " + f.get("detail", ""), re.I)]
        best, result = None, "missed"
        for f in hits:
            cited = {e.get("page") for e in f["evidence"]}
            ok = bool(cited & set(pages)) if row != 16 else len(cited & set(pages)) >= 3 or bool(
                re.search(r"\b6 page", f["title"]))
            if ok:
                tier = f["tier"] if f["tier"] == "issue" else "observation"
                if best is None or tier == "issue":
                    best, result = f, tier
            elif best is None:
                best, result = f, "wrong page"
        out.append(dict(row=row, group=group, what=what, result=result, note=note,
                        cited=sorted({e.get("page") for e in best["evidence"]}) if best else [],
                        title=best["title"] if best else ""))
    return out


def summarise(rows: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["result"]] = counts.get(r["result"], 0) + 1
    detected = counts.get("issue", 0) + counts.get("observation", 0)
    return dict(counts=counts, detected=detected, testable=len(rows) - counts.get("n/a", 0))


def main(argv: list[str]) -> int:
    path = Path(argv[1])
    report = json.loads(path.read_text())
    rows = score(report)
    s = summarise(rows)
    if "--json" in argv:
        print(json.dumps(dict(rows=rows, **s), indent=1))
        return 0
    for r in rows:
        if r["result"] in ("missed", "wrong page"):
            print(f"  row {r['row']:2d} {r['result']:10s} {r['group']}: {r['what']}"
                  + (f"  [cited {r['cited']}: {r['title'][:50]}]" if r["cited"] else ""))
    print(f"detected {s['detected']} of {s['testable']} testable rows "
          f"({s['counts'].get('issue', 0)} as issues, {s['counts'].get('observation', 0)} as observations); "
          f"wrong page {s['counts'].get('wrong page', 0)}; missed {s['counts'].get('missed', 0)}")
    extra = [t for t, rx in UNLISTED_TRUE if any(re.search(rx, f["title"]) for f in report["findings"])]
    if extra:
        print("true findings the key does not list:", "; ".join(extra))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
