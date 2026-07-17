"""Presentation of validated results: tables, charts, answers, trace.

Everything rendered here comes from the actual ExecutionResult — the model
never writes numbers into the answer. Charts are produced only when the chart
hint refers to columns that really exist in the result with sane shapes
(1 dimension + 1–2 numeric metrics, per the MVP scope).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .schemas import AgentState, ChartHint, ExecutionResult


def to_dataframe(execution: ExecutionResult) -> pd.DataFrame:
    return pd.DataFrame(execution.rows, columns=execution.columns or None)


def _is_numeric(series: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(series)


def maybe_chart(
    execution: ExecutionResult,
    hint: ChartHint | None,
    out_path: str | Path,
) -> Path | None:
    """Render a bar/line chart when — and only when — the hint is honest.

    Returns the saved path, or None when no chart is warranted. Never invents
    columns: every hinted column must exist in the executed result.
    """
    if execution.error or not execution.rows or hint is None or hint.kind == "none":
        return None
    if not hint.x or not hint.y or len(hint.y) > 2:
        return None

    df = to_dataframe(execution)
    if hint.x not in df.columns or any(col not in df.columns for col in hint.y):
        return None
    if not all(_is_numeric(df[col]) for col in hint.y):
        return None

    import matplotlib

    matplotlib.use("Agg")  # headless-safe; no GUI required
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8, 4.5))
    plot_df = df[[hint.x, *hint.y]].set_index(hint.x)
    plot_df.plot(kind=hint.kind, ax=ax)
    ax.set_title(f"{', '.join(hint.y)} by {hint.x}")
    ax.set_xlabel(hint.x)
    fig.tight_layout()

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=120)
    plt.close(fig)
    return out


def summarize(state: AgentState) -> str:
    """Short factual answer derived from the final execution only."""
    if state.status == "blocked":
        return f"Blocked by SQL policy: {state.stop_reason}"
    if state.status == "needs_clarification":
        return state.answer or "The question needs clarification."
    if state.status == "failed":
        return f"Failed after {len(state.attempts)} attempt(s): {state.stop_reason}"

    execution = state.final_execution
    if execution is None or not execution.ok:
        return "No result available."
    if execution.row_count == 0:
        return (
            "The query executed successfully but returned no rows for the "
            "given criteria."
        )
    cols = ", ".join(execution.columns)
    suffix = " (truncated)" if execution.truncated else ""
    return f"Returned {execution.row_count} row(s){suffix} with columns: {cols}."


def render_trace(state: AgentState) -> str:
    """Human-readable attempt-by-attempt trace for CLI and notebooks."""
    lines: list[str] = []
    lines.append(f"Question   : {state.question}")
    lines.append(f"Database   : {state.database_id}")
    lines.append(f"Tables     : {', '.join(state.selected_tables) or '-'}")
    lines.append(
        f"Model      : {state.provenance.model_name} "
        f"(prompt {state.provenance.prompt_version}, policy {state.provenance.policy_version})"
    )
    for attempt in state.attempts:
        lines.append(f"--- Attempt {attempt.attempt_no} ---")
        if attempt.feedback:
            lines.append(f"  feedback  : {attempt.feedback.splitlines()[0]}")
        if attempt.parse_error:
            lines.append(f"  parse     : FAILED ({attempt.parse_error})")
            continue
        if attempt.candidate:
            lines.append(f"  sql       : {attempt.candidate.sql}")
            if attempt.candidate.assumptions:
                lines.append(f"  assumes   : {'; '.join(attempt.candidate.assumptions)}")
        if attempt.policy:
            verdict = "ALLOWED" if attempt.policy.allowed else "BLOCKED"
            detail = ", ".join(attempt.policy.reasons) or "-"
            lines.append(f"  policy    : {verdict} ({detail})")
            if attempt.policy.warnings:
                lines.append(f"  warnings  : {', '.join(attempt.policy.warnings)}")
        if attempt.execution:
            if attempt.execution.ok:
                lines.append(
                    f"  execute   : OK {attempt.execution.row_count} rows "
                    f"in {attempt.execution.elapsed_ms:.0f} ms"
                )
            else:
                lines.append(
                    f"  execute   : ERROR ({attempt.execution.error_type}) "
                    f"{attempt.execution.error}"
                )
    lines.append(f"Status     : {state.status.upper()}")
    if state.stop_reason:
        lines.append(f"Stop reason: {state.stop_reason}")
    lines.append(f"Answer     : {summarize(state)}")
    lines.append(f"Elapsed    : {state.provenance.total_elapsed_ms:.0f} ms")
    return "\n".join(lines)
