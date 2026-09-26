"""Vendor-neutral operational metrics (Section 200D).

No metrics library existed in this project (no prometheus_client / OpenTelemetry / StatsD), so
this is the smallest useful implementation: thread-safe counters, gauges and histograms rendered
in the Prometheus text exposition format by `GET /metrics`. It has NO dependency, opens no
network connection and ships nothing anywhere -- an operator (or a later Prometheus / Cloud
Monitoring agent, Section 200E+) scrapes it.

Safety rules enforced HERE, not left to call sites:

  * Every metric is declared with a FIXED set of label names; recording with any other label
    name is dropped (never raised).
  * Label values are low-cardinality enums chosen by the instrumentation code. Trip / job / user /
    revision ids, destinations, free text, URLs and request ids are never labels. As a backstop each
    metric keeps at most `MAX_LABEL_SETS` distinct label sets; further new combinations collapse
    into one `__overflow__` series instead of growing without bound.
  * Recording NEVER raises and never blocks the caller for long (a single short lock): a metrics
    problem can never make the application unavailable. Metrics are observability, not state --
    `/health` and `/ready` do not depend on them.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from typing import Iterable, Mapping

MAX_LABEL_SETS = 200
OVERFLOW_VALUE = "__overflow__"

DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0)
_LabelKey = tuple[str, ...]


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _clean_value(value: object) -> str:
    text = str(value)
    return text if len(text) <= 64 else text[:64]


@dataclass
class _Metric:
    name: str
    help: str
    kind: str  # counter | gauge | histogram
    labels: tuple[str, ...]
    buckets: tuple[float, ...] = ()
    values: dict[_LabelKey, float] = field(default_factory=dict)
    hist: dict[_LabelKey, list[float]] = field(default_factory=dict)  # [bucket counts..., +Inf count, sum]


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._metrics: dict[str, _Metric] = {}
        self.enabled = True

    # -- declaration -------------------------------------------------------------------------------------
    def counter(self, name: str, help: str, labels: Iterable[str] = ()) -> None:
        self._declare(_Metric(name, help, "counter", tuple(labels)))

    def gauge(self, name: str, help: str, labels: Iterable[str] = ()) -> None:
        self._declare(_Metric(name, help, "gauge", tuple(labels)))

    def histogram(
        self, name: str, help: str, labels: Iterable[str] = (), buckets: tuple[float, ...] = DEFAULT_BUCKETS
    ) -> None:
        self._declare(_Metric(name, help, "histogram", tuple(labels), buckets=tuple(sorted(buckets))))

    def _declare(self, metric: _Metric) -> None:
        with self._lock:
            self._metrics.setdefault(metric.name, metric)

    # -- recording (never raises) ----------------------------------------------------------------------------
    def _key(self, metric: _Metric, labels: Mapping[str, object] | None) -> _LabelKey | None:
        provided = labels or {}
        if set(provided) - set(metric.labels):
            return None  # an undeclared label name is dropped, never recorded
        key = tuple(_clean_value(provided.get(name, "")) for name in metric.labels)
        store = metric.hist if metric.kind == "histogram" else metric.values
        if key not in store and len(store) >= MAX_LABEL_SETS:
            return tuple(OVERFLOW_VALUE for _ in metric.labels)
        return key

    def inc(self, name: str, labels: Mapping[str, object] | None = None, value: float = 1.0) -> None:
        try:
            if not self.enabled:
                return
            with self._lock:
                metric = self._metrics.get(name)
                if metric is None or metric.kind == "histogram":
                    return
                key = self._key(metric, labels)
                if key is not None:
                    metric.values[key] = metric.values.get(key, 0.0) + value
        except Exception:  # noqa: BLE001 - metrics must never break the app
            return

    def set(self, name: str, value: float, labels: Mapping[str, object] | None = None) -> None:
        try:
            if not self.enabled:
                return
            with self._lock:
                metric = self._metrics.get(name)
                if metric is None or metric.kind != "gauge":
                    return
                key = self._key(metric, labels)
                if key is not None:
                    metric.values[key] = float(value)
        except Exception:  # noqa: BLE001
            return

    def observe(self, name: str, value: float, labels: Mapping[str, object] | None = None) -> None:
        try:
            if not self.enabled or not math.isfinite(value):
                return
            with self._lock:
                metric = self._metrics.get(name)
                if metric is None or metric.kind != "histogram":
                    return
                key = self._key(metric, labels)
                if key is None:
                    return
                row = metric.hist.setdefault(key, [0.0] * (len(metric.buckets) + 2))
                for i, bound in enumerate(metric.buckets):
                    if value <= bound:
                        row[i] += 1
                row[len(metric.buckets)] += 1  # +Inf / count
                row[len(metric.buckets) + 1] += value  # sum
        except Exception:  # noqa: BLE001
            return

    # -- reading ---------------------------------------------------------------------------------------------
    def value(self, name: str, labels: Mapping[str, object] | None = None) -> float:
        """Current counter/gauge value (0.0 if absent) -- used by tests and the smoke checks."""
        with self._lock:
            metric = self._metrics.get(name)
            if metric is None:
                return 0.0
            key = tuple(_clean_value((labels or {}).get(n, "")) for n in metric.labels)
            if metric.kind == "histogram":
                row = metric.hist.get(key)
                return row[len(metric.buckets)] if row else 0.0
            return metric.values.get(key, 0.0)

    def total(self, name: str) -> float:
        with self._lock:
            metric = self._metrics.get(name)
            if metric is None:
                return 0.0
            if metric.kind == "histogram":
                return sum(r[len(metric.buckets)] for r in metric.hist.values())
            return sum(metric.values.values())

    def label_sets(self, name: str) -> list[dict[str, str]]:
        with self._lock:
            metric = self._metrics.get(name)
            if metric is None:
                return []
            store = metric.hist if metric.kind == "histogram" else metric.values
            return [dict(zip(metric.labels, key)) for key in store]

    def reset(self) -> None:
        with self._lock:
            for metric in self._metrics.values():
                metric.values.clear()
                metric.hist.clear()

    def render(self) -> str:
        lines: list[str] = []
        with self._lock:
            for metric in sorted(self._metrics.values(), key=lambda m: m.name):
                lines.append(f"# HELP {metric.name} {metric.help}")
                lines.append(f"# TYPE {metric.name} {metric.kind}")
                if metric.kind == "histogram":
                    for key, row in sorted(metric.hist.items()):
                        base = dict(zip(metric.labels, key))
                        for i, bound in enumerate(metric.buckets):
                            lines.append(f"{metric.name}_bucket{_fmt({**base, 'le': repr(bound)})} {row[i]:g}")
                        lines.append(f"{metric.name}_bucket{_fmt({**base, 'le': '+Inf'})} {row[len(metric.buckets)]:g}")
                        lines.append(f"{metric.name}_sum{_fmt(base)} {row[len(metric.buckets) + 1]:g}")
                        lines.append(f"{metric.name}_count{_fmt(base)} {row[len(metric.buckets)]:g}")
                else:
                    for key, value in sorted(metric.values.items()):
                        lines.append(f"{metric.name}{_fmt(dict(zip(metric.labels, key)))} {value:g}")
        return "\n".join(lines) + "\n"


def _fmt(labels: Mapping[str, str]) -> str:
    live = {k: v for k, v in labels.items()}
    if not live:
        return ""
    return "{" + ",".join(f'{k}="{_escape(v)}"' for k, v in live.items()) + "}"


registry = MetricsRegistry()

# -- the metric catalogue (names, labels and help are the contract) ------------------------------------------
_P = "travelobligator_"

registry.counter(_P + "http_requests_total", "HTTP requests by method, route TEMPLATE and status class.", ("method", "route", "status_class"))
registry.histogram(_P + "http_request_duration_seconds", "HTTP request duration by method and route TEMPLATE.", ("method", "route"))

registry.counter(_P + "db_transactions_total", "Database transactions by scope (single|unit_of_work) and outcome (commit|rollback).", ("scope", "outcome"))
registry.histogram(_P + "db_transaction_duration_seconds", "Database transaction duration by scope.", ("scope",), buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0))
registry.counter(_P + "db_concurrency_conflicts_total", "Optimistic-concurrency / compare-and-set conflicts (stale writer rejected) by kind.", ("kind",))
registry.counter(_P + "db_transaction_retries_total", "Transactions re-run after a transient database condition, by reason.", ("reason",))

registry.counter(_P + "jobs_total", "Async job lifecycle events by job type and event.", ("job_type", "event"))
registry.gauge(_P + "jobs_running_local", "Jobs this process currently owns (running under its lease).", ())

registry.counter(_P + "provider_cache_total", "Provider-response cache outcomes by provider and status (HIT|MISS|BYPASS|ERROR).", ("provider", "status"))

registry.counter(_P + "provider_calls_total", "External provider calls through the gateway by provider, stage and outcome.", ("provider", "stage", "outcome"))
registry.histogram(_P + "provider_call_duration_seconds", "External provider call duration by provider and stage.", ("provider", "stage"))

registry.gauge(_P + "dependency_up", "Last readiness observation per dependency (1 = healthy, 0.5 = degraded, 0 = failed).", ("dependency",))
