"""Pydantic v2 data models for the insurance contract extraction pipeline."""

from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict


class Premium(BaseModel):
    """Insurance premium details."""

    currency: str | None = None
    isCollection: bool | None = None


class ContractExtract(BaseModel):
    """
    Per-document LLM extraction result.

    Contains both document metadata (documentType, amendmentNumber) and CRM
    fields. Amendments will naturally have many null CRM fields — this is
    expected and correct.
    """
    model_config = ConfigDict(extra="forbid")

    # Document metadata (stripped before producing FinalContract)
    documentType: Literal["main", "amendment", "terms"]
    amendmentNumber: int | None = None

    # CRM fields
    contractNumber: str | None = None
    insurerName: str | None = None
    state: Literal["draft", "accepted", "cancelled"] | None = None
    assetType: Literal["other", "vehicle"] | None = None
    concludedAs: Literal["agent", "broker"] | None = None
    contractRegime: Literal["individual", "frame", "fleet", "coinsurance"] | None = None

    startAt: str | None = None
    endAt: str | None = None
    concludedAt: str | None = None

    installmentNumberPerInsurancePeriod: int | None = None
    insurancePeriodMonths: int | None = None

    premium: Premium | None = None

    actionOnInsurancePeriodTermination: Literal["auto-renewal", "policy-termination"] | None = None
    noticePeriod: str | None = None

    regPlate: str | None = None
    latestEndorsementNumber: str | None = None
    note: str | None = None
    annualPremiumTotal: int | None = None
    liabilityLimitHealth: int | None = None
    liabilityLimitProperty: int | None = None
    insuranceScope: str | None = None
    reasoning: dict[str, str] | None = None
    uncertainty: list[str] | None = None


class FinalContract(BaseModel):
    """
    Final merged contract state in CRM schema format.

    Contains no document metadata — only CRM fields representing the latest
    valid state of the contract after all amendments have been applied.
    """

    contractNumber: str | None = None
    insurerName: str | None = None
    state: Literal["draft", "accepted", "cancelled"] | None = None
    assetType: Literal["other", "vehicle"] | None = None
    concludedAs: Literal["agent", "broker"] | None = None
    contractRegime: Literal["individual", "frame", "fleet", "coinsurance"] | None = None

    startAt: str | None = None
    endAt: str | None = None
    concludedAt: str | None = None

    installmentNumberPerInsurancePeriod: int | None = None
    insurancePeriodMonths: int | None = None

    premium: Premium | None = None

    actionOnInsurancePeriodTermination: Literal["auto-renewal", "policy-termination"] | None = None
    noticePeriod: str | None = None

    regPlate: str | None = None
    latestEndorsementNumber: str | None = None
    note: str | None = None
    annualPremiumTotal: int | None = None
    liabilityLimitHealth: int | None = None
    liabilityLimitProperty: int | None = None
    insuranceScope: str | None = None


class PipelineState(TypedDict, total=False):
    """LangGraph-ready state carrier for the extraction pipeline."""

    documents: list[dict]           # input: [{filename, ocr_text}, ...]
    extracts: list["ContractExtract"]  # after extraction node
    aggregated: FinalContract       # after aggregation node
    refined: FinalContract          # after refinement node
    validated: FinalContract        # after validation node (final)


