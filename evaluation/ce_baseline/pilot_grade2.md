# Grade 2 pilot (Tender5, Sentech road maintenance): 100 DPI vs 150 DPI, both JPEG quality 85

Synthetic packs (fictional bidder) against the verified key (39 planted defects). One run per setting per pack, so run-to-run
noise is unmeasured: differences of one or two rows are not evidence. Provider: Fireworks AI, `muse-spark-open`, for every
call; no fallback lane exists. No per-token price is configured, so cost is shown as tokens.

| | 100 DPI q85 | 150 DPI q85 |
|---|---|---|
| Clean pack: seconds / vision calls | 49.7 / 45 | 116.8 / 42 |
| Clean pack: prompt + completion tokens | 82,859 + 14,066 | 140,793 + 12,889 |
| Tampered pack: seconds / vision calls | 55.9 / 42 | 63.2 / 42 |
| Tampered pack: prompt + completion tokens | 77,873 + 14,187 | 140,405 + 14,056 |
| Retries / failed pages | 0 / none | 0 / none |
| Peak memory | 463 MB | 295 MB |
| **Prompt tokens per page** | **about 2.4k** | **about 4.1k (+70%)** |

Tampered pack against the key (strict match: a finding on the key's page AND sharing wording with the expected finding):

| | 100 DPI | 150 DPI |
|---|---|---|
| Detected | 8 | 9 |
| Page only (a finding on that page, unrelated wording; not credited) | 21 | 18 |
| Wrong page (wording matches, other page) | 6 | 8 |
| Missed | 4 | 4 |
| Issue-tier findings on the CLEAN pack | 19 | 21 |

Rows that differed: T5-TC-22 (consent-form supplier name) was detected at 150 DPI and a wrong page at 100 DPI; T5-TC-07, 12 and 16
moved between "page only" and "wrong page". Missed in both: T5-TC-25 (reference letter 6 missing), 26 (functionality score below
minimum), 36 (bar-chart programme missing), 38 (duplicate page).

## Reading
- 100 DPI cut prompt tokens by about 41% with no sign of an accuracy loss beyond noise (8 vs 9 detected; one row differed).
- Baseline is low on the CE key, as expected: the engine's rules are written for the South African SBD forms.
- The CLEAN pack draws 19 to 21 issue-tier findings that a clean pack should not: "Missing: ..." for documents the packs do
  contain under other names, "Initials missing on 29 pages", "SBD 6.1: 1 of 5 pages found", "B-BBEE affidavit may be unsigned",
  and six "page cut off" notes. SBD checks are being applied to a non-SBD (Sentech) tender. Each needs a person to judge it
  against the key's must-not-flag list.
- Seconds are not comparable between the two runs: the 150 DPI clean run had a 94 s vision stage against 30 s for the same
  pages in the tampered run, which looks like provider latency, not image size.
