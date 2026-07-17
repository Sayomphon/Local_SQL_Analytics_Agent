"""Contract tests: SQLCandidate parsing (5+ JSON shapes per the blueprint),
schema subset logic, and the generator's format-retry behavior."""

from __future__ import annotations

from sql_agent.llm import ScriptedBackend
from sql_agent.schemas import AgentConfig, ColumnInfo, SchemaContext, TableInfo
from sql_agent.sql_generator import (
    SQLGenerator,
    build_generation_prompt,
    build_repair_prompt,
    extract_json_object,
    parse_candidate,
)

VALID_JSON = (
    '{"sql": "SELECT 1", "assumptions": [], "selected_tables": ["orders"],'
    ' "chart_hint": null, "clarification_needed": false, "clarification_question": null}'
)


def _schema() -> SchemaContext:
    return SchemaContext(
        database_id="t",
        tables=[
            TableInfo(
                name="orders",
                columns=[ColumnInfo(name="order_id"), ColumnInfo(name="order_date")],
            )
        ],
    )


# --- parse_candidate: valid & invalid shapes -------------------------------


def test_parse_plain_json():
    candidate, error = parse_candidate(VALID_JSON)
    assert error is None
    assert candidate.sql == "SELECT 1"


def test_parse_json_inside_markdown_fence():
    candidate, error = parse_candidate(f"```json\n{VALID_JSON}\n```")
    assert error is None
    assert candidate.sql == "SELECT 1"


def test_parse_json_with_surrounding_prose():
    text = f"Sure! Here is the query you asked for:\n{VALID_JSON}\nHope it helps."
    candidate, error = parse_candidate(text)
    assert error is None
    assert candidate.selected_tables == ["orders"]


def test_parse_rejects_no_json():
    candidate, error = parse_candidate("SELECT 1 -- just raw sql, no json")
    assert candidate is None
    assert error == "no_json_object_found"


def test_parse_rejects_truncated_json():
    candidate, error = parse_candidate('{"sql": "SELECT 1", "assumptions": [')
    assert candidate is None
    assert error is not None


def test_parse_rejects_wrong_types():
    candidate, error = parse_candidate('{"sql": 42}')
    assert candidate is None
    assert error.startswith("schema_validation")


def test_parse_rejects_json_array():
    candidate, error = parse_candidate('["SELECT 1"]')
    assert candidate is None


def test_sql_trailing_semicolon_stripped():
    candidate, _ = parse_candidate('{"sql": "SELECT 1;"}')
    assert candidate.sql == "SELECT 1"


def test_extract_json_handles_braces_inside_strings():
    text = '{"sql": "SELECT \'{weird}\' AS x", "assumptions": []}'
    assert extract_json_object(text) == text


# --- prompts ----------------------------------------------------------------


def test_generation_prompt_contains_schema_and_rules():
    prompt = build_generation_prompt("How many orders?", _schema())
    assert "orders" in prompt
    assert "Read-only" in prompt
    assert "JSON" in prompt


def test_repair_prompt_contains_error_and_intent_guard():
    prompt = build_repair_prompt(
        "How many orders?", _schema(), "SELECT revenue FROM orders",
        "no such column: revenue",
    )
    assert "no such column: revenue" in prompt
    assert "do NOT change its intent" in prompt


# --- generator format retry -------------------------------------------------


def test_generator_retries_format_once():
    backend = ScriptedBackend(["not json at all", VALID_JSON])
    generator = SQLGenerator(backend, AgentConfig(format_retries=1))
    outcome = generator.generate("How many orders?", _schema())
    assert outcome.candidate is not None
    assert len(outcome.raw_responses) == 2
    assert "not a single valid JSON" in backend.calls[1]


def test_generator_gives_up_after_format_budget():
    backend = ScriptedBackend(["garbage one", "garbage two"])
    generator = SQLGenerator(backend, AgentConfig(format_retries=1))
    outcome = generator.generate("How many orders?", _schema())
    assert outcome.candidate is None
    assert outcome.parse_error is not None
    assert len(outcome.raw_responses) == 2


# --- schema context helpers -------------------------------------------------


def test_schema_subset_keeps_fks_between_kept_tables(mini_schema):
    subset = mini_schema.subset(["orders", "customers"])
    assert subset.table_names() == {"orders", "customers"}
    assert all(
        fk.table in subset.table_names() and fk.ref_table in subset.table_names()
        for fk in subset.foreign_keys
    )


def test_schema_prompt_text_lists_fks(mini_schema):
    text = mini_schema.to_prompt_text(include_samples=False)
    assert "TABLE customers" in text
    assert "FK orders.customer_id -> customers.customer_id" in text
