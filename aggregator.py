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
        else:
            value = getattr(amendment, field, None)
            if value is not None:
                data[field] = value

    return FinalContract(**data)


def aggregate(extracts: list[ContractExtract]) -> FinalContract:
    """
    Merge all per-document extracts into the final contract state.

    Algorithm:
    1. Identify the main contract extract (documentType == "main").
    2. Collect amendments and sort them by amendmentNumber ascending so that
       later amendments correctly override earlier ones.
    3. Apply amendments sequentially to build the latest contract state.
    4. Compute latestEndorsementNumber as the string of the highest
       amendmentNumber found across all documents (or null if none).

    Args:
        extracts: Per-document ContractExtract results (any order).

    Returns:
        FinalContract representing the latest valid state of the contract.

    Raises:
        ValueError: If no main contract document is found in the extracts.
    """
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

    main = main_extracts[0]

    # Seed the result from the main contract, stripping document metadata
    result = FinalContract(
        **{k: v for k, v in main.model_dump().items() if k not in _METADATA_FIELDS}
    )

    # Apply amendments sequentially — each may override any subset of fields
    for amendment in amendments:
        result = _apply_amendment(result, amendment)

    # Compute latestEndorsementNumber from the highest amendment number present
    amendment_numbers = [e.amendmentNumber for e in amendments if e.amendmentNumber is not None]
    result.latestEndorsementNumber = str(max(amendment_numbers)) if amendment_numbers else None

    return result
