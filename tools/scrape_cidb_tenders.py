#!/usr/bin/env python3
"""Collect one historical (closed) construction tender per CIDB designation 6GB / 7GB / 8GB / 9GB from the
South African National Treasury eTenders portal (https://www.etenders.gov.za), using Playwright.

Setup (kept out of requirements.txt on purpose - this is a standalone tool, not part of the deployed app):
    pip install playwright pandas
    playwright install chromium
Run:
    python tools/scrape_cidb_tenders.py            # headless, prints a table and a JSON block
    python tools/scrape_cidb_tenders.py --visible  # watch the browser
    python tools/scrape_cidb_tenders.py --output samples.json
    python tools/scrape_cidb_tenders.py --documents   # OPEN tenders + download their PDFs (see below)

How it works (checked against the live portal's own page script, not guessed):
  - The "Closed" tab on /Home/opportunities loads its table from a server-side JSON endpoint,
    /Home/TenderFilter/ (status=3 means Closed), which accepts a category filter and up to 100 rows a page.
    There are ~110,000 closed tenders overall but only a few thousand in the construction categories, so this
    pages through those categories, newest first, instead of the whole archive.
  - The portal has no free-text search and no structured CIDB-grade field: the grade is written into the
    tender's description / special-conditions text. So each row's text is matched against the grade regex.
  - Playwright drives a real browser session (fake desktop User-Agent, opens the Closed tab like a person
    would) and makes the paging requests through that browser context, with a random 2-5 s pause between
    every page transition.
Documents: the portal keeps NO attached documents for closed tenders (checked: 0 of 100), only for tenders that
are still open. So `--documents` switches to the Advertised tab, picks one open tender per grade that has PDFs
(or a zip containing PDFs) attached, and saves them under tools/tender_documents/<grade>_<bid number>/.
Be a good citizen: this is public data, but the portal is shared infrastructure. Keep the delays.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import random
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

PORTAL = "https://www.etenders.gov.za"
LIST_PAGE = f"{PORTAL}/Home/opportunities?id=2"
FILTER_URL = f"{PORTAL}/Home/TenderFilter/"
STATUS_CLOSED = 3  # the portal's own status code for its "Closed" tab (see its page script)
STATUS_ADVERTISED = 1  # the "Active"/advertised tab: the only one whose rows carry attached documents
DOWNLOAD_URL = f"{PORTAL}/home/Download/"
MAX_DOC_BYTES = 150 * 1024 * 1024
PAGE_SIZE = 100
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0.0.0 Safari/537.36")

# Rarest first, so a tender that mentions several grades fills the hardest slot it can.
TARGET_GRADES = ("9GB", "8GB", "7GB", "6GB")
GRADE_RX = re.compile(r"\b(6|7|8|9)\s*(GB|GB\s*PE)\b", re.IGNORECASE)  # "PE" = potentially emerging
# Any grade of any common class ("4 GB", "7EB", "9 CE"): used only to tell a tender that names one grade from a
# panel / range notice ("grade 4 GB ... to 9 GB", "8GB and/or 7EB"), which makes a weaker sample.
ANY_GRADE_RX = re.compile(r"\b([1-9])\s*(GB|CE|EB|EP|ME)\b", re.IGNORECASE)
# "8GB RAM" / "16 GB"-style hits from IT tenders: not a CIDB grade.
_STORAGE_WORDS = re.compile(r"\b(RAM|MEMORY|STORAGE|SSD|HDD|DDR\d?|USB|FLASH|DISK|CACHE)\b", re.IGNORECASE)

# Categories (by name, in priority order) whose tenders are likely to carry a CIDB grade. IDs below are the
# portal's own, used only if the category list can't be read off the page.
CATEGORY_PRIORITY = [r"^construction of buildings$", r"^construction$", r"^civil engineering$",
                     r"^specialised construction", r"^services: building$", r"^services: civil$"]
FALLBACK_CATEGORIES = [{"id": "39", "name": "Construction of buildings"}, {"id": "27", "name": "Construction"},
                       {"id": "13", "name": "Civil engineering"},
                       {"id": "76", "name": "Specialised construction activities"}]


def find_grades(text: str) -> dict[str, str]:
    """{'9GB': 'snippet around the match', ...} for every CIDB-looking designation in `text`."""
    found: dict[str, str] = {}
    for m in GRADE_RX.finditer(text):
        around = text[max(0, m.start() - 15): m.end() + 15]
        if _STORAGE_WORDS.search(around):
            continue
        grade = f"{m.group(1)}GB"
        if grade not in found:
            found[grade] = " ".join(text[max(0, m.start() - 70): m.end() + 70].split())
    return found


def distinct_designations(text: str) -> set[str]:
    return {f"{m.group(1)}{m.group(2).upper()}" for m in ANY_GRADE_RX.finditer(text)
            if not _STORAGE_WORDS.search(text[max(0, m.start() - 15): m.end() + 15])}


def _date(value: str | None) -> str:
    try:
        return datetime.fromisoformat(value).date().isoformat() if value else ""
    except ValueError:
        return value or ""


def to_record(row: dict, grade: str, evidence: str, category: str) -> dict:
    """One output row. Every field is read defensively - the portal's JSON has nulls and odd layouts."""
    def get(key: str) -> str:
        try:
            return str(row.get(key) or "").strip()
        except Exception:  # noqa: BLE001 - a malformed row must never crash the run
            return ""
    return {
        "bid_number": get("tender_No"),
        "tender_title": " ".join(get("description").split()),
        "institution": get("organ_of_State") or get("department"),
        "date": _date(row.get("closing_Date")),
        "date_type": "Closing Date",
        "cidb_designation": grade,
        "category": category,
        "status": get("status") or "Closed",
        "evidence": evidence,
    }


async def polite_pause(lo: float, hi: float, why: str) -> None:
    delay = random.uniform(lo, hi)
    print(f"  ... pausing {delay:.1f}s ({why})", file=sys.stderr)
    await asyncio.sleep(delay)


async def open_tab(page, selector: str = "#btnClosed") -> None:
    await page.goto(LIST_PAGE, wait_until="domcontentloaded", timeout=60_000)
    try:
        # The portal opens an "educational" pop-up over the tabs on arrival, which swallows real clicks, so
        # close it (best effort) and fire the tab's click event directly rather than clicking its coordinates.
        await page.evaluate("document.querySelectorAll('.modal.show').forEach(m => window.jQuery && "
                            "window.jQuery(m).modal('hide'))")
        await page.dispatch_event(selector, "click", timeout=15_000)
        await page.wait_for_load_state("networkidle", timeout=30_000)
    except Exception as err:  # noqa: BLE001 - tab layout changed or slow: paging below doesn't depend on it
        print(f"  (could not click the Closed tab, continuing with the data endpoint directly: {err})",
              file=sys.stderr)


async def resolve_categories(page) -> list[dict]:
    try:
        options = await page.eval_on_selector_all(
            "#categorieslist option", "els => els.map(e => ({id: e.value, name: e.textContent.trim()}))")
    except Exception:  # noqa: BLE001
        options = []
    ranked = []
    for rank, pattern in enumerate(CATEGORY_PRIORITY):
        ranked += [dict(o, rank=rank) for o in options if o.get("id") and re.search(pattern, o["name"], re.I)]
    ranked.sort(key=lambda o: o["rank"])
    return [{"id": o["id"], "name": o["name"]} for o in ranked] or FALLBACK_CATEGORIES


async def fetch_page(context, category_id: str, start: int, attempts: int = 3,
                     status: int = STATUS_CLOSED) -> dict | None:
    params = {"draw": "1", "start": str(start), "length": str(PAGE_SIZE), "status": str(status),
              "category": category_id, "department": "", "province": "", "cluster": "", "type": "",
              "tenderNumber": "", "company": "", "supplierNumber": "",
              "order[0][column]": "2", "order[0][dir]": "desc"}
    for attempt in range(1, attempts + 1):
        try:
            resp = await context.request.get(
                FILTER_URL, params=params, timeout=90_000,
                headers={"User-Agent": USER_AGENT, "X-Requested-With": "XMLHttpRequest", "Referer": LIST_PAGE})
            if resp.ok:
                return await resp.json()
            print(f"  HTTP {resp.status} on attempt {attempt}/{attempts}", file=sys.stderr)
        except Exception as err:  # noqa: BLE001 - timeouts, resets, bad JSON: retry, then give up on this page
            print(f"  request failed on attempt {attempt}/{attempts}: {err}", file=sys.stderr)
        await asyncio.sleep(2 * attempt)
    return None


async def scrape(max_pages: int, headless: bool, delay: tuple[float, float]) -> list[dict]:
    found: dict[str, dict] = {}
    used_tenders: set = set()  # one tender may mention several grades; it should fill only one slot
    fallback: dict[str, tuple] = {}  # grade -> (tender id, record) from multi-grade tenders, used last
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context(user_agent=USER_AGENT, locale="en-ZA",
                                            viewport={"width": 1366, "height": 850})
        page = await context.new_page()
        try:
            print("Opening the eTenders Closed tab ...", file=sys.stderr)
            await open_tab(page)
            categories = await resolve_categories(page)
            print(f"Categories to search: {', '.join(c['name'] for c in categories)}", file=sys.stderr)

            for cat in categories:
                if len(found) == len(TARGET_GRADES):
                    break
                start = 0
                for page_no in range(1, max_pages + 1):
                    await polite_pause(*delay, f"before '{cat['name']}' page {page_no}")
                    data = await fetch_page(context, cat["id"], start)
                    if not data or not data.get("data"):
                        break
                    for row in data["data"]:
                        try:
                            text = f"{row.get('description') or ''} {row.get('conditions') or ''}"
                            grades = find_grades(text)
                            if not grades or row.get("id") in used_tenders:
                                continue
                            # A tender naming several grades (a panel, or a "4 GB to 9 GB" range) is a weak
                            # sample for any one of them: take it only if the scan runs out of clean ones.
                            clean = len(grades) == 1 and len(distinct_designations(text)) <= 1
                            for grade in TARGET_GRADES:
                                if grade in grades and grade not in found:
                                    record = to_record(row, grade, grades[grade], cat["name"])
                                    if not clean:
                                        fallback.setdefault(grade, (row.get("id"), record))
                                        continue
                                    found[grade] = record
                                    used_tenders.add(row.get("id"))
                                    print(f"  found {grade}: {record['bid_number']} ({record['institution']})",
                                          file=sys.stderr)
                                    break
                        except Exception as err:  # noqa: BLE001 - skip one bad row, keep scanning
                            print(f"  skipped a row: {err}", file=sys.stderr)
                    if len(found) == len(TARGET_GRADES):
                        break
                    start += PAGE_SIZE
                    if start >= int(data.get("recordsFiltered") or 0):
                        break  # this category is exhausted
        finally:
            await browser.close()
    for grade in TARGET_GRADES:
        if grade not in found and grade in fallback and fallback[grade][0] not in used_tenders:
            found[grade] = fallback[grade][1]
            used_tenders.add(fallback[grade][0])
            print(f"  {grade}: no single-grade tender found, using a multi-grade one", file=sys.stderr)
    return [found[g] for g in sorted(found)]


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .") or "file"


def pdf_or_zip_docs(row: dict) -> list[dict]:
    return [d for d in (row.get("supportDocument") or []) if str(d.get("extension", "")).lower() in (".pdf", ".zip")]


async def download_documents(context, row: dict, folder: Path, delay: tuple[float, float]) -> list[str]:
    """Save a tender's PDFs (and the PDFs inside any attached zip) into `folder`; returns the saved paths."""
    saved: list[str] = []
    folder.mkdir(parents=True, exist_ok=True)

    def save(name: str, data: bytes) -> None:
        target = folder / _safe_name(name)
        n = 1
        while target.exists():  # two zip members with the same file name in different sub-folders
            target = folder / f"{n}_{_safe_name(name)}"
            n += 1
        target.write_bytes(data)
        saved.append(str(target))

    for doc in pdf_or_zip_docs(row):
        await polite_pause(*delay, f"before downloading {doc.get('fileName')}")
        try:
            resp = await context.request.get(
                DOWNLOAD_URL, timeout=180_000,
                params={"blobName": f"{doc['supportDocumentID']}{doc['extension']}",
                        "downloadedFileName": doc.get("fileName") or ""},
                headers={"User-Agent": USER_AGENT, "Referer": LIST_PAGE})
            if not resp.ok:
                continue
            body = await resp.body()
        except Exception as err:  # noqa: BLE001 - one bad download must not stop the run
            print(f"  download failed ({doc.get('fileName')}): {err}", file=sys.stderr)
            continue
        if len(body) > MAX_DOC_BYTES:
            continue
        if str(doc["extension"]).lower() == ".pdf":
            if body.startswith(b"%PDF"):
                name = doc.get("fileName") or f"{doc['supportDocumentID']}.pdf"
                save(name if name.lower().endswith(".pdf") else name + ".pdf", body)
            continue
        try:
            with zipfile.ZipFile(io.BytesIO(body)) as zf:
                for info in zf.infolist():
                    if info.is_dir() or not info.filename.lower().endswith(".pdf") or info.file_size > MAX_DOC_BYTES:
                        continue
                    data = zf.read(info)
                    if data.startswith(b"%PDF"):
                        save(Path(info.filename).name, data)  # basename only: never trust paths inside a zip
        except zipfile.BadZipFile:
            print(f"  not a valid zip: {doc.get('fileName')}", file=sys.stderr)
    return saved


async def scrape_documents(max_pages: int, headless: bool, delay: tuple[float, float], out_dir: Path) -> list[dict]:
    """One OPEN tender per grade (the only ones with documents), its PDFs downloaded into out_dir."""
    candidates: dict[str, list] = {g: [] for g in TARGET_GRADES}
    seen: set = set()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context(user_agent=USER_AGENT, locale="en-ZA",
                                            viewport={"width": 1366, "height": 850})
        page = await context.new_page()
        try:
            print("Opening the eTenders Advertised tab ...", file=sys.stderr)
            await open_tab(page, "#btnActive")
            categories = await resolve_categories(page)
            for cat in categories + [{"id": "", "name": "All categories"}]:
                if cat["id"] == "" and all(candidates.values()):
                    break  # the broad all-categories pass is only needed if a grade has no candidate yet
                start = 0
                for page_no in range(1, max_pages + 1):
                    await polite_pause(*delay, f"before '{cat['name']}' page {page_no}")
                    data = await fetch_page(context, cat["id"], start, status=STATUS_ADVERTISED)
                    if not data or not data.get("data"):
                        break
                    for row in data["data"]:
                        try:
                            if row.get("id") in seen or not pdf_or_zip_docs(row):
                                continue
                            text = f"{row.get('description') or ''} {row.get('conditions') or ''}"
                            grades = find_grades(text)
                            if not grades:
                                continue
                            seen.add(row.get("id"))
                            clean = len(grades) == 1 and len(distinct_designations(text)) <= 1
                            docs = pdf_or_zip_docs(row)
                            has_pdf = any(str(d.get("extension", "")).lower() == ".pdf" for d in docs)
                            # A main tender document beats a pile of drawing sheets as a training sample.
                            main_doc = any(re.search(r"tender|bid doc|rfq|invitation", str(d.get("fileName", "")), re.I)
                                           for d in docs)
                            for grade, evidence in grades.items():
                                candidates[grade].append(((0 if clean else 1, 0 if main_doc else 1,
                                                           0 if has_pdf else 1),
                                                          to_record(row, grade, evidence, cat["name"]), row))
                        except Exception as err:  # noqa: BLE001
                            print(f"  skipped a row: {err}", file=sys.stderr)
                    start += PAGE_SIZE
                    if start >= int(data.get("recordsFiltered") or 0):
                        break

            chosen: dict[str, dict] = {}
            used: set = set()
            for grade in TARGET_GRADES:
                for _, record, row in sorted(candidates[grade], key=lambda c: c[0]):
                    if row.get("id") in used:
                        continue
                    folder = out_dir / f"{grade}_{_safe_name(record['bid_number'])}"
                    files = await download_documents(context, row, folder, delay)
                    if files:
                        record["documents"] = files
                        chosen[grade] = record
                        used.add(row.get("id"))
                        print(f"  {grade}: {record['bid_number']} ({record['institution']}) - {len(files)} PDF(s)",
                              file=sys.stderr)
                        break
                    if folder.exists() and not any(folder.iterdir()):
                        folder.rmdir()
        finally:
            await browser.close()
    return [chosen[g] for g in sorted(chosen)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--max-pages", type=int, default=60, help="page cap per category (100 rows each)")
    ap.add_argument("--visible", action="store_true", help="show the browser window")
    ap.add_argument("--min-delay", type=float, default=2.0)
    ap.add_argument("--max-delay", type=float, default=5.0)
    ap.add_argument("--output", help="also write the JSON to this file")
    ap.add_argument("--documents", action="store_true",
                    help="use OPEN tenders and download their PDFs (closed tenders have no documents)")
    ap.add_argument("--docs-dir", default=str(Path(__file__).resolve().parent / "tender_documents"),
                    help="where --documents saves the PDFs")
    args = ap.parse_args()

    delay = (args.min_delay, args.max_delay)
    if args.documents:
        records = asyncio.run(scrape_documents(args.max_pages, not args.visible, delay, Path(args.docs_dir)))
    else:
        records = asyncio.run(scrape(args.max_pages, not args.visible, delay))

    try:
        import pandas as pd
        print(pd.DataFrame(records).drop(columns=["evidence", "documents"], errors="ignore").to_string(index=False))
        print()
    except ImportError:
        pass  # pandas is optional; the JSON below is the primary output
    blob = json.dumps(records, indent=2, ensure_ascii=False)
    print(blob)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(blob)

    missing = [g for g in TARGET_GRADES if g not in {r["cidb_designation"] for r in records}]
    if missing:
        print(f"\nNot found after exhausting the searched pages: {', '.join(missing)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
