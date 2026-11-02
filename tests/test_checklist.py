from tenderlens import checklist as ck


class FakeLLM:
    configured = True

    def __init__(self, answers):
        self.answers, self.calls = answers, 0

    def complete_json(self, system, user, **kw):
        a = self.answers[self.calls % len(self.answers)]
        self.calls += 1
        if isinstance(a, Exception):
            raise a
        return a


TENDER = ("PART C1 Agreement\nThe bidder must attach a valid CIDB certificate, grading 4CE or higher.\n"
          "PART C2 Pricing\nSubmit a Tax Compliance Status PIN not older than 3 months.\n") * 600


def test_split_and_group():
    secs = ck.split_sections(TENDER)
    assert len(secs) > 50
    groups = ck.group_sections(secs, limit=2000)
    assert all(len(g) <= 2000 + 200 for g in groups) and len(groups) > 1


def test_oversize_section_is_split():
    groups = ck.group_sections(["x\n" * 5000], limit=1000)
    assert len(groups) >= 10 and all(len(g) <= 1000 for g in groups)


def test_quote_check_and_dedupe():
    ok = {"label": "CIDB certificate", "kind": "date_validity", "rule": "valid",
          "source_quote": "valid CIDB certificate, grading 4CE or higher"}
    bad = {"label": "Invented", "source_quote": "not in the tender"}
    dup = {"label": "cidb  Certificate", "kind": "value_rule", "rule": "grading 4CE or higher",
           "source_quote": "valid CIDB certificate, grading 4CE or higher"}
    dropped = []
    items = ck.parse_items({"items": [ok, bad, dup, "junk"]}, TENDER, dropped)
    assert dropped == ["Invented"] and len(items) == 2
    out = ck.dedupe(items)
    assert len(out) == 1 and out[0].rule == "grading 4CE or higher"


def test_unknown_kind_defaults_to_document():
    it = ck.parse_items({"items": [{"label": "TCC", "kind": "weird", "source_quote": "Tax Compliance Status PIN"}]}, TENDER)
    assert it[0].kind == "document"


def test_cap():
    items = [ck.Item(f"i{n}", f"thing {n}", "document", "", f"q{n}") for n in range(200)]
    assert len(ck.dedupe(items, cap=80)) == 80


def test_build_checklist_survives_failed_group():
    llm = FakeLLM([{"items": [{"label": "TCC", "kind": "date_validity", "source_quote": "Tax Compliance Status PIN"}]},
                   RuntimeError("boom")])
    items, notes = ck.build_checklist(llm, TENDER, workers=1)
    assert len(items) == 1 and any("failed" in n for n in notes)


def test_short_tender_and_unconfigured():
    assert ck.build_checklist(FakeLLM([{}]), "short")[0] == []
    llm = FakeLLM([{}]); llm.configured = False
    assert ck.build_checklist(llm, TENDER)[0] == []
