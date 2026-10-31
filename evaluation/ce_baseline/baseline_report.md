# CE baseline report

An honest baseline of the existing engine, not a training result: loading or evaluating this data did not train or improve the model. The tenders are authentic public documents; the clean and tampered packs are synthetic test documents (fictional bidders).

Grade folders discovered: 9 of 9; Complete: 9. Provider for every request: Fireworks AI (the only provider in this codebase; no fallback lane exists, so none fired).

## Scenarios, key status and files

| Folder | Tender | Key status | Tender (pages) | Clean (pages) | Tampered (pages) |
|---|---|---|---|---|---|
| 1CE | 4 | verified with flags | 1CE.pdf (110) | Tender4_clean_pack.pdf (134) | Tender4_tampered_pack.pdf (131) |
| 2CE | 5 | verified with flags | RFX6000003787.pdf (7); SCOPE OF WORK.pdf (6); SENTECH SUPPLIER CONSENT FORM.pdf (1); Standard Bidding Document (SDB) 4_Annexure A (00B) (002).pdf (3) | Tender5_clean_pack.pdf (35) | Tender5_tampered_pack.pdf (32) |
| 3CE Tender | 10 | verified with flags | another 3ce.pdf (226) | Tender10_clean_pack.pdf (273) | Tender10_tampered_pack.pdf (271) |
| 4ce | 6 | verified with flags | 4ce and gb.pdf (54) | Tender6_clean_pack.pdf (83) | Tender6_tampered_pack.pdf (80) |
| 5CE | 7 | verified with flags | Panel of Main Contractors 5CE 5GB Fv2.pdf.pdf (162) | Tender7_clean_pack.pdf (213) | Tender7_tampered_pack.pdf (209) |
| 6 CE Tender | 3 | verified with flags | 6CE Tender 2.pdf (293) | Tender3_clean_pack.pdf (375) | Tender3_tampered_pack.pdf (370) |
| 7CE Tender | 2 | verified with flags | 7CE Tender 1 .pdf (369) | Tender2_clean_pack.pdf (454) | Tender2_tampered_pack.pdf (449) |
| 8CE | 8 | verified with flags | 8CE.pdf (105) | Tender8_clean_pack.pdf (139) | Tender8_tampered_pack.pdf (135) |
| 9 CE | 9 | verified with flags | RFP FOR PE N2 FORESHORE REHABILITATION_.pdf (258) | Tender9_clean_pack.pdf (361) | Tender9_tampered_pack.pdf (354) |

See `manifest.md` (anomalies), `key_discrepancies.md` (answer-key checks).

## Results by grade (tampered pack against the verified key)

| Folder | Defects in key | Detected | Page only | Wrong page | Missed | Clean-pack issue findings | Tampered s | Clean s |
|---|---|---|---|---|---|---|---|---|
| 1CE | 50 | 9 | 18 | 6 | 17 | 26 | 741.0 | 518.6 |
| 4ce | 32 | 8 | 20 | 1 | 3 | 19 | 263.7 | 183.3 |
| 8CE | 32 | 2 | 12 | 3 | 15 | 40 | 246.6 | 220.1 |

## Results by defect class

| Class | Detected | Page only | Wrong page | Missed |
|---|---|---|---|---|
| arithmetic | 0 | 3 | 1 | 0 |
| authority | 0 | 0 | 0 | 1 |
| blank | 0 | 12 | 0 | 9 |
| claim | 3 | 0 | 2 | 0 |
| date | 2 | 4 | 2 | 4 |
| declaration | 2 | 4 | 0 | 2 |
| duplicate | 2 | 0 | 0 | 1 |
| ink | 0 | 2 | 0 | 0 |
| invalid | 1 | 8 | 2 | 2 |
| mismatch | 3 | 15 | 3 | 8 |
| missing | 5 | 0 | 0 | 8 |
| page order | 1 | 2 | 0 | 0 |

## Performance by grade and provider

| Folder | Pack | Pages | Seconds | Vision calls | Text calls | Tokens (in+out) | Retries | Peak MB | Provider |
|---|---|---|---|---|---|---|---|---|---|
| 1CE | clean | 134 | 518.6 | 104 | 1 | 194,984+30,029 | 1 | 563 | api.fireworks.ai (fallback fired: False) |
| 1CE | tampered | 131 | 741.0 | 110 | 1 | 204,501+32,642 | 5 | 563 | api.fireworks.ai (fallback fired: False) |
| 4ce | clean | 83 | 183.3 | 78 | 1 | 145,888+22,384 | 0 | 563 | api.fireworks.ai (fallback fired: False) |
| 4ce | tampered | 80 | 263.7 | 80 | 1 | 149,687+24,111 | 0 | 563 | api.fireworks.ai (fallback fired: False) |
| 8CE | clean | 139 | 220.1 | 82 | 1 | 157,538+22,932 | 0 | 585 | api.fireworks.ai (fallback fired: False) |
| 8CE | tampered | 135 | 246.6 | 77 | 1 | 147,590+23,465 | 0 | 585 | api.fireworks.ai (fallback fired: False) |

## Processing failures and items needing a person

- 1CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Missing: Letter of Good Standing with Workmen's Compensation Fund (pages [])
- 1CE clean pack: issue-tier finding to judge against the key's must-not-flag list: SBD 9 may be unsigned (pages [97])
- 1CE clean pack: issue-tier finding to judge against the key's must-not-flag list: B-BBEE sworn affidavit may be unsigned (pages [9])
- 1CE clean pack: issue-tier finding to judge against the key's must-not-flag list: B-BBEE sworn affidavit: no official stamp (pages [67])
- 1CE clean pack: issue-tier finding to judge against the key's must-not-flag list: SBD 6.1: 9 pages found, the standard form has 5 (pages [37, 38, 39, 40])
- 1CE clean pack: issue-tier finding to judge against the key's must-not-flag list: SBD 4: 3 of 4 pages found (pages [31, 32, 44])
- 1CE clean pack: issue-tier finding to judge against the key's must-not-flag list: SBD 9: 15 pages found, the standard form has 4 (pages [45, 46, 47, 48])
- 1CE clean pack: issue-tier finding to judge against the key's must-not-flag list: The CSD summary report is missing a section (pages [35, 36, 56])
- 1CE clean pack: issue-tier finding to judge against the key's must-not-flag list: B-BBEE sworn affidavit: its pages are not together (pages [9, 53, 57, 67])
- 1CE clean pack: issue-tier finding to judge against the key's must-not-flag list: CIPC registration certificate: its pages are not together (pages [20, 62])
- 1CE clean pack: issue-tier finding to judge against the key's must-not-flag list: SBD 4: its pages are not together (pages [31, 44])
- 1CE clean pack: issue-tier finding to judge against the key's must-not-flag list: CSD summary report: its pages are not together (pages [35, 56])
- 1CE clean pack: issue-tier finding to judge against the key's must-not-flag list: SBD 6.1: its pages are not together (pages [37, 55, 68, 71])
- 1CE clean pack: issue-tier finding to judge against the key's must-not-flag list: SBD 9: its pages are not together (pages [45, 81])
- 1CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Could not locate the signature line on SBD 6.1 (pages [71])
- 1CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Could not locate the signature line on SBD 4 (pages [44])
- 1CE clean pack: issue-tier finding to judge against the key's must-not-flag list: SBD 4: identity number is blank (pages [44])
- 1CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Printed text crossed out on B-BBEE sworn affidavit (page 67) (pages [67])
- 1CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Printed text crossed out on SBD 6.1 (page 68) (pages [68])
- 1CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Printed text crossed out on B-BBEE sworn affidavit (page 69) (pages [69])
- 1CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Company registration number differs between documents (pages [1, 7, 47, 77, 103])
- 1CE clean pack: issue-tier finding to judge against the key's must-not-flag list: SA ID number differs between documents (pages [63, 64])
- 1CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Email address differs between documents (pages [6, 8, 35, 57, 65, 76, 104, 106])
- 1CE clean pack: issue-tier finding to judge against the key's must-not-flag list: The affidavit does not say the income was under R10 million (pages [9])
- 1CE clean pack: issue-tier finding to judge against the key's must-not-flag list: The affidavit's recognition percentage does not match its level (pages [69])
- 1CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Initials missing on 39 page(s) (pages [1, 2, 12, 15, 16, 17, 18, 20, 21, 23, 29, 30, 33, 34, 35, 36, 43, 47, 60, 61, 62, 63, 64, 65, 103, 107, 113, 114, 117, 118, 119, 120, 121, 128, 129, 130, 131, 132, 134])
- 4ce clean pack: issue-tier finding to judge against the key's must-not-flag list: SBD 8 may be unsigned (pages [54])
- 4ce clean pack: issue-tier finding to judge against the key's must-not-flag list: SBD 6.1: 11 pages found, the standard form has 5 (pages [3, 4, 5, 6])
- 4ce clean pack: issue-tier finding to judge against the key's must-not-flag list: SBD 4: 6 pages found, the standard form has 4 (pages [2, 45, 46, 47])
- 4ce clean pack: issue-tier finding to judge against the key's must-not-flag list: SBD 9: 9 pages found, the standard form has 4 (pages [55, 56, 57, 58])
- 4ce clean pack: issue-tier finding to judge against the key's must-not-flag list: The CSD summary report is missing a section (pages [52, 63, 83])
- 4ce clean pack: issue-tier finding to judge against the key's must-not-flag list: SBD 4: its pages are not together (pages [2, 45])
- 4ce clean pack: issue-tier finding to judge against the key's must-not-flag list: SBD 6.1: its pages are not together (pages [3, 50, 74])
- 4ce clean pack: issue-tier finding to judge against the key's must-not-flag list: CSD summary report: its pages are not together (pages [52, 63, 83])
- 4ce clean pack: issue-tier finding to judge against the key's must-not-flag list: SBD 9: its pages are not together (pages [55, 76])
- 4ce clean pack: issue-tier finding to judge against the key's must-not-flag list: SARS tax compliance status: its pages are not together (pages [59, 64, 82])
- 4ce clean pack: issue-tier finding to judge against the key's must-not-flag list: Could not locate the signature line on SBD 6.1 (pages [75])
- 4ce clean pack: issue-tier finding to judge against the key's must-not-flag list: Could not locate the signature line on SBD 9 (pages [80])
- 4ce clean pack: issue-tier finding to judge against the key's must-not-flag list: Printed text crossed out on SBD 9 (page 78) (pages [78])
- 4ce clean pack: issue-tier finding to judge against the key's must-not-flag list: SA ID number differs between documents (pages [60])
- 4ce clean pack: issue-tier finding to judge against the key's must-not-flag list: Email address differs between documents (pages [1, 9, 32, 34, 36, 39, 42])
- 4ce clean pack: issue-tier finding to judge against the key's must-not-flag list: Company name differs between documents (pages [45, 56])
- 4ce clean pack: issue-tier finding to judge against the key's must-not-flag list: The affidavit does not say the income was under R10 million (pages [68])
- 4ce clean pack: issue-tier finding to judge against the key's must-not-flag list: Unexplained mark on SBD 8 (page 53) (pages [53])
- 4ce clean pack: issue-tier finding to judge against the key's must-not-flag list: Initials missing on 65 page(s) (pages [1, 3, 4, 5, 7, 8, 12, 13, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 42, 44, 45, 47, 49, 50, 51, 52, 53, 55, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 77, 79, 81, 83])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Missing: Letter of Intent from Financial Institution to provide Construction Guarantee (pages [])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: SBD 4 may be unsigned (pages [28])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: SBD 8 may be unsigned (pages [57])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: B-BBEE sworn affidavit may be unsigned (pages [59])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: SBD 6.1: 4 of 5 pages found (pages [37, 38, 39, 40])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: SBD 4: 15 pages found, the standard form has 4 (pages [6, 7, 8, 9])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: SBD 8: 3 pages found, the standard form has 2 (pages [55, 56, 57])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: SBD 9: 5 pages found, the standard form has 4 (pages [30, 31, 32, 33])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: The CSD summary report is missing a section (pages [4, 5])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: SBD 4: its pages are not together (pages [6, 18])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: CIPC registration certificate: its pages are not together (pages [11, 41])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: SARS tax compliance status: its pages are not together (pages [43, 102])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Could not locate the signature line on SBD 9 (pages [34])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Printed text crossed out on SBD 4 (page 19) (pages [19])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Printed text crossed out on SBD 4 (page 20) (pages [20])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Printed text crossed out on SBD 4 (page 21) (pages [21])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Entry overwritten or covered on SBD 4 (page 20) (pages [20])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Company registration number differs between documents (pages [47])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: SA ID number differs between documents (pages [24, 41, 42])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Email address differs between documents (pages [1, 3, 8, 23, 33, 62, 63, 64, 66, 72, 75, 77, 79, 82, 96, 97, 98, 105])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Company name differs between documents (pages [24, 30])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: The affidavit does not say the income was under R10 million (pages [59])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Unexplained mark on SBD 4 (page 6) (pages [6])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Unexplained mark on SBD 4 (page 7) (pages [7])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Unexplained mark on SBD 4 (page 7) (pages [7])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Unexplained mark on SBD 4 (page 7) (pages [7])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Unexplained mark on SBD 4 (page 7) (pages [7])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Unexplained mark on SBD 4 (page 7) (pages [7])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Unexplained mark on SBD 4 (page 7) (pages [7])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Unexplained mark on SBD 4 (page 10) (pages [10])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Unexplained mark on SBD 4 (page 10) (pages [10])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Unexplained mark on SBD 4 (page 10) (pages [10])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Unexplained mark on SBD 4 (page 10) (pages [10])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Unexplained mark on SBD 4 (page 10) (pages [10])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Unexplained mark on SBD 4 (page 10) (pages [10])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Unexplained mark on SBD 4 (page 10) (pages [10])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Unexplained mark on SBD 4 (page 18) (pages [18])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Unexplained mark on SBD 4 (page 18) (pages [18])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Unexplained mark on SBD 4 (page 20) (pages [20])
- 8CE clean pack: issue-tier finding to judge against the key's must-not-flag list: Unexplained mark on SBD 6.1 (page 39) (pages [39])

Pricing arithmetic (key section 4) and requirement interpretations are not matched by rule here; they need a person to compare the report with the key.
