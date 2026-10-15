"""What a bidder writes BY HAND, form by form.

One table drives three things: the questions a vision model is asked (``desc``), how its answer is cleaned up
(``kind``), and which entries the audit expects to be filled in (``required``). Nothing here depends on which
model reads the page, so the same rules apply to Llama vision, to a person's transcript, or to a test script.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

YESNO, TEXT, DATE, NUMBER, CHOICE, COUNT, BOOL = "yesno", "text", "date", "number", "choice", "count", "bool"

NOT_ON_PAGE = object()  # the model says the field is not on this page: not an answer, not a blank
UNREAD = object()       # the model could not read it
BLANK = None            # the field is on the page and nothing was written in it


@dataclass(frozen=True)
class Entry:
    key: str
    desc: str
    kind: str
    required: bool = True
    expect: str | None = None   # for yes/no questions: the answer a clean bid normally gives
    applies: str = ""           # "local_content" = only when local content applies to the bid
    label: str = ""             # short human name used in findings
    where: str = ""             # "last" = lives on the form's signing page (used to cite the right page for a blank)


def E(key, desc, kind, label, required=True, expect=None, applies="", where=""):
    if not where and (key.endswith((".date", ".declarant", ".signatory", ".signed_capacity", ".capacity",
                                     ".witness_signatures", ".commissioned", ".deponent_signed", ".commissioner_signed"))):
        where = "last"
    return Entry(key, desc, kind, required, expect, applies, label, where)


FORMS: dict[str, list[Entry]] = {
    "quote": [
        E("quote.signatory", "Name of the person who signed the quotation (printed or handwritten next to 'Name')",
          TEXT, "name of the signatory"),
        E("quote.signed_capacity", "Capacity of the signatory, e.g. Director or Owner", TEXT, "capacity of the signatory"),
        E("quote.date", "Date written by hand beside the signature at the bottom of the quotation", DATE, "date"),
    ],
    "sbd6_1": [
        E("sbd6_1.points_claimed", "Number of B-BBEE points claimed in section 7.1 (a number, 'maximum of 10 or 20 points')",
          NUMBER, "points claimed"),
        E("sbd6_1.subcontracting", "Section 4: will any part of the contract be subcontracted? Which box is ticked, YES or NO",
          YESNO, "subcontracting answer"),
        E("sbd6_1.company_type_ticked", "Section 8.5: which company type is ticked (e.g. Company, CC, Partnership, Sole proprietor)",
          CHOICE, "company type"),
        E("sbd6_1.classification_ticked", "Section 8.6: which company classification is ticked (Manufacturer, Supplier, Professional service provider, Other)",
          CHOICE, "company classification"),
        E("sbd6_1.years_in_business", "Total number of years the company has been in business", TEXT, "years in business"),
        E("sbd6_1.principal_activity", "Principal business activities written in section 8.3", TEXT, "principal activity",
          required=False),
        E("sbd6_1.company_name", "Company name written on the form", TEXT, "company name"),
        E("sbd6_1.vat_no", "VAT registration number (or N/A)", TEXT, "VAT number", required=False),
        E("sbd6_1.reg_no", "Company registration number written on the form", TEXT, "registration number"),
        E("sbd6_1.date", "Date written beside the signature at the end of the form", DATE, "date"),
        E("sbd6_1.witness_signatures", "How many witness signatures are on the form (0, 1 or 2)", COUNT, "witness signatures"),
    ],
    "sbd6_2": [
        E("sbd6_2.description", "Description of the goods in the table (section 3)", TEXT, "item description", required=False),
        E("sbd6_2.min_threshold_pct", "Stipulated minimum threshold for local content (%)", NUMBER, "minimum threshold",
          required=False),
        E("sbd6_2.has_imported_content", "Paragraph 4: does the bid include imported content? YES or NO", YESNO,
          "imported-content answer", required=False),
        E("sbd6_2.templates_audited_certified", "Paragraph 5: were the local content templates audited and certified? YES or NO",
          YESNO, "templates-audited answer", required=False),
    ],
    "local_content_decl": [
        E("local_content_decl.bid_no", "Bid number written on the declaration", TEXT, "bid number", applies="local_content"),
        E("local_content_decl.bidder", "Name of the bidder / enterprise written on the declaration", TEXT, "bidder name",
          applies="local_content"),
        E("local_content_decl.declarant", "Name of the person signing the declaration", TEXT, "declarant",
          applies="local_content"),
        E("local_content_decl.capacity", "Capacity of the person signing, e.g. Director", TEXT, "capacity",
          applies="local_content"),
        E("local_content_decl.bid_price_excl_vat", "Bid price excluding VAT written in the declaration, in rand", NUMBER,
          "bid price", applies="local_content"),
        E("local_content_decl.imported_content", "Imported content in rand written in the declaration", NUMBER,
          "imported content", applies="local_content"),
        E("local_content_decl.local_content_pct", "Local content percentage written in the declaration", NUMBER,
          "local content %", applies="local_content"),
        E("local_content_decl.date", "Date written beside the signature", DATE, "date", applies="local_content"),
        E("local_content_decl.witness_signatures", "How many witness signatures are on the declaration (0, 1 or 2)", COUNT,
          "witness signatures", applies="local_content"),
    ],
    "sbd4": [
        E("sbd4.name_field", "Question 2.1: full name of the bidder or representative", TEXT, "bidder name"),
        E("sbd4.id_no", "Question 2.2: identity number written on the form (13 digits)", TEXT, "identity number"),
        E("sbd4.position", "Question 2.3: position held in the company", TEXT, "position"),
        E("sbd4.reg_no", "Question 2.4: company registration number", TEXT, "registration number"),
        E("sbd4.tax_no", "Question 2.5: tax reference number (10 digits)", TEXT, "tax reference number"),
        E("sbd4.vat_no", "VAT registration number (or N/A)", TEXT, "VAT number", required=False),
        E("sbd4.q2_7_state_employed", "Question 2.7: are you or any person connected with the bidder presently employed by the state? Which box is ticked, YES or NO",
          YESNO, "question 2.7", expect="No"),
        E("sbd4.q2_7_2_outside_work_authority", "Question 2.7.2: if employed by the state, was authority to do remunerative work outside the employment obtained? YES, NO or not answered",
          YESNO, "question 2.7.2", required=False),
        E("sbd4.q2_8_business_with_state_12m", "Question 2.8: did you or your spouse or any director do business with the state in the previous twelve months? YES or NO",
          YESNO, "question 2.8", expect="No"),
        E("sbd4.q2_9_relationship_with_evaluator", "Question 2.9: any relationship with a person in the service of the state who may evaluate or adjudicate the bid? YES or NO",
          YESNO, "question 2.9", expect="No"),
        E("sbd4.q2_10_aware_of_other_bidder_relationship", "Question 2.10: aware of a relationship between any other bidder and a state employee? YES or NO",
          YESNO, "question 2.10", expect="No"),
        E("sbd4.q2_11_interest_in_related_companies", "Question 2.11: do you or any director have interest in any other related companies bidding? YES or NO",
          YESNO, "question 2.11", expect="No"),
        E("sbd4.director_table_name", "Section 3 table: the name of the first director listed", TEXT, "director table", required=False),
        E("sbd4.declarant", "Name of the person who signs the declaration (section 4 certification)", TEXT, "declarant"),
        E("sbd4.date", "Date written beside the signature at the end of the form", DATE, "date"),
    ],
    "sbd8": [
        E("sbd8.q4_1_restricted_supplier", "Question 4.1: bidder or director on the Database of Restricted Suppliers? Which box is ticked, YES or NO",
          YESNO, "question 4.1", expect="No"),
        E("sbd8.q4_2_tender_defaulter", "Question 4.2: bidder or director on the Register for Tender Defaulters? YES or NO",
          YESNO, "question 4.2", expect="No"),
        E("sbd8.q4_3_convicted", "Question 4.3: bidder or director convicted of fraud or corruption in the past five years? YES or NO",
          YESNO, "question 4.3", expect="No"),
        E("sbd8.q4_4_contract_terminated", "Question 4.4: any contract with an organ of state terminated in the past five years for failure to perform? YES or NO",
          YESNO, "question 4.4", expect="No"),
        E("sbd8.declarant", "Name of the person who signs the certification", TEXT, "declarant"),
        E("sbd8.date", "Date written beside the signature", DATE, "date"),
    ],
    "sbd9": [
        E("sbd9.bid_description", "Bid number and description written in the certificate", TEXT, "bid description"),
        E("sbd9.institution", "Name of the institution written in the certificate", TEXT, "institution"),
        E("sbd9.bidder", "Name of the bidder written in the certificate", TEXT, "bidder name"),
        E("sbd9.date", "Date written beside the signature", DATE, "date"),
    ],
    "tcs": [
        E("tcs.pin_expiry", "PIN Expiry Date as printed (yyyy/mm/dd or dd/mm/yyyy)", DATE, "PIN expiry date", required=False),
        E("tcs.issue_date", "Issue Date as printed at the top of the letter", DATE, "issue date", required=False),
    ],
    "bbbee_affidavit": [
        E("bbbee_affidavit.commissioned", "Date on the Commissioner of Oaths' stamp or written beside it", DATE,
          "date of commissioning"),
        E("bbbee_affidavit.stamp_present", "Is there a Commissioner of Oaths / police / community service centre stamp on the page? YES or NO",
          YESNO, "official stamp", expect="Yes"),
        E("bbbee_affidavit.commissioner_signed", "Has the Commissioner of Oaths signed (a pen signature above 'Commissioner of Oaths Signature')? YES or NO",
          YESNO, "commissioner's signature", expect="Yes"),
        E("bbbee_affidavit.deponent_signed", "Has the deponent signed (a pen signature above 'Deponent Signature')? YES or NO",
          YESNO, "deponent's signature", expect="Yes"),
    ],
}

# What a vision model is asked about EVERY handwritten page, whatever the form. A "yes" is only a lead:
# it is repeated with a second, differently worded question before it counts as an issue.
INSPECTIONS: dict[str, tuple[str, str, str]] = {
    # key: (question, second-look question, what it is called in a finding)
    "crossed_out": ("Is any printed text on this page crossed out or struck through with a pen line?",
                    "Look for a hand-drawn line running through printed words. Is printed text deleted by hand?",
                    "Printed text crossed out"),
    "overwritten": ("Has any entry been scratched out, written over, or covered with correction fluid or tape?",
                    "Is there any correction: an entry written over another, white-out, or a patch?",
                    "Entry overwritten or covered"),
    "annotation": ("Is there handwriting that is NOT an answer in a form field: a note, comment, drawing or doodle in a margin or between paragraphs?",
                   "Apart from the expected entries (names, dates, ticks, signatures), is there any other handwritten note?",
                   "Handwritten note outside the form fields"),
    "rotated": ("Is the page upside down or turned sideways?", "Is the text on this page rotated, not upright?",
                "Page rotated"),
    "cut_off": ("Is any of the printed page cut off at an edge, so that text or a form field is missing?",
                "Does text or a box run off the edge of the scan?", "Page cut off at the edge"),
    "illegible": ("Is any part of the page too faint, dark or blurred to read?",
                  "Are there areas where the writing cannot be read because of scan quality?", "Part of the page unreadable"),
}
INSPECTION_FORMS = ("quote", "sbd6_1", "sbd6_2", "local_content_decl", "sbd4", "sbd8", "sbd9", "bbbee_affidavit", "tcs")
SIGNING_PAGE = {"quote": -1, "sbd6_1": -1, "local_content_decl": -1, "sbd4": -1, "sbd8": -1, "sbd9": -1,
                "bbbee_affidavit": 0}

# --------------------------------------------------------------------------- cleaning what a reader returned

_BLANK_WORDS = {"blank", "empty", "nothing", "not filled", "not filled in", "unfilled", "none written", "left blank",
                "no entry", "not answered", "unanswered", "neither", "no tick", "not ticked", "no box ticked"}
_UNREAD_WORDS = {"unreadable", "illegible", "cannot read", "can't read", "unclear", "not legible", "?"}
_NA_WORDS = {"n/a on page", "not on this page", "not on page", "not visible", "absent from page", "null", "none", ""}

_FMT = ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%d/%m/%y",
        "%d-%m-%y", "%Y%m%d")


def parse_date(s) -> date | None:
    from datetime import datetime
    s = re.sub(r"\s+", " ", str(s)).strip().strip(".")
    for f in _FMT:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            pass
    return None


def clean(kind: str, raw):
    """Turn whatever a reader returned into BLANK / UNREAD / NOT_ON_PAGE or a tidy value.
    Yes/No answers become exactly 'Yes', 'No' or 'Both'; dates become ISO strings when they parse (and stay as
    written when they do not, so the audit can say the date is not a valid date)."""
    if raw is None:
        return NOT_ON_PAGE
    if isinstance(raw, bool):
        return ("Yes" if raw else "No") if kind == YESNO else raw
    if kind == BOOL:
        low = str(raw).strip().lower()
        return True if low in ("yes", "true", "y", "1") else False if low in ("no", "false", "n", "0") else UNREAD
    s = str(raw).strip()
    low = s.lower()
    if low in _NA_WORDS or (low in ("n/a", "na") and kind != TEXT):  # "N/A" is a real answer only for a text field
        return NOT_ON_PAGE
    if low in _UNREAD_WORDS:
        return UNREAD
    if low in _BLANK_WORDS:
        return BLANK
    if kind == YESNO:
        if re.search(r"\bboth\b|yes\s*(and|&|/)\s*no|no\s*(and|&|/)\s*yes", low):
            return "Both"
        if re.match(r"^(yes|y|ticked yes|yes ticked|✓ ?yes|✔ ?yes)\b", low):
            return "Yes"
        if re.match(r"^(no|n|ticked no|no ticked|✓ ?no|✔ ?no)\b", low):
            return "No"
        return UNREAD
    if kind in (NUMBER, COUNT):
        m = re.search(r"-?\d[\d\s,]*\.?\d*", s)
        if not m:
            return BLANK if low in ("none", "zero") and kind == COUNT else s
        txt = re.sub(r"[\s,]", "", m.group(0))
        v = float(txt)
        return int(v) if v == int(v) else v
    if kind == DATE:
        d = parse_date(s)
        return d.isoformat() if d else s
    return s


def is_deviation(entry: Entry, value) -> bool:
    """A reading worth a second look before it is allowed to become an issue."""
    if value is BLANK:
        return entry.required
    if entry.kind == YESNO:
        return value == "Both" or (entry.expect is not None and value != entry.expect) \
            or (entry.expect is None and value == "Yes")
    if entry.kind == DATE:
        return parse_date(value) is None
    if entry.kind == COUNT:
        return isinstance(value, (int, float)) and value < 2 and entry.key.endswith("witness_signatures")
    return False
