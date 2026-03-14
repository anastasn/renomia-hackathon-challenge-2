"""Post-aggregation LLM refinement for fields that benefit from cross-document reasoning."""

import json
from typing import Any

from models import ContractExtract, FinalContract
from prompts import PROMPT_FIELDS_RECONCILIATION, PROMPT_INSURER_NAME, PROMPT_NOTE_CONSOLIDATION


def refine(final: FinalContract, extracts: list[ContractExtract], gemini: Any, *, documents: list[dict] | None = None) -> FinalContract:
    """
    Apply LLM-based refinement to selected fields of the aggregated contract.

    1. insurerName — pick the canonical full legal name from all OCR variants.
    2. note        — consolidate the concatenated note fragments into a single coherent note.

    Args:
        final:    Aggregated FinalContract produced by aggregator.aggregate().
        extracts: Per-document ContractExtract results (used to collect insurer name variants).
        gemini:   GeminiTracker instance.

    Returns:
        Refined FinalContract with improved insurerName and note.
    """
    data = final.model_dump()

    variants = [e.insurerName for e in extracts if e.insurerName]
    unique_variants = list(dict.fromkeys(variants))  # deduplicate, preserve order
    if len(unique_variants) > 1:
        prompt = PROMPT_INSURER_NAME.format(variants="\n".join(f"- {v}" for v in unique_variants))
        response = gemini.generate(prompt,
            generation_config={
                "max_output_tokens": 256,
            },
        )
        refined_name = response.text.strip().strip('"').strip("'")
        if refined_name:
            data["insurerName"] = refined_name

    if final.note and " | " in final.note:
        prompt = PROMPT_NOTE_CONSOLIDATION.format(notes=final.note)
        response = gemini.generate(prompt,
            generation_config={
                "max_output_tokens": 4096,
            },
        )
        refined_note = response.text.strip()
        if refined_note:
            data["note"] = refined_note

    if documents:
        regime_values = [e.contractRegime for e in extracts]
        action_values = [e.actionOnInsurancePeriodTermination for e in extracts]
        unique_regimes = list(dict.fromkeys(v for v in regime_values if v))
        unique_actions = list(dict.fromkeys(v for v in action_values if v))

        if len(unique_regimes) > 1 or len(unique_actions) > 1:
            regime_lines = "\n".join(f"- {v}" for v in regime_values) if regime_values else "- (none)"
            action_lines = "\n".join(f"- {v}" for v in action_values) if action_values else "- (none)"
            documents_block = "\n\n".join(
                f"--- Document {i + 1}: {doc['filename']} ---\n{doc['ocr_text']}"
                for i, doc in enumerate(documents)
            )
            prompt = PROMPT_FIELDS_RECONCILIATION.format(
                regime_values=regime_lines,
                action_values=action_lines,
                documents_block=documents_block,
            )
            response = gemini.generate(prompt,
                generation_config={
                    "max_output_tokens": 256,
                    "temperature": 0.1,
                },
            )
            try:
                reconciled = json.loads(response.text.strip())
                if reconciled.get("contractRegime"):
                    data["contractRegime"] = reconciled["contractRegime"]
                if reconciled.get("actionOnInsurancePeriodTermination"):
                    data["actionOnInsurancePeriodTermination"] = reconciled["actionOnInsurancePeriodTermination"]
            except (json.JSONDecodeError, AttributeError):
                pass

    return FinalContract(**data)
