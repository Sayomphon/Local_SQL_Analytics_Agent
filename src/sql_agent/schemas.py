"""Typed contracts shared by every component in the agent pipeline.

Every boundary in the architecture (inspector -> selector -> generator ->
policy -> executor -> repair -> presenter) exchanges one of these models, so a
contract change is visible in exactly one place. All models are JSON-serializable
to keep the full agent trace exportable for provenance and evaluation.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Schema introspection
# ---------------------------------------------------------------------------


class ColumnInfo(BaseModel):
    name: str
    type: str = ""
    nullable: bool = True
    primary_key: bool = False


class ForeignKeyInfo(BaseModel):
    table: str
    column: str
    ref_table: str
    ref_column: str


class TableInfo(BaseModel):
    name: str
    columns: list[ColumnInfo] = Field(default_factory=list)
    row_count: int | None = None
    sample_rows: list[dict[str, Any]] = Field(default_factory=list)

    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]


class SchemaContext(BaseModel):
    """Snapshot of one database's structure used as the single source of truth
    for schema selection, prompt construction, and policy table allow-listing."""

    database_id: str
    dialect: str = "sqlite"
    tables: list[TableInfo] = Field(default_factory=list)
    foreign_keys: list[ForeignKeyInfo] = Field(default_factory=list)

    def table_names(self) -> set[str]:
        return {t.name for t in self.tables}

    def columns_for(self, table: str) -> set[str]:
        for t in self.tables:
            if t.name.lower() == table.lower():
                return set(t.column_names())
        return set()

    def all_columns(self) -> set[str]:
        cols: set[str] = set()
        for t in self.tables:
            cols.update(t.column_names())
        return cols

    def subset(self, tables: list[str]) -> SchemaContext:
        wanted = {t.lower() for t in tables}
        kept = [t for t in self.tables if t.name.lower() in wanted]
        kept_names = {t.name for t in kept}
        fks = [
            fk
            for fk in self.foreign_keys
            if fk.table in kept_names and fk.ref_table in kept_names
        ]
        return SchemaContext(
            database_id=self.database_id,
            dialect=self.dialect,
            tables=kept,
            foreign_keys=fks,
        )

    def to_prompt_text(self, *, include_samples: bool = True, max_samples: int = 2) -> str:
        """Compact schema description used inside LLM prompts."""
        lines: list[str] = []
        for t in self.tables:
            cols = []
            for c in t.columns:
                marks = []
                if c.primary_key:
                    marks.append("PK")
                if not c.nullable:
                    marks.append("NOT NULL")
                suffix = f" {' '.join(marks)}" if marks else ""
                cols.append(f"  {c.name} {c.type}{suffix}".rstrip())
            row_info = f"  -- ~{t.row_count} rows" if t.row_count is not None else ""
            lines.append(f"TABLE {t.name} ({row_info.strip()}".rstrip() + ")" if row_info else f"TABLE {t.name}")
            lines.extend(cols)
            if include_samples and t.sample_rows:
                for row in t.sample_rows[:max_samples]:
                    lines.append(f"  -- sample: {row}")
        for fk in self.foreign_keys:
            lines.append(
                f"FK {fk.table}.{fk.column} -> {fk.ref_table}.{fk.ref_column}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# LLM output contract
# ---------------------------------------------------------------------------


class ChartHint(BaseModel):
    kind: Literal["bar", "line", "none"] = "none"
    x: str | None = None
    y: list[str] = Field(default_factory=list)


class SQLCandidate(BaseModel):
    """Structured output the model must return. Anything that does not
    validate against this schema is treated as a format failure, never
    silently repaired into an executable query."""

    sql: str = ""
    assumptions: list[str] = Field(default_factory=list)
    selected_tables: list[str] = Field(default_factory=list)
    chart_hint: ChartHint | None = None
    clarification_needed: bool = False
    clarification_question: str | None = None

    @field_validator("sql")
    @classmethod
    def _strip_sql(cls, v: str) -> str:
        return v.strip().rstrip(";").strip()


# ---------------------------------------------------------------------------
# Policy and execution contracts
# ---------------------------------------------------------------------------

BlockCategory = Literal[
    "empty_sql",
    "sql_too_long",
    "parse_error",
    "multi_statement",
    "not_read_only",
    "forbidden_construct",
    "forbidden_function",
    "unknown_table",
    "unsupported_syntax",
]


class PolicyResult(BaseModel):
    """Deterministic verdict from the AST policy engine.

    ``repairable=True`` marks violations worth sending back to the model
    (e.g. hallucinated table names); destructive intent is terminal."""

    allowed: bool
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    normalized_sql: str | None = None
    repairable: bool = False
    policy_version: str = ""


ErrorType = Literal["syntax", "missing_entity", "timeout", "denied", "empty_sql", "other"]


class ExecutionResult(BaseModel):
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    elapsed_ms: float = 0.0
    error: str | None = None
    error_type: ErrorType | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


# ---------------------------------------------------------------------------
# Agent state machine
# ---------------------------------------------------------------------------

AgentStatus = Literal[
    "planning",
    "generating",
    "blocked",
    "executing",
    "repairing",
    "completed",
    "needs_clarification",
    "failed",
]


class SQLAttempt(BaseModel):
    attempt_no: int
    raw_response: str | None = None
    candidate: SQLCandidate | None = None
    parse_error: str | None = None
    policy: PolicyResult | None = None
    execution: ExecutionResult | None = None
    feedback: str | None = None
    elapsed_ms: float = 0.0


class Provenance(BaseModel):
    model_name: str = "unknown"
    prompt_version: str = ""
    policy_version: str = ""
    started_at: str = ""
    total_elapsed_ms: float = 0.0


class AgentState(BaseModel):
    """Full, serializable trace of one question's journey through the agent."""

    question: str
    database_id: str
    selected_tables: list[str] = Field(default_factory=list)
    attempts: list[SQLAttempt] = Field(default_factory=list)
    status: AgentStatus = "planning"
    stop_reason: str | None = None
    answer: str | None = None
    provenance: Provenance = Field(default_factory=Provenance)

    @property
    def final_attempt(self) -> SQLAttempt | None:
        return self.attempts[-1] if self.attempts else None

    @property
    def final_sql(self) -> str | None:
        for attempt in reversed(self.attempts):
            if attempt.policy and attempt.policy.normalized_sql:
                return attempt.policy.normalized_sql
            if attempt.candidate and attempt.candidate.sql:
                return attempt.candidate.sql
        return None

    @property
    def final_execution(self) -> ExecutionResult | None:
        for attempt in reversed(self.attempts):
            if attempt.execution is not None:
                return attempt.execution
        return None


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class AgentConfig(BaseModel):
    """Runtime limits. Every ceiling here is enforced in code, not by prompt."""

    max_retries: int = 2  # repair attempts after the first one
    format_retries: int = 1  # extra tries when the model returns invalid JSON
    default_limit: int = 200  # LIMIT injected when the query has none
    max_limit: int = 200  # LIMIT above this is clamped down
    row_cap: int = 200  # hard cap enforced by the executor
    timeout_ms: int = 5000
    max_sql_length: int = 4000
    small_schema_threshold: int = 5  # <= this many tables -> skip selector
    max_prompt_tables: int = 4
    max_new_tokens: int = 512
    temperature: float = 0.0
    model_name: str = "scripted"
    prompt_version: str = "v2"
