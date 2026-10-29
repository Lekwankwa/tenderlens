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
Be a good citizen: this is public data, but the portal is shared infrastructure. Keep the delays.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
from datetime import datetime

from playwright.async_api import async_playwright

PORTAL = "https://www.etenders.gov.za"
LIST_PAGE = f"{PORTAL}/Home/opportunities?id=2"
FILTER_URL = f"{PORTAL}/Home/TenderFilter/"
STATUS_CLOSED = 3  # the portal's own status code for its "Closed" tab (see its page script)
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


async def open_closed_tab(page) -> None:
    await page.goto(LIST_PAGE, wait_until="domcontentloaded", timeout=60_000)
    try:
        # The portal opens an "educational" pop-up over the tabs on arrival, which swallows real clicks, so
        # close it (best effort) and fire the tab's click event directly rather than clicking its coordinates.
        await page.evaluate("document.querySelectorAll('.modal.show').forEach(m => window.jQuery && "
                            "window.jQuery(m).modal('hide'))")
        await page.dispatch_event("#btnClosed", "click", timeout=15_000)
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


async def fetch_page(context, category_id: str, start: int, attempts: int = 3) -> dict | None:
    params = {"draw": "1", "start": str(start), "length": str(PAGE_SIZE), "status": str(STATUS_CLOSED),
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
            await open_closed_tab(page)
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--max-pages", type=int, default=60, help="page cap per category (100 rows each)")
    ap.add_argument("--visible", action="store_true", help="show the browser window")
    ap.add_argument("--min-delay", type=float, default=2.0)
    ap.add_argument("--max-delay", type=float, default=5.0)
    ap.add_argument("--output", help="also write the JSON to this file")
    args = ap.parse_args()

    records = asyncio.run(scrape(args.max_pages, not args.visible, (args.min_delay, args.max_delay)))

    try:
        import pandas as pd
        print(pd.DataFrame(records).drop(columns=["evidence"], errors="ignore").to_string(index=False))
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
