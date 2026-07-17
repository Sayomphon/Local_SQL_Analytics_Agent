"""SQL generation: prompt construction and strict output parsing.

The model must return a JSON object matching :class:`SQLCandidate`. Anything
else is a format failure — the generator retries the format once (blueprint
rule) and otherwise reports a parse error upward. It never "fixes" model
output into executable SQL on its own.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ValidationError

from .llm import LLMBackend, ScriptedBackendExhaustedError
from .schemas import AgentConfig, SchemaContext, SQLCandidate

PROMPT_VERSION = "v2"

_RULES = """\
Rules:
- SQLite dialect only. Produce exactly ONE SELECT statement (WITH/CTE allowed).
- Read-only: never INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/ATTACH/PRAGMA.
- Use ONLY tables and columns listed in the schema. Do not invent names.
- Prefer explicit JOINs following the foreign keys shown.
- Add LIMIT when the result could be large.
- If the question is ambiguous (missing metric, time range, or entity), set
  "clarification_needed": true and ask ONE precise question instead of guessing.
- Reply with ONE JSON object only — no prose, no markdown fence."""

_OUTPUT_SPEC = """\
JSON format:
{
  "sql": "SELECT ...",
  "assumptions": ["..."],
  "selected_tables": ["..."],
  "chart_hint": {"kind": "bar|line|none", "x": "column", "y": ["column"]},
  "clarification_needed": false,
  "clarification_question": null
}"""

_FEW_SHOT = """\
Example question: "Total number of orders per country, top 5"
Example answer:
{"sql": "SELECT c.country, COUNT(o.order_id) AS order_count FROM orders AS o JOIN customers AS c ON o.customer_id = c.customer_id GROUP BY c.country ORDER BY order_count DESC LIMIT 5", "assumptions": ["An order counts regardless of status"], "selected_tables": ["orders", "customers"], "chart_hint": {"kind": "bar", "x": "country", "y": ["order_count"]}, "clarification_needed": false, "clarification_question": null}"""


def build_generation_prompt(question: str, schema: SchemaContext) -> str:
    return (
        "You are a careful analytics engineer writing SQLite queries.\n\n"
        f"Database schema:\n{schema.to_prompt_text()}\n\n"
        f"{_RULES}\n\n{_OUTPUT_SPEC}\n\n{_FEW_SHOT}\n\n"
        f'Question: "{question}"\n'
        "Answer with the JSON object only."
    )


def build_repair_prompt(
    question: str,
    schema: SchemaContext,
    failed_sql: str,
    feedback: str,
) -> str:
    return (
        "You are a careful analytics engineer fixing a failed SQLite query.\n\n"
        f"Database schema:\n{schema.to_prompt_text()}\n\n"
        f'Original question (do NOT change its intent): "{question}"\n\n'
        f"Previous SQL:\n{failed_sql}\n\n"
        f"Failure feedback:\n{feedback}\n\n"
        "Fix the query. Keep it a single read-only SELECT. "
        f"Use only tables/columns from the schema above.\n\n{_RULES}\n\n{_OUTPUT_SPEC}\n\n"
        "Answer with the JSON object only."
    )


_FORMAT_REMINDER = (
    "Your previous reply was not a single valid JSON object. "
    "Reply again with ONLY the JSON object described earlier — "
    "no explanation, no markdown fences."
)


def extract_json_object(text: str) -> str | None:
    """Return the first balanced top-level JSON object in ``text``.

    Tolerates markdown fences and prose around the object, but performs no
    repair of the JSON itself.
    """
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        start = text.find("{", start + 1)
    return None


def parse_candidate(text: str) -> tuple[SQLCandidate | None, str | None]:
    """Parse raw model text into a SQLCandidate. Returns (candidate, error)."""
    blob = extract_json_object(text or "")
    if blob is None:
        return None, "no_json_object_found"
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        return None, f"invalid_json:{exc.msg}"
    if not isinstance(data, dict):
        return None, "json_not_an_object"
    try:
        return SQLCandidate.model_validate(data), None
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", ()))
        return None, f"schema_validation:{loc or 'unknown'}"


class GenerationOutcome(BaseModel):
    raw_responses: list[str] = []
    candidate: SQLCandidate | None = None
    parse_error: str | None = None


class SQLGenerator:
    """Turns (question, schema) or (repair feedback) into a SQLCandidate."""

    def __init__(self, backend: LLMBackend, config: AgentConfig | None = None) -> None:
        self.backend = backend
        self.config = config or AgentConfig()

    def _complete_and_parse(self, prompt: str) -> GenerationOutcome:
        outcome = GenerationOutcome()
        attempts_left = 1 + max(self.config.format_retries, 0)
        current_prompt = prompt
        while attempts_left > 0:
            attempts_left -= 1
            try:
                raw = self.backend.complete(
                    current_prompt,
                    max_new_tokens=self.config.max_new_tokens,
                    temperature=self.config.temperature,
                )
            except ScriptedBackendExhaustedError:
                raise
            except Exception as exc:  # noqa: BLE001 - backend failure is a terminal parse error
                outcome.parse_error = f"backend_error:{type(exc).__name__}"
                return outcome
            outcome.raw_responses.append(raw)
            candidate, error = parse_candidate(raw)
            if candidate is not None:
                outcome.candidate = candidate
                outcome.parse_error = None
                return outcome
            outcome.parse_error = error
            current_prompt = f"{prompt}\n\n{_FORMAT_REMINDER}"
        return outcome

    def generate(self, question: str, schema: SchemaContext) -> GenerationOutcome:
        return self._complete_and_parse(build_generation_prompt(question, schema))

    def repair(
        self,
        question: str,
        schema: SchemaContext,
        failed_sql: str,
        feedback: str,
    ) -> GenerationOutcome:
        return self._complete_and_parse(
            build_repair_prompt(question, schema, failed_sql, feedback)
        )
