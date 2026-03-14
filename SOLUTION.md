# Solution: Insurance Document Data Extraction

**Challenge 2 — Renomia Hackathon**
Extract structured CRM fields from Czech insurance contracts and amendments using LLM-based document understanding.

---

## 1. Problem

Czech insurance contracts (*pojistné smlouvy*) arrive as multi-document OCR bundles: a base contract, general terms (*VPP*), and a variable number of amendments (*dodatky*). Each document may partially override the previous one. The task is to produce a single, up-to-date JSON object of ~20 CRM fields — dates, premiums, contract regime, renewal rules, vehicle plate, and more.

Key challenges:
- **Multi-document amendment logic**: later amendments must win over earlier ones; terms are the lowest priority.
- **OCR noise**: insurer names appear in inconsistent, abbreviated forms across documents.
- **Context window limits**: a contract bundle can exceed 1 M tokens.
- **Correctness over speed**: extraction must be deterministic and cache-friendly.

---

## 2. Architecture Overview

```
POST /solve
    │
    ▼
┌─────────────────────────────────────────────┐
│  Layer 1: Full-result cache (PostgreSQL)    │
│  key = MD5(sorted filenames + ocr_texts)    │
└────────────────┬────────────────────────────┘
                 │ miss
                 ▼
┌─────────────────────────────────────────────┐
│  Layer 2: Per-document extract cache        │
│  key = MD5(filename + ocr_text)             │
│  Only uncached documents go to the LLM      │
└────────────────┬────────────────────────────┘
                 │
                 ▼
┌──────────────────────┐
│  Extraction Node     │  extractor.py
│                      │  • Pydantic→Gemini schema conversion
│                      │  • Recursive batch splitting
│                      │  
└──────────┬───────────┘
           │  List[ContractExtract]
           ▼
┌──────────────────────┐
│  Aggregation Node    │  aggregator.py
│                      │  • Priority: terms < main < amendments
│                      │  • Amendment ordering (ascending)
│                      │  • Recursive Premium merge
│                      │  • latestEndorsementNumber computation
└──────────┬───────────┘
           │  FinalContract
           ▼
┌──────────────────────┐
│  Refinement Node     │  refiner.py
│  Gemini Flash        │  • Insurer name disambiguation
│  (fast, cheap)       │  • Composable: more refiners can be added
└──────────┬───────────┘
           │  FinalContract (final)
           ▼
     Cache result → return JSON
```

---

## 3. Stage-by-Stage Walkthrough

### 3.1 Extraction (`extractor.py`)

Each document (or batch of documents) is sent to Gemini with a structured extraction prompt. The response is constrained to `application/json` matching the `ContractExtract` Pydantic schema.

**Dynamic Pydantic→Gemini schema conversion**

The `_to_gemini_schema()` function walks the Pydantic v2 JSON schema and converts it to Gemini's proto-compatible format:
- Strips unsupported fields (`default`, `title`)
- Resolves `$ref` by inlining `$defs` recursively
- Converts `anyOf: [{type: X}, {type: null}]` → `{type: X, nullable: true}`

This means the extraction schema is always derived directly from `ContractExtract` — zero schema drift between the model definition and what the LLM sees.

**Extended thinking**

All extraction calls use `ThinkingConfig(thinking_level="MEDIUM")`, giving Gemini time to reason through ambiguous Czech legal language before producing a structured answer.

**Recursive batch splitting for context overflow protection**

Before generating, the prompt token count is measured via the Gemini `count_tokens` API. If it exceeds 800 000 tokens (80% of the 1 M context limit), the document list is split in half and each half is extracted independently — recursively, until each sub-batch fits. Results are concatenated in document order.

```
_extract_batch(docs)
  if tokens > CONTEXT_LIMIT and len(docs) > 1:
      left  = _extract_batch(docs[:mid])
      right = _extract_batch(docs[mid:])
      return left + right
  else:
      return gemini.generate(prompt)
```

### 3.2 Aggregation (`aggregator.py`)

All `ContractExtract` objects are merged into a single `FinalContract` following a strict priority hierarchy:

```
Priority (lowest → highest):
  terms (VPP)  →  main contract  →  amendments (ascending by amendmentNumber)
```

For each field, the merge rule is: **non-null value in the higher-priority document wins**. The `premium` nested object is merged recursively so a partial amendment only updates the sub-fields it specifies.

`latestEndorsementNumber` is computed as the maximum across all `amendmentNumber` values and any `latestEndorsementNumber` strings found in any extract, using a sort key that handles numeric prefixes correctly (`"10"` > `"9"`, not `"9"` > `"10"`).

Business rule defaults applied post-merge:
- `actionOnInsurancePeriodTermination` defaults to `"auto-renewal"` when absent
- `startAt` ↔ `concludedAt` cross-fill when only one is present

### 3.3 Refinement (`refiner.py`)

After aggregation, a fast Gemini Flash call handles fields that benefit from cross-document reasoning rather than per-document extraction:

**Insurer name disambiguation**: Each document may abbreviate the insurer's name differently (e.g., "ČPP", "Česká podnikatelská pojišťovna, a.s."). If more than one unique variant is found across extracts, a dedicated prompt asks Gemini to pick the canonical full legal name. This uses Flash (fast and cheap) since it is a simple selection task.

The refinement node is composable: additional refiners (note consolidation, contractRegime reconciliation — implemented but disabled) can be enabled independently.

---

## 4. Two-Layer Cache

Both cache layers share a single PostgreSQL `cache` table (`key TEXT PRIMARY KEY, value JSONB`).

| Layer | Key | Stores | Benefit |
|-------|-----|--------|---------|
| 1 — full result | `result:MD5(all docs sorted)` | Final JSON | Zero LLM cost on identical request |
| 2 — per-document extract | `extract:MD5(filename+ocr_text)` | `ContractExtract` JSON | Skip LLM for unchanged docs in a re-submit |

When a contract bundle is re-submitted with one new amendment, only the new document triggers an LLM call. All previously extracted documents are served from cache. Both layers degrade gracefully: if the database is down, the system extracts fresh without error.

---

## 5. Differentiators

1. **Dynamic Pydantic→Gemini schema conversion** (`_to_gemini_schema`): The Gemini schema is derived at runtime from the same Pydantic model used for validation — guaranteed to stay in sync with no manual schema maintenance.

2. **Recursive batch splitting**: Context overflow is handled automatically by halving document batches until they fit. This scales to arbitrarily large contract bundles without manual tuning.

3. **Amendment priority system**: The three-tier ordering (terms → main → amendments) with per-field null-overwrite semantics correctly handles partial amendments — a common real-world pattern where an amendment changes only two or three fields.

4. **Two-layer cache**: Full-result and per-document caches mean the common case (re-check an existing contract) is a single Postgres read, and partial re-submissions pay only for new documents.

5. **Dual-model strategy**: Gemini Pro handles extraction (complex, structured reasoning); Gemini Flash handles refinement (fast, cheap single-field selection). Each model is matched to the task it does best.

7. **Modular architecture**: Each pipeline stage (`extraction_node`, `aggregation_node`, `refinement_node`) is a pure function over `PipelineState`. 

8. **Graceful degradation everywhere**: DB down → extract fresh. Parse failure → fallback to aggregated result. Missing main contract → clear error. No silent failures.

---

## 6. Tech Stack

| Component | Technology |
|-----------|-----------|
| API server | FastAPI + Uvicorn |
| LLM | Google Gemini 3.1 Pro (extraction) + Flash (refinement) |
| Data validation | Pydantic v2 |
| Cache / persistence | PostgreSQL (psycopg2) |
| Containerisation | Docker Compose |
| Deployment | Google Cloud Run (Cloud Build CI/CD) |
| Observability | Loguru structured logging + `/metrics` token tracking endpoint |
