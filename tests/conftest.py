"""Shared fixtures: a small self-contained SQLite database and its schema.

Tests never depend on the generated demo databases in data/ — each test run
builds its own tiny database in tmp_path so the suite is hermetic.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from sql_agent.schema_inspector import inspect_database
from sql_agent.schemas import AgentConfig, SchemaContext


@pytest.fixture()
def mini_db(tmp_path: Path) -> Path:
    """customers/orders/order_items/products with a handful of rows."""
    path = tmp_path / "mini.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE customers (
            customer_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            country TEXT NOT NULL
        );
        CREATE TABLE products (
            product_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            unit_price REAL NOT NULL
        );
        CREATE TABLE orders (
            order_id INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
            order_date TEXT NOT NULL,
            status TEXT NOT NULL
        );
        CREATE TABLE order_items (
            order_item_id INTEGER PRIMARY KEY,
            order_id INTEGER NOT NULL REFERENCES orders(order_id),
            product_id INTEGER NOT NULL REFERENCES products(product_id),
            quantity INTEGER NOT NULL,
            unit_price REAL NOT NULL
        );

        INSERT INTO customers VALUES
            (1, 'Customer 001', 'Thailand'),
            (2, 'Customer 002', 'Japan'),
            (3, 'Customer 003', 'Thailand');
        INSERT INTO products VALUES
            (1, 'Wireless Mouse', 'Electronics', 590.0),
            (2, 'Yoga Mat', 'Sports', 890.0);
        INSERT INTO orders VALUES
            (1, 1, '2025-01-10', 'completed'),
            (2, 1, '2025-02-14', 'completed'),
            (3, 2, '2025-02-20', 'cancelled'),
            (4, 3, '2025-03-05', 'completed');
        INSERT INTO order_items VALUES
            (1, 1, 1, 2, 590.0),
            (2, 1, 2, 1, 890.0),
            (3, 2, 1, 1, 590.0),
            (4, 3, 2, 3, 890.0),
            (5, 4, 2, 1, 890.0);
        """
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def mini_schema(mini_db: Path) -> SchemaContext:
    return inspect_database(mini_db, database_id="mini")


@pytest.fixture()
def config() -> AgentConfig:
    return AgentConfig(timeout_ms=2000, row_cap=50, default_limit=50, max_limit=50)
