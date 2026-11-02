from datetime import date
from types import SimpleNamespace as NS

from tenderlens import verify as vf
from tenderlens.checklist import Item
from tenderlens.facts import Facts

CL = date(2025, 3, 31)


def item(kind, rule="", label="CIDB certificate"):
    return Item("x", label, kind, rule, "q")


def test_expired_before_closing_fails():
    st, why = vf.decide(item("date_validity"), {"present": True, "valid_until": "2025-02-01"}, CL)
    assert st == "fail" and "expired" in why
    assert vf.decide(item("date_validity"), {"present": True, "valid_until": "2025-06-01"}, CL)[0] == "pass"


def test_older_than_three_months():
    it = item("date_validity", "not older than 3 months")
    assert vf.decide(it, {"present": True, "date_issued": "2024-10-01"}, CL)[0] == "fail"
    assert vf.decide(it, {"present": True, "date_issued": "2025-02-20"}, CL)[0] == "pass"


def test_value_rules_both_directions():
    low = item("value_rule", "minimum indemnity R5 000 000")
    assert vf.decide(low, {"present": True, "value": "R2 000 000"}, CL)[0] == "fail"
    assert vf.decide(low, {"present": True, "value": "R7 500 000"}, CL)[0] == "pass"
    hi = item("value_rule", "maximum 80 points")
    assert vf.decide(hi, {"present": True, "value": "90"}, CL)[0] == "fail"


def test_unsigned_missing_unread():
    assert vf.decide(item("signature"), {"present": True, "signed": False}, CL)[0] == "fail"
    assert vf.decide(item("document"), {"present": False}, CL)[0] == "missing"
    assert vf.decide(item("document"), None, CL)[0] == "unread"


def page(n, text):
    return NS(number=n, text=text, doc_type="unknown", image_path=f"p{n}.jpg")


def test_rank_pages():
    pages = [page(1, "CIDB registration certificate contractor grading 4CE " * 3), page(2, "unrelated words here " * 5)]
    assert [p.number for p in vf.rank_pages(pages, item("document"))] == [1]


class Scripted:
    def __init__(self, answers):
        self.answers, self.n = answers, 0

    def complete_json(self, system, user, **kw):
        a = self.answers[min(self.n, len(self.answers) - 1)]
        self.n += 1
        return a


def test_failure_needs_second_read_to_confirm():
    pages = [page(4, "CIDB registration certificate contractor grading " * 3)]
    bad = {"present": True, "valid_until": "2025-01-01", "quote": "valid until 2025-01-01", "page": 4}
    good = {"present": True, "valid_until": "2026-01-01", "quote": "x", "page": 4}
    it = item("date_validity")
    v = vf.verify_item(Scripted([bad, bad]), Facts(), pages, it, CL, vf.CallBudget())
    assert v.status == "fail" and v.confirmed
    v = vf.verify_item(Scripted([bad, good]), Facts(), pages, it, CL, vf.CallBudget())
    assert v.status == "fail" and not v.confirmed


def test_call_cap_and_no_candidate():
    pages = [page(4, "CIDB registration certificate contractor grading " * 3)]
    v = vf.verify_item(Scripted([{}]), Facts(), pages, item("document"), CL, vf.CallBudget(0))
    assert v.status == "unread"
    v = vf.verify_item(Scripted([{}]), Facts(), [], item("document"), CL, vf.CallBudget())
    assert v.status == "missing"


def test_audit_rule_tiers():
    from tenderlens.audit import Auditor
    a = Auditor.__new__(Auditor)
    a.res = NS(findings=[], passed=[]); a._n = 0
    a.f = NS(verdicts=[vf.Verdict(item("document"), "fail", "expired", [3], "q", True),
                       vf.Verdict(item("document", label="Other"), "missing", "none"),
                       vf.Verdict(item("document", label="Fine"), "pass", "", [5])])
    a.E = lambda *x, **k: None
    a.requirement_checks()
    tiers = [f.tier for f in a.res.findings]
    assert tiers == ["issue", "observation"] and a.res.passed
