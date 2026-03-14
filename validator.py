"""Post-aggregation LLM validation: re-extract uncertain fields from raw OCR."""

import json
import logging
from typing import Any

from models import ContractExtract, FinalContract
from prompts import PROMPT_VALIDATION

log = logging.getLogger(__name__)

_FIELD_RULES: dict[str, str] = {
    "state": '"accepted" if signed/active, "draft" if návrh/unsigned, "cancelled" if explicitly terminated.',
    "endAt": "DD.MM.YYYY or null. Must be null for 'na dobu neurčitou' / 'doba neurčitá' contracts.",
    "startAt": "DD.MM.YYYY. Policy inception date (počátek pojištění). Zero-padded.",
    "concludedAt": "DD.MM.YYYY. Contract signing date. Often same as startAt.",
    "assetType": '"vehicle" only if SPZ / registrační značka appears or explicit motor vehicle language. Otherwise "other".',
    "contractRegime": '"individual" default. "fleet" only if flotila/multiple vehicles explicit. "frame" only if rámcová smlouva explicit. "coinsurance" only if multiple insurers explicitly share the same risk.',
    "actionOnInsurancePeriodTermination": '"auto-renewal" ONLY if contract explicitly states automatic continuation. "policy-termination" if any explicit step is required to continue.',
    "installmentNumberPerInsurancePeriod": "ročně=1, pololetně=2, čtvrtletně=4, měsíčně=12. null if not explicitly stated.",
    "insurancePeriodMonths": "pojistný rok/ročně=12, pololetně=6, čtvrtletně=3, měsíčně=1. null if not explicitly stated.",
    "concludedAs": '"broker" if Renomia or makléř mentioned. "agent" if direct agent only.',
    "premium.currency": "ISO 4217 lowercase (czk, eur…).",
    "premium.isCollection": "true if inkaso makléřem. false if policyholder pays directly.",
    "noticePeriod": 'Hyphenated English: "six-weeks", "two-months". null if not stated.',
    "regPlate": "SPZ / registrační značka verbatim. null if not present.",
    "latestEndorsementNumber": "Highest amendment/endorsement number anywhere in documents. Return as string.",
    "contractNumber": "Contract/policy number (číslo smlouvy, číslo pojistky).",
    "insurerName": "Full legal name of the insurer (pojistitel).",
}


def _build_field_reference(fields: list[str]) -> str:
    lines = ["FIELD RULES (for the uncertain fields only):"]
    for f in fields:
        rule = _FIELD_RULES.get(f)
        if rule:
            lines.append(f"  {f}: {rule}")
    return "\n".join(lines)


def validate(
    documents: list[dict],
    extracts: list[ContractExtract],
    aggregated: FinalContract,
    gemini: Any,
) -> FinalContract:
    # Collect uncertain fields across all extracts (union, preserving order)
    uncertain: list[str] = []
    final_fields = set(FinalContract.model_fields)
    seen: set[str] = set()
    for e in extracts:
        for f in (e.uncertainty or []):
            if f in final_fields and f not in seen:
                uncertain.append(f)
                seen.add(f)

    if not uncertain:
        log.warning("Validator: no uncertain fields — skipping LLM call")
        return aggregated

    log.warning("Validator: uncertain fields = %s", uncertain)

    documents_block = "\n\n".join(
        f"--- Document {i + 1}: {doc['filename']} ---\n{doc['ocr_text']}"
        for i, doc in enumerate(documents)
    )

    prompt = PROMPT_VALIDATION.format(
        uncertain_fields=", ".join(uncertain),
        field_reference=_build_field_reference(uncertain),
        documents_block=documents_block,
    )

    response = gemini.generate(
        prompt,
        generation_config={"response_mime_type": "application/json", "temperature": 0.0},
    )

    try:
        result = json.loads(response.text.strip())
        log.warning("Validator result: %s", result)
        data = aggregated.model_dump()
        for field, value in result.items():
            if field in final_fields and value != getattr(aggregated, field):
                log.warning("Validator corrected %s: %r → %r", field, getattr(aggregated, field), value)
                data[field] = value
        return FinalContract(**data)
    except Exception as exc:
        log.warning("Validator parse failed (%s); returning aggregated unchanged.", exc)
        return aggregated
