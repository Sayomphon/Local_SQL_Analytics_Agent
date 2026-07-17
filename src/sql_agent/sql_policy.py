"""Deterministic AST-based SQL policy engine.

Design principles (see docs/SECURITY.md):

* **Fail closed** — anything the parser cannot fully understand is blocked.
* **Allowlist the shape** — only a single ``SELECT`` (including CTEs and set
  operations) may pass; every other statement type is rejected by structure,
  not by keyword matching.
* **The LLM has no override** — this module never sees the prompt and takes no
  input from the model besides the SQL text itself.
* **Repairable vs terminal** — hallucinated table names are worth one more
  model round-trip; destructive intent is terminal and reported as ``blocked``.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from .schemas import AgentConfig, PolicyResult, SchemaContext

POLICY_VERSION = "1.0.0"

# Statement/DDL/DML node types that must never appear anywhere in the tree.
# Resolved via getattr so minor sqlglot version drift cannot silently weaken
# the policy: a missing name simply drops out, while the root-type allowlist
# below still rejects any statement that is not a plain query.
_FORBIDDEN_NODE_NAMES = (
    "Insert",
    "Update",
    "Delete",
    "Drop",
    "Create",
    "Alter",
    "AlterTable",
    "Merge",
    "TruncateTable",
    "Command",
    "Transaction",
    "Commit",
    "Rollback",
    "Pragma",
    "Attach",
    "Detach",
    "Grant",
    "Set",
    "Use",
    "Into",
    "LoadData",
    "Copy",
)
_FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = tuple(
    t for name in _FORBIDDEN_NODE_NAMES if (t := getattr(exp, name, None)) is not None
)

# Query root types that are allowed after parsing.
_ALLOWED_ROOTS: tuple[type[exp.Expression], ...] = tuple(
    t
    for name in ("Select", "Union", "Intersect", "Except")
    if (t := getattr(exp, name, None)) is not None
)

# SQLite functions with filesystem / extension-loading side effects.
_BLOCKED_FUNCTIONS = {
    "load_extension",
    "readfile",
    "writefile",
    "edit",
    "fts3_tokenizer",
    "zipfile",
    "fsdir",
}

# System tables must stay invisible even for SELECT.
_SYSTEM_TABLE_PREFIX = "sqlite_"


def _unwrap_root(tree: exp.Expression) -> exp.Expression:
    """Peel parenthesized subqueries so ``(SELECT ...)`` validates like ``SELECT ...``."""
    while isinstance(tree, exp.Subquery) and tree.this is not None:
        tree = tree.this
    return tree


def _blocked(reasons: list[str], *, repairable: bool = False, warnings: list[str] | None = None) -> PolicyResult:
    return PolicyResult(
        allowed=False,
        reasons=reasons,
        warnings=warnings or [],
        repairable=repairable,
        policy_version=POLICY_VERSION,
    )


def _collect_alias_names(tree: exp.Expression) -> set[str]:
    aliases: set[str] = set()
    for node in tree.find_all(exp.Alias):
        if node.alias:
            aliases.add(node.alias.lower())
    for cte in tree.find_all(exp.CTE):
        if cte.alias_or_name:
            aliases.add(cte.alias_or_name.lower())
    return aliases


def _check_columns(
    tree: exp.Expression, schema: SchemaContext, referenced_tables: set[str]
) -> list[str]:
    """Best-effort unknown-column detection, reported as warnings only.

    Wrong columns are deliberately NOT blocked: the read-only executor makes
    them harmless, and the resulting ``no such column`` error is exactly the
    structured feedback the repair loop is designed to consume.
    """
    warnings: list[str] = []
    known_by_table = {t.name.lower(): {c.lower() for c in t.column_names()} for t in schema.tables}
    known_all: set[str] = set()
    for name in referenced_tables:
        known_all |= known_by_table.get(name, set())
    aliases = _collect_alias_names(tree)

    # Map table aliases (FROM orders o) back to real table names.
    alias_to_table: dict[str, str] = {}
    for t_node in tree.find_all(exp.Table):
        if t_node.alias:
            alias_to_table[t_node.alias.lower()] = t_node.name.lower()

    for col in tree.find_all(exp.Column):
        col_name = col.name.lower()
        if not col_name:
            continue
        qualifier = col.table.lower() if col.table else ""
        if qualifier:
            table_name = alias_to_table.get(qualifier, qualifier)
            table_cols = known_by_table.get(table_name)
            if table_cols is not None and col_name not in table_cols:
                warnings.append(f"unknown_column:{table_name}.{col_name}")
        elif known_all and col_name not in known_all and col_name not in aliases:
            warnings.append(f"unknown_column:{col_name}")
    return sorted(set(warnings))


def _enforce_limit(
    tree: exp.Expression, config: AgentConfig, warnings: list[str]
) -> exp.Expression | None:
    """Guarantee the outermost query carries LIMIT <= max_limit.

    Returns None when the limit cannot be enforced safely (fail closed).
    """
    try:
        limit_node = tree.args.get("limit")
        if limit_node is None:
            return tree.limit(config.default_limit, copy=True)

        value = limit_node.expression
        if isinstance(value, exp.Literal) and value.is_int:
            if int(value.this) > config.max_limit:
                warnings.append(f"limit_clamped:{value.this}->{config.max_limit}")
                return tree.limit(config.max_limit, copy=True)
            return tree
        # Non-literal LIMIT (expression, placeholder) — replace deterministically.
        warnings.append("limit_replaced:non_literal")
        return tree.limit(config.default_limit, copy=True)
    except Exception:  # noqa: BLE001 - any failure here must block, not pass through
        return None


def validate_sql(
    sql: str,
    schema: SchemaContext,
    config: AgentConfig | None = None,
) -> PolicyResult:
    """Validate one SQL string against the read-only policy.

    Returns a :class:`PolicyResult`; ``normalized_sql`` is only present when
    ``allowed=True`` and is the exact string the executor is given.
    """
    config = config or AgentConfig()
    warnings: list[str] = []

    sql = (sql or "").strip()
    if not sql:
        return _blocked(["empty_sql"])
    if len(sql) > config.max_sql_length:
        return _blocked([f"sql_too_long:{len(sql)}"])

    try:
        statements = [s for s in sqlglot.parse(sql, read="sqlite") if s is not None]
    except Exception as exc:  # sqlglot.ParseError and anything else: fail closed
        return _blocked([f"parse_error:{type(exc).__name__}"])

    if not statements:
        return _blocked(["empty_sql"])
    if len(statements) > 1:
        return _blocked(["multi_statement"])

    tree = _unwrap_root(statements[0])

    # 1) The root must be a plain query. This alone rejects INSERT/UPDATE/
    #    DELETE/DDL/PRAGMA/ATTACH/transaction control at the top level.
    if not isinstance(tree, _ALLOWED_ROOTS):
        return _blocked([f"not_read_only:{type(tree).__name__}"])

    # 2) No forbidden construct may appear anywhere in the tree (nested
    #    writes, SELECT INTO, commands smuggled into subqueries, ...).
    for node in tree.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            return _blocked([f"forbidden_construct:{type(node).__name__}"])

    # 3) Block functions with side effects (extension loading, file I/O).
    for func in tree.find_all(exp.Anonymous):
        name = str(func.this or "").lower()
        if name in _BLOCKED_FUNCTIONS:
            return _blocked([f"forbidden_function:{name}"])

    # 4) Every referenced table must exist in the schema allowlist.
    allowed_tables = {t.lower() for t in schema.table_names()}
    cte_names = {
        cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE) if cte.alias_or_name
    }
    unknown: list[str] = []
    referenced: set[str] = set()
    for t_node in tree.find_all(exp.Table):
        if t_node.db:
            return _blocked([f"forbidden_construct:cross_database:{t_node.db}"])
        name = t_node.name.lower()
        if not name:
            # FROM <function>() or other exotic sources — refuse what we
            # cannot attribute to a known table.
            return _blocked(["unsupported_syntax:table_expression"])
        if name.startswith(_SYSTEM_TABLE_PREFIX):
            return _blocked([f"forbidden_construct:system_table:{name}"])
        if name in cte_names:
            continue
        if name not in allowed_tables:
            unknown.append(name)
            continue
        referenced.add(name)
    if unknown:
        return _blocked(
            [f"unknown_table:{n}" for n in sorted(set(unknown))],
            repairable=True,
        )

    # 5) Unknown columns are warnings only — see _check_columns docstring.
    warnings.extend(_check_columns(tree, schema, referenced))

    # 6) Enforce LIMIT on the outermost query.
    limited = _enforce_limit(tree, config, warnings)
    if limited is None:
        return _blocked(["unsupported_syntax:limit_enforcement"], warnings=warnings)

    try:
        normalized = limited.sql(dialect="sqlite")
    except Exception:  # noqa: BLE001 - serialization failure: fail closed
        return _blocked(["unsupported_syntax:serialization"], warnings=warnings)

    return PolicyResult(
        allowed=True,
        reasons=[],
        warnings=sorted(set(warnings)),
        normalized_sql=normalized,
        repairable=False,
        policy_version=POLICY_VERSION,
    )
