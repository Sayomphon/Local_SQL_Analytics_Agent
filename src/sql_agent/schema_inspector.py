"""Read-only SQLite schema introspection.

Produces a :class:`~sql_agent.schemas.SchemaContext` from a database file using
``sqlite_master`` and ``PRAGMA`` metadata only. The connection is opened in
read-only mode so inspection can never mutate the target database, and sample
values are truncated before they ever reach a prompt.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .schemas import ColumnInfo, ForeignKeyInfo, SchemaContext, TableInfo

_MAX_SAMPLE_TEXT = 60


class SchemaInspectionError(RuntimeError):
    """Raised when the database cannot be read; the agent must stop before
    any model call happens (fail closed, per the architecture contract)."""


def open_readonly(db_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection that cannot write, shared by inspector and executor."""
    path = Path(db_path)
    if not path.is_file():
        raise SchemaInspectionError(f"database file not found: {path.name}")
    uri = f"file:{quote(str(path))}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA query_only = ON")
    return conn


def _quote_ident(name: str) -> str:
    """Safely double-quote an identifier that came from sqlite_master."""
    return '"' + name.replace('"', '""') + '"'


def _clean_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return f"<blob {len(value)} bytes>"
    if isinstance(value, str) and len(value) > _MAX_SAMPLE_TEXT:
        return value[:_MAX_SAMPLE_TEXT] + "…"
    return value


def inspect_database(
    db_path: str | Path,
    *,
    database_id: str | None = None,
    sample_rows: int = 2,
    include_row_counts: bool = True,
) -> SchemaContext:
    """Build a SchemaContext for one SQLite database.

    ``sample_rows=0`` disables sample values entirely (use this for databases
    that may contain sensitive values; the demo databases are synthetic).
    """
    path = Path(db_path)
    db_id = database_id or path.stem

    conn = open_readonly(path)
    try:
        table_names = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]

        tables: list[TableInfo] = []
        foreign_keys: list[ForeignKeyInfo] = []

        for table in table_names:
            qt = _quote_ident(table)

            columns = [
                ColumnInfo(
                    name=row[1],
                    type=(row[2] or ""),
                    nullable=not row[3],
                    primary_key=bool(row[5]),
                )
                for row in conn.execute(f"PRAGMA table_info({qt})")
            ]

            for fk in conn.execute(f"PRAGMA foreign_key_list({qt})"):
                # fk: (id, seq, ref_table, from_col, to_col, ...)
                ref_col = fk[4]
                if ref_col is None:
                    # Implicit reference to the referenced table's primary key.
                    ref_col = "rowid"
                foreign_keys.append(
                    ForeignKeyInfo(
                        table=table, column=fk[3], ref_table=fk[2], ref_column=ref_col
                    )
                )

            row_count = None
            if include_row_counts:
                # qt is an identifier read from sqlite_master and re-quoted by
                # _quote_ident — not user input, so interpolation is safe here.
                row_count = conn.execute(f"SELECT COUNT(*) FROM {qt}").fetchone()[0]  # noqa: S608

            samples: list[dict[str, Any]] = []
            if sample_rows > 0 and columns:
                cur = conn.execute(f"SELECT * FROM {qt} LIMIT ?", (sample_rows,))  # noqa: S608
                col_names = [d[0] for d in cur.description]
                for row in cur.fetchall():
                    samples.append(
                        {
                            name: _clean_value(v)
                            for name, v in zip(col_names, row, strict=True)
                        }
                    )

            tables.append(
                TableInfo(
                    name=table,
                    columns=columns,
                    row_count=row_count,
                    sample_rows=samples,
                )
            )

        return SchemaContext(
            database_id=db_id,
            dialect="sqlite",
            tables=tables,
            foreign_keys=foreign_keys,
        )
    except sqlite3.Error as exc:  # pragma: no cover - defensive
        raise SchemaInspectionError(f"failed to inspect database: {exc}") from exc
    finally:
        conn.close()
