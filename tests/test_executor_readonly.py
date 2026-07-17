"""Executor tests: read-only enforcement is independent of the policy layer.

Even if a write statement somehow bypassed the AST policy, the executor's
mode=ro URI + PRAGMA query_only + authorizer must still refuse it
(defense in depth).
"""

from __future__ import annotations

import pytest

from sql_agent.executor import ReadOnlyExecutor, execute_readonly, sanitize_error
from sql_agent.schemas import AgentConfig


def test_select_returns_rows(mini_db, config):
    result = execute_readonly(mini_db, "SELECT name, country FROM customers", config)
    assert result.ok
    assert result.columns == ["name", "country"]
    assert result.row_count == 3
    assert result.elapsed_ms >= 0


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM customers",
        "UPDATE products SET unit_price = 0",
        "INSERT INTO customers VALUES (99, 'X', 'Y')",
        "DROP TABLE orders",
        "CREATE TABLE evil (x INTEGER)",
    ],
)
def test_writes_rejected_even_without_policy(mini_db, config, sql):
    result = execute_readonly(mini_db, sql, config)
    assert not result.ok
    assert result.error_type == "denied"


def test_database_still_intact_after_write_attempts(mini_db, config):
    execute_readonly(mini_db, "DELETE FROM customers", config)
    result = execute_readonly(mini_db, "SELECT COUNT(*) FROM customers", config)
    assert result.ok
    assert result.rows[0][0] == 3


def test_pragma_write_rejected(mini_db, config):
    execute_readonly(mini_db, "PRAGMA query_only = OFF", config)
    # Whether the PRAGMA was denied or ignored, the connection used for the
    # follow-up must still be read-only.
    follow_up = execute_readonly(mini_db, "DELETE FROM customers", config)
    assert not follow_up.ok


def test_missing_column_classified(mini_db, config):
    result = execute_readonly(mini_db, "SELECT revenue FROM orders", config)
    assert not result.ok
    assert result.error_type == "missing_entity"
    assert "revenue" in result.error


def test_missing_table_classified(mini_db, config):
    result = execute_readonly(mini_db, "SELECT * FROM ghosts", config)
    assert not result.ok
    assert result.error_type == "missing_entity"


def test_syntax_error_classified(mini_db, config):
    result = execute_readonly(mini_db, "SELECT FROM WHERE", config)
    assert not result.ok
    assert result.error_type == "syntax"


def test_row_cap_truncates(mini_db):
    config = AgentConfig(row_cap=2)
    result = execute_readonly(
        mini_db, "SELECT order_item_id FROM order_items", config
    )
    assert result.ok
    assert result.row_count == 2
    assert result.truncated


def test_timeout_interrupts_runaway_query(mini_db):
    config = AgentConfig(timeout_ms=200)
    # 100M-step recursive CTE: takes many seconds unless interrupted.
    sql = (
        "WITH RECURSIVE cnt(x) AS "
        "(SELECT 1 UNION ALL SELECT x + 1 FROM cnt WHERE x < 100000000) "
        "SELECT COUNT(*) FROM cnt"
    )
    result = execute_readonly(mini_db, sql, config)
    assert not result.ok
    assert result.error_type == "timeout"


def test_missing_database_is_structured_error(tmp_path, config):
    result = execute_readonly(tmp_path / "nope.sqlite", "SELECT 1", config)
    assert not result.ok
    assert result.error_type == "other"


def test_error_messages_hide_paths(mini_db, config):
    result = execute_readonly(mini_db, "SELECT * FROM ghosts", config)
    assert str(mini_db) not in (result.error or "")


def test_sanitize_error_strips_paths():
    message = "unable to open database file:/Users/someone/secret/db.sqlite"
    cleaned = sanitize_error(message, "/Users/someone/secret/db.sqlite")
    assert "/Users/someone" not in cleaned


def test_empty_sql_is_structured_error(mini_db, config):
    result = execute_readonly(mini_db, "   ", config)
    assert not result.ok
    assert result.error_type == "empty_sql"


def test_executor_wrapper_binds_path(mini_db, config):
    executor = ReadOnlyExecutor(mini_db, config)
    result = executor.execute("SELECT COUNT(*) FROM products")
    assert result.ok
    assert result.rows == [[2]]
