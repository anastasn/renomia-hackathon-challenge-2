"""Post-aggregation LLM validation: fact-check aggregated fields against source documents."""

import json
import logging
from typing import Any

from models import ContractExtract, FinalContract
from prompts import PROMPT_VALIDATION

log = logging.getLogger(__name__)


def _build_extracts_block(extracts: list[ContractExtract]) -> str:
    """Format per-document extracts (with reasoning) for the validation prompt."""
    parts = []
    for i, e in enumerate(extracts):
        parts.append(f"--- Extract {i + 1} (documentType={e.documentType}) ---")
        parts.append(json.dumps(e.model_dump(), ensure_ascii=False, indent=2))
    return "\n".join(parts)


def validate(
    documents: list[dict],
    extracts: list[ContractExtract],
    aggregated: FinalContract,
    gemini: Any,
) -> FinalContract:
    """
    Fact-check the aggregated contract against the source documents.

    Asks Gemini to return only the fields that need correction. Corrections are
    applied in Python on top of the aggregated result so unmentioned fields are
    never touched.
    """
    documents_block = "\n\n".join(
        f"--- Document {i + 1}: {doc['filename']} ---\n{doc['ocr_text']}"
        for i, doc in enumerate(documents)
    )
    extracts_block = _build_extracts_block(extracts)
    aggregated_json = json.dumps(aggregated.model_dump(), ensure_ascii=False, indent=2)

    prompt = PROMPT_VALIDATION.format(
        documents_block=documents_block,
        extracts_block=extracts_block,
        aggregated_json=aggregated_json,
    )

    response = gemini.generate(
        prompt,
        generation_config={
            "response_mime_type": "application/json",
            "temperature": 0.0,
        },
    )

    try:
        raw = json.loads(response.text.strip())
        corrections: dict = raw.get("corrections", {})
        if not corrections:
            return aggregated

        # Apply only the corrected fields on top of the aggregated result
        data = aggregated.model_dump()
        final_fields = set(FinalContract.model_fields)
        for field, correction in corrections.items():
            if field not in final_fields:
                continue
            original = correction.get("original")
            corrected = correction.get("corrected")
            evidence = correction.get("evidence", "")
            log.debug(
                "Validator corrected %s: %r → %r | evidence: %s",
                field, original, corrected, evidence,
            )
            data[field] = corrected

        return FinalContract(**data)

    except Exception as exc:
        log.warning("Validator output parse failed (%s); returning aggregated result unchanged.", exc)
        return aggregated
