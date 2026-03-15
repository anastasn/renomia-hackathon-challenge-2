"""Post-aggregation LLM refinement for fields that benefit from cross-document reasoning."""

from google.genai.types import GenerateContentConfig
from typing import Any

from utils.models import ContractExtract, FinalContract
from utils.prompts import PROMPT_INSURER_NAME, PROMPT_NOTE_CONSOLIDATION


def refine(final: FinalContract, extracts: list[ContractExtract], gemini: Any) -> FinalContract:
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
            config=GenerateContentConfig(
                max_output_tokens=256,
                temperature=0.1,  # low temperature for consistency
            ),
        )
        refined_name = response.text.strip().strip('"').strip("'")
        if refined_name:
            data["insurerName"] = refined_name

    if final.note and " | " in final.note:
        prompt = PROMPT_NOTE_CONSOLIDATION.format(notes=final.note)
        response = gemini.generate(prompt,
            config=GenerateContentConfig(
                max_output_tokens=4096,
                temperature=0.1,  # low temperature for consistency
            ),
        )
        refined_note = response.text.strip()
        if refined_note:
            data["note"] = refined_note

    return FinalContract(**data)
