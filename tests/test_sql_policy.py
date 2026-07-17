"""Policy engine tests: every guardrail from the blueprint's security section.

The invariant under test: no statement that could mutate state, touch the
filesystem, or read outside the schema allowlist may come back ``allowed``.
"""

from __future__ import annotations

import pytest

from sql_agent.sql_policy import POLICY_VERSION, validate_sql

DESTRUCTIVE_STATEMENTS = [
    "DELETE FROM customers",
    "DELETE FROM customers WHERE customer_id = 1",
    "DROP TABLE orders",
    "DROP TABLE IF EXISTS orders",
    "UPDATE products SET unit_price = 0",
    "INSERT INTO orders (order_id) VALUES (999)",
    "CREATE TABLE evil (x INTEGER)",
    "CREATE TRIGGER t AFTER INSERT ON orders BEGIN DELETE FROM order_items; END",
    "ALTER TABLE customers ADD COLUMN hacked TEXT",
    "PRAGMA query_only = OFF",
    "ATTACH DATABASE '/etc/passwd' AS pw",
    "BEGIN; DELETE FROM customers; COMMIT",
]


@pytest.mark.parametrize("sql", DESTRUCTIVE_STATEMENTS)
def test_destructive_statements_blocked(sql, mini_schema, config):
    result = validate_sql(sql, mini_schema, config)
    assert not result.allowed
    assert not result.repairable  # destructive intent is terminal, not retried
    assert result.normalized_sql is None


def test_multi_statement_blocked(mini_schema, config):
    result = validate_sql(
        "SELECT * FROM orders; DROP TABLE orders", mini_schema, config
    )
    assert not result.allowed
    assert result.reasons == ["multi_statement"]


def test_trailing_semicolon_is_not_multi_statement(mini_schema, config):
    result = validate_sql("SELECT * FROM orders;", mini_schema, config)
    assert result.allowed


def test_parse_error_fails_closed(mini_schema, config):
    result = validate_sql("this is not sql at all !!!", mini_schema, config)
    assert not result.allowed
    assert result.reasons[0].startswith("parse_error")


def test_empty_sql_blocked(mini_schema, config):
    assert not validate_sql("", mini_schema, config).allowed
    assert not validate_sql("   ", mini_schema, config).allowed


def test_overlong_sql_blocked(mini_schema, config):
    sql = "SELECT " + ", ".join(f"{i}" for i in range(5000)) + " FROM orders"
    result = validate_sql(sql, mini_schema, config)
    assert not result.allowed
    assert result.reasons[0].startswith("sql_too_long")


def test_unknown_table_blocked_but_repairable(mini_schema, config):
    result = validate_sql("SELECT * FROM revenue_summary", mini_schema, config)
    assert not result.allowed
    assert result.repairable
    assert result.reasons == ["unknown_table:revenue_summary"]


def test_system_table_blocked(mini_schema, config):
    result = validate_sql("SELECT * FROM sqlite_master", mini_schema, config)
    assert not result.allowed
    assert not result.repairable


def test_forbidden_function_blocked(mini_schema, config):
    result = validate_sql("SELECT load_extension('/tmp/evil.so')", mini_schema, config)
    assert not result.allowed
    assert result.reasons == ["forbidden_function:load_extension"]


def test_cross_database_reference_blocked(mini_schema, config):
    result = validate_sql("SELECT * FROM otherdb.orders", mini_schema, config)
    assert not result.allowed


def test_simple_select_allowed_and_limited(mini_schema, config):
    result = validate_sql("SELECT name FROM customers", mini_schema, config)
    assert result.allowed
    assert result.policy_version == POLICY_VERSION
    assert f"LIMIT {config.default_limit}" in result.normalized_sql


def test_existing_limit_preserved(mini_schema, config):
    result = validate_sql("SELECT name FROM customers LIMIT 5", mini_schema, config)
    assert result.allowed
    assert "LIMIT 5" in result.normalized_sql


def test_oversized_limit_clamped(mini_schema, config):
    result = validate_sql(
        "SELECT name FROM customers LIMIT 99999", mini_schema, config
    )
    assert result.allowed
    assert f"LIMIT {config.max_limit}" in result.normalized_sql
    assert any(w.startswith("limit_clamped") for w in result.warnings)


def test_cte_allowed(mini_schema, config):
    sql = (
        "WITH totals AS (SELECT order_id, SUM(quantity * unit_price) AS total "
        "FROM order_items GROUP BY order_id) "
        "SELECT o.order_id, t.total FROM orders o JOIN totals t ON o.order_id = t.order_id"
    )
    result = validate_sql(sql, mini_schema, config)
    assert result.allowed, result.reasons


def test_cte_name_not_reported_as_unknown_table(mini_schema, config):
    sql = "WITH x AS (SELECT customer_id FROM orders) SELECT * FROM x"
    result = validate_sql(sql, mini_schema, config)
    assert result.allowed, result.reasons


def test_union_allowed(mini_schema, config):
    sql = "SELECT name FROM customers UNION SELECT name FROM products"
    result = validate_sql(sql, mini_schema, config)
    assert result.allowed, result.reasons
    assert "LIMIT" in result.normalized_sql


def test_unknown_column_is_warning_not_block(mini_schema, config):
    # Wrong columns must reach the executor so the repair loop can see the
    # structured 'no such column' error (core demo flow).
    result = validate_sql("SELECT revenue FROM orders", mini_schema, config)
    assert result.allowed
    assert "unknown_column:revenue" in result.warnings


def test_qualified_unknown_column_warning(mini_schema, config):
    result = validate_sql(
        "SELECT o.revenue FROM orders AS o", mini_schema, config
    )
    assert result.allowed
    assert "unknown_column:orders.revenue" in result.warnings


def test_select_into_blocked(mini_schema, config):
    result = validate_sql(
        "SELECT * INTO backup FROM customers", mini_schema, config
    )
    assert not result.allowed


def test_case_insensitive_table_match(mini_schema, config):
    result = validate_sql("SELECT * FROM Customers", mini_schema, config)
    assert result.allowed, result.reasons


def test_default_config_used_when_none():
    from sql_agent.schemas import ColumnInfo, SchemaContext, TableInfo

    schema = SchemaContext(
        database_id="t",
        tables=[TableInfo(name="t1", columns=[ColumnInfo(name="a")])],
    )
    result = validate_sql("SELECT a FROM t1", schema)
    assert result.allowed
    assert "LIMIT 200" in result.normalized_sql
