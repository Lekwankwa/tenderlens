"""Tamper tests: start from the pack that won, change ONE thing, and check TenderLens notices.

    python tests/tamper.py [--only name] [--keep]

The winning pack must come back with 0 issues (the control). Each variant then removes a page, changes a
typed figure, blanks a signature, or adds a mark, and the test records whether an *issue* was raised
and what it said. Output: out/tamper/summary.md and out/tamper/<variant>/report.json
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter

import hand_edits as H
from tenderlens import vision as V
from tenderlens.cli import audit_pack
from tenderlens.ingest import classify_pages, load_pdf
from tenderlens.report import render_html, to_json

ROOT = Path(__file__).resolve().parents[1]
S = ROOT / "samples"
PACK = S / "response" / "pack.pdf"
TRANSCRIPT = S / "response" / "pack.transcript.json"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
OUT = ROOT / "out" / "tamper"
WORK = ROOT / ".cache"


# --------------------------------------------------------------------------- page editing helpers

def img(pages, n):
    return Image.open(pages[n - 1].image_path).convert("RGB")


def span(pages, n, first, last=None, nth=0):
    """Box around OCR words from the nth match of `first` to the next match of `last` (same line)."""
    ws = pages[n - 1].words
    hits = [i for i, w in enumerate(ws) if re.search(first, w.text)]
    i = hits[nth]
    j = i
    if last:
        j = next(k for k in range(i, len(ws)) if re.search(last, ws[k].text))
    grp = ws[i:j + 1]
    return (min(w.left for w in grp), min(w.top for w in grp),
            max(w.left + w.width for w in grp), max(w.top + w.height for w in grp))


def repaint(im, box, text, pad=3):
    """Cover the old text with the page's own paper colour and write new text in the same place."""
    x0, y0, x1, y1 = box
    ring = np.array(im.crop((max(0, x0 - 12), max(0, y0 - 12), x1 + 12, y1 + 12)))
    bg = tuple(int(v) for v in np.percentile(ring.reshape(-1, 3), 90, axis=0))
    d = ImageDraw.Draw(im)
    d.rectangle((x0 - pad, y0 - pad, x1 + pad, y1 + pad), fill=bg)
    size = max(10, int((y1 - y0) * 0.95))
    f = ImageFont.truetype(FONT, size)
    d.text((x0, y0 - 1), text, font=f, fill=(25, 25, 25))
    return im


def whiteout(im, box):
    return repaint(im, box, "", pad=0)


def scribble(im, x, y, w=110, h=46, seed=1):
    rng = np.random.default_rng(seed)
    d = ImageDraw.Draw(im)
    pts = [(x + i * w / 9, y + h / 2 + rng.uniform(-h / 2, h / 2)) for i in range(10)]
    d.line(pts, fill=(20, 30, 90), width=4, joint="curve")
    return im


def strike(im, box, seed=3):
    """A pen strike-through: slightly wavy and sloped, as drawn by hand."""
    x0, y0, x1, y1 = box
    rng = np.random.default_rng(seed)
    ym = (y0 + y1) / 2
    n = 24
    pts = [(x0 + (x1 - x0) * i / n, ym + 5 * np.sin(i / 2.2) + (i / n) * 5 + rng.uniform(-1.5, 1.5)) for i in range(n + 1)]
    ImageDraw.Draw(im).line(pts, fill=(20, 30, 90), width=4, joint="curve")
    return im


def phrase(pages, n, tokens, nth=0, pick=None):
    """Box around consecutive OCR words matching the regexes in `tokens` (or just the word at index `pick`)."""
    ws = pages[n - 1].words
    hits = [i for i in range(len(ws) - len(tokens) + 1)
            if all(re.fullmatch(t, ws[i + k].text) for k, t in enumerate(tokens))]
    i = hits[nth]
    grp = ws[i:i + len(tokens)] if pick is None else [ws[i + pick]]
    return (min(w.left for w in grp), min(w.top for w in grp),
            max(w.left + w.width for w in grp), max(w.top + w.height for w in grp))


def after(pages, n, label, count, nth=0):
    """Box around the `count` words that follow the label phrase on the same line."""
    ws = pages[n - 1].words
    hits = [i for i in range(len(ws) - len(label) + 1) if all(re.fullmatch(t, ws[i + k].text) for k, t in enumerate(label))]
    i = hits[nth] + len(label)
    grp = ws[i:i + count]
    return (min(w.left for w in grp), min(w.top for w in grp),
            max(w.left + w.width for w in grp), max(w.top + w.height for w in grp))


def build(pack_pdf: Path, ops, out_pdf: Path):
    """ops: list of ('orig', n) | ('img', PIL image). Untouched pages are copied from the original PDF
    byte for byte so they render identically; edited pages are embedded as images at the scan resolution."""
    src = PdfReader(str(pack_pdf))
    w = PdfWriter()
    tmp = out_pdf.parent / "_edited"
    tmp.mkdir(parents=True, exist_ok=True)
    for i, (kind, val) in enumerate(ops):
        if kind == "orig":
            w.add_page(src.pages[val - 1])
        else:
            f = tmp / f"{out_pdf.stem}-{i}.pdf"
            val.convert("RGB").save(f, "PDF", resolution=150, quality=92)
            w.add_page(PdfReader(str(f)).pages[0])
    with open(out_pdf, "wb") as fh:
        w.write(fh)


def keep_all(n=29):
    return [("orig", i) for i in range(1, n + 1)]


def drop(*nums):
    return [("orig", i) for i in range(1, 30) if i not in nums]


def edit(pages, **changes):
    """changes: page_number -> callable(PIL image) -> PIL image"""
    ops = keep_all()
    for n, fn in changes.items():
        n = int(n.lstrip("p"))
        ops[n - 1] = ("img", fn(img(pages, n)))
    return ops


# --------------------------------------------------------------------------- the variants
# (name, what was done, substring that an ISSUE title must contain, builder(pages) -> ops)

def variants(pages):
    def blank_sig(n, label=r"^signature"):
        ev = sorted(V.signature_evidence(pages[n - 1], label), key=lambda e: e["label_box"][1])[-1]
        z = ev["zone"]   # the area where the pen strokes are measured; never the printed label itself
        return lambda im: whiteout(im, z)

    quote_total = span(pages, 1, r"^R72$", r"^324$", nth=1)
    quote_total_item = span(pages, 1, r"^R72$", r"^324$", nth=0)
    unit = span(pages, 1, r"^R2410", None)
    qty = span(pages, 1, r"^30$", None)
    desc = span(pages, 1, r"^105AH$", None)
    ref = span(pages, 1, r"^NO:5/2", None)
    valid = span(pages, 1, r"^60$", None)
    mail = span(pages, 1, r"^Quotations", None)
    city = span(pages, 1, r"^Pretoria$", None, nth=1)  # delivery address line (first is the supplier address)
    closing = span(pages, 1, r"^date_2021", None)
    csd = span(pages, 1, r"^MAAA", None)
    reg24 = span(pages, 24, r"^2020/534725/07$", None)
    sa24 = span(pages, 24, r"^0204145509087$", None)
    tax23 = span(pages, 23, r"^9628891187$", None)
    name24 = span(pages, 24, r"^EXCEL$", None)
    # a body line on SBD 9 to strike through
    ws17 = [w for w in pages[16].words if w.top > 700 and w.conf > 80 and len(w.text) > 3]
    first = ws17[0]
    row = [w for w in pages[16].words if abs(w.top - first.top) < 12]
    strike_line = (min(w.left for w in row), min(w.top for w in row), max(w.left + w.width for w in row),
                   max(w.top + w.height for w in row))

    V_ = []
    add = lambda name, what, expect, ops, known_miss=False, tx=None, vision=False: V_.append(
        (name, what, expect, ops, known_miss, tx, vision))

    add("00_control_winning_pack", "Untouched pack that won the RFQ", None, keep_all())
    # --- pages removed
    add("01_remove_sbd8_signing_page", "Removed pack page 18 (SBD 8 certification and signature)", "SBD 8", drop(18))
    add("02_remove_tax_compliance", "Removed pack page 23 (SARS tax compliance status)", "Tax compliance", drop(23))
    add("03_remove_bbbee_affidavit", "Removed pack page 24 (B-BBEE sworn affidavit)", "B-BBEE", drop(24))
    add("04_remove_sbd4_signing_page", "Removed pack page 16 (SBD 4 signing page)", "SBD 4", drop(16))
    add("05_remove_sbd9_middle_page", "Removed pack page 20 (SBD 9, second page)", "SBD 9", drop(20))
    add("06_remove_csd_bbbee_page", "Removed pack page 28 (CSD report: B-BBEE and tax section)", "CSD", drop(28))
    add("07_remove_quotation", "Removed pack page 1 (the quotation)", "quotation", drop(1))
    add("08_remove_sbd6_1_page", "Removed pack page 5 (SBD 6.1, page 3 of 5)", "SBD 6.1", drop(5))
    add("09_duplicate_page", "Inserted a second copy of pack page 14 (SBD 4)", "same page appears more than once",
        keep_all()[:14] + [("orig", 14)] + keep_all()[14:])
    # --- typed figures changed on the quote
    add("10_total_changed", "Quote TOTAL changed from R72 324 to R70 324", "Quote total",
        edit(pages, p1=lambda im: repaint(im, quote_total, "R70 324")))
    add("11_unit_price_changed", "Unit price changed from R2410.8 to R2310.8", "Quote total",
        edit(pages, p1=lambda im: repaint(im, unit, "R2310.8")))
    add("12_quantity_changed", "Quantity changed from 30 to 20", "Quantity",
        edit(pages, p1=lambda im: repaint(im, qty, "20")))
    add("13_validity_shortened", "Validity changed from 60 days to 20 days", "validity",
        edit(pages, p1=lambda im: repaint(im, valid, "20")))
    add("14_reference_changed", "RFQ reference changed from 5/2/1(0310)21/22 to 5/2/1(0311)21/22", "reference",
        edit(pages, p1=lambda im: repaint(im, ref, "NO:5/2/1(0311)21/22")))
    add("15_wrong_submission_email", "Addressed to Tenders@dalrrd.gov.za instead of the RFQ's address", "different email",
        edit(pages, p1=lambda im: repaint(im, mail, "Tenders@dalrrd.gov.za")))
    add("16_delivery_city_changed", "Delivery address changed to Polokwane", "Delivery address",
        edit(pages, p1=lambda im: repaint(im, city, "Polokwane")))
    add("17_item_capacity_changed", "Battery capacity changed from 105AH to 100AH", "description",
        edit(pages, p1=lambda im: repaint(im, desc, "100AH")))
    add("18_closing_date_changed", "Closing date on the quote changed to 2021/10/22", "Closing date",
        edit(pages, p1=lambda im: repaint(im, closing, "date_2021/10/22")))
    # --- identifiers changed on supporting documents
    add("19_reg_number_changed", "Company registration number on the affidavit changed (…534725… to …534752…)",
        "registration number", edit(pages, p24=lambda im: repaint(im, reg24, "2020/534752/07")))
    add("20_csd_number_changed", "CSD number on the quote changed (MAAA0966851 to MAAA0966951)", "CSD supplier number",
        edit(pages, p1=lambda im: repaint(im, csd, "MAAA0966951")))
    add("21_id_number_changed", "SA ID on the affidavit changed (…087 to …097)", "SA ID",
        edit(pages, p24=lambda im: repaint(im, sa24, "0204145509097")))
    add("22_tax_number_changed", "Tax reference on the SARS page changed (…1187 to …1178)", "Tax reference",
        edit(pages, p23=lambda im: repaint(im, tax23, "9628891178")))
    add("23_company_name_changed", "Company name on the affidavit changed to EXCELLENT SUPPLIES", "Company name",
        edit(pages, p24=lambda im: repaint(im, span(pages, 24, r"^EXCEL$", r"^HOLDINGS", nth=0), "EXCELLENT SUPPLIES")))
    # --- marks, signatures, initials
    add("24_quote_signature_blanked", "Signature removed from the quotation", "Quotation may be unsigned",
        edit(pages, p1=blank_sig(1)))
    add("25_sbd9_signature_blanked", "Signature removed from SBD 9", "SBD 9 may be unsigned",
        edit(pages, p22=blank_sig(22)))
    add("26_margin_scribble_sbd4", "Added a pen scribble in the margin of SBD 4 (pack page 14)", "Unexplained mark",
        edit(pages, p14=lambda im: scribble(im, im.width - 120, 700)))
    add("27_strike_through_sbd8", "Hand-drawn strike-through across a printed heading on SBD 8 (page 17)",
        "mark", edit(pages, p17=lambda im: strike(im, strike_line)), known_miss=True)
    add("28_initials_removed", "Initials removed from pack page 10", "Initials missing",
        edit(pages, p10=lambda im: whiteout(im, (int(im.width * 0.68), int(im.height * 0.90), im.width, im.height))))
    corner = lambda im: whiteout(im, (int(im.width * 0.68), int(im.height * 0.90), im.width, im.height))
    add("34_initials_removed_from_8_pages", "Initials removed from 8 pages (pack pages 3, 5, 8, 11, 13, 17, 19, 21)",
        "Initials missing", edit(pages, **{f"p{n}": corner for n in (3, 5, 8, 11, 13, 17, 19, 21)}))
    # --- a blank page slipped in
    blank = lambda: ("img", Image.new("RGB", img(pages, 1).size, (255, 255, 255)))
    add("30_blank_page_inside_sbd4", "Inserted a blank page inside SBD 4 (after pack page 14)", "is blank",
        keep_all()[:14] + [blank()] + keep_all()[14:])
    add("31_blank_page_after_sbd9", "Inserted a blank page between SBD 9 and the SARS page (after page 22)", "blank",
        keep_all()[:22] + [blank()] + keep_all()[22:])
    add("32_blank_page_at_end", "Inserted a blank page at the very end of the pack", "is blank", keep_all() + [blank()])
    add("33_blank_page_after_quote", "Inserted a blank page after the quotation (after page 1)", "is blank",
        keep_all()[:1] + [blank()] + keep_all()[1:])

    # ======================================================================================
    # Added: handwriting, page order, certificate contents, and what only a vision model can see.
    # "Reading" variants change what the HANDWRITING READER reports (a person's transcript here, Llama vision in
    # real use), not the image: they test the rules that act on a reading, not the model's eyes.
    # ======================================================================================
    base = keep_all()
    swap = lambda a, b: [base[b - 1] if i == a else base[a - 1] if i == b else base[i - 1] for i in range(1, 30)]
    # --- handwritten answers (simulated reading)
    add("40_sbd4_q2_7_yes", "SBD 4 question 2.7 (state employee) read as YES", "question 2.7", base,
        tx={"set": {"sbd4.q2_7_state_employed": "Yes"}})
    add("41_sbd8_convicted_yes", "SBD 8 question 4.3 (convicted of fraud) read as YES", "question 4.3", base,
        tx={"set": {"sbd8.q4_3_convicted": "Yes"}})
    add("42_sbd8_restricted_yes", "SBD 8 question 4.1 (restricted supplier) read as YES", "question 4.1", base,
        tx={"set": {"sbd8.q4_1_restricted_supplier": "Yes"}})
    add("43_sbd4_date_blank", "SBD 4 date left blank", "date is blank", base, tx={"set": {"sbd4.date": "blank"}})
    add("44_sbd8_declarant_blank", "SBD 8 declarant's name left blank", "declarant is blank", base,
        tx={"set": {"sbd8.declarant": "blank"}})
    add("45_sbd4_both_ticked", "SBD 4 question 2.9 has both YES and NO marked", "both YES and NO", base,
        tx={"set": {"sbd4.q2_9_relationship_with_evaluator": "Both"}})
    add("46_sbd4_question_unanswered", "SBD 4 question 2.10 has neither box ticked", "question 2.10 is blank", base,
        tx={"set": {"sbd4.q2_10_aware_of_other_bidder_relationship": "blank"}})
    add("47_other_person_signed_quote", "Quotation signed by a different person (Thabo Mokoena)",
        "person who signed differs", base, tx={"set": {"quote.signatory": "Thabo Mokoena"}})
    add("48_points_claimed_10", "SBD 6.1 claims 10 points although the affidavit gives Level 1 (20 points)",
        "Points claimed", base, tx={"set": {"sbd6_1.points_claimed": 10}})
    add("49_years_in_business_6", "SBD 6.1 says 6 years in business; the company was registered in 2020",
        "years in business", base, tx={"set": {"sbd6_1.years_in_business": "6 years"}})
    add("50_sbd9_dated_after_closing", "SBD 9 dated 2021-10-20, after the closing date", "after the closing date", base,
        tx={"set": {"sbd9.date": "20/10/2021"}})
    add("51_forms_dated_differently", "SBD 4 dated 2021-10-01 while the other forms say 2021-10-06",
        "different dates", base, tx={"set": {"sbd4.date": "2021-10-01"}})
    add("52_invalid_date", "SBD 8 date written as 31/02/2021", "not a valid date", base,
        tx={"set": {"sbd8.date": "31/02/2021"}})
    add("53_subcontracting_yes", "SBD 6.1 subcontracting answered YES", "subcontracting", base,
        tx={"set": {"sbd6_1.subcontracting": "Yes"}})
    add("54_one_witness_only", "SBD 6.1 has one witness signature instead of two", "witness", base,
        tx={"set": {"sbd6_1.witness_signatures": 1}})
    add("55_affidavit_no_stamp", "Affidavit has no commissioner's stamp", "official stamp", base,
        tx={"set": {"bbbee_affidavit.stamp_present": "No"}})
    add("56_affidavit_not_signed_by_commissioner", "Affidavit not signed by the commissioner", "commissioner's signature",
        base, tx={"set": {"bbbee_affidavit.commissioner_signed": "No"}})
    add("57_affidavit_commissioned_after_closing", "Commissioner's stamp dated 2021-11-20, after the closing date",
        "commissioned after the closing date", base, tx={"set": {"bbbee_affidavit.commissioned": "2021-11-20"}})
    # --- what only a vision model can see (image edited AND the reading simulated)
    add("60_strike_through_seen_by_vision", "Strike-through on SBD 8 (the image) and a vision reading that reports it",
        "crossed out", edit(pages, p17=lambda im: strike(im, strike_line)),
        tx={"add": [("inspect.crossed_out", True, 17, {})]})
    add("61_scribble_in_form_seen_by_vision", "Pen scribble in the white space inside SBD 4 plus a vision reading",
        "note outside the form fields", edit(pages, p14=lambda im: scribble(im, 300, 1000)),
        tx={"add": [("inspect.annotation", True, 14, {})]})
    add("62_correction_fluid_seen_by_vision", "A patch of correction fluid on SBD 9 plus a vision reading",
        "overwritten", edit(pages, p21=lambda im: whiteout(im, (200, 900, 700, 960))),
        tx={"add": [("inspect.overwritten", True, 21, {})]})
    add("63_page_rotated", "Pack page 9 scanned upside down plus a vision reading", "rotated",
        edit(pages, p9=lambda im: im.rotate(180)), tx={"add": [("inspect.rotated", True, 9, {})]})
    add("64_page_cut_off", "The bottom quarter of pack page 12 cut off plus a vision reading", "cut off",
        edit(pages, p12=lambda im: whiteout(im, (0, int(im.height * 0.75), im.width, im.height))),
        tx={"add": [("inspect.cut_off", True, 12, {})]})
    add("65_unconfirmed_vision_lead", "A vision model reports crossed-out text once, and a second look does NOT confirm it",
        "obs:crossed out", base,
        tx={"add": [("inspect.crossed_out", True, 17, {"confidence": 0.4, "verified": False})]})
    add("66_signature_hand_differs", "A vision comparison judges the SBD 4 signature different from the quote's",
        "obs:different hand", base,
        tx={"add": [("sigmatch.sbd4", "different", 16, {"confidence": 0.9, "verified": True})]})
    add("67_typed_name_on_signature_line", "Vision says the 'signature' on the quote is not a pen signature",
        "obs:may not be a signature", base,
        tx={"add": [("inspect.signature_missing", True, 1, {})]})
    # --- page order
    add("70_swap_sbd4_pages", "Pack pages 13 and 14 (SBD 4, pages 1 and 2) swapped", "out of order", swap(13, 14))
    add("71_swap_csd_pages", "Pack pages 26 and 27 (CSD report, pages 2 and 3) swapped", "out of order", swap(26, 27))
    add("72_sbd8_page_inside_sbd4", "SBD 8 first page (pack page 17) moved into the middle of SBD 4", "not together",
        base[:14] + [base[16]] + base[14:16] + base[17:])
    add("73_swap_sbd9_pages", "Pack pages 19 and 20 (SBD 9, pages 1 and 2) swapped", "out of order", swap(19, 20))
    add("74_swap_sbd6_1_pages", "Pack pages 5 and 6 (SBD 6.1, pages 3 and 4) swapped", "out of order", swap(5, 6))
    add("75_quotation_moved_to_end", "Quotation moved to the end of the pack (order across documents is not policed)",
        None, base[1:] + base[:1])
    # --- certificate contents
    add("80_tcs_purpose_not_tender", "SARS PIN 'Purpose of Request' changed from Tender to Other", "purpose",
        edit(pages, p23=lambda im: repaint(im, phrase(pages, 23, [r"Purpose", r"of", r"Request", r"Tender"], pick=3), "Other")))
    add("81_cipc_deregistering", "CIPC status changed from 'In Business' to 'Deregistration process'", "not 'In Business'",
        edit(pages, p29=lambda im: repaint(im, after(pages, 29, [r"Enterprise", r"Status"], 2), "Deregistration process")))
    add("82_csd_not_tax_compliant", "CSD overall tax status changed to 'Tax Non-Compliant'", "tax compliant",
        edit(pages, p25=lambda im: repaint(im, after(pages, 25, [r"Overall", r"Tax", r"Status"], 2), "Tax Non-Compliant")))
    add("83_csd_restricted_yes", "CSD 'Restricted Supplier' changed to Yes", "Restricted Supplier",
        edit(pages, p25=lambda im: repaint(im, after(pages, 25, [r"Restricted", r"Supplier"], 1), "Yes")))
    add("84_affidavit_level_wrong", "Affidavit level changed from One to Three (still 100% black owned)",
        "level does not follow", edit(pages, p24=lambda im: repaint(im, phrase(pages, 24, [r"Level", r"One"], pick=1), "Three")))
    add("85_affidavit_income_exceeded", "Affidavit says the income exceeded R10 million", "income exceeded",
        edit(pages, p24=lambda im: repaint(im, phrase(pages, 24, [r"did", r"not", r"exceed"]), "exceeded")))
    add("86_old_csd_report", "CSD report dated 27 Mar 2021 (about 200 days before closing)", "days old",
        edit(pages, p28=lambda im: repaint(im, phrase(pages, 28, [r"27", r"Sep", r"2021"]), "27 Mar 2021")))
    add("87_tcs_name_differs", "SARS page names the taxpayer 'Excel Trading Holdings'", "Company name differs",
        edit(pages, p23=lambda im: repaint(im, after(pages, 23, [r"Taxpayer", r"Name"], 3), "Excel Trading Holdings")))
    # --- changes drawn on the scan that ONLY a vision model can read (skipped unless one is configured; run with
    #     TENDERLENS_VISION_* set, see OLLAMA.md). No transcript is used, so the model reads everything itself.
    for k, (fn, page, what, where, expect) in enumerate(H.HAND_EDITS):
        add(f"{90 + k}_drawn_{fn.__name__}", f"{what} (drawn on the page)", expect,
            edit(pages, **{f"p{page}": fn}), vision=True)
    # --- negative control: not an RFQ requirement, so removing it must NOT raise an issue
    add("29_remove_cipc_certificate", "Removed pack page 29 (CIPC certificate: the RFQ does not ask for it)", None,
        drop(29))
    return V_


def make_transcript(base_path: Path, tx, new_pages, out: Path) -> Path:
    """The base handwriting transcript, with some readings replaced ('set') and some added ('add') for pages
    of the tampered pack (the reading is tied to that page's image hash, as every reading is)."""
    import json
    d = json.loads(base_path.read_text())
    for field, value in (tx or {}).get("set", {}).items():
        hit = [f for f in d["fields"] if f["field"] == field]
        assert hit, f"transcript has no field {field}"
        for f in hit:
            f["value"] = value
            f.pop("confidence", None)   # the simulated reader is sure of what it reports
    for field, value, page, extra in (tx or {}).get("add", []):
        d["fields"].append({"field": field, "value": value, "page": page, "page_sha": new_pages[page - 1].sha, **extra})
    out.write_text(json.dumps(d, indent=1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--keep", action="store_true", help="keep each variant's HTML report")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    pages = load_pdf(PACK, WORK)
    classify_pages(pages)
    rows = []
    from tenderlens.llm import LlamaClient
    vision_on = LlamaClient.for_vision(LlamaClient()).configured
    for name, what, expect, ops, known_miss, tx, needs_vision in variants(pages):
        if a.only and a.only not in name:
            continue
        if needs_vision and not vision_on:
            print(f"SKIP  {name:34s} needs a vision model (see OLLAMA.md)")
            continue
        d = OUT / name
        d.mkdir(parents=True, exist_ok=True)
        pdf = d / "pack.pdf"
        build(PACK, ops, pdf)
        t0 = time.time()
        tpath = None if needs_vision else TRANSCRIPT   # a vision run reads everything itself
        if tx:
            tpath = make_transcript(TRANSCRIPT, tx, load_pdf(pdf, WORK), d / "transcript.json")
        req, res, facts, pg = audit_pack(S / "rfq" / "rfq_email.pdf", S / "rfq" / "spec_0310.pdf", pdf, S / "blank",
                                         tpath, WORK)
        (d / "report.json").write_text(to_json(req, res))
        if a.keep:
            (d / "report.html").write_text(render_html(req, res, facts, pg, "Excel Supply Holdings", req.reference,
                                                       "rules only", redact=True))
        issues = [f for f in res.findings if f.tier == "issue"]
        obs = [f for f in res.findings if f.tier != "issue"]
        hay = lambda fs: [(f.title + " " + f.detail).lower() for f in fs]
        if expect is None:
            ok = not issues
        elif expect.startswith("obs:"):   # must be reported, but only as an observation (never an issue)
            key = expect[4:].lower()
            ok = any(key in h for h in hay(obs)) and not any(key in h for h in hay(issues))
        else:
            ok = any(expect.lower() in h for h in hay(issues))
        rows.append((name, what, expect, ok, issues, len(obs), known_miss, bool(tx)))
        print(f"{'PASS' if ok else ('MISS' if known_miss else 'FAIL')}  {name:34s} {len(issues)} issue(s) {time.time() - t0:4.0f}s")
        for f in issues:
            print(f"        - {f.title}")
    # summary
    lines = ["# TenderLens tamper tests", "",
             "Each row starts from the pack that won the RFQ and changes one thing. "
             "'Caught' means TenderLens raised an *issue* about it (observations do not count). Rows marked "
             "*(reading simulated)* change what the handwriting reader reports (a person's transcript here, Llama "
             "vision in real use): they test the rules that act on a reading, not the vision model's eyes.", "",
             "| # | What was done | Caught? | What TenderLens said |", "|---|---|---|---|"]
    for name, what, expect, ok, issues, nobs, known_miss, simulated in rows:
        said = "; ".join(f.title for f in issues[:3]) or "no issues"
        if expect and expect.startswith("obs:"):
            said = "(observation only) " + said
        if expect is None and name.startswith("00"):
            mark = "clean (as it should be)" if ok else "NOT CLEAN"
        elif expect is None:
            mark = "no issue (correct: not required)" if ok else "wrongly flagged"
        else:
            mark = "yes" if ok else ("**no (known limit)**" if known_miss else "**NO**")
        lines.append(f"| {name.split('_')[0]} | {what}{' *(reading simulated)*' if simulated else ''} | {mark} | {said} |")
    (OUT / "summary.md").write_text("\n".join(lines) + "\n")
    caught = sum(1 for r in rows if r[3])
    print(f"\n{caught}/{len(rows)} behaved as expected -> {OUT / 'summary.md'}")


if __name__ == "__main__":
    main()
