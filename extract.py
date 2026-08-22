"""Layer 1 — turn free-text inspection notes into four condition flags.

Each flag means: this defect was outstanding at the time of this inspection. The
two cases that matter are a defect the note records as fixed and a defect the
note explicitly records as absent; both are false, and both are what the keyword
baseline at the bottom of this file gets wrong.

Every non-null evidence quote must appear verbatim in the note. A quote that does
not is a hallucination, and the extraction is retried once and then recorded as
failed rather than guessed at.

Writes: cache/extractions/, output/extraction_cost.txt
"""

import argparse
import json
import sys

import pandas as pd
from pydantic import BaseModel, ValidationError

import config
import llm


class FlagResult(BaseModel):
    present: bool
    evidence: str | None


class Extraction(BaseModel):
    cooling_degraded: FlagResult
    ventilation_obstructed: FlagResult
    oil_issue: FlagResult
    overdue_remedial: FlagResult


FLAG_DEFINITIONS = """\
cooling_degraded        Fans not running or failed, radiators fouled or blocked,
                        cooling underperforming.
ventilation_obstructed  Louvres blocked, vegetation encroachment, debris
                        restricting airflow.
oil_issue               Low level, seepage or leak, failed or marginal oil test.
overdue_remedial        Previously raised work not completed, work order open,
                        deferred repair."""

# The two rules the whole layer exists to enforce. They appear verbatim in the
# prompt; bump PROMPT_VERSION in config.py if either is edited.
RESOLVED_RULE = ("Resolved means false. If the note records that the defect was fixed, "
                 "the flag is false.")
NEGATED_RULE = ("Negated means false. If the note explicitly states the defect is absent, "
                "the flag is false.")

FEW_SHOT_EXAMPLES = """\
Example 1 — a straightforward positive.
Note: "Fan bank 2 not running on inspection; ambient noise from bank 1 only. Perimeter fencing intact. Signage legible."
Output: {"cooling_degraded": {"present": true, "evidence": "Fan bank 2 not running on inspection"}, "ventilation_obstructed": {"present": false, "evidence": null}, "oil_issue": {"present": false, "evidence": null}, "overdue_remedial": {"present": false, "evidence": null}}

Example 2 — the defect was recorded and then resolved.
Note: "Radiator fins heavily fouled with dust and pollen, airflow visibly reduced. Radiator bank cleaned since the last visit, airflow restored to normal. Drainage channel clear of silt."
Output: {"cooling_degraded": {"present": false, "evidence": null}, "ventilation_obstructed": {"present": false, "evidence": null}, "oil_issue": {"present": false, "evidence": null}, "overdue_remedial": {"present": false, "evidence": null}}

Example 3 — the defect is explicitly negated.
Note: "No evidence of oil seepage at the base or around the gaskets. Work order WO-3182 for the cooling controller remains open from last season. Warning notices present and in date."
Output: {"cooling_degraded": {"present": false, "evidence": null}, "ventilation_obstructed": {"present": false, "evidence": null}, "oil_issue": {"present": false, "evidence": null}, "overdue_remedial": {"present": true, "evidence": "Work order WO-3182 for the cooling controller remains open from last season"}}

Example 4 — nothing but distractors.
Note: "Earth strap connections checked and found tight. Cable trench covers seated correctly. Site lighting operational on manual test."
Output: {"cooling_degraded": {"present": false, "evidence": null}, "ventilation_obstructed": {"present": false, "evidence": null}, "oil_issue": {"present": false, "evidence": null}, "overdue_remedial": {"present": false, "evidence": null}}"""

SYSTEM_PROMPT = f"""\
You are reading maintenance inspection notes for substation transformers and \
recording which of four defects was outstanding at the time of the inspection.

The four flags:

{FLAG_DEFINITIONS}

Two rules govern every flag:

{RESOLVED_RULE}
{NEGATED_RULE}

Evidence: for every flag you set to true, quote the words from the note that \
support it, copied exactly as they appear. Do not paraphrase, correct or \
shorten beyond a contiguous span. For every flag you set to false, evidence is \
null.

Output a single JSON object with exactly the four keys above, each mapping to an \
object with keys "present" (boolean) and "evidence" (string or null). Output the \
JSON only. No markdown fences, no commentary.

{FEW_SHOT_EXAMPLES}"""


def build_cache_key(note_text, provider, model):
    return llm.cache_key(note_text + config.PROMPT_VERSION + provider + model)


def parse_and_validate(raw_text, note_text):
    """Parse the model's output and check every quote against the note.

    Raises rather than returning a partial result: a quote that is not in the
    note means the extraction cannot be trusted, and the caller retries once
    before recording a failure.
    """
    body, was_fenced = llm.strip_code_fences(raw_text)
    extraction = Extraction.model_validate_json(body)

    for flag in config.CONDITION_FLAGS:
        result = getattr(extraction, flag)
        if result.evidence is not None:
            assert result.evidence in note_text, \
                f"evidence for {flag} is not verbatim in the note: {result.evidence!r}"
        if result.present:
            assert result.evidence is not None, f"{flag} is present with no evidence quote"

    return extraction, was_fenced


def extract_one(inspection, provider, model, offline):
    """Extract one note, using the cache when it has the answer.

    On a parse or validation failure the identical prompt is sent once more. If
    that also fails, every flag is recorded false with status "failed" — the
    interface then says the condition data is unavailable rather than showing
    four clean falses as though the asset were sound.
    """
    key = build_cache_key(inspection.note_text, provider, model)
    cached = llm.read_cache(config.EXTRACTION_CACHE_DIR, key)
    if cached is not None:
        return cached, True

    if offline:
        raise RuntimeError(
            f"offline mode: no cached extraction for {inspection.inspection_id} "
            f"(key {key}, provider {provider}, model {model}). "
            "Run without --offline and with an API key to populate the cache."
        )

    input_tokens = 0
    output_tokens = 0
    fence_count = 0
    failure_reason = None

    for attempt in range(config.LLM_MAX_RETRIES + 1):
        raw_text, used_in, used_out = llm.call_llm(
            SYSTEM_PROMPT, inspection.note_text, model, provider, config.EXTRACTION_MAX_TOKENS)
        input_tokens += used_in
        output_tokens += used_out
        try:
            extraction, was_fenced = parse_and_validate(raw_text, inspection.note_text)
        except (ValidationError, AssertionError, json.JSONDecodeError) as error:
            failure_reason = f"{type(error).__name__}: {error}"
            continue
        fence_count += int(was_fenced)
        record = {
            "inspection_id": inspection.inspection_id,
            "provider": provider,
            "model": model,
            "prompt_version": config.PROMPT_VERSION,
            "extraction_status": "ok",
            "attempts": attempt + 1,
            "fenced_output": fence_count,
            "failure_reason": None,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "flags": {
                flag: getattr(extraction, flag).model_dump()
                for flag in config.CONDITION_FLAGS
            },
        }
        llm.write_cache(config.EXTRACTION_CACHE_DIR, key, record)
        return record, False

    record = {
        "inspection_id": inspection.inspection_id,
        "provider": provider,
        "model": model,
        "prompt_version": config.PROMPT_VERSION,
        "extraction_status": "failed",
        "attempts": config.LLM_MAX_RETRIES + 1,
        "fenced_output": fence_count,
        "failure_reason": failure_reason,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "flags": {flag: {"present": False, "evidence": None} for flag in config.CONDITION_FLAGS},
    }
    llm.write_cache(config.EXTRACTION_CACHE_DIR, key, record)
    return record, False


def load_extractions(inspections, provider=None, model=None):
    """Read every extraction back out of the cache as a flat table.

    The cache is the store: there is no separate extractions file to drift out of
    step with it. Missing keys raise, because a silently absent extraction would
    become an asset with no recorded defects.
    """
    provider = provider or config.LLM_PROVIDER
    model = model or extraction_model_for(provider)

    rows = []
    for inspection in inspections.itertuples():
        key = build_cache_key(inspection.note_text, provider, model)
        record = llm.read_cache(config.EXTRACTION_CACHE_DIR, key)
        if record is None:
            raise RuntimeError(
                f"no cached extraction for {inspection.inspection_id} "
                f"(key {key}, provider {provider}, model {model}). Run extract.py first."
            )
        row = {
            "inspection_id": inspection.inspection_id,
            "asset_id": inspection.asset_id,
            "inspection_date": inspection.inspection_date,
            "extraction_status": record["extraction_status"],
        }
        for flag in config.CONDITION_FLAGS:
            row[f"{flag}_present"] = record["flags"][flag]["present"]
            row[f"{flag}_evidence"] = record["flags"][flag]["evidence"]
        rows.append(row)

    table = pd.DataFrame(rows)
    assert len(table) == len(inspections), "lost a note between the inspections file and the cache"
    return table


def extraction_model_for(provider):
    if provider == "anthropic":
        return config.EXTRACTION_MODEL
    elif provider == "gemini":
        return config.GEMINI_EXTRACTION_MODEL
    else:
        raise ValueError(f"unknown provider {provider!r}")


# ---------------------------------------------------------------------------
# Keyword baseline
#
# Deliberately naive, and used only as an evaluation comparator. It has no
# notion of a defect being resolved or negated, which is the whole point: the
# extraction evaluation in validate.py reports what that costs.
# ---------------------------------------------------------------------------

BASELINE_TERMS = {
    "cooling_degraded": ["fan", "cooling", "radiator", "fins"],
    "ventilation_obstructed": ["louvre", "ventilation", "airflow", "vegetation", "grille"],
    "oil_issue": ["oil", "seepage", "leak", "conservator", "dielectric"],
    "overdue_remedial": ["work order", "outstanding", "deferred", "not been completed",
                         "carried forward"],
}


def keyword_baseline(note_text):
    """Case-insensitive substring match, no negation or resolution handling."""
    lowered = note_text.lower()
    flags = {}
    for flag, terms in BASELINE_TERMS.items():
        flags[flag] = any(term in lowered for term in terms)
    return flags


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", default=config.LLM_PROVIDER,
                        choices=["anthropic", "gemini"],
                        help="which API to call on a cache miss")
    parser.add_argument("--offline", action="store_true",
                        help="read the cache only; fail loudly on a miss rather than calling out")
    parser.add_argument("--limit", type=int, default=None,
                        help="extract only the first N notes, for a single live demonstration")
    args = parser.parse_args()

    model = extraction_model_for(args.provider)
    inspections = pd.read_csv(config.DATA_DIR / "inspections.csv")
    if args.limit is not None:
        inspections = inspections.head(args.limit)

    records = []
    cache_hits = 0
    for inspection in inspections.itertuples():
        record, from_cache = extract_one(inspection, args.provider, model, args.offline)
        records.append(record)
        cache_hits += int(from_cache)

    n_failed = sum(1 for r in records if r["extraction_status"] == "failed")
    n_retried = sum(1 for r in records if r["attempts"] > 1)
    n_fenced = sum(r["fenced_output"] for r in records)
    input_tokens = sum(r["input_tokens"] for r in records)
    output_tokens = sum(r["output_tokens"] for r in records)

    lines = [
        "Extraction run",
        f"provider: {args.provider}",
        f"model: {model}",
        f"prompt version: {config.PROMPT_VERSION}",
        f"notes processed: {len(records)}",
        f"served from cache: {cache_hits}",
        f"API calls made: {len(records) - cache_hits}",
        f"extractions retried: {n_retried}",
        f"extractions failed after retry: {n_failed}",
        f"outputs wrapped in markdown fences: {n_fenced}",
        f"input tokens this run: {input_tokens}",
        f"output tokens this run: {output_tokens}",
    ]
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (config.OUTPUT_DIR / "extraction_cost.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))

    if n_failed:
        print(f"\n{n_failed} extraction(s) failed after retry; those assets carry "
              f"extraction_status='failed' and the interface will say so.", file=sys.stderr)


if __name__ == "__main__":
    main()
