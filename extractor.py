"""Per-document LLM extraction using Gemini."""

import json
import re
from google.genai.types import GenerateContentConfig, ThinkingConfig
from typing import Any

from models import ContractExtract
from prompts import PROMPT_BATCH_EXTRACTION


def _sanitise_none_strings(obj: Any) -> Any:
    """Recursively replace any string value exactly equal to "None" with None."""
    if isinstance(obj, dict):
        return {k: _sanitise_none_strings(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitise_none_strings(v) for v in obj]
    if obj == "None":
        return None
    return obj


def _to_gemini_schema(schema: dict, defs: dict) -> dict:
    """
    Convert a Pydantic v2 JSON schema node to a Gemini-compatible schema dict.

    Pydantic v2 emits fields that protos.Schema rejects:
    - "default"  — not a valid proto field → stripped
    - "title"    — not needed → stripped
    - "$ref"     — Gemini has no $ref support → resolved by inlining $defs
    - anyOf: [{type: X}, {type: null}]  → converted to {type: X, nullable: true}
    """
    # Resolve $ref by substituting the referenced definition inline
    if "$ref" in schema:
        ref_name = schema["$ref"].split("/")[-1]
        return _to_gemini_schema(defs[ref_name], defs)

    # Handle nullable union: anyOf: [<real schema>, {type: null}]
    if "anyOf" in schema:
        resolved = [_to_gemini_schema(s, defs) for s in schema["anyOf"]]
        non_null = [s for s in resolved if s.get("type") != "null"]
        has_null = any(s.get("type") == "null" for s in resolved)
        if non_null:
            result = dict(non_null[0])
            if has_null:
                result["nullable"] = True
            return result
        return {}

    result = {}

    # Copy only the fields protos.Schema supports
    for key in ("type", "format", "description", "enum", "nullable"):
        if key in schema:
            result[key] = schema[key]

    if "properties" in schema:
        result["properties"] = {
            k: _to_gemini_schema(v, defs)
            for k, v in schema["properties"].items()
        }

    if "items" in schema:
        result["items"] = _to_gemini_schema(schema["items"], defs)

    if "required" in schema:
        result["required"] = schema["required"]

    return result


def extract_all_documents(documents: list[dict], gemini: Any) -> list[ContractExtract]:
    """
    Extract structured CRM fields from all documents in a single Gemini call.

    Args:
        documents: List of {"filename": str, "ocr_text": str} dicts.
        gemini:    GeminiTracker instance (configured in main.py).

    Returns:
        List of ContractExtract objects in the same order as the input documents.
    """
    documents_block = "\n\n".join(
        f"--- Document {i + 1}: {doc['filename']} ---\n{doc['ocr_text']}"
        for i, doc in enumerate(documents)
    )
    prompt = PROMPT_BATCH_EXTRACTION.format(documents_block=documents_block)

    response = gemini.generate(
        prompt,
        config=GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.0,  # deterministic output
            thinking_config=ThinkingConfig(
                thinking_level="MEDIUM",  # allow Gemini to "think" but keep latency reasonable
            ),
        ),
    )
    raw: str = response.text.strip()

    # Strip markdown code fences 
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw.strip())

    parsed = json.loads(raw)
    return [
        ContractExtract.model_validate(_sanitise_none_strings(item))
        for item in parsed
    ]
