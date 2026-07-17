#!/usr/bin/env python
"""Create the synthetic demo databases (deterministic, no PII).

Two databases are produced under data/databases/:

* ``retail.sqlite`` — customers / products / orders / order_items covering
  aggregation, joins, date logic, and ranking questions. Sales exist only for
  2024-01 .. 2025-12 so "empty result" test cases have honest empty answers.
* ``hr.sqlite`` — departments / employees / salaries for cross-database
  variety.

All values are synthetic (Customer 001, product catalog strings, seeded RNG),
so sample rows can safely appear in prompts and in the repository.
Re-running the script recreates identical databases (fixed seed).
"""

from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

SEED = 20260717
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "databases"

COUNTRIES = [
    ("Thailand", ["Bangkok", "Chiang Mai", "Khon Kaen", "Phuket"]),
    ("Singapore", ["Singapore"]),
    ("Japan", ["Tokyo", "Osaka"]),
    ("Germany", ["Berlin", "Munich"]),
    ("United States", ["New York", "Austin", "Seattle"]),
]

PRODUCTS = [
    # (name, category, unit_price)
    ("Wireless Mouse", "Electronics", 590.0),
    ("Mechanical Keyboard", "Electronics", 2290.0),
    ("27-inch Monitor", "Electronics", 6990.0),
    ("USB-C Hub", "Electronics", 1190.0),
    ("Noise-cancelling Headphones", "Electronics", 4990.0),
    ("Standing Desk", "Home Office", 8990.0),
    ("Ergonomic Chair", "Home Office", 5490.0),
    ("Desk Lamp", "Home Office", 790.0),
    ("Monitor Arm", "Home Office", 1590.0),
    ("Cotton T-Shirt", "Fashion", 350.0),
    ("Running Shoes", "Fashion", 2590.0),
    ("Rain Jacket", "Fashion", 1890.0),
    ("Canvas Tote Bag", "Fashion", 450.0),
    ("Yoga Mat", "Sports", 890.0),
    ("Dumbbell Set 10kg", "Sports", 1290.0),
    ("Cycling Helmet", "Sports", 1690.0),
    ("Tennis Racket", "Sports", 3290.0),
    ("Facial Cleanser", "Beauty", 420.0),
    ("Sunscreen SPF50", "Beauty", 550.0),
    ("Vitamin C Serum", "Beauty", 990.0),
    ("Arabica Coffee Beans 500g", "Grocery", 480.0),
    ("Organic Green Tea", "Grocery", 320.0),
    ("Dark Chocolate 85%", "Grocery", 180.0),
    ("Thai Jasmine Rice 5kg", "Grocery", 290.0),
]

DEPARTMENTS = [
    ("Engineering", "Bangkok"),
    ("Data & Analytics", "Bangkok"),
    ("Sales", "Singapore"),
    ("Marketing", "Bangkok"),
    ("Finance", "Singapore"),
    ("People Operations", "Bangkok"),
]

POSITIONS = {
    "Engineering": ["Software Engineer", "Senior Software Engineer", "Engineering Manager"],
    "Data & Analytics": ["Data Analyst", "Data Engineer", "Analytics Manager"],
    "Sales": ["Account Executive", "Sales Manager"],
    "Marketing": ["Marketing Specialist", "Content Lead"],
    "Finance": ["Accountant", "Finance Manager"],
    "People Operations": ["HR Specialist", "Recruiter"],
}


def create_retail(path: Path, rng: random.Random) -> None:
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE customers (
            customer_id INTEGER PRIMARY KEY,
            name        TEXT NOT NULL,
            city        TEXT NOT NULL,
            country     TEXT NOT NULL,
            created_at  TEXT NOT NULL
        );

        CREATE TABLE products (
            product_id  INTEGER PRIMARY KEY,
            name        TEXT NOT NULL,
            category    TEXT NOT NULL,
            unit_price  REAL NOT NULL
        );

        CREATE TABLE orders (
            order_id    INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
            order_date  TEXT NOT NULL,
            status      TEXT NOT NULL CHECK (status IN ('completed', 'pending', 'cancelled'))
        );

        CREATE TABLE order_items (
            order_item_id INTEGER PRIMARY KEY,
            order_id      INTEGER NOT NULL REFERENCES orders(order_id),
            product_id    INTEGER NOT NULL REFERENCES products(product_id),
            quantity      INTEGER NOT NULL,
            unit_price    REAL NOT NULL
        );

        CREATE INDEX idx_orders_customer ON orders(customer_id);
        CREATE INDEX idx_orders_date ON orders(order_date);
        CREATE INDEX idx_items_order ON order_items(order_id);
        CREATE INDEX idx_items_product ON order_items(product_id);
        """
    )

    customers = []
    for i in range(1, 41):
        country, cities = rng.choice(COUNTRIES)
        created = date(2023, 1, 1) + timedelta(days=rng.randint(0, 700))
        customers.append(
            (i, f"Customer {i:03d}", rng.choice(cities), country, created.isoformat())
        )
    conn.executemany("INSERT INTO customers VALUES (?, ?, ?, ?, ?)", customers)

    conn.executemany(
        "INSERT INTO products VALUES (?, ?, ?, ?)",
        [(i + 1, *p) for i, p in enumerate(PRODUCTS)],
    )

    # Orders only within 2024-01-01 .. 2025-12-31; volume grows over time so
    # month-over-month questions have a visible trend.
    order_rows = []
    item_rows = []
    order_id = 0
    item_id = 0
    start = date(2024, 1, 1)
    months = 24
    for month_index in range(months):
        month_start = date(
            start.year + (start.month - 1 + month_index) // 12,
            (start.month - 1 + month_index) % 12 + 1,
            1,
        )
        n_orders = 18 + month_index + rng.randint(-3, 3)
        for _ in range(n_orders):
            order_id += 1
            day = rng.randint(0, 27)
            order_date = month_start + timedelta(days=day)
            status = rng.choices(
                ["completed", "pending", "cancelled"], weights=[86, 6, 8]
            )[0]
            customer_id = rng.randint(1, len(customers))
            order_rows.append(
                (order_id, customer_id, order_date.isoformat(), status)
            )
            for _ in range(rng.randint(1, 4)):
                item_id += 1
                product_index = rng.randint(0, len(PRODUCTS) - 1)
                quantity = rng.randint(1, 5)
                base_price = PRODUCTS[product_index][2]
                discount = rng.choice([1.0, 1.0, 1.0, 0.9, 0.85])
                item_rows.append(
                    (
                        item_id,
                        order_id,
                        product_index + 1,
                        quantity,
                        round(base_price * discount, 2),
                    )
                )

    conn.executemany("INSERT INTO orders VALUES (?, ?, ?, ?)", order_rows)
    conn.executemany("INSERT INTO order_items VALUES (?, ?, ?, ?, ?)", item_rows)
    conn.commit()
    conn.close()
    print(f"created {path.name}: {len(customers)} customers, {len(PRODUCTS)} products, "
          f"{len(order_rows)} orders, {len(item_rows)} order items")


def create_hr(path: Path, rng: random.Random) -> None:
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE departments (
            department_id INTEGER PRIMARY KEY,
            name          TEXT NOT NULL,
            location      TEXT NOT NULL
        );

        CREATE TABLE employees (
            employee_id   INTEGER PRIMARY KEY,
            full_name     TEXT NOT NULL,
            department_id INTEGER NOT NULL REFERENCES departments(department_id),
            position      TEXT NOT NULL,
            hire_date     TEXT NOT NULL
        );

        CREATE TABLE salaries (
            salary_id      INTEGER PRIMARY KEY,
            employee_id    INTEGER NOT NULL REFERENCES employees(employee_id),
            amount         REAL NOT NULL,
            effective_date TEXT NOT NULL
        );

        CREATE INDEX idx_employees_department ON employees(department_id);
        CREATE INDEX idx_salaries_employee ON salaries(employee_id);
        """
    )

    conn.executemany(
        "INSERT INTO departments VALUES (?, ?, ?)",
        [(i + 1, *d) for i, d in enumerate(DEPARTMENTS)],
    )

    employees = []
    salaries = []
    salary_id = 0
    for emp_id in range(1, 61):
        dept_id = rng.randint(1, len(DEPARTMENTS))
        dept_name = DEPARTMENTS[dept_id - 1][0]
        position = rng.choice(POSITIONS[dept_name])
        hire = date(2019, 1, 1) + timedelta(days=rng.randint(0, 2400))
        employees.append(
            (emp_id, f"Employee {emp_id:03d}", dept_id, position, hire.isoformat())
        )
        base = rng.randint(35, 180) * 1000.0
        n_raises = rng.randint(1, 3)
        for raise_index in range(n_raises):
            salary_id += 1
            effective = hire + timedelta(days=365 * raise_index)
            salaries.append(
                (
                    salary_id,
                    emp_id,
                    round(base * (1.0 + 0.08 * raise_index), 2),
                    effective.isoformat(),
                )
            )

    conn.executemany("INSERT INTO employees VALUES (?, ?, ?, ?, ?)", employees)
    conn.executemany("INSERT INTO salaries VALUES (?, ?, ?, ?)", salaries)
    conn.commit()
    conn.close()
    print(f"created {path.name}: {len(DEPARTMENTS)} departments, "
          f"{len(employees)} employees, {len(salaries)} salary records")


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    create_retail(DATA_DIR / "retail.sqlite", random.Random(SEED))
    create_hr(DATA_DIR / "hr.sqlite", random.Random(SEED + 1))


if __name__ == "__main__":
    main()
