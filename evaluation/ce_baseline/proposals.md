# Measured proposals (from the grade 2, 4, 1 and 8 runs; nothing here has been implemented except where stated)

No training or model improvement happened. These come from what the baseline showed.

1. **Stop applying SBD-only rules to non-SBD tenders.** Clean packs draw 50 (grade 4), 53 (grade 1) and 89 (grade 8) findings
   and 19 to 21 at issue tier in grade 2: "Initials missing", "N of M pages found", "may be unsigned", "Missing: ...", and
   "Page cut off" on 25 to 50 pages. Gate these rules on the tender actually being an SA SBD tender, or on the form being found.
2. **Collapse duplicate findings.** The same "Small mark on SBD 4 (page 7)" is emitted six or more times in one run; one finding
   per page and kind is enough.
3. **Add CE/NEC3 requirement checks the keys test**: attendance certificate and meeting date, CIDB grade and expiry, insurance
   amounts, Form of Offer totals against the bill of quantities, reference letters and equipment lists, missing/duplicated pages
   against the tender's own page list. The "missing" class is 0 detected of 12 so far.
4. **Page-completeness check against the tender's page list** (duplicates 0 of 3, missing 0 of 12 detected).
5. **Cut-off check**: the non-AI edge test fires on most pages of these scans; calibrate it per pack before counting it.
6. **Done in code during this work**: the requirements step no longer aborts on a null or odd model answer (it cost five runs,
   about $17), and a blank identifier reading no longer crashes the identifier comparison.
