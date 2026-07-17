"""Metric math and presentation tests (no LLM anywhere)."""

from __future__ import annotations

from sql_agent.evaluation import (
    EvalRecord,
    canonicalize_rows,
    compare_results,
    compute_metrics,
    has_order_by,
)
from sql_agent.presenter import maybe_chart, summarize, to_dataframe
from sql_agent.schemas import AgentState, ChartHint, ExecutionResult

# --- canonicalization / comparison -----------------------------------------


def _exec(columns, rows, error=None) -> ExecutionResult:
    return ExecutionResult(
        columns=columns, rows=rows, row_count=len(rows), error=error
    )


def test_has_order_by():
    assert has_order_by("SELECT a FROM t ORDER BY a")
    assert not has_order_by("SELECT a FROM t")
    # Subquery ORDER BY does not make the outer result ordered.
    assert not has_order_by("SELECT * FROM (SELECT a FROM t ORDER BY a)")


def test_canonicalize_sorts_when_order_free():
    rows = [[2, "b"], [1, "a"]]
    assert canonicalize_rows(rows, respect_order=False) == [(1, "a"), (2, "b")]
    assert canonicalize_rows(rows, respect_order=True) == [(2, "b"), (1, "a")]


def test_canonicalize_rounds_floats():
    a = canonicalize_rows([[0.1 + 0.2]], respect_order=True)
    b = canonicalize_rows([[0.3]], respect_order=True)
    assert a == b


def test_canonicalize_handles_mixed_types_without_raising():
    rows = [[None, 1], ["x", 2], [3.5, 0]]
    assert len(canonicalize_rows(rows, respect_order=False)) == 3


def test_compare_matches_regardless_of_alias_names():
    predicted = _exec(["total"], [[42]])
    gold = _exec(["cnt"], [[42]])
    assert compare_results(predicted, gold, respect_order=False).match


def test_compare_detects_column_count_mismatch():
    result = compare_results(
        _exec(["a", "b"], [[1, 2]]), _exec(["a"], [[1]]), respect_order=False
    )
    assert not result.match
    assert result.reason.startswith("column_count")


def test_compare_detects_row_differences():
    result = compare_results(
        _exec(["a"], [[1]]), _exec(["a"], [[2]]), respect_order=False
    )
    assert not result.match
    assert result.reason == "row_values"


def test_compare_respects_order_when_required():
    predicted = _exec(["a"], [[2], [1]])
    gold = _exec(["a"], [[1], [2]])
    assert compare_results(predicted, gold, respect_order=False).match
    assert not compare_results(predicted, gold, respect_order=True).match


def test_compare_execution_error_never_matches():
    result = compare_results(
        _exec([], [], error="boom"), _exec(["a"], [[1]]), respect_order=False
    )
    assert not result.match


# --- metrics ----------------------------------------------------------------


def _record(**kwargs) -> EvalRecord:
    base = {
        "question_id": "q", "category": "business", "status": "completed",
        "attempts": 1, "first_attempt_executed": True, "final_executed": True,
        "result_match": True, "blocked": False, "expected_blocked": False,
        "needs_clarification": False, "latency_ms": 100.0,
    }
    base.update(kwargs)
    return EvalRecord(**base)


def test_metrics_business_rates():
    records = [
        _record(question_id="q1"),
        _record(question_id="q2", first_attempt_executed=False),  # repaired
        _record(
            question_id="q3",
            first_attempt_executed=False,
            final_executed=False,
            result_match=False,
            status="failed",
        ),
    ]
    metrics = compute_metrics(records)
    assert metrics.business_total == 3
    assert metrics.execution_success_rate == round(2 / 3, 4)
    assert metrics.first_attempt_success_rate == round(1 / 3, 4)
    assert metrics.repair_opportunities == 2
    assert metrics.repair_success_rate == 0.5
    assert metrics.median_latency_ms == 100.0


def test_metrics_safety_block_rate():
    records = [
        _record(
            question_id=f"s{i}", category="safety", blocked=True,
            expected_blocked=True, final_executed=False, result_match=None,
            status="blocked",
        )
        for i in range(9)
    ] + [
        _record(
            question_id="s9", category="safety", blocked=False,
            expected_blocked=True, final_executed=True, result_match=None,
            status="completed",
        )
    ]
    metrics = compute_metrics(records)
    assert metrics.safety_total == 10
    assert metrics.unsafe_block_rate == 0.9


def test_metrics_false_block_counted():
    records = [_record(question_id="q1", blocked=True, status="blocked", final_executed=False)]
    metrics = compute_metrics(records)
    assert metrics.false_block_count == 1


def test_metrics_clarification_rate():
    records = [
        _record(
            question_id="a1", category="ambiguous", needs_clarification=True,
            result_match=None, final_executed=False, first_attempt_executed=False,
            status="needs_clarification",
        ),
        _record(
            question_id="a2", category="ambiguous", needs_clarification=False,
            result_match=None,
        ),
    ]
    metrics = compute_metrics(records)
    assert metrics.clarification_rate == 0.5


def test_metrics_empty_records():
    metrics = compute_metrics([])
    assert metrics.total == 0
    assert metrics.execution_success_rate is None


# --- presenter --------------------------------------------------------------


def test_to_dataframe_shapes():
    df = to_dataframe(_exec(["a", "b"], [[1, "x"], [2, "y"]]))
    assert list(df.columns) == ["a", "b"]
    assert len(df) == 2


def test_summarize_completed_and_empty():
    state = AgentState(question="q", database_id="db", status="completed")
    state.attempts = []
    assert "No result" in summarize(state)


def test_chart_created_for_honest_hint(tmp_path):
    execution = _exec(["month", "revenue"], [["2025-01", 10.0], ["2025-02", 20.0]])
    hint = ChartHint(kind="bar", x="month", y=["revenue"])
    out = maybe_chart(execution, hint, tmp_path / "chart.png")
    assert out is not None and out.exists()


def test_chart_refused_for_hallucinated_columns(tmp_path):
    execution = _exec(["month", "revenue"], [["2025-01", 10.0]])
    hint = ChartHint(kind="bar", x="month", y=["profit"])  # profit doesn't exist
    assert maybe_chart(execution, hint, tmp_path / "chart.png") is None


def test_chart_refused_for_non_numeric_metric(tmp_path):
    execution = _exec(["month", "note"], [["2025-01", "hello"]])
    hint = ChartHint(kind="bar", x="month", y=["note"])
    assert maybe_chart(execution, hint, tmp_path / "chart.png") is None


def test_chart_refused_on_error_or_empty(tmp_path):
    hint = ChartHint(kind="bar", x="a", y=["b"])
    assert maybe_chart(_exec([], [], error="boom"), hint, tmp_path / "c.png") is None
    assert maybe_chart(_exec(["a", "b"], []), hint, tmp_path / "c.png") is None
