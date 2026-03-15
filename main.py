"""
Challenge 2: Vyčítání dat ze souborů (Document Data Extraction)

Input:  OCR text from insurance contract documents (main contract + amendments)
Output: Structured CRM fields extracted from the documents
"""

import hashlib
import json
import os
import threading
import time

import google.genai as genai
import psycopg2
from fastapi import FastAPI
from loguru import logger as log
import uvicorn

from pipeline import run_pipeline
from nodes.extractor import extract_all_documents
from utils.models import ContractExtract

app = FastAPI(title="Challenge 2: Document Data Extraction")

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://hackathon:hackathon@localhost:5432/hackathon"
)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")


class GeminiTracker:
    """Wrapper around Gemini that tracks token usage."""
    def __init__(self, api_key: str, model_name: str = "gemini-2.5-flash"):
        self.enabled = bool(api_key)
        self.model_name = model_name
        if self.enabled:
            self.client = genai.Client(api_key=api_key)
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.request_count = 0
        self._lock = threading.Lock()

    def generate(self, prompt, model: str | None = None, **kwargs):
        if not self.enabled:
            raise RuntimeError("Gemini API key not configured")
        response = self.client.models.generate_content(
            model=model or self.model_name,
            contents=prompt,
            **kwargs,
        )
        with self._lock:
            self.request_count += 1
            meta = getattr(response, "usage_metadata", None)
            if meta:
                self.prompt_tokens += getattr(meta, "prompt_token_count", 0)
                self.completion_tokens += getattr(meta, "candidates_token_count", 0)
                self.total_tokens += getattr(meta, "total_token_count", 0)
        return response

    def get_metrics(self):
        with self._lock:
            return {
                "gemini_request_count": self.request_count,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
            }

    def reset(self):
        with self._lock:
            self.prompt_tokens = 0
            self.completion_tokens = 0
            self.total_tokens = 0
            self.request_count = 0


gemini = GeminiTracker(GEMINI_API_KEY)
gemini_pro = GeminiTracker(GEMINI_API_KEY, model_name="gemini-3.1-pro-preview")


def get_db():
    return psycopg2.connect(DATABASE_URL)


@app.on_event("startup")
def init_db():
    for _ in range(15):
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute(
                """CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    value JSONB,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )"""
            )
            conn.commit()
            cur.close()
            conn.close()
            return
        except Exception:
            time.sleep(1)


@app.get("/")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return gemini.get_metrics()


@app.post("/metrics/reset")
def reset_metrics():
    gemini.reset()
    return {"status": "reset"}

@app.post("/solve")
def solve(payload: dict):
    """
    Extract structured CRM fields from insurance contract documents.

    Pipeline:
    1. Check the PostgreSQL cache — if this exact set of documents was seen
       before, return the cached result immediately.
    2. Extract each document independently via Gemini (extractor.py).
    3. Merge all extracts into the final contract state (aggregator.py):
       - Main contract provides the base values.
       - Amendments are applied in order; non-null fields override the base.
       - latestEndorsementNumber is computed as the highest amendment number.
    4. Persist the result to the cache and return it.
    """
    documents: list[dict] = payload.get("documents", [])

    # Layer 1: full-result cache
    result_key = "result:" + hashlib.md5(
        json.dumps(
            sorted(
                [{"f": d["filename"], "t": d["ocr_text"]} for d in documents],
                key=lambda x: x["f"],
            ),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()

    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT value FROM cache WHERE key = %s", (result_key,))
        row = cur.fetchone(); cur.close(); conn.close()
        if row:
            cached = row[0]
            return cached
    except Exception:
        pass

    # Layer 2: per-document extract cache
    doc_extracts: list = [None] * len(documents)
    try:
        conn = get_db(); cur = conn.cursor()
        for i, doc in enumerate(documents):
            doc_key = "extract:" + hashlib.md5(
                json.dumps({"f": doc["filename"], "t": doc["ocr_text"]},
                           ensure_ascii=False).encode()
            ).hexdigest()
            cur.execute("SELECT value FROM cache WHERE key = %s", (doc_key,))
            row = cur.fetchone()
            if row:
                try:
                    doc_extracts[i] = ContractExtract.model_validate(row[0])
                except Exception:
                    pass  # corrupted entry → treat as cache miss
        cur.close(); conn.close()
    except Exception:
        pass  # DB error → extract everything

    uncached_indices = [i for i, e in enumerate(doc_extracts) if e is None]

    if uncached_indices:
        uncached_docs = [documents[i] for i in uncached_indices]
        new_extracts = extract_all_documents(uncached_docs, gemini_pro)
        try:
            conn = get_db(); cur = conn.cursor()
            for idx, extract in zip(uncached_indices, new_extracts):
                doc_key = "extract:" + hashlib.md5(
                    json.dumps({"f": documents[idx]["filename"], "t": documents[idx]["ocr_text"]},
                               ensure_ascii=False).encode()
                ).hexdigest()
                cur.execute(
                    "INSERT INTO cache (key, value) VALUES (%s, %s) "
                    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                    (doc_key, json.dumps(extract.model_dump()))
                )
                doc_extracts[idx] = extract
            conn.commit(); cur.close(); conn.close()
        except Exception:
            for idx, extract in zip(uncached_indices, new_extracts):
                doc_extracts[idx] = extract

    all_extracts = [e for e in doc_extracts if e is not None]

    log.debug(f"Running pipeline on {len(documents)} documents ({len(uncached_indices)} freshly extracted)...")
    final = run_pipeline(
        documents,
        gemini_extract=gemini_pro,
        gemini_refine=gemini,
        extracts=all_extracts,
    )
    log.debug("Pipeline complete.")

    result = final.model_dump()

    log.debug(f"Aggregated result: {result}")
    log.debug(f"Gemini Pro metrics: {gemini_pro.get_metrics()}")
    log.debug(f"Gemini metrics: {gemini.get_metrics()}")

    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(
            "INSERT INTO cache (key, value) VALUES (%s, %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (result_key, json.dumps(result)),
        )
        conn.commit(); cur.close(); conn.close()
    except Exception:
        pass

    return result


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
