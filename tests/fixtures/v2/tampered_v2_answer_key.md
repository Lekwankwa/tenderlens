# Tampered pack v2: answer key

`tampered_pack_v2.pdf` is the pack that won, with every change below (the original is untouched). `tampered_pack_v2_missing_documents.pdf` is a second copy with whole documents and pages removed.

**Detected by:** *Rules (text)* = OCR and the rules can find it with no vision model. *(Vision model)* = it is handwriting or a mark on the scan, so a vision model has to read the page first. *Gap* rows are read by a vision model but no rule compares them yet, so no finding is expected.

**Run 1, rules only:** the real run with no vision model. **Run 2, vision readings simulated:** a transcript that reads the altered pages correctly, so it shows the rules WOULD raise the finding once a model has read it. Neither run shows how well the real vision model reads the page: that is the test you run next.

## A. Main pack

| # | What was changed | Where | Detected by | Run 1: rules only | Run 2: readings simulated |
|---|---|---|---|---|---|
| 1 | Quantity changed from 30 to 20 | Quotation, quantity | Rules (text) | yes | yes |
| 2 | Validity changed from 60 days to 20 days | Quotation, validity line | Rules (text) | yes | yes |
| 3 | RFQ reference changed to 5/2/1(0311)21/22 | Quotation, reference line | Rules (text) | yes | yes |
| 4 | Addressed to Tenders@dalrrd.gov.za instead of the RFQ address | Quotation, 'To' email | Rules (text) | yes | yes |
| 5 | Delivery address city changed to Polokwane | Quotation, delivery address | Rules (text) | yes | yes |
| 6 | Battery capacity changed from 105AH to 100AH | Quotation, item line | Rules (text) | yes | yes |
| 7 | Closing date on the quote changed to 2021/10/22 | Quotation, closing date | Rules (text) | yes | yes |
| 8 | CSD number on the quote changed (…0966851 to …0966951) | Quotation, header | Rules (text) | yes | yes |
| 9 | Signature removed from the quotation | Quotation, signature line | Rules (text) | yes | yes |
| 10 | Registration number changed (…534725… to …534752…) | B-BBEE affidavit | Rules (text) | yes | yes |
| 11 | SA ID number changed (…087 to …097) | B-BBEE affidavit | Rules (text) | yes | yes |
| 12 | Company name changed to EXCELLENT SUPPLIES | B-BBEE affidavit | Rules (text) | yes | yes |
| 13 | Tax reference changed (…1187 to …1178) | SARS tax compliance page | Rules (text) | yes | yes |
| 14 | Signature removed from SBD 9 | SBD 9, signing page | Rules (text) | yes | yes |
| 15 | Pen scribble added in the margin | SBD 4, page 2 | Rules (text) | yes | yes |
| 16 | Initials removed from original pages 3, 6, 11, 13, 19, 25 | Bottom-right corners | Rules (text) | yes | yes |
| 17 | CSD overall tax status changed to 'Tax Non-Compliant' | CSD report, page 1 | Rules (text) | yes | yes |
| 18 | CSD 'Restricted Supplier' changed from No to Yes | CSD report, page 1 | Rules (text) | yes | yes |
| 19 | Affidavit level changed from One to Three (still 100% black owned) | B-BBEE affidavit | Rules (text) | yes | yes |
| 20 | CSD report dated 27 Mar 2021 instead of 27 Sep 2021 (about 200 days old) | CSD report, page 4 | Rules (text) | yes | yes |
| 21 | SARS page names the taxpayer 'Excel Trading Holdings' | SARS tax compliance page | Rules (text) | yes | yes |
| 22 | SARS PIN 'Purpose of Request' changed from Tender to Other | SARS tax compliance page | Rules (text) | yes | yes |
| 23 | Affidavit says the income EXCEEDED R10 million | B-BBEE affidavit | Rules (text) | yes | yes |
| 24 | Unit price changed from R2410.8 to R2310.8 | Quotation, price per unit | Rules (text) | yes | yes |
| 25 | SBD 4 question 2.7 (state employee) changed from NO to YES | SBD 4, page 2 | (Vision model) | not run: needs a vision model | yes |
| 26 | SBD 4 question 2.9: YES circled as well as NO | SBD 4, page 2 | (Vision model) | not run: needs a vision model | yes |
| 27 | SBD 8 question 4.3 (convicted of fraud) ticked YES instead of NO | SBD 8, page 1 | (Vision model) | not run: needs a vision model | yes |
| 28 | SBD 8 date wiped out | SBD 8, signing page | (Vision model) | not run: needs a vision model | yes |
| 29 | SBD 9 dated 2021/10/20, after the closing date | SBD 9, signing page | (Vision model) | not run: needs a vision model | yes |
| 30 | SBD 6.1 'years in business' changed from 1 to 6 | SBD 6.1, page 4 | (Vision model) | not run: needs a vision model | yes |
| 31 | Quotation signed in the name of a different person (Thabo Mokoena) | Quotation, name line | (Vision model) | not run: needs a vision model | yes |
| 32 | Commissioner's stamp and signature removed from the affidavit | B-BBEE affidavit | (Vision model) | not run: needs a vision model | yes |
| 33 | Pen line struck through a printed paragraph | SBD 8, page 1 | (Vision model) | not run: needs a vision model | yes |
| 34 | Correction fluid and a new word written over 'N/A' | SBD 8, page 1 | (Vision model) | not run: needs a vision model | yes |
| 35 | A handwritten note: 'price to be confirmed later' | SBD 4, page 2 | (Vision model) | not run: needs a vision model | yes |
| 36 | SBD 6.1 sub-contracting ticked YES instead of NO | SBD 6.1, page 3 | (Vision model) | not run: needs a vision model | yes |
| 37 | SBD 6.1 points claimed written as 10 (the B-BBEE level gives 20) | SBD 6.1, page 3 | (Vision model) | not run: needs a vision model | yes |
| 38 | SBD 6.1 registration number written 2020/534752/07 | SBD 6.1, page 4 | (Vision model) | not run: needs a vision model | yes |
| 39 | SBD 6.1 company type moved from Company to Close corporation (the CIPC certificate says Private Company) | SBD 6.1, page 4 | (Vision model), but no rule acts on it yet | n/a: no rule compares this yet | n/a: no rule compares this yet |
| 40 | SBD 6.1 second witness signature removed | SBD 6.1, page 5 | (Vision model) | not run: needs a vision model | yes |
| 41 | SBD 6.1 dated 2021/10/01, the other forms say 2021/10/06 | SBD 6.1, page 5 | (Vision model) | not run: needs a vision model | yes |
| 42 | SBD 6.2 paragraph 4: imported content ticked YES, but the declaration says R0 imported | SBD 6.2, page 3 | (Vision model) | not run: needs a vision model | yes |
| 43 | Local content declaration: bid price written R70324, the quote says R72324 | Local content declaration, page 1 | (Vision model) | not run: needs a vision model | yes |
| 44 | Local content declaration: local content written 40% with R0 imported | Local content declaration, page 1 | (Vision model) | not run: needs a vision model | yes |
| 45 | Local content declaration: witness 1 signature removed, witness 2 dated 2021/10/09 | Local content declaration, page 2 | (Vision model) | not run: needs a vision model | yes |
| 46 | SBD 4 identity number written ...078 (every other page says ...087) | SBD 4, page 1 | (Vision model) | not run: needs a vision model | yes |
| 47 | SBD 4 tax reference written ...178 (the SARS page says ...187) | SBD 4, page 1 | (Vision model) | not run: needs a vision model | yes |
| 48 | SBD 4 question 2.10: neither YES nor NO marked | SBD 4, page 3 | (Vision model) | not run: needs a vision model | yes |
| 49 | SBD 4 question 2.11 answered YES instead of NO | SBD 4, page 3 | (Vision model) | not run: needs a vision model | yes |
| 50 | SBD 4 dated 2021/10/01, the other forms say 2021/10/06 | SBD 4, signing page | (Vision model) | not run: needs a vision model | yes |
| 51 | SBD 4 signature replaced by a visibly different signature | SBD 4, signing page | (Vision model) | not run: needs a vision model | yes |
| 52 | SBD 8 question 4.1 (restricted supplier) ticked YES | SBD 8, page 1 | (Vision model) | not run: needs a vision model | yes |
| 53 | SBD 8 question 4.2 (tender defaulter) ticked YES | SBD 8, page 1 | (Vision model) | not run: needs a vision model | yes |
| 54 | SBD 8 question 4.4 (contract terminated) ticked YES | SBD 8, page 2 | (Vision model) | not run: needs a vision model | yes |
| 55 | SBD 8 declarant's name wiped | SBD 8, signing page | (Vision model) | not run: needs a vision model | yes |
| 56 | Page scanned upside down | SBD 6.2, page 2 | (Vision model) | not run: needs a vision model | yes |
| 57 | Bottom quarter of the page cut off | Local content declaration, page 2 | (Vision model) | not run: needs a vision model | yes |
| 58 | SBD 4: its first two pages swapped | Pack pages 13 and 14 | Rules (text) | yes | yes |
| 59 | CSD report: its middle pages swapped | Pack pages 26 and 27 | Rules (text) | yes | yes |
| 60 | A blank sheet slipped in after original page 9 | Between SBD 6.2 pages | Rules (text) | yes | yes |
| 61 | Original page 4 (SBD 6.1, page 2) copied in twice | SBD 6.1 | Rules (text) | yes | yes |
| 62 | Page removed: SBD 9, second page | Original page 20 | Rules (text) | yes | yes |

**62 changes: 29 Rules (text), 32 (Vision model), 1 gap (a vision model can read it, but no rule acts on it yet).** Run 1 caught 29 of 29 Rules (text) changes. Run 2 caught 61 of 61 changes in total.

Issues raised in run 1 (rules only):

- Quotation may be unsigned
- SBD 9 may be unsigned
- Quantity on the quote differs from the specification
- Quote total does not equal quantity x unit price
- Quote reference differs from the RFQ
- Quote is addressed to a different email than the RFQ's
- Quote validity shorter than required
- CSD report lists the supplier as a Restricted Supplier
- CSD report does not show the supplier as tax compliant
- The affidavit says the income exceeded R10 million
- SBD 6.1: 6 pages found, the standard form has 5
- SBD 9: 3 of 4 pages found
- Pack page 11 is blank
- The same page appears more than once (pack pages [4, 5])
- SBD 4: pages are out of order
- CSD summary report: pages are out of order
- Company registration number differs between documents
- CSD supplier number differs between documents
- Tax reference number differs between documents
- SA ID number differs between documents
- Company name differs between documents
- Closing date on the quote differs from the RFQ
- Item description on the quote does not match the specification
- Delivery address on the quote differs from the specification
- SARS TCS PIN purpose is not 'Tender'
- The CSD report is 202 days old at closing
- The affidavit's level does not follow from its ownership
- The affidavit's recognition percentage does not match its level
- Unexplained mark on SBD 4 (page 15)
- Initials missing on 8 page(s) that the other pages carry

Issues raised in run 2 (readings simulated):

- Quotation may be unsigned
- SBD 9 may be unsigned
- SBD 4: question 2.10 is blank
- SBD 8: question 4.1 is answered Yes
- SBD 8: question 4.2 is answered Yes
- SBD 8: question 4.3 is answered Yes
- SBD 8: question 4.4 is answered Yes
- B-BBEE sworn affidavit: no official stamp
- B-BBEE sworn affidavit: no commissioner's signature
- Local content percentage does not follow from the declared figures
- Quantity on the quote differs from the specification
- Quote total does not equal quantity x unit price
- Bid price differs between quote and local content declaration
- Quote reference differs from the RFQ
- Quote is addressed to a different email than the RFQ's
- Quote validity shorter than required
- SBD 9 is dated after the closing date
- CSD report lists the supplier as a Restricted Supplier
- CSD report does not show the supplier as tax compliant
- The affidavit says the income exceeded R10 million
- SBD 6.1: 6 pages found, the standard form has 5
- SBD 9: 3 of 4 pages found
- Pack page 11 is blank
- The same page appears more than once (pack pages [4, 5])
- SBD 4: pages are out of order
- CSD summary report: pages are out of order
- SBD 6.1: subcontracting answer is answered Yes
- SBD 6.1: only 1 witness signature(s)
- SBD 6.2: imported-content answer is answered Yes
- SBD 4: question 2.7 is answered Yes
- SBD 4: both YES and NO are marked for the question 2.9
- SBD 4: question 2.11 is answered Yes
- SBD 8: declarant is blank
- SBD 8: date is blank
- The person who signed differs between documents
- Printed text crossed out on SBD 8 (page 19)
- Entry overwritten or covered on SBD 8 (page 19)
- Handwritten note outside the form fields on SBD 4 (page 15)
- Page cut off at the edge on Local content declaration (page 14)
- Points claimed do not match the B-BBEE level
- Company registration number differs between documents
- CSD supplier number differs between documents
- Tax reference number differs between documents
- SA ID number differs between documents
- Company name differs between documents
- Closing date on the quote differs from the RFQ
- Item description on the quote does not match the specification
- Delivery address on the quote differs from the specification
- Forms are signed on different dates
- SARS TCS PIN purpose is not 'Tender'
- SBD 6.1 'years in business' is more than the company's age
- The CSD report is 202 days old at closing
- The affidavit's level does not follow from its ownership
- The affidavit's recognition percentage does not match its level
- Unexplained mark on SBD 4 (page 15)
- Initials missing on 8 page(s) that the other pages carry
- Page rotated on SBD 6.2 (page 10)

## B. Companion pack: documents and pages missing, and an altered CIPC certificate

| # | What was removed | Detected by | Caught? |
|---|---|---|---|
| B1 | Quotation (pack page 1) | Rules (text) | yes |
| B2 | SBD 6.1, page 3 of 5 | Rules (text) | yes |
| B3 | SBD 4 signing page | Rules (text) | yes |
| B4 | SBD 8 signing page | Rules (text) | yes |
| B5 | SARS tax compliance status (PIN) page | Rules (text) | yes |
| B6 | B-BBEE sworn affidavit | Rules (text) | yes |
| B7 | CSD report: B-BBEE and tax section | Rules (text) | yes |
| B8 | CIPC certificate: status changed from 'In Business' to 'Deregistration process' | Rules (text) | yes |

**8 of 8 caught.** (The CIPC change is here and not in the main pack because the 'years in business' check reads the CIPC registration date, and an altered CIPC page no longer reads it.)

## What no single pack can show

- A discrepancy whose finding is the same as another row's (for example a handwritten registration number and the typed one on the affidavit both give *Company registration number differs*): open the report and check that the evidence names the SBD page, not only the affidavit.
- A commissioner's stamp dated after the closing date, a stamp that is present but forged, and other authenticity questions are for a person: TenderLens reports what is on the page, not whether it is genuine.
- Rows 'Detected by (Vision model)' that the real model reads wrongly will be missing from, or wrong in, the report. That is what the real run measures.

## Run it with the vision model (DeepInfra)

Follow `DEEPINFRA_PROMPT.md`, then:

```
python -m tenderlens check-llm
python -m tenderlens eval-vision
python -m tenderlens run --rfq-email samples/rfq/rfq_email.pdf --spec samples/rfq/spec_0310.pdf \
    --pack tests/fixtures/v2/tampered_pack_v2.pdf --blank samples/blank --out out/v2
```

Leave out `--transcript`. For each row marked *(Vision model)*, tick **found as an issue**, **found as an observation** or **missed**, and write the real counts in the table. A single reading counts as an issue only if a second, differently worded look agrees; otherwise it appears under observations.
