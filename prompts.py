PROMPT_BATCH_EXTRACTION = """
You are an expert at extracting structured data from Czech insurance contracts (pojistné smlouvy) and their amendments (dodatky).

Analyze OCR text and extract fields per the schema below. Return a JSON array — one object per document, in input order. No markdown fences, no preamble.

══════════════════════════════════════════════
CARDINAL RULES
══════════════════════════════════════════════

NULL:
- Use JSON null for any field not explicitly stated. NEVER output the string "None", "null", or "".
- "Not stated" and "implied" are both null.

AMENDMENTS — override only:
- Only populate fields the amendment EXPLICITLY changes.
- startAt: ALWAYS null for amendments (never use the amendment's effective date as startAt).
- concludedAt: ALWAYS null for amendments.
- actionOnInsurancePeriodTermination: null UNLESS the amendment explicitly changes it.
- premium.isCollection: null for amendments unless explicitly changed.

══════════════════════════════════════════════
FIELD EXTRACTION RULES
══════════════════════════════════════════════

documentType:
  "main"      — pojistná smlouva / smlouva
  "amendment" — dodatek / rider / změna smlouvy
  "terms"     — VPP / všeobecné pojistné podmínky

amendmentNumber: integer from "DODATEK č. N" / "Dodatek č. N"; null for main

contractNumber: číslo smlouvy / číslo pojistky

insurerName:
  Full legal name of the insurer (pojistitel).
  It might be found in various places: "pojistitel", "pojišťovna", "smluvní strana", "uzavírající strana", "pojistná společnost", or even just a URL.
  Specifically for URL you are allowed to infer the insurer name from the domain (e.g. "generali.cz" → "Generali Česká pojišťovna a.s.").

state:
  "accepted"  — signed/active (default for signed documents)
  "cancelled" — zrušena / vypovězena
  "draft"     — unsigned / návrh

assetType:
  "vehicle" — motor vehicle (SPZ / vozidlo / automobil present)
  "other"   — all other types

concludedAs:
  "broker" — via makléř (e.g. Renomia)
  "agent"  — via insurance agent directly

contractRegime:
  "individual"   — standard individual policy
  "frame"        — rámcová smlouva (framework covering multiple policies)
  "fleet"        — flotila (multiple vehicles under one contract)
  "coinsurance"  — ONLY when two or more legal entities explicitly share the same risk
                   (co-insurers, participation shares, lead insurer language).
                   Name variants of one insurer ≠ coinsurance.

startAt (DD.MM.YYYY, zero-padded):
  Original policy inception date (počátek pojištění / datum počátku).
  Main contract: populate if explicitly stated.
  Amendment: ALWAYS null — do NOT use the amendment's effective date.

endAt (DD.MM.YYYY or null):
  Policy expiry date. null for indefinite term (doba neurčitá / na dobu neurčitou).

concludedAt (DD.MM.YYYY, zero-padded):
  Signing / conclusion date (datum uzavření / datum podpisu).
  Main contract: if not separately stated, same as startAt.
  Amendment: ALWAYS null.

installmentNumberPerInsurancePeriod:
  Number of premium payments per insurance period.
  Map ONLY explicit payment frequency words:
    ročně / jednou ročně / 1× ročně    → 1
    pololetně / dvakrát ročně          → 2
    čtvrtletně / čtyřikrát ročně       → 4
    měsíčně                            → 12
    null if no explicit frequency stated.
  Keywords: "Splátky", "frekvence placení", "pojistné se platí", "platební frekvence".

insurancePeriodMonths:
  Length of one pojistné období in months.
    pojistný rok / ročně → 12
    pololetně            → 6
    čtvrtletně           → 3
    měsíčně              → 1
    null if no explicit frequency stated.
  Use only pojistné období wording. Never infer from contract duration or discounts.

premium.currency: ISO 4217 lowercase (czk, eur, usd…)

premium.isCollection:
  true  — broker collects on behalf of insurer (inkaso makléřem)
  false — policyholder pays insurer directly
  null  — amendments only (unless explicitly changed)
  Default false for main contracts if not stated.

actionOnInsurancePeriodTermination:
  "auto-renewal"       — contract automatically continues unless cancelled
                         (must be EXPLICIT: "automaticky se prodlužuje", "automatické prodloužení")
  "policy-termination" — contract ends after the period, even if it can be extended.
  
  CRITICAL distinction:
  - "policy-termination" applies when: continuation requires an amendment, addendum,
    agreement of the parties, prolongation, or any explicit step.
  - Phrases like "uplynutím pojistné doby pojištění zanikne", "nedohodnou-li se strany",
    "formou číslovaného dodatku", "dohodly se na prolongaci" → "policy-termination".
  - Only set "auto-renewal" if the document says the contract renews AUTOMATICALLY
    without any action required.

noticePeriod:
  Hyphenated English string, e.g. "six-weeks", "two-months", "one-month", "three-months".
  null if not explicitly stated.

regPlate: SPZ / registrační značka; null for non-vehicle.

latestEndorsementNumber:
  Highest amendment/endorsement number found ANYWHERE across all documents for this contract.
  Check for: "Dodatek", "Doložka", "DP", endorsement numbers, amendment IDs.
  Return as string (e.g. "3"). null if none found.

note:
    Return one very short sentence for each unusual fact in the documents.
    The language of the note must be the same as the document (usually Czech).

    Examples of unusual facts:
      - country/region excluded or restricted
      - insured companies changed
      - retroactive coverage
      - sanctions-related restrictions
      - any other clearly unusual contractual condition

    Rules:
      - Each fact must be its own sentence.
      - Do NOT combine multiple facts in one sentence.
      - Keep each sentence very short (max ~8 words).
      - Ignore normal things (premium updates, payment schedules, standard amendments).

    If nothing unusual appears → return null.

uncertainty:
  List of field names you are not fully confident about.
  Include a field if ANY of the following apply:
    - No clear verbatim evidence was found (value was inferred or implied)
    - The document is ambiguous and another interpretation is plausible
    - You returned null but suspect a value might exist
    - The field requires interpreting a rule (not just finding text), and the
      document wording is not completely unambiguous:
        * actionOnInsurancePeriodTermination — include unless the document
          explicitly uses "automaticky se prodlužuje" or explicit termination language
        * contractRegime — include if you cannot point to explicit flotila/rámcová/
          coinsurance language (i.e., you defaulted to "individual")
        * installmentNumberPerInsurancePeriod / insurancePeriodMonths — include if
          no explicit frequency keyword (ročně/pololetně/čtvrtletně/měsíčně) was found
  Leave empty list (or null) if all extracted values are well-supported by explicit text.
  Do NOT include fields that are always null for this document type (e.g. startAt for amendments).

══════════════════════════════════════════════
AGGREGATION (after per-document extraction)
══════════════════════════════════════════════

When the input contains a main contract + one or more amendments:
- Start with main contract values as the base.
- Apply each amendment in chronological order, overwriting ONLY the fields
  that amendment explicitly changes.
- latestEndorsementNumber: maximum amendmentNumber across ALL documents (as string).
- The final output object represents the current merged state of the contract.

══════════════════════════════════════════════
DOCUMENTS
══════════════════════════════════════════════

{documents_block}

══════════════════════════════════════════════
OUTPUT FORMAT
══════════════════════════════════════════════

JSON array, one object per document, same order as input.
Each object contains exactly the fields above plus "reasoning".
No markdown, no explanation outside the array.
All absent/unknown fields: JSON null. NEVER the string "None".
"""

PROMPT_INSURER_NAME = """You are an expert on Czech and European insurance companies.

Below are insurer name variants extracted by OCR from the same insurance contract.
They may contain truncations, abbreviations, OCR artifacts, or encoding errors.

Your task: identify the single correct full legal name of the insurer.

Rules:
- Return the full legal name exactly as it would appear in the Czech commercial register
  (e.g. "Generali Česká pojišťovna a.s.", "Allianz pojišťovna, a.s.", "Kooperativa pojišťovna, a.s., Vienna Insurance Group")
- If multiple variants point to the same company, resolve to the canonical legal name
- If a URL or domain is present (e.g. "generali.cz"), use it to identify the company
- If variants are genuinely ambiguous, prefer the longest / most complete variant

Return only the full legal name as a plain string — no JSON, no explanation, no quotes.

Variants:
{variants}
"""


PROMPT_FIELDS_RECONCILIATION = """You are reconciling two insurance contract fields that may differ across documents.

Below are the per-document extracted values for:

contractRegime:
{regime_values}

actionOnInsurancePeriodTermination:
{action_values}

══════════════════════════════════════════════
RECONCILIATION RULES
══════════════════════════════════════════════

contractRegime — pick the most specific value supported by any document:
  "individual"   — standard individual policy (default; use when no other regime is explicit)
  "frame"        — rámcová smlouva explicitly stated
  "fleet"        — flotila / multiple vehicles explicitly stated
  "coinsurance"  — ONLY when two or more legal entities explicitly share the same risk
                   (co-insurers named, participation shares stated, lead insurer language present)
  
  DO NOT use "coinsurance" for:
  - Different name variants of the same insurer across documents
  - Documents that simply omit the insurer name
  When in doubt → "individual"

actionOnInsurancePeriodTermination — latest explicit statement wins:
  "auto-renewal"       — ONLY if a document explicitly states the contract automatically
                         continues unless notice is given / cancelled
                         (e.g. "automaticky se prodlužuje", "automatické prodloužení")
  "policy-termination" — contract ends after the period, OR continuation requires any
                         explicit step (amendment, agreement, prolongation by any party)
  
  CRITICAL: these phrases mean "policy-termination", NOT "auto-renewal":
  - "uplynutím pojistné doby pojištění zanikne"
  - "nedohodnou-li se smluvní strany"
  - "formou číslovaného dodatku"
  - "dohodly se na prolongaci pojistné doby"
  - any language requiring mutual agreement or a new document to continue
  
  If values conflict across documents, "policy-termination" takes precedence
  unless a later document explicitly reinstates auto-renewal.
  null only if no document mentions termination behaviour at all.

══════════════════════════════════════════════
DOCUMENTS
══════════════════════════════════════════════

{documents_block}

══════════════════════════════════════════════
OUTPUT
══════════════════════════════════════════════

Return a JSON object with exactly two keys:
  "contractRegime": one of "individual", "frame", "fleet", "coinsurance", or null
  "actionOnInsurancePeriodTermination": one of "auto-renewal", "policy-termination", or null

No markdown fences, no explanation — JSON object only.
"""


PROMPT_VALIDATION = """You are a precise field extractor for Czech insurance contracts.

The primary extractor flagged the following fields as uncertain:
{uncertain_fields}

Your task: extract ONLY these fields from the original documents below.
Focus entirely on finding direct, explicit evidence for each field.
Do not infer — if a field cannot be determined from explicit text, return null.

{field_reference}

=== DOCUMENTS ===

{documents_block}

=== OUTPUT ===
Return a JSON object containing exactly the uncertain fields listed above.
Each key is a field name; each value is the extracted value or null.
No markdown fences, no explanation — JSON object only.
"""


PROMPT_NOTE_CONSOLIDATION = """You are consolidating insurance contract notes written in Czech.

The fragments below were extracted from different documents (main contract and amendments)
belonging to the same contract. They are separated by " | ".

Your task: produce one consolidated note in Czech that:
- Retains ALL unique facts: special conditions, coverage scope and extensions,
  territorial limits, deductibles, exclusions, discounts, declarations, named insured parties
- Removes exact duplicates, but keeps near-duplicates if the wording difference is meaningful
- Where an amendment changes a condition from the main contract, state the amended version
  and note it supersedes the original (e.g. "dle Dodatku č. 2: ...")
- Uses clear, concise Czech — no bullet points, write in flowing sentences or short paragraphs

Return only the consolidated note as plain text in target language — no JSON, no explanation, no labels.

Notes:
{notes}
"""
