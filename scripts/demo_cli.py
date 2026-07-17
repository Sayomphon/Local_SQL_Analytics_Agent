#!/usr/bin/env python
"""Interactive/demo CLI for the SQL analytics agent.

Two modes:

* ``--scenario {success,repair,blocked,clarify}`` — the golden demo cases from
  the blueprint, driven by a deterministic scripted backend (simulated model
  output; real policy, executor, and state machine). No GPU needed — ideal
  for recording the 90-second demo.
* ``--backend {ollama,transformers} --question "..."`` — ask a real local
  model an arbitrary question.

Examples:
    python scripts/demo_cli.py --scenario repair
    python scripts/demo_cli.py --backend ollama --question "Top 5 products by revenue"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from sql_agent.agent import SQLAnalyticsAgent  # noqa: E402
from sql_agent.executor import ReadOnlyExecutor  # noqa: E402
from sql_agent.llm import ScriptedBackend  # noqa: E402
from sql_agent.presenter import maybe_chart, render_trace, to_dataframe  # noqa: E402
from sql_agent.schema_inspector import inspect_database  # noqa: E402
from sql_agent.schemas import AgentConfig  # noqa: E402
from sql_agent.sql_generator import SQLGenerator  # noqa: E402


def _candidate(sql: str, **overrides) -> str:
    body = {
        "sql": sql,
        "assumptions": [],
        "selected_tables": [],
        "chart_hint": None,
        "clarification_needed": False,
        "clarification_question": None,
    }
    body.update(overrides)
    return json.dumps(body)


# Golden demo cases (blueprint section 12): success, repaired success,
# blocked attack, and clarification stop. The scripted responses simulate the
# model; policy/execution/repair behavior is fully real.
SCENARIOS: dict[str, dict] = {
    "success": {
        "question": "Total completed-order revenue by product category, highest first",
        "responses": [
            _candidate(
                "SELECT p.category, ROUND(SUM(oi.quantity * oi.unit_price), 2) AS revenue "
                "FROM order_items AS oi "
                "JOIN orders AS o ON oi.order_id = o.order_id "
                "JOIN products AS p ON oi.product_id = p.product_id "
                "WHERE o.status = 'completed' "
                "GROUP BY p.category ORDER BY revenue DESC",
                assumptions=["Revenue counts completed orders only"],
                selected_tables=["order_items", "orders", "products"],
                chart_hint={"kind": "bar", "x": "category", "y": ["revenue"]},
            )
        ],
    },
    "repair": {
        "question": "Monthly revenue from completed orders in 2025",
        "responses": [
            # Attempt 1: hallucinated column `revenue` -> structured SQLite error.
            _candidate(
                "SELECT strftime('%Y-%m', order_date) AS month, SUM(revenue) AS revenue "
                "FROM orders WHERE status = 'completed' "
                "AND order_date >= '2025-01-01' AND order_date < '2026-01-01' "
                "GROUP BY month ORDER BY month",
                assumptions=["Orders table stores revenue directly"],
            ),
            # Attempt 2: repaired using quantity * unit_price via order_items.
            _candidate(
                "SELECT strftime('%Y-%m', o.order_date) AS month, "
                "ROUND(SUM(oi.quantity * oi.unit_price), 2) AS revenue "
                "FROM orders AS o JOIN order_items AS oi ON o.order_id = oi.order_id "
                "WHERE o.status = 'completed' "
                "AND o.order_date >= '2025-01-01' AND o.order_date < '2026-01-01' "
                "GROUP BY month ORDER BY month",
                assumptions=["Revenue = quantity * unit_price from order_items"],
                chart_hint={"kind": "line", "x": "month", "y": ["revenue"]},
            ),
        ],
    },
    "blocked": {
        "question": "ลบข้อมูลลูกค้าทั้งหมด (delete all customer data)",
        "responses": [_candidate("DELETE FROM customers")],
    },
    "clarify": {
        "question": "Which product is the best?",
        "responses": [
            _candidate(
                "",
                clarification_needed=True,
                clarification_question=(
                    "Best by total revenue, by units sold, or by number of orders — "
                    "and over which time period?"
                ),
            )
        ],
    },
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS))
    parser.add_argument("--backend", choices=["ollama", "transformers"])
    parser.add_argument("--question", help="question for a real LLM backend")
    parser.add_argument("--model", default=None)
    parser.add_argument("--db", default="data/databases/retail.sqlite")
    parser.add_argument("--chart", default=None, help="save chart PNG here if applicable")
    args = parser.parse_args()

    db_path = REPO_ROOT / args.db
    if not db_path.is_file():
        print(f"database not found: {db_path}\nrun: python scripts/create_demo_db.py")
        return 1

    if args.scenario:
        scenario = SCENARIOS[args.scenario]
        question = scenario["question"]
        backend = ScriptedBackend(scenario["responses"])
        print(f"[scenario: {args.scenario}] (scripted model output; "
              "real policy + executor + state machine)\n")
    elif args.backend and args.question:
        question = args.question
        if args.backend == "ollama":
            from sql_agent.llm import OllamaBackend

            backend = OllamaBackend(args.model or "qwen3:4b-instruct")
        else:
            from sql_agent.llm import DEFAULT_MODEL_ID, TransformersBackend

            backend = TransformersBackend(args.model or DEFAULT_MODEL_ID)
    else:
        parser.error("use --scenario, or --backend with --question")
        return 2

    config = AgentConfig(model_name=getattr(backend, "name", "unknown"))
    schema = inspect_database(db_path)
    agent = SQLAnalyticsAgent(
        generator=SQLGenerator(backend, config),
        executor=ReadOnlyExecutor(db_path, config),
        schema=schema,
        config=config,
    )

    state = agent.run(question)

    print(render_trace(state))

    execution = state.final_execution
    if execution is not None and execution.ok and execution.rows:
        print("\nResult preview:")
        print(to_dataframe(execution).head(12).to_string(index=False))

        final_attempt = state.final_attempt
        hint = final_attempt.candidate.chart_hint if final_attempt.candidate else None
        if args.chart and hint is not None:
            saved = maybe_chart(execution, hint, args.chart)
            if saved:
                print(f"\nchart saved: {saved}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
