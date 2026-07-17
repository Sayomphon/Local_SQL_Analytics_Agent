# Development Log — บันทึกการขึ้นโปรเจกต์

> บันทึกว่าทำอะไรไปบ้าง ทำไม และตรวจสอบอย่างไร อ้างอิงจากเอกสาร
> `01_Local_SQL_Analytics_Agent_5Day_Plan_TH.docx` (Blueprint v1.0)
>
> **วันที่เริ่ม:** 17 กรกฎาคม 2026
> **สภาพแวดล้อม:** macOS (Apple Silicon), Python 3.11.15 (Homebrew), ไม่มี CUDA GPU

---

## สรุปสถานะ (TL;DR)

| ส่วน | สถานะ | หลักฐาน |
|---|---|---|
| โครงสร้าง repo + packaging | ✅ เสร็จ | `pyproject.toml`, `Makefile`, src-layout |
| Core modules ทั้ง 10 ตัว | ✅ เสร็จ | `src/sql_agent/` |
| ฐานข้อมูลเดโม 2 ชุด | ✅ เสร็จ | `scripts/create_demo_db.py` (deterministic seed) |
| ชุดคำถาม 16 + 10 + 5 ข้อ | ✅ เสร็จ | `data/*.jsonl` — gold SQL ผ่านการ verify ทุกข้อ |
| Tests | ✅ 106 ผ่านทั้งหมด | `pytest` + `ruff` สะอาด |
| Eval harness + รายงาน | ✅ เสร็จ | `reports/evaluation.csv`, `metrics.json`, `traces/` |
| Demo CLI (golden 4 scenario) | ✅ เสร็จ | `scripts/demo_cli.py` |
| Notebooks 3 ไฟล์ | ✅ เสร็จ + execute ผ่านจริงบน CPU | `notebooks/` |
| เอกสาร | ✅ เสร็จ | `README.md`, `docs/SECURITY.md`, `docs/ARCHITECTURE.md` |
| **งานที่ต้องใช้ GPU (ค้าง)** | ⏳ รอ Colab | รัน Qwen3-4B จริง + วัด model-quality metrics |

**ผลวัดที่ยืนยันแล้วบนเครื่องนี้ (deterministic harness):**
- Unsafe block rate: **10/10 = 100%** (การวัด policy engine ของจริง)
- False block บนคำถามปกติ 16 ข้อ: **0**
- Gold SQL วิ่งผ่าน pipeline เต็มรูป (policy → executor): 16/16
- Ambiguous suite เข้าสถานะ `needs_clarification`: 5/5

---

## 1. การตีความเอกสารและการตัดสินใจหลัก

อ่านเอกสาร blueprint ทั้ง 469 บรรทัด สาระสำคัญคือสร้าง **Agentic Text-to-SQL**
ที่ไม่ใช่ one-shot แต่เป็น controlled loop: `inspect → select → generate →
policy → execute → repair (≤2) → present` โดย LLM ห้าม execute SQL ตรง ๆ
และทุกอย่าง trace ได้

การตัดสินใจที่ deviate จากแผนเล็กน้อย (พร้อมเหตุผล):

1. **src-layout package** (`src/sql_agent/*.py` + `pip install -e .`)
   แทนไฟล์หลวม ๆ ใน `src/` — แผนต้องการแสดงว่า "ต่อยอดจาก POC ไปเป็น
   service ได้" การเป็น installable package คือคำตอบที่ตรงกว่า
   ชื่อ module ภายในตรงตามแผนทุกตัว ยกเว้น `repair_loop.py` →
   ใช้ชื่อ `agent.py` (มี state machine + repair loop อยู่ด้วยกัน)
2. **เพิ่ม `OllamaBackend`** (localhost เท่านั้น) — เครื่องพัฒนาเป็น macOS
   ไม่มี CUDA จึงเพิ่มทางเลือกรันโมเดลจริงบนเครื่องได้ผ่าน Ollama
   โดยไม่แตะ cloud API (ตรงเงื่อนไข "ค่าใช้จ่าย API 0 บาท")
3. **สร้างฐานข้อมูล retail + hr เอง** แทน Spider 2.0 subset — เอกสารระบุ
   fallback นี้ไว้เอง (Risk Register: "เลือก SQLite subset ที่ตรวจแล้วหรือใช้
   Chinook + เขียน gold questions เอง") ข้อดีคือ deterministic (seed คงที่),
   ไม่มี PII แน่นอน และออกแบบให้มีช่วงเวลาที่ "ไม่มีข้อมูล" (ปี 2023)
   สำหรับ empty-result case โดยตั้งใจ
4. **Unknown column = warning ไม่ใช่ block** — ถ้า policy บล็อก column ผิด
   ตั้งแต่แรก repair loop จะไม่มีวันได้เห็น error `no such column` จาก SQLite
   ซึ่งเป็นหัวใจของ demo (executor เป็น read-only จึงไม่มีอันตราย)
   ส่วน **unknown table = block แบบ repairable** เพราะ table allowlist
   เป็นเรื่อง security (ห้ามถึง executor) แต่ให้โอกาสโมเดลแก้ 1 รอบ

## 2. สิ่งที่สร้างทีละส่วน

### 2.1 โครงสร้างและ packaging

- `pyproject.toml` — dependencies หลัก 4 ตัว (pydantic, sqlglot, pandas,
  matplotlib) + optional extras: `[dev]` (pytest, ruff), `[llm]`
  (torch/transformers/accelerate/bitsandbytes สำหรับ Colab), `[ui]` (gradio)
- `Makefile` — `setup / db / test / lint / eval-gold / demo-*`
- `.gitignore` — กัน `.env*`, keys, ฐานข้อมูล generated, cache ต่าง ๆ
- เวอร์ชันที่ติดตั้งจริง: sqlglot 27.29.0, pydantic 2.13.4, pandas 3.0.3,
  matplotlib 3.11.0, pytest 9.1.1, ruff 0.15.22 (pin ใน `requirements.txt`)

### 2.2 Core modules (`src/sql_agent/`)

| ไฟล์ | หน้าที่ | จุดสำคัญ |
|---|---|---|
| `schemas.py` | Pydantic contracts ทุกตัว | `SchemaContext`, `SQLCandidate`, `PolicyResult`, `ExecutionResult`, `SQLAttempt`, `AgentState`, `AgentConfig` — ทุก limit บังคับใน code |
| `schema_inspector.py` | อ่าน SQLite → `SchemaContext` | เปิด `mode=ro`, ใช้ `sqlite_master` + `PRAGMA table_info/foreign_key_list`, sample values ตัดที่ 60 ตัวอักษร, ปิด samples ได้ |
| `schema_selector.py` | เลือกตารางแบบ lexical | token overlap + synonym ไทย/อังกฤษ + FK expansion; ฐานเล็ก (≤5 ตาราง) ข้าม selector ตามแผน |
| `llm.py` | abstraction ของโมเดล | `ScriptedBackend` (deterministic), `TransformersBackend` (Qwen3-4B 4-bit NF4, lazy import), `OllamaBackend` (บังคับ localhost) |
| `sql_generator.py` | prompt + strict JSON parse | balanced-brace JSON extractor, Pydantic validation, format retry 1 ครั้งตามแผน, ไม่มีการ "ซ่อม" output เป็น SQL เอง |
| `sql_policy.py` | **หัวใจ security** | SQLGlot AST: single statement, root ต้องเป็น query, forbidden nodes/functions, table allowlist, LIMIT inject/clamp, fail closed ทุกกรณี |
| `executor.py` | execute แบบ read-only | 4 ชั้น: URI `mode=ro` + `PRAGMA query_only` + authorizer (deny-by-default) + deadline/row cap; sanitize error (ตัด path) |
| `agent.py` | state machine + repair | budget = 1 + 2 retries, terminal states ชัดเจน, feedback = error + ชื่อตาราง/คอลัมน์เท่านั้น (ไม่ส่ง rows) |
| `presenter.py` | ตาราง/กราฟ/answer/trace | chart สร้างเฉพาะเมื่อ hint ตรงกับคอลัมน์จริง + dtype ตัวเลข; empty result ตอบตรง ๆ |
| `evaluation.py` | canonicalize + metrics | เทียบ multiset (sort เมื่อ gold ไม่มี ORDER BY), float round 6 หลัก, ไม่มี LLM judge |

### 2.3 ข้อมูลและฐานข้อมูล

- `scripts/create_demo_db.py` — seed คงที่ (`20260717`) สร้าง:
  - `retail.sqlite`: 40 customers, 24 products (6 หมวด), 709 orders
    (2024-01 ถึง 2025-12 ปริมาณโตขึ้นตามเวลา), 1,779 order_items
  - `hr.sqlite`: 6 departments, 60 employees, 123 salary records
- `data/evaluation_questions.jsonl` — 16 ข้อ (มี `question_th` ทุกข้อ)
  ครอบคลุม aggregation, join 2-3 ตาราง, date logic, ranking, CTE,
  empty result — **gold SQL ทุกข้อถูก execute ตรวจแล้วว่า valid**
- `data/safety_prompts.jsonl` — 10 ข้อ: DELETE/DROP/UPDATE, stacked query,
  prompt injection, `sqlite_master`, `load_extension`, `ATTACH`, `PRAGMA`,
  CREATE TRIGGER
- `data/ambiguous_questions.jsonl` — 5 ข้อ พร้อมเหตุผลว่ากำกวมอย่างไร

### 2.4 Tests (106 ข้อ ผ่านทั้งหมด)

- `test_sql_policy.py` — destructive 12 แบบ (parametrized), multi-statement,
  parse fail → fail closed, unknown table (repairable), system table,
  forbidden function, cross-db, LIMIT inject/preserve/clamp, CTE/UNION,
  unknown column เป็น warning, SELECT INTO, case-insensitive
- `test_executor_readonly.py` — write 5 แบบโดนปฏิเสธ**แม้ไม่ผ่าน policy**
  (defense in depth), ฐานข้อมูลไม่เปลี่ยนหลังพยายามเขียน, timeout ด้วย
  recursive CTE 100M steps (โดน interrupt จริง), row cap + truncated flag,
  error classification, path ไม่หลุดใน error
- `test_schemas_and_generator.py` — JSON parse 8 รูปแบบ (valid/fence/prose/
  no-json/truncated/wrong-type/array/semicolon), format retry, prompts
- `test_inspector_and_selector.py` — PK/FK/samples/row counts, truncation,
  missing file, selector เลือกถูก + synonym ไทย + fallback full schema
- `test_agent_loop.py` — ทุก terminal state: success ครั้งแรก, repair
  wrong column สำเร็จ (และ feedback ไม่มี data rows), blocked destructive
  (execution เป็น None — ไม่เคยถึง DB), prompt injection stacked query,
  needs_clarification, budget exhausted (เพดานบังคับจริง), unknown table
  repairable, invalid JSON จนหมด budget, empty result + คำอธิบาย, trace
  serialize เป็น JSON ได้
- `test_evaluation_and_presenter.py` — canonicalize (order/float/mixed types),
  compare (alias ต่างกันได้/คอลัมน์ไม่ครบ/ค่าไม่ตรง/order), metrics ทุกสูตร,
  chart สร้างจริง + ปฏิเสธ hallucinated columns / non-numeric / error / empty

### 2.5 Eval harness + Demo

- `scripts/run_eval.py` — 3 backend: `gold-replay` (ไม่ใช้โมเดล — พิสูจน์
  harness + วัด policy จริง), `transformers`, `ollama` → เขียน
  `reports/evaluation.csv`, `reports/metrics.json`, `reports/traces/*.json`
  และ exit code 2 ถ้า unsafe_block_rate < 100%
- `scripts/demo_cli.py` — golden scenarios 4 แบบตรงตามสคริปต์เดโม 90 วินาที
  ในเอกสาร: `success` / `repair` (เห็น `no such column: revenue` แล้วซ่อมเป็น
  `quantity * unit_price`) / `blocked` (ภาษาไทย "ลบข้อมูลลูกค้าทั้งหมด" →
  BLOCKED ก่อนถึง DB) / `clarify` + โหมดถามโมเดลจริง (`--backend ollama`)
- ผลรันจริงบนเครื่องนี้เก็บไว้ที่ `reports/` แล้ว (backend: gold-replay)

### 2.6 Notebooks (execute ผ่านจริงทั้ง 3 ไฟล์บน CPU)

- `01_dataset_and_schema.ipynb` — environment check ตาม Cell 00 ของแผน,
  สร้าง DB, inspect schema, ดูคำถาม
- `02_agent_pipeline.ipynb` — เลือก backend อัตโนมัติ (GPU → Qwen3-4B 4-bit,
  fallback 3B ตาม CHECKPOINT ของแผน; ไม่มี GPU → scripted golden cases),
  golden 3 cases พร้อม assert, policy playground 8 การโจมตี
- `03_evaluation_and_demo.ipynb` — รัน eval, อ่าน metrics/traces, วาดกราฟ
  จากข้อมูลจริง, แนวทางอ่านตัวเลขอย่างซื่อสัตย์

### 2.7 คุณภาพโค้ด

- `ruff` (rules: E,F,W,I,B,UP,S,N,C4,RET,SIM) — ผ่านสะอาด; จุดที่กด noqa
  มีคำอธิบายทุกจุด (เช่น identifier จาก `sqlite_master` ที่ quote แล้ว)
- ทุก public function มี docstring อธิบาย "ทำไม" ไม่ใช่แค่ "ทำอะไร"

## 3. วิธีตรวจสอบซ้ำ (reproduce)

```bash
cd local-enterprise-sql-agent
make setup        # หรือ: python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"
make db           # สร้างฐานข้อมูล (ได้ผลเหมือนเดิมทุกครั้ง — seed คงที่)
make test         # 106 tests ต้องผ่านทั้งหมด
make lint         # ruff ต้องสะอาด
make eval-gold    # unsafe_block_rate ต้องเป็น 1.0, false_block_count = 0
make demo-repair  # ต้องเห็น attempt 1 error → attempt 2 สำเร็จ 12 rows
make demo-blocked # ต้องเห็น BLOCKED (not_read_only:Delete)
```

## 4. งานที่เหลือ (ต้องใช้ GPU / เป็นขั้นถัดไป)

ตาม mapping แผน 5 วัน สิ่งที่ทำครบแล้วคือส่วนที่รันบน CPU ได้ทั้งหมดของ
Day 1–5 สิ่งที่เหลือคือส่วนที่ต้องมีโมเดลจริง:

1. **รัน Qwen3-4B-Instruct-2507 จริงบน Colab GPU** —
   เปิด `notebooks/02_agent_pipeline.ipynb` บน Colab, `pip install -e ".[llm]"`
   แล้วรัน `scripts/run_eval.py --backend transformers` เพื่อได้ตัวเลข
   execution success / result match / repair rate ของโมเดลจริง
   (README ตั้งใจ**ไม่อ้างตัวเลขเหล่านี้จนกว่าจะวัดจริง**)
2. **Ablation Day 2** — full schema vs selected schema (โครงพร้อมแล้ว:
   selector เปิด/ปิดได้ผ่าน `small_schema_threshold`)
3. **Failure analysis จากผลโมเดลจริง** — เติม `reports/failure_analysis.md`
   ด้วย case จริง ≥5 รายการพร้อม root cause
4. **อัด GIF/วิดีโอ 90 วินาที** — ใช้ `demo_cli.py` scenario
   success → repair → blocked ตามสคริปต์ในเอกสาร
5. **(Optional) Gradio UI** — แผนบอกทำเฉพาะถ้ามีเวลา; extras `[ui]` เตรียมไว้แล้ว
6. **Clean-room test บน Colab** — clone ใหม่ รัน notebook ตามลำดับ
   (ทำแล้วบนเครื่องนี้ผ่าน nbclient; ต้องทำซ้ำบน Colab ก่อน ship ตาม SHIP GATE)

## 5. Checklist เทียบกับ Final Submission Checklist ในเอกสาร

- [x] Notebook รันจาก runtime ใหม่โดยไม่แก้ cell กลางทาง (ตรวจด้วย nbclient บนเครื่องนี้)
- [x] มี test ≥15 business questions + 10 safety prompts (16 + 10 + 5)
- [x] แสดง baseline / first-attempt / final / repair metrics แยกกัน (โครง metrics ครบ — ตัวเลขโมเดลจริงรอ GPU)
- [x] SQL ทุกชุดผ่าน policy ก่อน execute และ connection เป็น read-only (บังคับใน code + tests)
- [x] README มี architecture, data lineage, limitations และ license
- [ ] failure_analysis.md ≥5 กรณีจากโมเดลจริง (template พร้อม — รอผล GPU)
- [x] มี demo success, repair และ blocked attack (CLI ทำงานแล้วทั้ง 4 scenario)
- [x] ไม่มี API key, secret หรือข้อมูลส่วนบุคคลใน repo
