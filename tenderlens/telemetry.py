"""Live counters for one audit run, safe to update from many threads.

The worker threads (OCR chunks, model calls) report into one RunTelemetry; the app, the job log and the evaluation harness read
snapshot(). Everything is behind one lock, so totals stay exact under concurrency - which the old pattern (several threads
adding into a shared dict) did not guarantee. No Streamlit and no I/O in here.
"""
from __future__ import annotations

import threading
import time

# Fireworks serverless pricing for muse-spark-open, US$ per 1,000,000 tokens (see tools/ce_eval.py, which uses the same figures)
INPUT_PRICE, CACHED_PRICE, OUTPUT_PRICE = 1.25, 0.15, 4.25


class RunTelemetry:
    def __init__(self, clock=time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self.started = clock()
        self.total_pages = 0
        self.progress: dict[str, tuple[int, int]] = {}     # stage -> (done, total)
        self.active_workers = self.peak_workers = 0
        self.calls = self.retries = self.timeouts = 0
        self.prompt_tokens = self.cached_tokens = self.completion_tokens = 0
        self._stage_open: dict[str, float] = {}
        self.stages: dict[str, float] = {}                 # stage -> wall seconds
        self.events: list[dict] = []

    # -- counters written by workers -------------------------------------------------------------
    def set_total_pages(self, n: int) -> None:
        with self._lock:
            self.total_pages = n

    def set_progress(self, stage: str, done: int, total: int) -> None:
        with self._lock:
            self.progress[stage] = (done, total)

    def worker_started(self) -> None:
        with self._lock:
            self.active_workers += 1
            self.peak_workers = max(self.peak_workers, self.active_workers)
            self.calls += 1

    def worker_finished(self) -> None:
        with self._lock:
            self.active_workers = max(0, self.active_workers - 1)

    def add_retry(self) -> None:
        with self._lock:
            self.retries += 1

    def add_timeout(self) -> None:
        with self._lock:
            self.timeouts += 1

    def add_tokens(self, prompt: int, completion: int, cached: int = 0) -> None:
        with self._lock:
            self.prompt_tokens += prompt
            self.completion_tokens += completion
            self.cached_tokens += cached

    # -- stages and events -------------------------------------------------------------------------
    def stage_start(self, name: str) -> None:
        with self._lock:
            self._stage_open[name] = self._clock()
            self.events.append({"t": round(self._clock() - self.started, 2), "event": "start", "stage": name})

    def stage_end(self, name: str) -> None:
        """Wall-clock seconds the stage took (stages may overlap, so they do not add up to the run)."""
        with self._lock:
            t0 = self._stage_open.pop(name, None)
            if t0 is not None:
                self.stages[name] = self.stages.get(name, 0.0) + (self._clock() - t0)
            self.events.append({"t": round(self._clock() - self.started, 2), "event": "end", "stage": name})

    def event(self, name: str, **detail) -> None:
        with self._lock:
            self.events.append({"t": round(self._clock() - self.started, 2), "event": name, **detail})

    # -- reading ----------------------------------------------------------------------------------------
    def cost_usd(self) -> float:
        with self._lock:
            return self._cost()

    def _cost(self) -> float:
        cached = min(self.cached_tokens, self.prompt_tokens)
        return ((self.prompt_tokens - cached) * INPUT_PRICE + cached * CACHED_PRICE + self.completion_tokens * OUTPUT_PRICE) / 1e6

    def snapshot(self) -> dict:
        with self._lock:
            done = max((d for d, _ in self.progress.values()), default=0)
            return {"processed_pages": min(done, self.total_pages) if self.total_pages else done,
                    "total_pages": self.total_pages, "active_workers": self.active_workers,
                    "peak_workers": self.peak_workers, "calls": self.calls, "retries": self.retries,
                    "timeouts": self.timeouts, "prompt_tokens": self.prompt_tokens, "cached_tokens": self.cached_tokens,
                    "completion_tokens": self.completion_tokens, "cost_usd": round(self._cost(), 4),
                    "elapsed_s": round(self._clock() - self.started, 1),
                    "stages": {k: round(v, 1) for k, v in self.stages.items()},
                    "progress": {k: list(v) for k, v in self.progress.items()}}
