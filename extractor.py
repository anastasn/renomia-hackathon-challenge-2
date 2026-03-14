"""Per-document LLM extraction using Gemini."""

import re
from typing import Any

from models import ContractExtract
from prompts import SYSTEM_PROMPT_EXTRACTION


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


# Build once at import time — the schema is static
_pydantic_schema = ContractExtract.model_json_schema()
_GEMINI_SCHEMA = _to_gemini_schema(_pydantic_schema, _pydantic_schema.get("$defs", {}))


def extract_document(ocr_text: str, filename: str, gemini: Any) -> ContractExtract:
    """
    Extract structured CRM fields from a single insurance document.

    Sends the OCR text to Gemini with a structured extraction prompt and
    parses the JSON response into a ContractExtract model.

    Args:
        ocr_text: Raw OCR text of the document.
        filename: Original filename — used as a hint for document type detection.
        gemini:   GeminiTracker instance (configured in main.py).

    Returns:
        ContractExtract with all available fields populated; missing fields
        default to None.

    Raises:
        ValueError: If the LLM response cannot be parsed as valid JSON or does
                    not conform to the ContractExtract schema.
    """
    prompt = SYSTEM_PROMPT_EXTRACTION.format(filename=filename, ocr_text=ocr_text)

    response = gemini.generate(
        prompt,
        generation_config={
            "response_mime_type": "application/json",
            "response_schema": _GEMINI_SCHEMA,
        },
    )
    raw: str = response.text.strip()

    # Strip markdown code fences — fallback for older model behaviour
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw.strip())

    # model_validate_json handles all coercion, including the nested Premium object
    return ContractExtract.model_validate_json(raw)
