"""What a running audit can show before it is finished: which pages are being read, what each page's first
reading turned up, and the buyer's requirements the moment the text step has them.

A plain, thread-safe holder with no Streamlit in it. The audit's worker threads write to it; the app's own thread
reads a snapshot every half second and draws it. Final findings are NOT here: the rules compare pages with each
other, so a finding only exists once every page has been read.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

WAITING, READING, SECOND_LOOK, DONE, FAILED = "waiting", "reading", "second look", "done", "not read"


@dataclass
class PageLive:
    number: int
    form: str = ""
    status: str = WAITING
    leads: list[str] = field(default_factory=list)


class LiveState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pages: dict[int, PageLive] = {}   # only pages that will be read by the vision model
        self._forms: dict[int, str] = {}
        self._requirements = None
        self.stage = ""

    def set_pages(self, pages, label=lambda dtype: dtype) -> None:
        """The form type of every page, once the pages are classified."""
        with self._lock:
            for p in pages:
                self._forms[p.number] = label(p.doc_type) if p.doc_type != "blank" else "Blank page"
                if p.number in self._pages:
                    self._pages[p.number].form = self._forms[p.number]

    def expect(self, numbers) -> None:
        """These pages are going to be read: they show as waiting, and count towards "page N of M"."""
        with self._lock:
            for n in numbers:
                self._pages.setdefault(n, PageLive(n, self._forms.get(n, "")))

    def set_requirements(self, req) -> None:
        with self._lock:
            self._requirements = req

    def page(self, number: int, status: str, leads: list[str] | None = None) -> None:
        with self._lock:
            row = self._pages.setdefault(number, PageLive(number, self._forms.get(number, "")))
            row.status = status
            if leads is not None:
                row.leads = list(leads)

    def requirements(self):
        with self._lock:
            return self._requirements

    def snapshot(self) -> list[PageLive]:
        """A copy of the page rows, in page order, safe to read while the audit keeps writing."""
        with self._lock:
            return [PageLive(r.number, r.form, r.status, list(r.leads)) for _, r in sorted(self._pages.items())]

    def counts(self) -> tuple[int, int]:
        """(pages whose first reading and second looks are finished, pages being tracked)."""
        rows = self.snapshot()
        return sum(1 for r in rows if r.status in (DONE, FAILED)), len(rows)
