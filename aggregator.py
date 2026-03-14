"""Contract aggregation: merge per-document extracts into a single FinalContract."""

from models import ContractExtract, FinalContract, Premium

# Fields that belong only to ContractExtract and must not be copied to FinalContract
_METADATA_FIELDS = {"documentType", "amendmentNumber", "latestEndorsementNumber"}


def _merge_premium(base: Premium | None, override: Premium | None) -> Premium | None:
    """
    Recursively merge two Premium objects.

    Non-null fields in *override* win; null override fields fall back to *base*.
    """
    if override is None:
        return base
    if base is None:
        return override
    return Premium(
        currency=override.currency if override.currency is not None else base.currency,
        isCollection=override.isCollection if override.isCollection is not None else base.isCollection,
    )


def _apply_amendment(base: FinalContract, amendment: ContractExtract) -> FinalContract:
    """
    Apply a single amendment on top of the current contract state.

    Merge rule: for each field in FinalContract, if the amendment supplies a
    non-null value it overrides the base. Nested objects (premium) are merged
    recursively so that a partial amendment only updates changed sub-fields.

    Args:
        base:      Current contract state.
        amendment: Extracted amendment document.

    Returns:
        New FinalContract with amendment fields applied.
    """
    # Start from a mutable copy of the current state
    data = base.model_dump()

    for field in FinalContract.model_fields:
        if field == "premium":
            merged = _merge_premium(base.premium, amendment.premium)
            data["premium"] = merged.model_dump() if merged is not None else None
        elif field == "note":
            # Concatenate notes from all layers so context is preserved
            base_note = base.note
            amendment_note = getattr(amendment, "note", None)
            if base_note and amendment_note:
                data["note"] = f"{base_note} | {amendment_note}"
            elif amendment_note:
                data["note"] = amendment_note
            # else: keep base_note (already in data)
        else:
            value = getattr(amendment, field, None)
            if value is not None:
                data[field] = value

    return FinalContract(**data)


def aggregate(extracts: list[ContractExtract]) -> FinalContract:
    """
    Merge all per-document extracts into the final contract state.

    Priority (lowest → highest):
      terms (VPP) → main contract → amendments (ascending by number)

    Algorithm:
    1. Seed the result from the terms document (VPP), if present.
    2. Apply the main contract on top — its non-null fields override terms.
    3. Apply amendments sequentially in ascending order — each overrides the
       accumulated state so far.
    4. Compute latestEndorsementNumber as the highest amendmentNumber found.

    Args:
        extracts: Per-document ContractExtract results (any order).

    Returns:
        FinalContract representing the latest valid state of the contract.

    Raises:
        ValueError: If no main contract document is found in the extracts.
    """
    terms_extracts = [e for e in extracts if e.documentType == "terms"]
    main_extracts = [e for e in extracts if e.documentType == "main"]
    amendments = sorted(
        [e for e in extracts if e.documentType == "amendment"],
        key=lambda e: e.amendmentNumber or 0,
    )

    if not main_extracts:
        raise ValueError(
            "No main contract document found. Ensure at least one document is "
            "classified as documentType='main'."
        )

    # Seed from terms (lowest priority) if present, otherwise start empty
    if terms_extracts:
        result = FinalContract(
            **{k: v for k, v in terms_extracts[0].model_dump().items() if k not in _METADATA_FIELDS}
        )
    else:
        result = FinalContract()

    # Main contract overrides terms
    result = _apply_amendment(result, main_extracts[0])

    # Amendments override main contract, applied in ascending order
    for amendment in amendments:
        result = _apply_amendment(result, amendment)

    # Fallback: if no document explicitly set actionOnInsurancePeriodTermination, default to policy-termination
    if result.actionOnInsurancePeriodTermination is None:
        result.actionOnInsurancePeriodTermination = "policy-termination"

    # Fallback: if no document explicitly set installmentNumberPerInsurancePeriod, default to 1
    if result.installmentNumberPerInsurancePeriod is None:
        result.installmentNumberPerInsurancePeriod = 1

    return result
