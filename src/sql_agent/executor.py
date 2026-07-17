"""Read-only SQL execution with layered defenses.

Execution happens only after the AST policy has approved the query, and even
then the database connection itself cannot write:

1. SQLite URI ``mode=ro`` — the OS-level file handle is read-only.
2. ``PRAGMA query_only = ON`` — the connection rejects writes.
3. ``sqlite3`` authorizer callback — every prepared operation outside
   SELECT/READ/FUNCTION is denied at statement-compile time.
4. Progress-handler deadline + row cap — runaway queries are interrupted and
   result sizes are bounded.

Error messages are sanitized (no filesystem paths) before they are stored in
traces or fed back to the model.
"""

from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from .schema_inspector import open_readonly
from .schemas import AgentConfig, ErrorType, ExecutionResult

# Authorizer action codes that a pure SELECT needs. Everything else is denied.
_ALLOWED_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    getattr(sqlite3, "SQLITE_FUNCTION", 31),
    getattr(sqlite3, "SQLITE_RECURSIVE", 33),
}

_PROGRESS_HANDLER_OPS = 2_000  # VM instructions between deadline checks

_PATH_PATTERN = re.compile(r"(file:)?/[\w\-./%~]+")
_MAX_ERROR_LEN = 500


def _readonly_authorizer(action: int, *_args: Any) -> int:
    if action in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def sanitize_error(message: str, db_path: str | Path | None = None) -> str:
    """Strip filesystem details from an error before it reaches trace or model."""
    text = str(message)
    if db_path is not None:
        text = text.replace(str(db_path), "<database>")
    text = _PATH_PATTERN.sub("<path>", text)
    if len(text) > _MAX_ERROR_LEN:
        text = text[:_MAX_ERROR_LEN] + "…"
    return text


def _classify_error(message: str) -> ErrorType:
    lowered = message.lower()
    if "interrupted" in lowered:
        return "timeout"
    if "no such table" in lowered or "no such column" in lowered:
        return "missing_entity"
    if "syntax error" in lowered:
        return "syntax"
    if "not authorized" in lowered or "readonly" in lowered or "read-only" in lowered:
        return "denied"
    return "other"


def _clean_cell(value: Any) -> Any:
    if isinstance(value, bytes):
        return f"<blob {len(value)} bytes>"
    return value


def execute_readonly(
    db_path: str | Path,
    sql: str,
    config: AgentConfig | None = None,
) -> ExecutionResult:
    """Execute one approved SELECT against a SQLite file, read-only.

    Never raises for SQL-level problems; failures come back as a structured
    :class:`ExecutionResult` with a sanitized ``error`` so the repair loop can
    consume them.
    """
    config = config or AgentConfig()
    sql = (sql or "").strip()
    if not sql:
        return ExecutionResult(error="empty sql", error_type="empty_sql")

    started = time.monotonic()
    deadline = started + config.timeout_ms / 1000.0

    def _check_deadline() -> int:
        # Non-zero return makes SQLite abort with 'interrupted'.
        return 1 if time.monotonic() > deadline else 0

    try:
        conn = open_readonly(db_path)
    except Exception as exc:  # noqa: BLE001 - surface as structured error
        return ExecutionResult(
            error=sanitize_error(str(exc), db_path), error_type="other"
        )

    try:
        conn.set_progress_handler(_check_deadline, _PROGRESS_HANDLER_OPS)
        conn.set_authorizer(_readonly_authorizer)
        try:
            cursor = conn.execute(sql)
            raw_rows = cursor.fetchmany(config.row_cap + 1)
        except sqlite3.Error as exc:
            message = sanitize_error(str(exc), db_path)
            return ExecutionResult(
                error=message,
                error_type=_classify_error(message),
                elapsed_ms=(time.monotonic() - started) * 1000.0,
            )

        truncated = len(raw_rows) > config.row_cap
        if truncated:
            raw_rows = raw_rows[: config.row_cap]

        columns = [d[0] for d in cursor.description] if cursor.description else []
        rows = [[_clean_cell(v) for v in row] for row in raw_rows]

        return ExecutionResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            elapsed_ms=(time.monotonic() - started) * 1000.0,
        )
    finally:
        conn.close()


class ReadOnlyExecutor:
    """Small wrapper binding a database path + config, used by the agent loop."""

    def __init__(self, db_path: str | Path, config: AgentConfig | None = None) -> None:
        self.db_path = Path(db_path)
        self.config = config or AgentConfig()

    def execute(self, sql: str) -> ExecutionResult:
        return execute_readonly(self.db_path, sql, self.config)
