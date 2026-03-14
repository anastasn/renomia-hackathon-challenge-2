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

import google.generativeai as genai
import psycopg2
from fastapi import FastAPI
from loguru import logger as log
import uvicorn

from aggregator import aggregate
from extractor import extract_document
from refiner import refine

app = FastAPI(title="Challenge 2: Document Data Extraction")

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://hackathon:hackathon@localhost:5432/hackathon"
)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")


class GeminiTracker:
    """Wrapper around Gemini that tracks token usage."""

    def __init__(self, api_key: str, model_name: str = "gemini-2.5-flash"):
        self.enabled = bool(api_key)
        if self.enabled:
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel(model_name)
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.request_count = 0
        self._lock = threading.Lock()

    def generate(self, prompt, **kwargs):
        if not self.enabled:
            raise RuntimeError("Gemini API key not configured")
        response = self.model.generate_content(prompt, **kwargs)
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

    # --- Cache lookup -----------------------------------------------------------
    # Key is an MD5 hash of the sorted (filename, ocr_text) pairs so that
    # document order in the request does not affect cache hits.
    #cache_key = hashlib.md5(
    #    json.dumps(
    #        sorted(
    #            [{"f": d["filename"], "t": d["ocr_text"]} for d in documents],
    #            key=lambda x: x["f"],
    #        ),
    #        ensure_ascii=False,
    #    ).encode()
    #).hexdigest()

    #try:
    #    conn = get_db()
    #    cur = conn.cursor()
    #    cur.execute("SELECT value FROM cache WHERE key = %s", (cache_key,))
    #    row = cur.fetchone()
    #    cur.close()
    #    conn.close()
    #    if row:
    #        return row[0]
    #except Exception:
    #    pass  # Cache miss or DB unavailable — proceed with extraction

    # --- Per-document extraction ------------------------------------------------
    extracts = [
        extract_document(doc["ocr_text"], doc["filename"], gemini)
        for doc in documents
    ]

    # --- Debug logging of extracts (can be removed in production) ----------------
    for i, extract in enumerate(extracts):
        log.debug(f"Extract for document {i} ({documents[i]['filename']}): {extract.model_dump()}")

    # --- Aggregation ------------------------------------------------------------
    final = aggregate(extracts)

    # --- Refinement (LLM post-processing) ---------------------------------------
    final = refine(final, extracts, gemini)

    result = final.model_dump()

    log.debug(f"Aggregated result: {result}")

    # --- Cache write ------------------------------------------------------------
    #try:
    #    conn = get_db()
    #    cur = conn.cursor()
    #    cur.execute(
    #        """
    #        INSERT INTO cache (key, value) VALUES (%s, %s)
    #        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    #        """,
    #        (cache_key, json.dumps(result)),
    #    )
    #    conn.commit()
    #    cur.close()
    #    conn.close()
    #except Exception:
    #    pass  # Non-fatal — result is still returned to the caller

    return result


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
