"""Lexical schema selection.

Reduces the schema handed to the model to the tables that plausibly matter for
the question, which lowers token usage and column hallucination. Deliberately
non-LLM: scoring is token overlap plus a small synonym map (Thai business
vocabulary included), then foreign-key neighbors are pulled in so join paths
stay intact. Small databases skip selection entirely (blueprint fallback rule).
"""

from __future__ import annotations

import re

from .schemas import AgentConfig, SchemaContext

# Minimal bilingual synonym map: question tokens -> schema vocabulary.
# Extend per database; keys must be lowercase.
DEFAULT_SYNONYMS: dict[str, set[str]] = {
    # Thai -> English business terms
    "ยอดขาย": {"sales", "orders", "order_items", "revenue", "amount"},
    "ขาย": {"sales", "orders", "order_items"},
    "ลูกค้า": {"customers", "customer"},
    "สินค้า": {"products", "product", "items"},
    "คำสั่งซื้อ": {"orders", "order"},
    "ออเดอร์": {"orders", "order"},
    "หมวดหมู่": {"category", "categories"},
    "ราคา": {"price", "unit_price", "amount"},
    "จำนวน": {"quantity", "count"},
    "เดือน": {"month", "date", "order_date"},
    "ปี": {"year", "date", "order_date"},
    "วันที่": {"date", "order_date", "created_at"},
    "พนักงาน": {"employees", "employee", "staff"},
    "แผนก": {"departments", "department"},
    "เงินเดือน": {"salary", "salaries"},
    "ประเทศ": {"country"},
    "เมือง": {"city"},
    # English aliases that appear in questions but not schema names
    "revenue": {"orders", "order_items", "unit_price", "quantity"},
    "sales": {"orders", "order_items"},
    "customer": {"customers"},
    "product": {"products"},
    "employee": {"employees"},
    "department": {"departments"},
    "salary": {"salaries", "salary"},
}

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[฀-๿]+")


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def _expand_tokens(tokens: list[str], synonyms: dict[str, set[str]]) -> set[str]:
    expanded: set[str] = set(tokens)
    for token in tokens:
        expanded |= synonyms.get(token, set())
        # Thai has no spaces: also match synonym keys embedded in a token.
        for key, values in synonyms.items():
            if key in token:
                expanded |= values
    # Split snake_case question tokens (e.g. "order_items" -> "order", "items")
    for token in list(expanded):
        if "_" in token:
            expanded |= set(token.split("_"))
    return expanded


def _table_vocabulary(schema: SchemaContext, table_name: str) -> set[str]:
    vocab: set[str] = set()
    for table in schema.tables:
        if table.name != table_name:
            continue
        vocab.add(table.name.lower())
        vocab |= set(table.name.lower().split("_"))
        for col in table.column_names():
            vocab.add(col.lower())
            vocab |= set(col.lower().split("_"))
    return vocab


def select_tables(
    question: str,
    schema: SchemaContext,
    config: AgentConfig | None = None,
    synonyms: dict[str, set[str]] | None = None,
) -> list[str]:
    """Pick the tables most relevant to the question.

    Returns table names in schema order. Falls back to the full schema when
    the database is small or nothing scores above zero (never starves the
    generator of schema).
    """
    config = config or AgentConfig()
    synonyms = synonyms if synonyms is not None else DEFAULT_SYNONYMS
    all_tables = [t.name for t in schema.tables]

    if len(all_tables) <= config.small_schema_threshold:
        return all_tables

    question_tokens = _expand_tokens(_tokenize(question), synonyms)

    scores: dict[str, int] = {}
    for name in all_tables:
        vocab = _table_vocabulary(schema, name)
        scores[name] = len(question_tokens & vocab)

    ranked = [name for name in all_tables if scores[name] > 0]
    ranked.sort(key=lambda n: (-scores[n], all_tables.index(n)))
    selected = ranked[: config.max_prompt_tables]

    if not selected:
        return all_tables  # fallback: full schema beats an empty prompt

    # Pull in FK neighbors so join paths are never severed mid-selection.
    selected_set = {s.lower() for s in selected}
    for fk in schema.foreign_keys:
        if len(selected_set) >= config.max_prompt_tables:
            break
        if fk.table.lower() in selected_set and fk.ref_table.lower() not in selected_set:
            selected_set.add(fk.ref_table.lower())
        elif fk.ref_table.lower() in selected_set and fk.table.lower() not in selected_set:
            selected_set.add(fk.table.lower())

    return [name for name in all_tables if name.lower() in selected_set]
