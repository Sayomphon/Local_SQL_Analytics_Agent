"""Local Enterprise SQL Analytics Agent.

Agentic Text-to-SQL with tool execution, a bounded repair loop, and
deterministic read-only guardrails, designed to run entirely on local models.
"""

from .agent import SQLAnalyticsAgent
from .executor import ReadOnlyExecutor, execute_readonly
from .llm import (
    DEFAULT_MODEL_ID,
    FALLBACK_MODEL_ID,
    LLMBackend,
    OllamaBackend,
    ScriptedBackend,
    TransformersBackend,
)
from .presenter import maybe_chart, render_trace, summarize, to_dataframe
from .schema_inspector import SchemaInspectionError, inspect_database
from .schema_selector import select_tables
from .schemas import (
    AgentConfig,
    AgentState,
    ChartHint,
    ExecutionResult,
    PolicyResult,
    SchemaContext,
    SQLAttempt,
    SQLCandidate,
)
from .sql_generator import SQLGenerator, parse_candidate
from .sql_policy import POLICY_VERSION, validate_sql

__version__ = "0.1.0"

__all__ = [
    "AgentConfig",
    "AgentState",
    "ChartHint",
    "DEFAULT_MODEL_ID",
    "ExecutionResult",
    "FALLBACK_MODEL_ID",
    "LLMBackend",
    "OllamaBackend",
    "POLICY_VERSION",
    "PolicyResult",
    "ReadOnlyExecutor",
    "SQLAnalyticsAgent",
    "SQLAttempt",
    "SQLCandidate",
    "SQLGenerator",
    "SchemaContext",
    "SchemaInspectionError",
    "ScriptedBackend",
    "TransformersBackend",
    "execute_readonly",
    "inspect_database",
    "maybe_chart",
    "parse_candidate",
    "render_trace",
    "select_tables",
    "summarize",
    "to_dataframe",
    "validate_sql",
    "__version__",
]
