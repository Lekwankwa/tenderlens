"""Priced-schedule arithmetic: parsing, each check, confirmation by a second reading, page detection. No network."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest

from tenderlens.ingest import Page
from tenderlens.pricing import (check_pack, check_page, confirm, parse_money, pricing_pages, run_pricing_check)


@pytest.mark.parametrize("text,want", [("R 11 790.00", 11790.0), ("1 852 656.00", 1852656.0), ("1,852,656.00", 1852656.0),
                                       ("4 250,50", 4250.5), ("168.00", 168.0), ("992 900.00", 992900.0), ("48500", 48500.0),
                                       ("-1 200.00", -1200.0), (42, 42.0), ("Sum", None), ("", None), (None, None), ("R", None)])
def test_amounts_are_parsed_as_printed(text, want):
    assert parse_money(text) == want


def row(item, q, r, a):
    return {"item": item, "description": "x", "unit": "m3", "quantity": q, "rate": r, "amount": a}


def line(label, amount, **kw):
    return {"label": label, "text": label, "amount": amount, "percent": kw.get("percent"), "base_amount": kw.get("base")}


def test_a_correct_page_has_no_discrepancy():
    page = {"rows": [row("B1", "42", "285.00", "11 970.00"), row("B2", "0.3", "1 250.00", "375.00")],
            "lines": [line("brought_forward", "1 000.00"), line("carried_forward", "13 345.00")]}
    assert check_page(5, page) == []


def test_an_amount_that_is_not_quantity_times_rate_is_found_including_a_decimal_shift():
    page = {"rows": [row("B1", "42", "285.00", "11 790.00"), row("C2", "0.3", "1 250.00", "3 750.00"),
                     row("D1", "1", "48 500.00", "48 500.00")], "lines": []}
    got = check_page(125, page)
    assert [d.ref for d in got] == ["B1", "C2"] and all(d.kind == "item" for d in got)
    assert "11,970.00" in got[0].detail and "375.00" in got[1].detail


def test_a_page_total_that_is_not_brought_forward_plus_the_items_is_found():
    page = {"rows": [row("1", "1", "100.00", "100.00"), row("2", "1", "200.00", "200.00")],
            "lines": [line("brought_forward", "1 000.00"), line("carried_forward", "1 400.00")]}
    got = check_page(7, page)
    assert [d.kind for d in got] == ["page"] and "1,300.00" in got[0].detail


def test_a_carry_forward_that_differs_from_what_the_next_page_brings_forward():
    readings = {10: {"rows": [], "lines": [line("carried_forward", "637 176.50")]},
                11: {"rows": [], "lines": [line("brought_forward", "647 176.50")]}}
    got = check_pack(readings)
    assert [d.kind for d in got] == ["handover"] and got[0].page == 10 and got[0].ref == "to11"


def test_vat_at_14_percent_instead_of_15_is_found_and_15_percent_is_accepted():
    bad = {"rows": [], "lines": [line("vat", "110 656.70", percent=15, base="790 405.00")]}
    ok = {"rows": [], "lines": [line("vat", "118 560.75", percent=15, base="790 405.00")]}
    assert [d.kind for d in check_page(21, bad)] == ["vat"] and check_page(21, ok) == []
    stated14 = {"rows": [], "lines": [line("vat", "110 656.70", percent=14, base="790 405.00")]}
    assert [d.kind for d in check_page(21, stated14)] == ["vat"]          # VAT is 15%, whatever the line says


def test_an_escalation_line_that_is_not_its_stated_percentage_is_found():
    page = {"rows": [], "lines": [line("escalation", "4 961 341.73", percent=10, base="33 075 611.50")]}
    got = check_page(234, page)
    assert [d.kind for d in got] == ["percent"] and "3,307,561.15" in got[0].detail


def test_an_amount_carried_to_the_summary_that_the_summary_does_not_show():
    readings = {20: {"rows": [], "lines": [line("carried_to_summary", "2 100 656.00")]},
                21: {"rows": [], "lines": [line("section_total", "992 900.00"), line("section_total", "2 100 006.00"),
                                           line("subtotal", "3 092 906.00")]}}
    got = check_pack(readings)
    assert [d.kind for d in got] == ["sections"]


def test_a_discrepancy_is_confirmed_only_when_the_second_reading_reproduces_it():
    bad = {"rows": [row("B1", "42", "285.00", "11 790.00")], "lines": []}
    first = check_page(5, bad)
    assert confirm(first, check_page(5, bad))[0].confirmed is True
    first2 = check_page(5, bad)
    assert confirm(first2, check_page(5, {"rows": [row("B1", "42", "285.00", "11 970.00")], "lines": []}))[0].confirmed is False


def _page(n, text):
    return Page(n, "x", text=text, doc_type="unknown")


def test_pricing_pages_include_the_sparse_ones_the_ocr_gives_few_numbers_for():
    summary = _page(1, "SUMMARY OF SECTIONS\n1 SANS 1200 A 992 900.00\n4 POND LINING 2 100 656.00\nB) 15% Value Added Tax (VAT) 531 253.11\n")
    carry = _page(2, "PART C2: PRICING DATA\nSERIES 3 : SEWERS\nbrought forward 5 024 700.00\nTotal carried forward to Summary 5 084 700.00")
    prose = _page(3, "The contractor shall complete the works in accordance with the conditions of contract and the drawings. " * 5)
    got = pricing_pages([summary, carry, prose, _page(4, "")])
    assert [p.number for p in got] == [1, 2]


def test_the_number_of_pricing_pages_read_is_capped():
    many = [_page(i, "BILL OF QUANTITIES quantity rate amount\n" + "item 12 345.00 6 789.00\n" * 3) for i in range(1, 120)]
    assert len(pricing_pages(many)) == 60


class _Reader:
    """A scripted vision reader: the first reading of each page, then (for a re-read) the second."""
    configured = True

    def __init__(self, first, second=None):
        self.first, self.second, self.calls, self.errors, self.timeouts = first, second or first, 0, [], 0
        self.seen = {}

    def complete_json(self, system, user, images=None, timeout=None):
        self.calls += 1
        self.timeouts_seen = getattr(self, 'timeouts_seen', []) + [timeout]
        key = images[0]
        reread = "again" in user
        return (self.second if reread else self.first)[key]


def _facts():
    from tenderlens.facts import Facts
    return Facts()


def test_the_check_reads_the_pages_rereads_those_with_a_discrepancy_and_records_the_result(tmp_path):
    text = "BILL OF QUANTITIES quantity rate amount carried forward\n" + "x 1 000.00 2 000.00\n" * 3
    pages = [Page(1, "p1", text=text), Page(2, "p2", text=text)]
    bad = {"rows": [row("B1", "42", "285.00", "11 790.00")], "lines": []}
    good = {"rows": [row("B1", "42", "285.00", "11 970.00")], "lines": []}
    llm = _Reader({"p1": bad, "p2": good})              # page 1 reads wrong both times, page 2 is fine
    facts = _facts()
    run_pricing_check(llm, pages, facts)
    assert facts.pricing_pages == [1, 2] and len(facts.pricing) == 1
    assert facts.pricing[0].confirmed is True and llm.calls == 3          # 2 first readings + 1 re-read of page 1
    assert set(llm.timeouts_seen) == {150.0}                              # table reads get the long timeout
    llm2 = _Reader({"p1": bad, "p2": good}, second={"p1": good, "p2": good})   # the second reading disagrees
    facts2 = _facts()
    run_pricing_check(llm2, pages, facts2)
    assert facts2.pricing[0].confirmed is False


def test_the_report_makes_a_confirmed_discrepancy_an_issue_and_an_unconfirmed_one_an_observation():
    from tenderlens.audit import Auditor
    from tenderlens.requirements import Requirements
    from tenderlens.pricing import Discrepancy
    facts = _facts()
    facts.pricing_pages = [5, 6]
    facts.pricing = [Discrepancy("item", 5, "B1", "Item B1: amount is not quantity x rate", "42 x 285.00 = 11,970.00 ...", confirmed=True),
                     Discrepancy("vat", 6, "vat", "VAT is not 15% of the amount before VAT", "...", confirmed=False)]
    a = Auditor(Requirements(), [Page(5, "x"), Page(6, "x")], facts, Path("samples/blank"))
    a.pricing_checks()
    tiers = {f.title: f.tier for f in a.res.findings}
    assert tiers == {"Item B1: amount is not quantity x rate": "issue", "VAT is not 15% of the amount before VAT": "observation"}
    assert all(f.category == "arithmetic" for f in a.res.findings)


def test_a_page_whose_reading_lists_fewer_rows_than_the_page_prints_is_not_summed():
    page = {"row_count": 6, "rows": [row("1", "1", "100.00", "100.00"), row("2", "1", "200.00", "200.00")],
            "lines": [line("carried_forward", "900.00")]}                  # 4 rows missing from the reading
    assert check_page(7, page) == []
    complete = dict(page, row_count=2)
    assert [d.kind for d in check_page(7, complete)] == ["page"]


def test_two_readings_that_disagree_on_a_figure_do_not_confirm_each_other():
    a = check_page(5, {"rows": [row("B1", "42", "285.00", "11 790.00")], "lines": []})
    b = check_page(5, {"rows": [row("B1", "42", "285.00", "11 790.00")], "lines": []})
    c = check_page(5, {"rows": [row("B1", "42", "285.00", "11 000.00")], "lines": []})   # still wrong, but another figure
    assert confirm(a, b)[0].confirmed is True
    assert confirm(check_page(5, {"rows": [row("B1", "42", "285.00", "11 790.00")], "lines": []}), c)[0].confirmed is False


def _sched(n):
    return Page(n, f"p{n}", text="BILL OF QUANTITIES quantity rate amount carried forward\n" + "x 1 000.00 2 000.00\n" * 3)


def test_the_streaming_reader_starts_reads_on_priced_pages_only_and_collects_the_batch_set():
    from tenderlens.pricing import PricingReader
    good = {"rows": [row("B1", "42", "285.00", "11 970.00")], "lines": []}
    llm = _Reader({f"p{n}": good for n in range(1, 8)})
    prose = Page(9, "p9", text="The contractor shall complete the works in accordance with the conditions. " * 5)
    reader = PricingReader(llm, _facts())
    pages = [_sched(1), prose, _sched(2), _sched(3)]
    for p in pages:                     # announced one by one, as the OCR workers would
        reader.offer(p)
    got = reader.collect(pages)
    assert sorted(got) == [1, 2, 3] and all(v == good for v in got.values())
    assert llm.calls == 3               # the prose page was never read, the others once each (collect did not read again)


def test_a_streamed_check_gives_the_same_discrepancies_as_the_batch_check():
    from tenderlens.pricing import PricingReader
    bad = {"rows": [row("B1", "42", "285.00", "11 790.00")], "lines": []}
    good = {"rows": [row("B1", "42", "285.00", "11 970.00")], "lines": []}
    pages = [_sched(1), _sched(2)]
    script = {"p1": bad, "p2": good}

    batch_facts = _facts()
    run_pricing_check(_Reader(script), pages, batch_facts)

    reader_llm = _Reader(script)
    reader = PricingReader(reader_llm, _facts())
    for p in pages:
        reader.offer(p)
    streamed_facts = _facts()
    run_pricing_check(reader_llm, pages, streamed_facts, prefetched=reader.collect(pages), reader_facts=reader.facts)
    key = lambda f: [(d.kind, d.page, d.ref, d.confirmed) for d in f.pricing]       # noqa: E731
    assert key(streamed_facts) == key(batch_facts) == [("item", 1, "B1", True)]
    assert streamed_facts.pricing_pages == batch_facts.pricing_pages == [1, 2]


def test_the_streaming_reader_starts_at_most_the_page_cap_ahead_of_time():
    from tenderlens.pricing import MAX_PAGES, PricingReader
    llm = _Reader({f"p{n}": {"rows": [], "lines": []} for n in range(1, 120)})
    reader = PricingReader(llm, _facts())
    for n in range(1, 120):
        reader.offer(_sched(n))
    assert len(reader._futures) == MAX_PAGES
    reader.collect([_sched(n) for n in range(1, 120)])
