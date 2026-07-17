"""Agent orchestration: a small, explicit state machine with a repair loop.

The loop is deliberately framework-free so every transition is visible:

    planning -> generating -> (blocked | needs_clarification |
                executing -> (completed | repairing -> generating ...))
                -> failed when the retry budget is exhausted

Hard rules enforced here, not by prompts:

* The model's SQL never reaches the executor without a ``PolicyResult.allowed``.
* At most ``config.max_retries`` repair rounds after the first attempt.
* Repair feedback contains the sanitized error plus schema hints only —
  never database rows.
* Every attempt is recorded in the returned :class:`AgentState`, which is the
  complete provenance trace for evaluation and demos.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from .executor import ReadOnlyExecutor
from .schema_selector import select_tables
from .schemas import (
    AgentConfig,
    AgentState,
    ExecutionResult,
    PolicyResult,
    Provenance,
    SchemaContext,
    SQLAttempt,
)
from .sql_generator import PROMPT_VERSION, SQLGenerator
from .sql_policy import POLICY_VERSION, validate_sql


def _schema_hint(schema: SchemaContext, tables: list[str]) -> str:
    """Compact table->columns listing used as repair feedback (no data rows)."""
    lines = []
    wanted = {t.lower() for t in tables} if tables else None
    for table in schema.tables:
        if wanted is not None and table.name.lower() not in wanted:
            continue
        lines.append(f"{table.name}: {', '.join(table.column_names())}")
    return "\n".join(lines)


def _execution_feedback(execution: ExecutionResult, schema: SchemaContext, tables: list[str]) -> str:
    hint = _schema_hint(schema, tables)
    return (
        f"SQLite error ({execution.error_type}): {execution.error}\n"
        f"Available tables and columns:\n{hint}"
    )


def _policy_feedback(policy: PolicyResult, schema: SchemaContext) -> str:
    return (
        f"The query was rejected by the SQL policy: {', '.join(policy.reasons)}\n"
        f"Available tables: {', '.join(sorted(schema.table_names()))}"
    )


class SQLAnalyticsAgent:
    """Binds generator + policy + executor over one database."""

    def __init__(
        self,
        generator: SQLGenerator,
        executor: ReadOnlyExecutor,
        schema: SchemaContext,
        config: AgentConfig | None = None,
    ) -> None:
        self.generator = generator
        self.executor = executor
        self.schema = schema
        self.config = config or generator.config

    def run(self, question: str) -> AgentState:
        started = time.monotonic()
        state = AgentState(
            question=question,
            database_id=self.schema.database_id,
            status="planning",
            provenance=Provenance(
                model_name=getattr(self.generator.backend, "name", "unknown"),
                prompt_version=PROMPT_VERSION,
                policy_version=POLICY_VERSION,
                started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )

        state.selected_tables = select_tables(question, self.schema, self.config)
        prompt_schema = self.schema.subset(state.selected_tables)

        max_attempts = 1 + max(self.config.max_retries, 0)
        feedback: str | None = None
        last_sql = ""

        for attempt_no in range(1, max_attempts + 1):
            attempt_started = time.monotonic()
            state.status = "generating" if attempt_no == 1 else "repairing"
            attempt = SQLAttempt(attempt_no=attempt_no, feedback=feedback)

            if attempt_no == 1:
                outcome = self.generator.generate(question, prompt_schema)
            else:
                outcome = self.generator.repair(
                    question, prompt_schema, last_sql, feedback or ""
                )

            attempt.raw_response = (
                outcome.raw_responses[-1] if outcome.raw_responses else None
            )

            if outcome.candidate is None:
                attempt.parse_error = outcome.parse_error or "unknown_parse_error"
                attempt.elapsed_ms = (time.monotonic() - attempt_started) * 1000.0
                state.attempts.append(attempt)
                if outcome.parse_error and outcome.parse_error.startswith("backend_error"):
                    state.status = "failed"
                    state.stop_reason = outcome.parse_error
                    break
                feedback = (
                    "Previous response was not valid JSON matching the required "
                    "schema. Return only the JSON object."
                )
                if attempt_no == max_attempts:
                    state.status = "failed"
                    state.stop_reason = "invalid_model_output"
                continue

            candidate = outcome.candidate
            attempt.candidate = candidate

            if candidate.clarification_needed:
                attempt.elapsed_ms = (time.monotonic() - attempt_started) * 1000.0
                state.attempts.append(attempt)
                state.status = "needs_clarification"
                state.stop_reason = "ambiguous_question"
                state.answer = candidate.clarification_question or (
                    "The question is ambiguous; please clarify."
                )
                break

            # Policy check runs against the FULL schema: a real table outside
            # the prompt subset is legitimate, not a hallucination.
            policy = validate_sql(candidate.sql, self.schema, self.config)
            attempt.policy = policy
            last_sql = candidate.sql

            if not policy.allowed:
                attempt.elapsed_ms = (time.monotonic() - attempt_started) * 1000.0
                state.attempts.append(attempt)
                if policy.repairable and attempt_no < max_attempts:
                    feedback = _policy_feedback(policy, self.schema)
                    continue
                state.status = "blocked" if not policy.repairable else "failed"
                state.stop_reason = ";".join(policy.reasons) or "policy_blocked"
                break

            state.status = "executing"
            execution = self.executor.execute(policy.normalized_sql or candidate.sql)
            attempt.execution = execution
            attempt.elapsed_ms = (time.monotonic() - attempt_started) * 1000.0
            state.attempts.append(attempt)

            if execution.ok:
                state.status = "completed"
                state.stop_reason = None
                break

            if attempt_no == max_attempts:
                state.status = "failed"
                state.stop_reason = "retry_budget_exhausted"
                break

            feedback = _execution_feedback(execution, self.schema, state.selected_tables)

        state.provenance.total_elapsed_ms = (time.monotonic() - started) * 1000.0
        return state
