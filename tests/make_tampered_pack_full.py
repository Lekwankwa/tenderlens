"""ONE tampered pack with every kind of discrepancy TenderLens knows about: typed, structural, page order, certificate
contents, and HANDWRITTEN changes drawn on the scans for a vision model to find.

    python tests/make_tampered_pack_full.py   ->  out/tampered_pack_full.pdf, out/tampered_full_answer_key.md

Run it through TenderLens twice: once as it is (rules only: every non-handwriting change should be caught) and once with
a vision model connected and NO transcript (see OLLAMA.md): then the handwriting rows should be caught too.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import hand_edits as H
import make_tampered_pack as M
import tamper as T
from PIL import Image
from tenderlens.cli import audit_pack
from tenderlens.ingest import classify_pages, load_pdf
from tenderlens.llm import LlamaClient

OUT, S = T.ROOT / "out", T.S


def build(pages):
    typed, _removed = M.typed_edits(pages)
    removed = {20: (15, "Page removed: SBD 9, second page", "SBD 9")}
    sp = pages
    cert = [  # (page, what, where, finding text, edit)
        (29, "CIPC status changed from 'In Business' to 'Deregistration process'", "CIPC certificate", "not 'In Business'",
         lambda im: T.repaint(im, T.after(sp, 29, [r"Enterprise", r"Status"], 2), "Deregistration process")),
        (25, "CSD overall tax status changed to 'Tax Non-Compliant'", "CSD report, page 1", "tax compliant",
         lambda im: T.repaint(im, T.after(sp, 25, [r"Overall", r"Tax", r"Status"], 2), "Tax Non-Compliant")),
        (24, "Affidavit level changed from One to Three (still 100% black owned)", "B-BBEE affidavit",
         "level does not follow", lambda im: T.repaint(im, T.phrase(sp, 24, [r"Level", r"One"], pick=1), "Three")),
        (28, "CSD report dated 27 Mar 2021 instead of 27 Sep 2021 (about 200 days old)", "CSD report, page 4", "days old",
         lambda im: T.repaint(im, T.phrase(sp, 28, [r"27", r"Sep", r"2021"]), "27 Mar 2021")),
        (23, "SARS page names the taxpayer 'Excel Trading Holdings'", "SARS tax compliance page", "Company name differs",
         lambda im: T.repaint(im, T.after(sp, 23, [r"Taxpayer", r"Name"], 3), "Excel Trading Holdings")),
        (24, "Affidavit says the income EXCEEDED R10 million", "B-BBEE affidavit", "income exceeded",
         lambda im: T.repaint(im, T.phrase(sp, 24, [r"did", r"not", r"exceed"]), "exceeded")),
    ]
    layout = [  # (page, what, where, finding text, edit)
        (9, "Page scanned upside down", "SBD 6.2, page 2", "rotated", lambda im: im.rotate(180)),
        (12, "Bottom quarter of the page cut off", "Local content declaration, page 2", "cut off",
         lambda im: T.whiteout(im, (0, int(im.height * 0.75), im.width, im.height))),
    ]
    rows = []   # (what, where, finding text, kind)
    by_page: dict[int, list] = {}
    for _n, what, where, expect, p, fn in typed:
        if what.startswith("Initials removed") and any(r[0] == what for r in rows):
            by_page.setdefault(p, []).append(fn)
            continue
        rows.append((what, where, expect, "typed"))
        by_page.setdefault(p, []).append(fn)
    for p, what, where, expect, fn in cert:
        rows.append((what, where, expect, "certificate content"))
        by_page.setdefault(p, []).append(fn)
    for fn, p, what, where, expect in H.HAND_EDITS:
        kind = "handwriting (needs vision)" if fn.__name__ not in ("strike_through", "correction_fluid", "margin_note") \
            else "vision inspection (needs vision)"
        rows.append((what, where, expect, kind))
        by_page.setdefault(p, []).append(fn)
    for p, what, where, expect, fn in layout:
        rows.append((what, where, expect, "vision inspection (needs vision)"))
        by_page.setdefault(p, []).append(fn)

    ops = []   # (original page number, op)
    for n in range(1, 30):
        if n in removed:
            continue
        if n in by_page:
            im = T.img(pages, n)
            for fn in by_page[n]:
                im = fn(im)
            ops.append((n, ("img", im)))
        else:
            ops.append((n, ("orig", n)))
        if n == 5:
            ops.append((5.5, ("orig", 5)))                                              # a duplicated page
        if n == 9:
            ops.append((9.5, ("img", Image.new("RGB", T.img(pages, 1).size, (255, 255, 255)))))   # a blank sheet

    def swap(a, b):
        ia, ib = [i for i, (n, _) in enumerate(ops) if n == a][0], [i for i, (n, _) in enumerate(ops) if n == b][0]
        ops[ia], ops[ib] = ops[ib], ops[ia]
    swap(13, 14)   # SBD 4: first two pages swapped
    swap(26, 27)   # CSD report: pages 2 and 3 swapped
    rows += [("SBD 4: its first two pages swapped", "Pack pages 13 and 14", "pages are out of order", "page order"),
             ("CSD report: its middle pages swapped", "Pack pages 26 and 27", "out of order", "page order"),
             ("A blank sheet slipped in after original page 9", "Between SBD 6.2 pages", "is blank", "typed"),
             ("Original page 5 (SBD 6.1) copied in twice", "SBD 6.1", "more than once", "typed")]
    for p, (_n, what, expect) in removed.items():
        rows.append((what, f"Original page {p}", expect, "typed"))
    rows += [("Page removed: nothing else (SBD 8 and the CSD report keep their pages so their handwriting and date can be tampered)",
              "", None, "note")]
    return [op for _, op in ops], [r for r in rows if r[3] != "note"]


def main():
    pages = load_pdf(T.PACK, T.WORK)
    classify_pages(pages)
    ops, rows = build(pages)
    OUT.mkdir(exist_ok=True)
    pdf = OUT / "tampered_pack_full.pdf"
    T.build(T.PACK, ops, pdf)
    vision = LlamaClient.for_vision(LlamaClient())
    req, res, facts, _ = audit_pack(S / "rfq" / "rfq_email.pdf", S / "rfq" / "spec_0310.pdf", pdf, S / "blank",
                                    None if vision.configured else T.TRANSCRIPT, T.WORK)
    issues = [f for f in res.findings if f.tier == "issue"]
    text = [(f.title + " " + f.detail).lower() for f in issues]
    lines = ["# Full tampered pack: answer key", "",
             "`tampered_pack_full.pdf` is the pack that won, with every change below. The original is untouched.", "",
             f"This run: {'a vision model was connected (' + vision.model + '), no transcript used' if vision.configured else 'rules only, no vision model'}.",
             "", "| # | What was changed | Where | Kind | Caught in this run? |", "|---|---|---|---|---|"]
    caught = total = 0
    for i, (what, where, expect, kind) in enumerate(rows, 1):
        needs = "needs vision" in kind
        if needs and not vision.configured:
            mark = "not run: needs a vision model"
        else:
            ok = any(expect.lower() in t for t in text)
            caught += ok
            total += 1
            mark = "yes" if ok else "**NO**"
        lines.append(f"| {i} | {what} | {where} | {kind} | {mark} |")
    lines += ["", f"**{caught} of {total} runnable changes caught.** TenderLens raised {len(issues)} issue(s):", ""]
    lines += [f"- {f.title}" for f in issues]
    lines += ["", "## Test it with Ollama", "",
              "```", "python -m tenderlens check-llm", "python -m tenderlens eval-vision",
              "python -m tenderlens run --rfq samples/rfq/rfq_email.pdf --spec samples/rfq/spec_0310.pdf \\",
              "    --pack out/tampered_pack_full.pdf --blank samples/blank --out out/full", "```", "",
              "Leave out `--transcript`: the transcript describes the ORIGINAL pack's handwriting, and where it matches it "
              "would take the place of the model's own reading. The rows marked *needs vision* should appear as issues. "
              "A single reading by the model counts as an issue only if a second, differently worded look agrees; "
              "otherwise it appears under observations."]
    (OUT / "tampered_full_answer_key.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
