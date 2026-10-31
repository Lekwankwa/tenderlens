# CE dataset manifest

Root: `/Users/lehlogonolokgatomaabane/Downloads/CE ` (note the trailing space in the folder name).

Labels: the tender is the *authentic source* (public procurement document). Clean and tampered packs are *synthetic test documents* (fictional bidder), never genuine submissions.

Grade folders found: 9 of 9. Complete: 9.

| Folder | Tender | Tender file(s) (pages) | Clean pack (pages) | Tampered pack (pages) | Requirements | Key | Complete |
|---|---|---|---|---|---|---|---|
| 1CE | Tender4 | 1CE.pdf (110) | Tender4_clean_pack.pdf (134) | Tender4_tampered_pack.pdf (131) | CE1_Tender4_requirements.csv | Tender4_answer_key.md | yes |
| 2CE | Tender5 | RFX6000003787.pdf (7); SCOPE OF WORK.pdf (6); SENTECH SUPPLIER CONSENT FORM.pdf (1); Standard Bidding Document (SDB) 4_Annexure A (00B) (002).pdf (3) | Tender5_clean_pack.pdf (35) | Tender5_tampered_pack.pdf (32) | CE2_Tender5_requirements.csv | Tender5_answer_key.md | yes |
| 3CE Tender | Tender10 | another 3ce.pdf (226) | Tender10_clean_pack.pdf (273) | Tender10_tampered_pack.pdf (271) | CE3_Tender10_requirements.csv | Tender10_answer_key.md | yes |
| 4ce | Tender6 | 4ce and gb.pdf (54) | Tender6_clean_pack.pdf (83) | Tender6_tampered_pack.pdf (80) | CE4_Tender6_requirements.csv | Tender6_answer_key.md | yes |
| 5CE | Tender7 | Panel of Main Contractors 5CE 5GB Fv2.pdf.pdf (162) | Tender7_clean_pack.pdf (213) | Tender7_tampered_pack.pdf (209) | CE5_Tender7_requirements.csv | Tender7_answer_key.md | yes |
| 6 CE Tender | Tender3 | 6CE Tender 2.pdf (293) | Tender3_clean_pack.pdf (375) | Tender3_tampered_pack.pdf (370) | CE6_Tender3_requirements.csv | Tender3_answer_key.md | yes |
| 7CE Tender | Tender2 | 7CE Tender 1 .pdf (369) | Tender2_clean_pack.pdf (454) | Tender2_tampered_pack.pdf (449) | CE7_Tender2_requirements.csv | Tender2_answer_key.md | yes |
| 8CE | Tender8 | 8CE.pdf (105) | Tender8_clean_pack.pdf (139) | Tender8_tampered_pack.pdf (135) | CE8_Tender8_requirements.csv | Tender8_answer_key.md | yes |
| 9 CE | Tender9 | RFP FOR PE N2 FORESHORE REHABILITATION_.pdf (258) | Tender9_clean_pack.pdf (361) | Tender9_tampered_pack.pdf (354) | CE9_Tender9_requirements.csv | Tender9_answer_key.md | yes |

## Anomalies (reported only; nothing on disk was changed)

- **1CE**: Folder is grade 1 but its files are named Tender4. Proposed: Keep as is; use the manifest's folder-to-tender map. Optionally name folders by tender number.
- **2CE**: Folder is grade 2 but its files are named Tender5. Proposed: Keep as is; use the manifest's folder-to-tender map. Optionally name folders by tender number.
- **2CE**: The tender is split over 4 PDFs (17 pages together). Proposed: Pass all of them to the engine in the order given by the key; do not merge on disk.
- **3CE Tender**: Folder is grade 3 but its files are named Tender10. Proposed: Keep as is; use the manifest's folder-to-tender map. Optionally name folders by tender number.
- **3CE Tender**: Folder name '3CE Tender' does not follow one pattern (spaces, case, suffixes). Proposed: Rename to '3CE' in a copy of the dataset if scripts need a uniform name.
- **4ce**: Folder is grade 4 but its files are named Tender6. Proposed: Keep as is; use the manifest's folder-to-tender map. Optionally name folders by tender number.
- **4ce**: Folder name '4ce' does not follow one pattern (spaces, case, suffixes). Proposed: Rename to '4CE' in a copy of the dataset if scripts need a uniform name.
- **4ce**: Tender '4ce and gb.pdf' covers GB (general building) as well as CE; GB is out of scope. Proposed: Evaluate the CE parts only; the key's requirements decide what counts.
- **5CE**: Folder is grade 5 but its files are named Tender7. Proposed: Keep as is; use the manifest's folder-to-tender map. Optionally name folders by tender number.
- **5CE**: Double extension: Panel of Main Contractors 5CE 5GB Fv2.pdf.pdf. Proposed: Rename to a single .pdf in a copy.
- **5CE**: Tender 'Panel of Main Contractors 5CE 5GB Fv2.pdf.pdf' covers GB (general building) as well as CE; GB is out of scope. Proposed: Evaluate the CE parts only; the key's requirements decide what counts.
- **6 CE Tender**: Folder is grade 6 but its files are named Tender3. Proposed: Keep as is; use the manifest's folder-to-tender map. Optionally name folders by tender number.
- **6 CE Tender**: Folder name '6 CE Tender' does not follow one pattern (spaces, case, suffixes). Proposed: Rename to '6CE' in a copy of the dataset if scripts need a uniform name.
- **7CE Tender**: Folder is grade 7 but its files are named Tender2. Proposed: Keep as is; use the manifest's folder-to-tender map. Optionally name folders by tender number.
- **7CE Tender**: Folder name '7CE Tender' does not follow one pattern (spaces, case, suffixes). Proposed: Rename to '7CE' in a copy of the dataset if scripts need a uniform name.
- **9 CE**: Folder name '9 CE' does not follow one pattern (spaces, case, suffixes). Proposed: Rename to '9CE' in a copy of the dataset if scripts need a uniform name.
- **9 CE**: 27 supporting annexure files (specs and drawings, reference only). Proposed: Not passed to the engine; the key marks them as reference only.
