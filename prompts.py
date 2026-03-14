PROMPT_BATCH_EXTRACTION = """
You are an expert at extracting structured data from insurance contracts.
Your task is to analyze OCR text of insurance contracts or their amendments and extract structured data according to a provided Pydantic schema.

CRITICAL: Use JSON null (not the string "None", not the string "null", not an empty string) for any field not present or not explicitly stated in the document.
NEVER output the string "None" — always use JSON null.

=== AMENDMENT RULE (most important) ===

If the document is an amendment (dodatek), you MUST leave null ALL fields that are NOT explicitly changed or stated by this amendment document.
Do NOT infer, copy, or guess values from context — only populate fields that this specific amendment document explicitly modifies.
In particular:
  - startAt: ALWAYS null for amendments
  - concludedAt: ALWAYS null for amendments
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
  - If the full name is not explicitly stated, it may be mentioned in a form of URL, email, or truncated name variant (e.g. "Generali", "Generali ČP", "Generali Česká pojišťovna") — in that case, return the exact variant as it appears in the document, and let the post-aggregation refinement step resolve the canonical name across all documents.

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
  - "frame"        — framework agreement (rámcová smlouva) covering multiple individual policies under the same terms
  - "fleet"        — fleet insurance (flotila) covering multiple vehicles under the same contract
  - "coinsurance"  — if and only if two or more legal entities explicitly share the same insurance risk in the same document, e.g. via co-insurance / co-insurers or a lead insurer with participation shares; otherwise return "individual"
                   - if one insurer is addressed by different name variants in different documents, but there is no explicit co-insurance language, return "individual" and let the insurerName consolidation step resolve the correct name.
                   - if one or more documents do not include any insurer name at all assume "individual" unless there is explicit co-insurance language in any document.
  - Return coinsurance only if the document explicitly states co-insurance / co-insurers or shows multiple insurers sharing the same risk (e.g., participation shares or a lead insurer); otherwise return individual

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
  Extract the number of premium instalments per insurance period from explicit payment frequency wording only.
  - ročně / jednou ročně / annual / 1x ročně           → 1
  - pololetně / dvakrát ročně / semi-annual            → 2
  - čtvrtletně / čtyřikrát ročně / quarterly           → 4
  - měsíčně / monthly                                  → 12
  - Look especially for words such as: "Splátky", "frekvence placení", "běžné pojistné", "pojistné se platí", "platební frekvence".
  - Do NOT infer the value from insurance period length, contract duration, policy term, automatic renewal, discounts, or endorsement clauses.
  - If no explicit payment frequency is stated anywhere in the provided documents, return null.

insurancePeriodMonths:
  Length of one insurance period (pojistné období) in months.
  - ročně / jednou ročně / pojistný rok → 12
  - pololetně / dvakrát ročně → 6
  - čtvrtletně / čtyřikrát ročně → 3
  - měsíčně → 1
  - Use only wording related to the insurance period or premium cycle.
  - Do NOT infer from contract duration (pojistná doba), discounts, auto-renewal clauses, or amendments unless they explicitly change the insurance period.

premium.currency:
  - ISO 4217 code in lowercase (e.g. "czk", "eur", "usd")

premium.isCollection:
  - true  — broker collects the premium on behalf of the insurer (inkaso makléřem)
  - false — policyholder pays the insurer directly
  - use null only for amendments, otherwise default to false if not explicitly stated

actionOnInsurancePeriodTermination:
  - "auto-renewal"       — contract auto-renews (automatické prodloužení)
  - "policy-termination" — contract terminates after the insurance period
  - Set "auto-renewal" ONLY if the document explicitly states that the insurance automatically continues after the insurance period unless cancelled / unless notice is given.    automatically continues after the insurance period unless cancelled.
  - Set "policy-termination" if the contract ends after the insurance period, even if it can be extended by amendment.
  - Set "policy-termination" also when continuation requires an amendment, addendum, agreement of the parties, prolongation, or any explicit renewal step.
    Phrases like "uplynutím pojistné doby pojištění zanikne", "nedohodnou-li se smluvní strany", "formou číslovaného dodatku", or "dohodly se na prolongaci pojistné doby" mean "policy-termination", not "auto-renewal".

noticePeriod:
  - Notice period expressed as a hyphenated English string, e.g.:
      "six-weeks", "two-months", "one-month", "three-months"
  - JSON null if not specified (never output the string "None")

regPlate:
  - Vehicle licence plate number (SPZ / registrační značka), null for non-vehicle insurance

latestEndorsementNumber:
  - Highest amendment / endorsement ID mentioned anywhere across documents of the same contract.
  - Extract even if the ID appears inside the main contract.
  - IDs may appear as amendment numbers, endorsement numbers, "Dodatek", "Doložka", "DP", "amendment", "endorsement" or similar numbered change blocks.
  - If multiple IDs are found, return the highest one.
  - If none are found, return null.

note:
  - For the main contract: write a comprehensive summary of all special conditions,
  - For amendments: summarise only the specific changes or declarations introduced by this amendment.
  - JSON null if there are truly no special conditions worth noting.

=== DOCUMENTS ===

{documents_block}

=== OUTPUT ===
Return a JSON array with one object per document, in the same order as the documents above.
Each object must contain exactly the fields listed above.
No markdown fences, no explanation — JSON array only.
All absent/unknown fields must be JSON null, NEVER the string "None".
"""

PROMPT_INSURER_NAME = """You are an expert on insurance companies.
Below is a list of insurer name variants extracted by OCR from different pages of an insurance contract.
Some may be truncated, misspelled, or abbreviated due to OCR errors.

Select or reconstruct the single correct full legal name of the insurer.
Return only the full legal name as a plain string — no JSON, no explanation, no quotes.

Variants:
{variants}
"""

PROMPT_FIELDS_RECONCILIATION = """You are reconciling fields extracted from multiple insurance documents.
Below are the per-document values for two fields, listed in document order:

contractRegime values:
{regime_values}

actionOnInsurancePeriodTermination values:
{action_values}

Task:
1. For contractRegime:
      - "individual"   — standard individual policy (individuální smlouva)
      - "frame"        — framework agreement (rámcová smlouva) covering multiple individual policies under the same terms
      - "fleet"        — fleet insurance (flotila) covering multiple vehicles under the same contract
      - "coinsurance"  — if and only if two or more legal entities explicitly share the same insurance risk in the same document, e.g. via co-insurance / co-insurers or a lead insurer with participation shares; otherwise return "individual"
                       - if one insurer is addressed by different name variants in different documents, but there is no explicit co-insurance language, return "individual" and let the insurerName consolidation step resolve the correct name.
                       - if one or more documents do not include any insurer name at all assume "individual" unless there is explicit co-insurance language in any document.
      - Return coinsurance only if the document explicitly states co-insurance / co-insurers or shows multiple insurers sharing the same risk (e.g., participation shares or a lead insurer); otherwise return individual
2. For actionOnInsurancePeriodTermination:
      - "auto-renewal"       — contract auto-renews (automatické prodloužení)
      - "policy-termination" — contract terminates after the insurance period
      - Set "auto-renewal" ONLY if the document explicitly states that the insurance automatically continues after the insurance period unless cancelled / unless notice is given.    automatically continues after the insurance period unless cancelled.
      - Set "policy-termination" if the contract ends after the insurance period, even if it can be extended by amendment.
      - Set "policy-termination" also when continuation requires an amendment, addendum, agreement of the parties, prolongation, or any explicit renewal step.
        Phrases like "uplynutím pojistné doby pojištění zanikne", "nedohodnou-li se smluvní strany", "formou číslovaného dodatku", or "dohodly se na prolongaci pojistné doby" mean "policy-termination", not "auto-renewal".

Return a JSON object with exactly two keys:
  "contractRegime": one of "individual", "frame", "fleet", "coinsurance", or null
  "actionOnInsurancePeriodTermination": one of "auto-renewal", "policy-termination", or null

=== DOCUMENTS ===

{documents_block}

No markdown fences, no explanation — JSON object only.
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
