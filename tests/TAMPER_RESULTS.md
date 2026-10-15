# TenderLens tamper tests

Each row starts from the pack that won the RFQ and changes one thing. 'Caught' means TenderLens raised an *issue* about it (observations do not count). Rows marked *(reading simulated)* change what the handwriting reader reports (a person's transcript here, Llama vision in real use): they test the rules that act on a reading, not the vision model's eyes.

| # | What was done | Caught? | What TenderLens said |
|---|---|---|---|
| 00 | Untouched pack that won the RFQ | clean (as it should be) | no issues |
| 01 | Removed pack page 18 (SBD 8 certification and signature) | yes | SBD 8: 1 of 2 pages found; Could not locate the signature line on SBD 8 |
| 02 | Removed pack page 23 (SARS tax compliance status) | yes | Missing: Tax compliance (TCC / SARS TCS PIN) |
| 03 | Removed pack page 24 (B-BBEE sworn affidavit) | yes | Missing: B-BBEE certificate or sworn affidavit |
| 04 | Removed pack page 16 (SBD 4 signing page) | yes | SBD 4: 3 of 4 pages found; Could not locate the signature line on SBD 4 |
| 05 | Removed pack page 20 (SBD 9, second page) | yes | SBD 9: 3 of 4 pages found |
| 06 | Removed pack page 28 (CSD report: B-BBEE and tax section) | yes | The CSD summary report is missing a section |
| 07 | Removed pack page 1 (the quotation) | yes | Missing: the quotation |
| 08 | Removed pack page 5 (SBD 6.1, page 3 of 5) | yes | SBD 6.1: 4 of 5 pages found |
| 09 | Inserted a second copy of pack page 14 (SBD 4) | yes | SBD 4: 5 pages found, the standard form has 4; The same page appears more than once (pack pages [14, 15]) |
| 10 | Quote TOTAL changed from R72 324 to R70 324 | yes | Quote total does not equal quantity x unit price; Bid price differs between quote and local content declaration |
| 11 | Unit price changed from R2410.8 to R2310.8 | yes | Quote total does not equal quantity x unit price |
| 12 | Quantity changed from 30 to 20 | yes | Quantity on the quote differs from the specification; Quote total does not equal quantity x unit price |
| 13 | Validity changed from 60 days to 20 days | yes | Quote validity shorter than required |
| 14 | RFQ reference changed from 5/2/1(0310)21/22 to 5/2/1(0311)21/22 | yes | Quote reference differs from the RFQ |
| 15 | Addressed to Tenders@dalrrd.gov.za instead of the RFQ's address | yes | Quote is addressed to a different email than the RFQ's |
| 16 | Delivery address changed to Polokwane | yes | Delivery address on the quote differs from the specification |
| 17 | Battery capacity changed from 105AH to 100AH | yes | Item description on the quote does not match the specification |
| 18 | Closing date on the quote changed to 2021/10/22 | yes | Closing date on the quote differs from the RFQ |
| 19 | Company registration number on the affidavit changed (…534725… to …534752…) | yes | Company registration number differs between documents |
| 20 | CSD number on the quote changed (MAAA0966851 to MAAA0966951) | yes | CSD supplier number differs between documents |
| 21 | SA ID on the affidavit changed (…087 to …097) | yes | SA ID number differs between documents |
| 22 | Tax reference on the SARS page changed (…1187 to …1178) | yes | Tax reference number differs between documents; Company name differs between documents |
| 23 | Company name on the affidavit changed to EXCELLENT SUPPLIES | yes | Company name differs between documents |
| 24 | Signature removed from the quotation | yes | Quotation may be unsigned |
| 25 | Signature removed from SBD 9 | yes | SBD 9 may be unsigned |
| 26 | Added a pen scribble in the margin of SBD 4 (pack page 14) | yes | Unexplained mark on SBD 4 (page 14) |
| 27 | Hand-drawn strike-through across a printed heading on SBD 8 (page 17) | **no (known limit)** | no issues |
| 28 | Initials removed from pack page 10 | yes | Initials missing on 1 page(s) that the other pages carry |
| 34 | Initials removed from 8 pages (pack pages 3, 5, 8, 11, 13, 17, 19, 21) | yes | Initials missing on 8 page(s) that the other pages carry |
| 30 | Inserted a blank page inside SBD 4 (after pack page 14) | yes | Pack page 15 is blank |
| 31 | Inserted a blank page between SBD 9 and the SARS page (after page 22) | yes | Pack page 23 is blank |
| 32 | Inserted a blank page at the very end of the pack | yes | Pack page 30 is blank |
| 33 | Inserted a blank page after the quotation (after page 1) | yes | Pack page 2 is blank |
| 40 | SBD 4 question 2.7 (state employee) read as YES *(reading simulated)* | yes | SBD 4: question 2.7 is answered Yes |
| 41 | SBD 8 question 4.3 (convicted of fraud) read as YES *(reading simulated)* | yes | SBD 8: question 4.3 is answered Yes |
| 42 | SBD 8 question 4.1 (restricted supplier) read as YES *(reading simulated)* | yes | SBD 8: question 4.1 is answered Yes |
| 43 | SBD 4 date left blank *(reading simulated)* | yes | SBD 4: date is blank |
| 44 | SBD 8 declarant's name left blank *(reading simulated)* | yes | SBD 8: declarant is blank |
| 45 | SBD 4 question 2.9 has both YES and NO marked *(reading simulated)* | yes | SBD 4: both YES and NO are marked for the question 2.9 |
| 46 | SBD 4 question 2.10 has neither box ticked *(reading simulated)* | yes | SBD 4: question 2.10 is blank |
| 47 | Quotation signed by a different person (Thabo Mokoena) *(reading simulated)* | yes | The person who signed differs between documents |
| 48 | SBD 6.1 claims 10 points although the affidavit gives Level 1 (20 points) *(reading simulated)* | yes | Points claimed do not match the B-BBEE level |
| 49 | SBD 6.1 says 6 years in business; the company was registered in 2020 *(reading simulated)* | yes | SBD 6.1 'years in business' is more than the company's age |
| 50 | SBD 9 dated 2021-10-20, after the closing date *(reading simulated)* | yes | SBD 9 is dated after the closing date; Forms are signed on different dates |
| 51 | SBD 4 dated 2021-10-01 while the other forms say 2021-10-06 *(reading simulated)* | yes | Forms are signed on different dates |
| 52 | SBD 8 date written as 31/02/2021 *(reading simulated)* | yes | SBD 8: the date is not a valid date |
| 53 | SBD 6.1 subcontracting answered YES *(reading simulated)* | yes | SBD 6.1: subcontracting answer is answered Yes |
| 54 | SBD 6.1 has one witness signature instead of two *(reading simulated)* | yes | SBD 6.1: only 1 witness signature(s) |
| 55 | Affidavit has no commissioner's stamp *(reading simulated)* | yes | B-BBEE sworn affidavit: no official stamp |
| 56 | Affidavit not signed by the commissioner *(reading simulated)* | yes | B-BBEE sworn affidavit: no commissioner's signature |
| 57 | Commissioner's stamp dated 2021-11-20, after the closing date *(reading simulated)* | yes | The B-BBEE affidavit was commissioned after the closing date |
| 60 | Strike-through on SBD 8 (the image) and a vision reading that reports it *(reading simulated)* | yes | Printed text crossed out on SBD 8 (page 17) |
| 61 | Pen scribble in the white space inside SBD 4 plus a vision reading *(reading simulated)* | yes | Handwritten note outside the form fields on SBD 4 (page 14) |
| 62 | A patch of correction fluid on SBD 9 plus a vision reading *(reading simulated)* | yes | Entry overwritten or covered on SBD 9 (page 21) |
| 63 | Pack page 9 scanned upside down plus a vision reading *(reading simulated)* | yes | Initials missing on 1 page(s) that the other pages carry; Page rotated on SBD 6.2 (page 9) |
| 64 | The bottom quarter of pack page 12 cut off plus a vision reading *(reading simulated)* | yes | Page cut off at the edge on Local content declaration (page 12); Initials missing on 1 page(s) that the other pages carry |
| 65 | A vision model reports crossed-out text once, and a second look does NOT confirm it *(reading simulated)* | yes | (observation only) no issues |
| 66 | A vision comparison judges the SBD 4 signature different from the quote's *(reading simulated)* | yes | (observation only) no issues |
| 67 | Vision says the 'signature' on the quote is not a pen signature *(reading simulated)* | yes | (observation only) no issues |
| 70 | Pack pages 13 and 14 (SBD 4, pages 1 and 2) swapped | yes | SBD 4: pages are out of order |
| 71 | Pack pages 26 and 27 (CSD report, pages 2 and 3) swapped | yes | CSD summary report: pages are out of order |
| 72 | SBD 8 first page (pack page 17) moved into the middle of SBD 4 | yes | SBD 4: its pages are not together; SBD 8: its pages are not together |
| 73 | Pack pages 19 and 20 (SBD 9, pages 1 and 2) swapped | yes | SBD 9: pages are out of order |
| 74 | Pack pages 5 and 6 (SBD 6.1, pages 3 and 4) swapped | yes | SBD 6.1: pages are out of order |
| 75 | Quotation moved to the end of the pack (order across documents is not policed) | no issue (correct: not required) | no issues |
| 80 | SARS PIN 'Purpose of Request' changed from Tender to Other | yes | Company name differs between documents; SARS TCS PIN purpose is not 'Tender' |
| 81 | CIPC status changed from 'In Business' to 'Deregistration process' | yes | CIPC certificate shows the company as 'Deregistration process', not 'In Business' |
| 82 | CSD overall tax status changed to 'Tax Non-Compliant' | yes | CSD report does not show the supplier as tax compliant |
| 83 | CSD 'Restricted Supplier' changed to Yes | yes | SBD 8 says not restricted, but the CSD report says restricted; CSD report lists the supplier as a Restricted Supplier |
| 84 | Affidavit level changed from One to Three (still 100% black owned) | yes | The affidavit's level does not follow from its ownership; The affidavit's recognition percentage does not match its level |
| 85 | Affidavit says the income exceeded R10 million | yes | The affidavit says the income exceeded R10 million |
| 86 | CSD report dated 27 Mar 2021 (about 200 days before closing) | yes | The CSD report is 202 days old at closing |
| 87 | SARS page names the taxpayer 'Excel Trading Holdings' | yes | Company name differs between documents |
| 29 | Removed pack page 29 (CIPC certificate: the RFQ does not ask for it) | no issue (correct: not required) | no issues |
