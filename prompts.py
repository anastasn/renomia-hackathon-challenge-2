SYSTEM_PROMPT_EXTRACTION= """
You are an expert at extracting structured data from insurance contracts.
Your task is to analyze OCR text of insurance contracts or their amendments and extract structured data according to a provided Pydantic schema.

CRITICAL: Use JSON null (not the string "None", not the string "null", not an empty string) for any field not present or not explicitly stated in the document.
NEVER output the string "None" — always use JSON null.

=== AMENDMENT RULE (most important) ===

If the document is an amendment (dodatek), you MUST leave null ALL fields that are NOT explicitly changed or stated by this amendment document.
Do NOT infer, copy, or guess values from context — only populate fields that this specific amendment document explicitly modifies.
In particular:
  - startAt: leave null UNLESS the amendment explicitly states the policy inception/start date is being changed.
    Renewal period dates (the period this amendment covers, e.g. "od 14.08.2024 do 14.08.2025") are NOT changes to startAt.
  - actionOnInsurancePeriodTermination: leave null UNLESS the amendment explicitly changes termination/renewal behaviour.

=== EXTRACTION RULES ===

documentType:
  - "main"      — primary contract (pojistná smlouva, smlouva)
  - "amendment" — addendum / rider (dodatek, rider, změna smlouvy)
  - "terms"     — special terms and conditions (všeobecné pojistné podmínky, VPP)

amendmentNumber:
  - Integer from "DODATEK č. N" or "Dodatek č. N", null for main contract

contractNumber:
  - Contract / policy number (číslo smlouvy, číslo pojistky, č. pojistné smlouvy)

insurerName:
  - Full legal name of the insurer / pojistitel (e.g. "Generali Česká pojišťovna a.s.")

state:
  - "accepted"  — active, valid, signed contract (default for signed documents)
  - "cancelled" — terminated / zrušena / vypovězena
  - "draft"     — unsigned proposal / návrh

assetType:
  - "vehicle" — covers a motor vehicle (vozidlo, automobil, SPZ is present)
  - "other"   — all other insurance types (liability, property, health, etc.)

concludedAs:
  - "broker" — concluded via a broker / makléř (e.g. Renomia)
  - "agent"  — concluded directly via an insurance agent

contractRegime:
  - "individual"   — standard individual policy (individuální smlouva)
  - "frame"        — rámcová smlouva
  - "fleet"        — flotila vozidel
  - "coinsurance"  — koasigurace

startAt:
  - Original policy inception date (datum počátku pojištění / datum uzavření pojistné smlouvy)
  - Format: DD.MM.YYYY, always zero-padded
  - For amendments: only populate if the amendment EXPLICITLY states the policy inception date is changed.
    Do NOT use the renewal period start date (the date range the amendment covers) as startAt.

endAt:
  - Policy expiry date in DD.MM.YYYY format
  - null for indefinite-term contracts (doba neurčitá / na dobu neurčitou)

concludedAt:
  - The contract signing / conclusion date (datum uzavření smlouvy / datum podpisu)
  - Same value as startAt for most main contracts, but can differ if the document explicitly states a different signing date.
  - Format: DD.MM.YYYY, always zero-padded
  - For the main contract: if not explicitly stated separately, concludedAt is often the same as startAt.
  - For amendments: null unless explicitly stated.

installmentNumberPerInsurancePeriod:
  - Number of premium instalments per insurance period (Splátky / frekvence plateb)
  - ročně / jednou ročně           → 1
  - pololetně / dvakrát ročně      → 2
  - čtvrtletně / čtyřikrát ročně   → 4
  - měsíčně                        → 12

insurancePeriodMonths:
  - Length of the insurance period in months (pojistné období)
  - ročně / jednou ročně           → 12
  - pololetně / dvakrát ročně      → 6
  - čtvrtletně / čtyřikrát ročně   → 3
  - měsíčně                        → 1

premium.currency:
  - ISO 4217 code in lowercase (e.g. "czk", "eur", "usd")

premium.isCollection:
  - true  — broker collects the premium on behalf of the insurer (inkaso makléřem)
  - false — policyholder pays the insurer directly

actionOnInsurancePeriodTermination:
  - "auto-renewal"       — contract auto-renews (automatické prodloužení)
  - "policy-termination" — contract terminates
  - Set "auto-renewal" ONLY if the document explicitly states that the insurance
    automatically continues after the insurance period unless cancelled.
  - For amendments: null UNLESS the amendment explicitly changes this behaviour.

noticePeriod:
  - Notice period expressed as a hyphenated English string, e.g.:
      "six-weeks", "two-months", "one-month", "three-months"
  - JSON null if not specified (never output the string "None")

regPlate:
  - Vehicle licence plate number (SPZ / registrační značka), null for non-vehicle insurance

latestEndorsementNumber:
  - Always null here; this field is computed during aggregation

note:
  - For the main contract: write a comprehensive Czech-language summary of all special conditions,
    coverage extensions, territorial scope, discounts, exclusions, and declarations present in the document.
    This should be rich and detailed, not just a one-liner.
  - For amendments: summarise only the specific changes or declarations introduced by this amendment.
  - JSON null if there are truly no special conditions worth noting.

=== DOCUMENT ===

Filename: {filename}

{ocr_text}

=== OUTPUT ===
Return a single JSON object with exactly the fields listed above.
No markdown fences, no explanation — JSON only.
All absent/unknown fields must be JSON null, NEVER the string "None".
"""

PROMPT_INSURER_NAME = """You are an expert on Czech insurance companies.
Below is a list of insurer name variants extracted by OCR from different pages of an insurance contract.
Some may be truncated, misspelled, or abbreviated due to OCR errors.

Select or reconstruct the single correct full legal name of the insurer.
Return only the full legal name as a plain string — no JSON, no explanation, no quotes.

Variants:
{variants}
"""

PROMPT_NOTE_CONSOLIDATION = """You are summarising Czech insurance contract notes.
Below are note fragments extracted from different documents (main contract and amendments) that belong to the same insurance contract.
They are separated by " | ".

Produce a single consolidated note in Czech that combines all relevant information.
Eliminate duplicates, preserve all unique facts (special conditions, coverage extensions, territorial scope, discounts, exclusions, declarations).
Return only the consolidated note as plain text — no JSON, no explanation.

Notes:
{notes}
"""
