#!/usr/bin/env python
"""Evaluation harness.

Runs the three suites from the blueprint and writes reports/:

* business questions (gold SQL -> result match)
* safety prompts (destructive/injection SQL -> must be blocked)
* ambiguous questions (agent should stop for clarification)

Backends
--------
``gold-replay``
    No LLM. Replays each business question's gold SQL (and each safety
    prompt's attack SQL) through the *real* policy + executor + state machine.
    This validates the harness and the guardrails deterministically; result
    metrics are trivially perfect by construction and are labeled as such.
``transformers`` / ``ollama``
    Real local model inference; measures actual Text-to-SQL quality.

Outputs: reports/evaluation.csv, reports/metrics.json, reports/traces/*.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from sql_agent.agent import SQLAnalyticsAgent  # noqa: E402
from sql_agent.evaluation import (  # noqa: E402
    EvalRecord,
    compare_results,
    compute_metrics,
    has_order_by,
)
from sql_agent.executor import ReadOnlyExecutor, execute_readonly  # noqa: E402
from sql_agent.llm import LLMBackend, ScriptedBackend  # noqa: E402
from sql_agent.schema_inspector import inspect_database  # noqa: E402
from sql_agent.schemas import AgentConfig, AgentState, SchemaContext  # noqa: E402
from sql_agent.sql_generator import SQLGenerator  # noqa: E402


def load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def candidate_json(sql: str, *, clarification: str | None = None) -> str:
    """Build a SQLCandidate JSON string for scripted replay."""
    if clarification is not None:
        return json.dumps(
            {"sql": "", "clarification_needed": True, "clarification_question": clarification}
        )
    return json.dumps({"sql": sql, "assumptions": ["gold replay"], "selected_tables": []})


def make_backend(
    kind: str, question: dict, model: str | None
) -> LLMBackend:
    if kind == "gold-replay":
        if question["category"] == "business":
            return ScriptedBackend([candidate_json(question["gold_sql"])])
        if question["category"] == "safety":
            return ScriptedBackend([candidate_json(question["example_attack_sql"])])
        return ScriptedBackend(
            [candidate_json("", clarification=question.get("why_ambiguous", "Please clarify."))]
        )
    if kind == "transformers":
        from sql_agent.llm import DEFAULT_MODEL_ID, TransformersBackend

        # One shared instance is created lazily by main(); this branch is not
        # reached per-question.
        return TransformersBackend(model or DEFAULT_MODEL_ID)
    if kind == "ollama":
        from sql_agent.llm import OllamaBackend

        return OllamaBackend(model or "qwen3:4b-instruct")
    raise ValueError(f"unknown backend: {kind}")


def state_to_record(
    question: dict, state: AgentState, gold_match: bool | None
) -> EvalRecord:
    first = state.attempts[0] if state.attempts else None
    return EvalRecord(
        question_id=question["id"],
        category=question["category"],
        question=question["question"],
        status=state.status,
        attempts=len(state.attempts),
        first_attempt_executed=bool(first and first.execution and first.execution.ok),
        final_executed=bool(state.final_execution and state.final_execution.ok),
        result_match=gold_match,
        blocked=state.status == "blocked",
        expected_blocked=question.get("expected") == "blocked",
        needs_clarification=state.status == "needs_clarification",
        latency_ms=state.provenance.total_elapsed_ms,
        final_sql=state.final_sql or "",
        error=state.stop_reason or "",
    )


def run_suite(
    questions: list[dict],
    *,
    backend_kind: str,
    shared_backend: LLMBackend | None,
    schemas: dict[str, SchemaContext],
    db_paths: dict[str, Path],
    config: AgentConfig,
    traces_dir: Path,
) -> list[EvalRecord]:
    records: list[EvalRecord] = []
    for question in questions:
        db = question["database"]
        schema = schemas[db]
        backend = shared_backend or make_backend(backend_kind, question, None)
        agent = SQLAnalyticsAgent(
            generator=SQLGenerator(backend, config),
            executor=ReadOnlyExecutor(db_paths[db], config),
            schema=schema,
            config=config,
        )
        state = agent.run(question["question"])

        gold_match: bool | None = None
        if question["category"] == "business" and "gold_sql" in question:
            gold_execution = execute_readonly(db_paths[db], question["gold_sql"], config)
            if state.final_execution is not None and state.final_execution.ok:
                comparison = compare_results(
                    state.final_execution,
                    gold_execution,
                    respect_order=has_order_by(question["gold_sql"]),
                )
                gold_match = comparison.match
            else:
                gold_match = False

        record = state_to_record(question, state, gold_match)
        records.append(record)

        trace_path = traces_dir / f"{question['id']}.json"
        trace_path.write_text(state.model_dump_json(indent=2))

        marker = {
            "completed": "OK ",
            "blocked": "BLK",
            "needs_clarification": "ASK",
            "failed": "FAIL",
        }.get(state.status, "?  ")
        print(f"  [{marker}] {question['id']:10s} {state.status:20s} "
              f"attempts={len(state.attempts)} match={gold_match}")
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend",
        choices=["gold-replay", "transformers", "ollama"],
        default="gold-replay",
    )
    parser.add_argument("--model", default=None, help="model id/name for LLM backends")
    parser.add_argument("--questions", default="data/evaluation_questions.jsonl")
    parser.add_argument("--safety", default="data/safety_prompts.jsonl")
    parser.add_argument("--ambiguous", default="data/ambiguous_questions.jsonl")
    parser.add_argument("--databases-dir", default="data/databases")
    parser.add_argument("--out-dir", default="reports")
    parser.add_argument("--limit", type=int, default=None, help="run only the first N per suite")
    args = parser.parse_args()

    out_dir = REPO_ROOT / args.out_dir
    traces_dir = out_dir / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)

    suites = {
        "business": load_jsonl(REPO_ROOT / args.questions),
        "safety": load_jsonl(REPO_ROOT / args.safety),
        "ambiguous": load_jsonl(REPO_ROOT / args.ambiguous),
    }
    if args.limit:
        suites = {k: v[: args.limit] for k, v in suites.items()}

    databases = {q["database"] for qs in suites.values() for q in qs}
    db_paths = {db: REPO_ROOT / args.databases_dir / f"{db}.sqlite" for db in databases}
    missing = [str(p) for p in db_paths.values() if not p.is_file()]
    if missing:
        print(f"missing databases: {missing}\nrun: python scripts/create_demo_db.py")
        return 1
    schemas = {db: inspect_database(path) for db, path in db_paths.items()}

    config = AgentConfig()

    # LLM backends are expensive to construct — build once and share.
    shared_backend: LLMBackend | None = None
    if args.backend == "transformers":
        from sql_agent.llm import DEFAULT_MODEL_ID, TransformersBackend

        shared_backend = TransformersBackend(args.model or DEFAULT_MODEL_ID)
        config = config.model_copy(update={"model_name": shared_backend.name})
    elif args.backend == "ollama":
        from sql_agent.llm import OllamaBackend

        shared_backend = OllamaBackend(args.model or "qwen3:4b-instruct")
        config = config.model_copy(update={"model_name": shared_backend.name})

    all_records: list[EvalRecord] = []
    for suite_name, questions in suites.items():
        if not questions:
            continue
        print(f"\n=== suite: {suite_name} ({len(questions)} questions, "
              f"backend={args.backend}) ===")
        all_records.extend(
            run_suite(
                questions,
                backend_kind=args.backend,
                shared_backend=shared_backend,
                schemas=schemas,
                db_paths=db_paths,
                config=config,
                traces_dir=traces_dir,
            )
        )

    metrics = compute_metrics(all_records)
    if args.backend == "gold-replay":
        metrics.notes.append(
            "gold-replay backend: business/ambiguous outcomes are by construction; "
            "safety blocking is a real policy-engine measurement."
        )

    csv_path = out_dir / "evaluation.csv"
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(EvalRecord.model_fields))
        writer.writeheader()
        for record in all_records:
            writer.writerow(record.model_dump())

    metrics_path = out_dir / "metrics.json"
    payload = {"backend": args.backend, "metrics": metrics.model_dump()}
    metrics_path.write_text(json.dumps(payload, indent=2))

    print(f"\nwrote {csv_path}")
    print(f"wrote {metrics_path}")
    print(json.dumps(payload, indent=2))

    if metrics.unsafe_block_rate is not None and metrics.unsafe_block_rate < 1.0:
        print("\nWARNING: unsafe_block_rate < 100% — investigate before shipping")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
