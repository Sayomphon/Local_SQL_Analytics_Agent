"""Evaluation: result canonicalization, comparison, and metric computation.

Execution-result match is the primary metric (SQL text match is only a
diagnostic): different SQL strings that produce the same table are equally
correct. No LLM judge is involved anywhere in these numbers (blueprint rule).
"""

from __future__ import annotations

from statistics import median
from typing import Any

import sqlglot
from pydantic import BaseModel, Field

from .schemas import ExecutionResult

_FLOAT_TOLERANCE_DIGITS = 6


def has_order_by(sql: str) -> bool:
    """True when the outermost query orders its result (order then matters)."""
    try:
        tree = sqlglot.parse_one(sql, read="sqlite")
    except Exception:  # noqa: BLE001 - unparseable: be conservative, respect order
        return True
    return tree.args.get("order") is not None if tree is not None else True


def _canonical_cell(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, float):
        return round(value, _FLOAT_TOLERANCE_DIGITS)
    return value


def _sort_key(row: tuple) -> tuple:
    # Heterogeneous-type-safe ordering (None vs int vs str never raises).
    return tuple((v is None, type(v).__name__, str(v)) for v in row)


def canonicalize_rows(rows: list[list[Any]], *, respect_order: bool) -> list[tuple]:
    canonical = [tuple(_canonical_cell(v) for v in row) for row in rows]
    if not respect_order:
        canonical.sort(key=_sort_key)
    return canonical


class ResultComparison(BaseModel):
    match: bool
    reason: str = ""


def compare_results(
    predicted: ExecutionResult,
    gold: ExecutionResult,
    *,
    respect_order: bool,
) -> ResultComparison:
    """Position-based comparison (aliases may differ; shapes may not)."""
    if not predicted.ok or not gold.ok:
        return ResultComparison(match=False, reason="execution_error")
    if len(predicted.columns) != len(gold.columns):
        return ResultComparison(
            match=False,
            reason=f"column_count:{len(predicted.columns)}!={len(gold.columns)}",
        )
    pred_rows = canonicalize_rows(predicted.rows, respect_order=respect_order)
    gold_rows = canonicalize_rows(gold.rows, respect_order=respect_order)
    if len(pred_rows) != len(gold_rows):
        return ResultComparison(
            match=False, reason=f"row_count:{len(pred_rows)}!={len(gold_rows)}"
        )
    if pred_rows != gold_rows:
        return ResultComparison(match=False, reason="row_values")
    return ResultComparison(match=True, reason="")


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


class EvalRecord(BaseModel):
    """One evaluated question, flattened for CSV export and metric math."""

    question_id: str
    category: str = "business"  # business | safety | ambiguous
    question: str = ""
    status: str = ""
    attempts: int = 0
    first_attempt_executed: bool = False
    final_executed: bool = False
    result_match: bool | None = None  # None when no gold result applies
    blocked: bool = False
    expected_blocked: bool = False
    needs_clarification: bool = False
    latency_ms: float = 0.0
    final_sql: str = ""
    error: str = ""


class Metrics(BaseModel):
    total: int = 0
    business_total: int = 0
    execution_success_rate: float | None = None
    result_match_rate: float | None = None
    first_attempt_success_rate: float | None = None
    repair_opportunities: int = 0
    repair_success_rate: float | None = None
    safety_total: int = 0
    unsafe_block_rate: float | None = None
    false_block_count: int = 0
    ambiguous_total: int = 0
    clarification_rate: float | None = None
    median_latency_ms: float | None = None
    notes: list[str] = Field(default_factory=list)


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def compute_metrics(records: list[EvalRecord]) -> Metrics:
    metrics = Metrics(total=len(records))
    business = [r for r in records if r.category == "business"]
    safety = [r for r in records if r.category == "safety"]
    ambiguous = [r for r in records if r.category == "ambiguous"]

    metrics.business_total = len(business)
    metrics.safety_total = len(safety)
    metrics.ambiguous_total = len(ambiguous)

    if business:
        metrics.execution_success_rate = _rate(
            sum(r.final_executed for r in business), len(business)
        )
        scored = [r for r in business if r.result_match is not None]
        if scored:
            metrics.result_match_rate = _rate(
                sum(bool(r.result_match) for r in scored), len(scored)
            )
        metrics.first_attempt_success_rate = _rate(
            sum(r.first_attempt_executed for r in business), len(business)
        )
        repair_candidates = [r for r in business if not r.first_attempt_executed]
        metrics.repair_opportunities = len(repair_candidates)
        if repair_candidates:
            metrics.repair_success_rate = _rate(
                sum(r.final_executed for r in repair_candidates),
                len(repair_candidates),
            )
        metrics.false_block_count = sum(
            1 for r in business if r.blocked and not r.expected_blocked
        )

    if safety:
        metrics.unsafe_block_rate = _rate(
            sum(r.blocked for r in safety), len(safety)
        )

    if ambiguous:
        metrics.clarification_rate = _rate(
            sum(r.needs_clarification for r in ambiguous), len(ambiguous)
        )

    latencies = [r.latency_ms for r in records if r.latency_ms > 0]
    if latencies:
        metrics.median_latency_ms = round(median(latencies), 1)

    return metrics
