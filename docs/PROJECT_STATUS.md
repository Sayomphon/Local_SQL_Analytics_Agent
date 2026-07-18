# Project Status — เทียบกับ Blueprint (docx)

> อ้างอิง `01_Local_SQL_Analytics_Agent_5Day_Plan_TH.docx` (v1.0)
> อัปเดตล่าสุด: 18 กรกฎาคม 2026
> สัญลักษณ์: ✅ เสร็จ+ตรวจแล้ว · 🟡 โครงพร้อม รอวัดจริงด้วยโมเดล (ต้อง GPU) · ⏳ ยังไม่เริ่ม

---

## 1. ทำอะไรไปแล้วบ้าง (สรุป)

Bootstrap โปรเจกต์ครบทุกส่วนที่รันบน **CPU** ได้ และ push ขึ้น GitHub แล้ว
(https://github.com/Sayomphon/Local_SQL_Analytics_Agent)

- **Core package** `src/sql_agent/` 10 โมดูล: schemas, schema_inspector,
  schema_selector, llm, sql_generator, sql_policy, executor, agent,
  presenter, evaluation
- **Security 3 ชั้นอิสระ**: SQLGlot AST policy → read-only executor
  (mode=ro + authorizer + timeout + row cap) → agent loop ที่บังคับ retry budget
- **106 tests ผ่านทั้งหมด** + ruff สะอาด
- **ฐานข้อมูลเดโม** retail.sqlite + hr.sqlite (deterministic, ไม่มี PII)
- **ชุดคำถาม**: 16 business + 10 safety + 5 ambiguous (gold SQL verify แล้วทุกข้อ)
- **Eval harness + Demo CLI + 3 notebooks** (execute ผ่านจริงบน CPU)
- **เอกสาร**: README, SECURITY, ARCHITECTURE, DEVELOPMENT_LOG (TH), failure_analysis

**ผลที่ยืนยันแล้ว (deterministic harness):** unsafe block rate 10/10 = 100%,
false block = 0, gold SQL ผ่าน pipeline 16/16, ambiguous → clarification 5/5

---

## 2. ความคืบหน้าเทียบแผน 5 วัน (บทที่ 7 ของ docx)

### Day 1 — Dataset, Schema Inspector, Baseline
| งาน | สถานะ |
|---|---|
| repo, requirements, environment check, เลือกฐานข้อมูล 2 ชุด | ✅ |
| `schema_inspector` อ่าน tables/columns/PK/FK/samples | ✅ |
| โหลด Qwen3-4B 4-bit + prompt baseline | 🟡 โครง `TransformersBackend` พร้อม รอรันบน GPU |
| executor เบื้องต้น + บันทึกผล/error | ✅ (executor เสร็จ; `baseline_results.jsonl` สร้างเมื่อรันโมเดล) |
| **DoD**: DB version-controlled ✅ · SchemaContext เป็น Pydantic ✅ · baseline reproducible ✅ | ✅ |

### Day 2 — Structured Generation, Schema Selection
| งาน | สถานะ |
|---|---|
| นิยาม `SQLCandidate` + JSON parse/repair | ✅ (ทดสอบ 8 รูปแบบ JSON) |
| lexical schema selector (token overlap + FK) | ✅ (+ synonym ไทย/อังกฤษ) |
| prompt v2 (role, constraints, dialect, few-shot) | ✅ (`PROMPT_VERSION="v2"`) |
| ablation full vs selected schema | 🟡 selector เปิด/ปิดได้ผ่าน `small_schema_threshold` — รอวัดจริง GPU |
| **DoD**: output schema บังคับ ✅ · prompt version ใน config ✅ · ablation ≥10 ข้อ 🟡 | 🟡 |

### Day 3 — SQL Guardrail, Read-only Execution, Repair Loop
| งาน | สถานะ |
|---|---|
| SQLGlot AST + allowlist SELECT/WITH | ✅ |
| unknown table/column check, LIMIT, timeout, row cap | ✅ |
| repair controller (error + schema feedback) | ✅ |
| รัน test set 15–25 ข้อ + เก็บ first/final | ✅ harness; ตัวเลขโมเดลจริง 🟡 |
| **DoD**: unsafe ไม่ถึง execute ✅ · retry budget บังคับใน code ✅ · trace sanitized ✅ | ✅ |

### Day 4 — Evaluation, Presenter, Failure Analysis
| งาน | สถานะ |
|---|---|
| result canonicalization (เทียบไม่ยึด order เมื่อไม่จำเป็น) | ✅ |
| metrics: execution success, result match, repair, block rate | ✅ (มี unit tests) |
| presenter table/chart จากผลจริง | ✅ (chart ปฏิเสธ hallucinated values) |
| failure taxonomy + 3 demo cases | ✅ taxonomy 8 หมวด + harness cases; **≥5 เคสโมเดลจริง 🟡** |
| **DoD**: baseline/agent แยกกัน 🟡 · safety ≥10 ✅ · ไม่ใช้ LLM judge ✅ | 🟡 |

### Day 5 — Demo Surface, README, Reproducibility
| งาน | สถานะ |
|---|---|
| notebook UI แสดง question/schema/SQL/trace/result | ✅ (Gradio = optional ยังไม่ทำ) |
| README: problem, architecture, setup, results, limitations | ✅ |
| clean-room test บน runtime ใหม่ | ✅ local (nbclient); **🟡 บน Colab จริงก่อน ship** |
| GIF/วิดีโอ 90 วินาที + interview notes | ⏳ |
| **DoD**: README metrics จริง 🟡 · requirements pinned ✅ · ไม่มี secret ✅ | 🟡 |

---

## 3. ขั้นตอนต่อไป (ลำดับแนะนำ)

**เตรียมก่อนขึ้น Colab (ทำบน CPU ได้):**
1. แก้ `<YOUR_REPO_URL>` ใน Cell 00 ของ notebook → URL จริง + เพิ่มปุ่ม Open in Colab ใน README
2. (optional) GitHub Actions CI รัน pytest + ruff อัตโนมัติ

**บน Colab GPU (ปลดล็อกตัวเลขจริง):**
3. รัน `scripts/run_eval.py --backend transformers` → ได้ execution success /
   result match / first-attempt / repair rate ของ Qwen3-4B จริง
4. ทำ ablation full vs selected schema (Day 2)
5. เติมตัวเลขจริงลง README + `reports/failure_analysis.md` (≥5 เคส)
6. อัด GIF/วิดีโอ 90 วินาที (`demo_cli.py`: success → repair → blocked)
7. clean-room test บน Colab runtime ใหม่ (SHIP GATE)

---

## 4. เหลืออะไรตาม Final Submission Checklist (บทที่ 13 ของ docx)

| # | เกณฑ์ | สถานะ |
|---|---|---|
| 1 | Notebook รันจาก runtime ใหม่โดยไม่แก้ cell กลางทาง | ✅ local · 🟡 ต้องยืนยันบน Colab |
| 2 | test ≥15 business + 10 safety prompts | ✅ (16 + 10 + 5) |
| 3 | แสดง baseline / first-attempt / final / repair metrics แยกกัน | 🟡 โครง metrics ครบ — รอตัวเลขโมเดลจริง |
| 4 | SQL ทุกชุดผ่าน policy ก่อน execute + connection read-only | ✅ |
| 5 | README มี architecture, data lineage, limitations, license | ✅ |
| 6 | failure_analysis.md ≥5 กรณี | 🟡 template + harness cases พร้อม — รอเคสโมเดลจริง |
| 7 | demo success, repair, blocked attack | ✅ (CLI 4 scenario) |
| 8 | ไม่มี API key, secret, PII | ✅ |

**SHIP GATE (บทที่ 13):** ยังไม่ผ่านจนกว่าจะ clone ใหม่บน Colab runtime สะอาด →
รัน notebook ตามลำดับ → ได้ metrics จริงจากโมเดล → เปิด demo ได้โดยไม่ต้อง patch มือ

---

## 5. สิ่งที่อยู่นอก scope ตั้งแต่แรก (บทที่ 3 ของ docx — ไม่ต้องทำ)

Multi-agent, MCP server เต็มรูป, long-term memory, cloud database จริง
(Snowflake/BigQuery) + OAuth, fine-tuning/RL, benchmark เต็ม 632 ข้อของ
Spider 2.0, execute DDL/DML ทุกประเภท, production API deployment
