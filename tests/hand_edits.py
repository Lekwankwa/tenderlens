"""Handwritten-looking changes to specific pages of the winning pack (coordinates are for pack.pdf at 150 dpi).

Each function takes a PIL page image and returns it changed. They exist so a tampered pack can carry changes that only
a vision model can read: a tick moved from NO to YES, a date wiped out, a stamp removed, a strike-through.
"""
from __future__ import annotations

from PIL import ImageDraw, ImageFont

import pen


def q27_yes(im):                       # SBD 4 (pack page 14): question 2.7 answered YES instead of NO
    pen.clear(im, (786, 348, 852, 412))
    ImageDraw.Draw(im).text((788, 362), "/ NO", font=ImageFont.truetype(pen.BOLD, 24), fill=(30, 30, 30))
    return pen.circle(im, (733, 356, 794, 392), seed=4)


def q29_both(im):                      # SBD 4 (page 14): question 2.9 has YES circled as well as NO
    return pen.circle(im, (781, 1503, 846, 1543), seed=5)


def q43_yes(im):                       # SBD 8 (page 17): question 4.3 ticked YES instead of NO
    pen.clear(im, (1106, 1418, 1144, 1458))
    ImageDraw.Draw(im).rectangle((1116, 1428, 1135, 1447), outline=(40, 40, 40), width=2)
    return pen.cross(im, (1043, 1430, 1062, 1450), seed=6)


def sbd8_date_blank(im):               # SBD 8 (page 18): the date wiped out
    pen.clear(im, (715, 648, 1050, 700))
    return pen.dots(im, 780, 1042, 686)


def sbd9_date_late(im):                # SBD 9 (page 22): dated 2021/10/20, after the closing date
    pen.clear(im, (765, 690, 1080, 742))
    pen.dots(im, 775, 1068, 731)
    return pen.hand_text(im, (790, 688), "2021/10/20", size=38, seed=2)


def years_six(im):                     # SBD 6.1 (page 6): "1 year" becomes "6 years"
    pen.clear(im, (392, 1328, 530, 1360))
    return pen.hand_text(im, (398, 1330), "6 years", size=30, seed=3)


def other_signatory(im):               # Quotation (page 1): signed by someone else
    pen.clear(im, (205, 1392, 965, 1440))
    return pen.hand_text(im, (215, 1396), "Thabo Mokoena", size=44, seed=7)


def affidavit_unstamped(im):           # Affidavit (page 24): commissioner's stamp and signature removed
    pen.clear(im, (836, 1185, 1180, 1388))
    return pen.clear(im, (590, 1395, 1150, 1580))


def strike_through(im):                # SBD 8 (page 17): a pen line through "abused the institution's supply chain..."
    return pen.strike(im, (322, 578), (905, 548), seed=8)


def correction_fluid(im):              # SBD 8 (page 17): the "N/A" under 4.1.1 covered with fluid and written over
    pen.clear(im, (498, 1090, 700, 1162))
    return pen.hand_text(im, (520, 1100), "none", size=42, seed=9)


def margin_note(im):                   # SBD 4 (page 14): a note nobody asked for
    return pen.hand_text(im, (160, 108), "price to be confirmed later", size=38, seed=11)


# what each does, for the answer keys: (function, pack page, description, where, what a finding should mention)
HAND_EDITS = [
    (q27_yes, 14, "SBD 4 question 2.7 (state employee) changed from NO to YES", "SBD 4, page 2", "question 2.7"),
    (q29_both, 14, "SBD 4 question 2.9: YES circled as well as NO", "SBD 4, page 2", "both YES and NO"),
    (q43_yes, 17, "SBD 8 question 4.3 (convicted of fraud) ticked YES instead of NO", "SBD 8, page 1", "question 4.3"),
    (sbd8_date_blank, 18, "SBD 8 date wiped out", "SBD 8, signing page", "date is blank"),
    (sbd9_date_late, 22, "SBD 9 dated 2021/10/20, after the closing date", "SBD 9, signing page", "after the closing date"),
    (years_six, 6, "SBD 6.1 'years in business' changed from 1 to 6", "SBD 6.1, page 4", "years in business"),
    (other_signatory, 1, "Quotation signed in the name of a different person (Thabo Mokoena)", "Quotation, name line",
     "person who signed differs"),
    (affidavit_unstamped, 24, "Commissioner's stamp and signature removed from the affidavit", "B-BBEE affidavit",
     "official stamp"),
    (strike_through, 17, "Pen line struck through a printed paragraph", "SBD 8, page 1", "crossed out"),
    (correction_fluid, 17, "Correction fluid and a new word written over 'N/A'", "SBD 8, page 1", "overwritten"),
    (margin_note, 14, "A handwritten note: 'price to be confirmed later'", "SBD 4, page 2", "note outside the form fields"),
]
