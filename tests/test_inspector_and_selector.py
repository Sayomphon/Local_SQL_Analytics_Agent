"""Schema inspector and lexical selector tests."""

from __future__ import annotations

import pytest

from sql_agent.schema_inspector import SchemaInspectionError, inspect_database
from sql_agent.schema_selector import select_tables
from sql_agent.schemas import AgentConfig, ColumnInfo, SchemaContext, TableInfo

# --- inspector --------------------------------------------------------------


def test_inspector_reads_tables_and_columns(mini_schema):
    assert mini_schema.table_names() == {
        "customers",
        "products",
        "orders",
        "order_items",
    }
    assert mini_schema.columns_for("customers") == {"customer_id", "name", "country"}


def test_inspector_marks_primary_keys(mini_schema):
    customers = next(t for t in mini_schema.tables if t.name == "customers")
    pk_cols = [c.name for c in customers.columns if c.primary_key]
    assert pk_cols == ["customer_id"]


def test_inspector_reads_foreign_keys(mini_schema):
    fk_pairs = {(fk.table, fk.ref_table) for fk in mini_schema.foreign_keys}
    assert ("orders", "customers") in fk_pairs
    assert ("order_items", "orders") in fk_pairs
    assert ("order_items", "products") in fk_pairs


def test_inspector_collects_row_counts_and_samples(mini_schema):
    orders = next(t for t in mini_schema.tables if t.name == "orders")
    assert orders.row_count == 4
    assert 0 < len(orders.sample_rows) <= 2
    assert "order_id" in orders.sample_rows[0]


def test_inspector_can_disable_samples(mini_db):
    ctx = inspect_database(mini_db, sample_rows=0)
    assert all(not t.sample_rows for t in ctx.tables)


def test_inspector_missing_file_raises(tmp_path):
    with pytest.raises(SchemaInspectionError):
        inspect_database(tmp_path / "missing.sqlite")


def test_inspector_truncates_long_sample_values(tmp_path):
    import sqlite3

    path = tmp_path / "long.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
    conn.execute("INSERT INTO notes VALUES (1, ?)", ("x" * 500,))
    conn.commit()
    conn.close()

    ctx = inspect_database(path)
    body = ctx.tables[0].sample_rows[0]["body"]
    assert len(body) < 100


# --- selector ---------------------------------------------------------------


def _wide_schema() -> SchemaContext:
    """Schema with more tables than the small-schema threshold."""
    def table(name: str, cols: list[str]) -> TableInfo:
        return TableInfo(name=name, columns=[ColumnInfo(name=c) for c in cols])

    return SchemaContext(
        database_id="wide",
        tables=[
            table("customers", ["customer_id", "name", "country"]),
            table("products", ["product_id", "name", "category", "unit_price"]),
            table("orders", ["order_id", "customer_id", "order_date", "status"]),
            table("order_items", ["order_item_id", "order_id", "product_id", "quantity"]),
            table("suppliers", ["supplier_id", "name"]),
            table("warehouses", ["warehouse_id", "city"]),
            table("shipments", ["shipment_id", "warehouse_id", "order_id"]),
        ],
        foreign_keys=[],
    )


def test_small_schema_returns_all_tables(mini_schema, config):
    # mini schema has 4 tables <= threshold 5 -> selector passes everything.
    assert select_tables("anything at all", mini_schema, config) == [
        t.name for t in mini_schema.tables
    ]


def test_selector_picks_relevant_tables():
    schema = _wide_schema()
    config = AgentConfig(small_schema_threshold=3, max_prompt_tables=3)
    selected = select_tables("total revenue per product category", schema, config)
    assert "products" in selected
    assert len(selected) <= 3


def test_selector_supports_thai_synonyms():
    schema = _wide_schema()
    config = AgentConfig(small_schema_threshold=3, max_prompt_tables=4)
    selected = select_tables("ยอดขายรวมของลูกค้าแต่ละประเทศ", schema, config)
    assert "customers" in selected
    assert "orders" in selected


def test_selector_falls_back_to_full_schema_when_no_match():
    schema = _wide_schema()
    config = AgentConfig(small_schema_threshold=3, max_prompt_tables=3)
    selected = select_tables("xyzzy plugh nothing matches", schema, config)
    assert selected == [t.name for t in schema.tables]
