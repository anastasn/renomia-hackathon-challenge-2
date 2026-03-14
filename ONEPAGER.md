# Renomia Hackathon — Challenge 2
## Automated Insurance Document Data Extraction

---

### Problem

Czech insurance contract bundles (base contract + general terms + amendments) arrive as raw OCR text. Today, CRM operators manually read each document and type ~20 structured fields into the system. Amendments silently override earlier values with no automated tracking.

---

### Solution

A FastAPI microservice that accepts OCR text and returns a fully populated CRM JSON object — in one POST call.

- **LLM-based extraction** — Gemini Pro reads each document and outputs a validated `ContractExtract` per file
- **Amendment-aware aggregation** — documents are merged in priority order
- **LLM refinement** — a second Gemini Flash pass resolves ambiguous fields (e.g. insurer name) using the full contract context
- **Two-layer PostgreSQL cache** — full-result cache for identical requests; per-document cache so only new amendments hit the LLM on re-submit

---

### Key Differentiators

- **Zero schema drift**: Gemini extraction schema is generated at runtime from the same Pydantic model used for validation — one source of truth
- **Scales to any bundle size**: recursive batch splitting automatically handles context window overflow — no manual tuning
- **Dual-model strategy**: Pro for complex structured extraction, Flash for fast single-field refinement — cost matched to task complexity
- **Modular pipeline**: each pipeline stage is a pure function over shared state — trivial to add nodes, retries, or human-in-the-loop steps

---

### Tech Stack

- **Runtime**: Python 3.12, FastAPI, Uvicorn
- **LLM**: Google Gemini 3.1 Pro + Flash 
- **Validation**: Pydantic v2
- **Cache**: PostgreSQL (single `cache` table, JSONB values)
- **Deploy**: Docker Compose → Google Cloud Run (Cloud Build CI/CD)
- **Observability**: Loguru + `/metrics` token-usage endpoint

---

### Result

Structured CRM extraction from multi-document insurance bundles with amendment-aware field merging, context-safe LLM batching, and cost-optimised dual-model inference — delivered as a single HTTP endpoint.
