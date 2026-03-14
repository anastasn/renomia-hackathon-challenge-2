"""
Challenge 2: Vyčítání dat ze souborů (Document Data Extraction)

Input:  OCR text from insurance contract documents (main contract + amendments)
Output: Structured CRM fields extracted from the documents
"""

from pathlib import Path
import hashlib
import json
import os
import tempfile
import threading
import time
from difflib import SequenceMatcher

import google.genai as genai
import psycopg2
from fastapi import FastAPI
from loguru import logger as log
import uvicorn

from pipeline import run_pipeline

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

DATASET_PATH = Path(__file__).parent / "data.json"
RESULT_PATH = Path(__file__).parent / "result.json"
OUTPUT_PATH = Path(__file__).parent / "output.json"


def _safe_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        delete=False,
    ) as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        temp_name = tmp.name
    Path(temp_name).replace(path)
    path.chmod(0o644)


def _load_fixture_by_id(input_id: int | str) -> dict:
    try:
        normalized_id = int(input_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("Payload field 'input' or 'id' must be an integer test case id.") from exc

    with DATASET_PATH.open(encoding="utf-8") as fixture_file:
        fixtures = json.load(fixture_file)

    for fixture in fixtures:
        if fixture.get("id") == normalized_id:
            return fixture

    raise ValueError(f"Input fixture with id {normalized_id} was not found in {DATASET_PATH.name}.")

def _load_documents_from_fixture(input_id: int | str) -> list[dict]:
    """
        Load a sample input from data.json by its case id.

        The fixture file is expected to be a JSON array where each item contains:
        
    id
    input.documents"""
    fixture = _load_fixture_by_id(input_id)
    normalized_id = int(input_id)
    payload = fixture.get("input", {})
    documents = payload.get("documents", [])
    if not isinstance(documents, list):
        raise ValueError(f"Fixture {normalized_id} has invalid 'documents' data.")
    return documents


def _resolve_fixture_id(payload: dict) -> int | str:
    if "id" in payload:
        return payload["id"]
    return payload.get("input", 0)


def _flatten_json(value, prefix: str = "") -> dict[str, object]:
    if isinstance(value, dict):
        flattened: dict[str, object] = {}
        for key, item in value.items():
            next_prefix = f"{prefix}.{key}" if prefix else key
            flattened.update(_flatten_json(item, next_prefix))
        return flattened
    return {prefix: value}


def _compare_value(expected, actual) -> tuple[float, str]:
    if expected is None or actual is None:
        return (1.0, "match") if expected is actual else (0.0, "mismatch")

    if isinstance(expected, bool) or isinstance(actual, bool):
        return (1.0, "match") if expected == actual else (0.0, "mismatch")

    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        if expected == 0:
            return (1.0, "match") if actual == 0 else (0.0, "mismatch")
        return (1.0, "match") if abs(expected - actual) <= abs(expected) * 0.10 else (0.0, "mismatch")

    if isinstance(expected, str) and isinstance(actual, str):
        similarity = SequenceMatcher(None, expected.casefold(), actual.casefold()).ratio()
        if similarity == 1.0:
            return (1.0, "match")
        if similarity >= 0.85:
            return (similarity, "similar")
        return (similarity, "mismatch")

    return (1.0, "match") if expected == actual else (0.0, "mismatch")


def _build_comparison(expected_output: dict, result: dict, fixture_id: int | str) -> dict:
    flat_expected = _flatten_json(expected_output)
    flat_result = _flatten_json(result)
    all_paths = sorted(set(flat_expected) | set(flat_result))
    rows = []
    mismatch_count = 0

    for path in all_paths:
        expected = flat_expected.get(path)
        actual = flat_result.get(path)
        score, status = _compare_value(expected, actual)
        if status == "mismatch":
            mismatch_count += 1
            rows.append(
                {
                    "field": path,
                    "expected_output": expected,
                    "result": actual,
                }
            )

    return {
        "id": int(fixture_id),
        "matches": mismatch_count == 0,
        "summary": {
            "mismatchCount": mismatch_count,
        },
        "comparison": rows,
    }

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
    fixture_id = _resolve_fixture_id(payload)
    used_fixture = False
    documents: list[dict] = payload.get("documents", [])
    if not documents:
        documents = _load_documents_from_fixture(fixture_id)
        used_fixture = True

    # --- Cache lookup -----------------------------------------------------------
    # Key is an MD5 hash of the sorted (filename, ocr_text) pairs so that
    # document order in the request does not affect cache hits.
    cache_key = hashlib.md5(
        json.dumps(
            sorted(
                [{"f": d["filename"], "t": d["ocr_text"]} for d in documents],
                key=lambda x: x["f"],
            ),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()

    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT value FROM cache WHERE key = %s", (cache_key,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            cached = row[0]
            return cached
    except Exception:
        pass  # Cache miss or DB unavailable — proceed with extraction

    log.debug(f"Running pipeline on {len(documents)} documents...")
    final = run_pipeline(
        documents,
        gemini_extract=gemini_pro,
        gemini_refine=gemini,
    )
    log.debug("Pipeline complete.")

    result = final.model_dump()
    _safe_write_json(RESULT_PATH, result)
    if used_fixture:
        fixture = _load_fixture_by_id(fixture_id)
        comparison = _build_comparison(fixture.get("expected_output", {}), result, fixture_id)
        _safe_write_json(OUTPUT_PATH, comparison)

    log.debug(f"Aggregated result: {result}")
    log.debug(f"Gemini Pro metrics: {gemini_pro.get_metrics()}")
    log.debug(f"Gemini metrics: {gemini.get_metrics()}")

    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO cache (key, value) VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """,
            (cache_key, json.dumps(result)),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass  # Non-fatal — result is still returned to the caller

    return result


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
