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
  "accepted"  — issued/active policy document
  "cancelled" — explicitly cancelled/terminated/voided, or an unpaid pay-to-conclude policy that did not clearly come into effect
  "draft"     — mere proposal/offer only

  Rules for state:
    - Return "draft" ONLY for a pure proposal/offer, not for a full policy document.
    - Return "cancelled" if the document explicitly says the insurance/contract was cancelled, terminated, voided, or did not arise.
    - For Direct-style pay-to-conclude documents ("Pojistná smlouva bude uzavřena zaplacením pojistného"), do NOT automatically return accepted.
    - If the document is a full policy document but effectiveness depends on payment, use this tie-breaker:
    - return "accepted" if the document looks like a standard issued policy pack arranged in advance(e.g. "Datum sjednání pojištění" is earlier than "Datum požadovaného počátku pojištění");
    - return "cancelled" if the document still depends on payment and there is no clear evidence the policy actually came into effect, especially when the requested start is the same day as arrangement or the text frames the policy as still pending until payment.
    - If there is no explicit evidence of actual effectiveness, prefer "cancelled" over "accepted" for pay-to-conclude wording that keeps contract formation conditional.
    - If the contract duration is stated as "na dobu neurčitou", classify the state as "accepted".

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

annualPremiumTotal:
  Total annual premium across all coverages, as a number (no currency symbol).
  If only a periodic premium is stated, annualise it (e.g. monthly × 12).
  null if not stated.

liabilityLimitHealth:
  Liability coverage limit for bodily injury / health (újma na zdraví / újma na životě).
  Number only (no currency). null if not stated or not applicable.

liabilityLimitProperty:
  Liability coverage limit for property damage (věcná škoda / škoda na věci).
  Number only (no currency). null if not stated or not applicable.

insuranceScope:
  Short description of what is insured, e.g. "Povinné ručení a havarijní pojištění",
  "Pojištění odpovědnosti", "Pojištění majetku". One short phrase, Czech language.
  null if not clear from the document.

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
