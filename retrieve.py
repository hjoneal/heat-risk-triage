"""Layer 3 — find the procedures that apply to an asset, then write the brief.

The query is built deterministically from the asset's positive feature
contributions, so the same explanation that appears on screen is the thing that
selected the documents. Nothing is embedded and nothing is learned; BM25 over 25
documents is enough at this corpus size and can be read off in full.

The brief may cite only the documents that were actually retrieved. That is
checked in tests/test_citations.py across every brief.

Writes: output/briefs_*.json, output/bm25_scores.txt
"""

import argparse
import json
import re

from rank_bm25 import BM25Okapi

import config
import llm

TOKEN = re.compile(config.TOKEN_PATTERN)
DOC_ID_IN_TEXT = re.compile(config.DOC_ID_PATTERN)


def tokenise(text):
    """Lowercase, then split on the token pattern.

    The pattern keeps hyphenated identifiers whole, so `sop-014` and `de-rating`
    survive as single tokens. `\\w+` would split both and lose the match.
    """
    return TOKEN.findall(text.lower())


def load_corpus():
    """Read the 25 procedure documents, front matter and body."""
    documents = []
    for path in sorted(config.PROCEDURES_DIR.glob("*.md")):
        text = path.read_text()
        _, header, body = text.split("---\n", 2)
        fields = {}
        for line in header.strip().splitlines():
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
        documents.append({
            "doc_id": fields["doc_id"],
            "title": fields["title"],
            "category": fields["category"],
            # Parsed rather than kept as the raw "[ONAN, ONAF, OFAF]" string,
            # because it is now a filter and not just a field to display.
            "applies_to": [t.strip() for t in fields["applies_to"].strip("[]").split(",") if t.strip()],
            "body": body.strip(),
        })
    assert len(documents) == config.N_PROCEDURES, \
        f"expected {config.N_PROCEDURES} procedures, found {len(documents)}"
    return documents


def build_index(documents):
    """Index title and body together. The document is the retrieval unit — no
    chunking, because 300 words is already the size a supervisor reads."""
    corpus = [tokenise(d["title"] + " " + d["body"]) for d in documents]
    return BM25Okapi(corpus, k1=config.BM25_K1, b=config.BM25_B)


def build_query(contributions, peak_temp_c):
    """Assemble query terms from the contributions that pushed risk up.

    Only positive contributions: a feature that lowered this asset's risk is not
    a reason to send a crew, and including it would retrieve procedures for a
    problem the asset does not have.

    The asset's cooling type used to be appended here. It is a filter now, applied
    in `retrieve` against each document's `applies_to`, because as a query term it
    could not do the job: `applies_to` is not indexed, so it only ever matched
    cooling types written into a document's prose. See DECISIONS.md D-037.
    """
    terms = []
    for contribution in contributions:
        if contribution["contribution"] > 0:
            terms += config.QUERY_TERMS[contribution["feature"]]
    if peak_temp_c > config.HIGH_AMBIENT_QUERY_C:
        terms += config.HIGH_AMBIENT_QUERY_TERMS
    return terms


def retrieve(index, documents, query_terms, cooling_type=None):
    """Top-k applicable documents above the score floor.

    Applicability is checked before relevance, not after. A procedure for a
    forced-air cooling system does not become relevant to a naturally-cooled unit
    by scoring well against its query — the unit has no fans — so an inapplicable
    document is removed from the candidate set rather than ranked and hoped
    against. `cooling_type` of None disables the filter, which is what the corpus
    tests use when they are checking the index rather than an asset.

    Below the floor there is no useful match, and returning the least-bad of a
    bad set would put a procedure in front of a crew that does not apply.
    """
    scores = index.get_scores([term.lower() for term in query_terms])
    candidates = range(len(documents))
    if cooling_type is not None and config.FILTER_BY_COOLING_TYPE:
        candidates = [i for i in candidates if cooling_type in documents[i]["applies_to"]]
        assert candidates, f"no procedure applies to cooling type {cooling_type}"
    ranked = sorted(candidates, key=lambda i: -scores[i])[:config.BM25_TOP_K]
    top_score = float(scores[ranked[0]]) if ranked else 0.0

    # Per term, because a BM25 total scales with query length and a short query
    # is not a bad one. See config.BM25_FLOOR_PER_TERM.
    per_term = top_score / len(query_terms) if query_terms else 0.0
    if per_term < config.BM25_FLOOR_PER_TERM:
        return [], "no_match", top_score

    hits = [
        {"doc_id": documents[i]["doc_id"],
         "title": documents[i]["title"],
         "score": round(float(scores[i]), 4)}
        for i in ranked
    ]
    return hits, "ok", top_score


BRIEF_SYSTEM_PROMPT = """\
You are writing a short action brief for the crew supervisor of an electricity \
distribution utility, to be read alongside a ranked list of substation \
transformers due for attention before a forecast heat event.

Constraints:
- Three or four sentences. No headings, no bullets, no salutation.
- Lead with the action the crew should take.
- Where the inspection notes record a specific defect, name the defect. "Attend \
to the recorded condition" is not usable by a crew; "the oil sight glass reads \
below minimum" is.
- Use only the supplied asset facts, the supplied inspection findings and the \
supplied procedure documents. Do not introduce any procedure, threshold or \
figure that is not in them.
- The inspection findings are observations about this asset, not procedure \
content: they record what was found, not what to do about it. Do not attach a \
doc_id to one and do not present one as the source of an instruction.
- Cite the doc_id of every document you draw on.
- Refer only to the doc_ids supplied below. A supplied document may itself \
mention another procedure by id; do not pass that id on as though it were \
attached, and do not attribute a supplied document's instruction to the wrong id.
- If the supplied documents do not cover the situation, say so plainly rather \
than improvising guidance.

Return a single JSON object with keys "brief" (string) and "cited_doc_ids" \
(array of strings). Output the JSON only. No markdown fences, no commentary."""


def build_condition_block(asset):
    """What the last inspections actually recorded, in the inspector's words.

    This was missing from the prompt entirely, and it is the most concrete thing
    the system holds. The extraction layer reads 1,800 free-text notes to find
    exactly these observations, the asset page shows them beside the brief, and
    the brief was written without them — so a unit whose sight glass reads below
    minimum and whose ventilation grille is packed with nesting material got a
    brief about the general management of ageing assets. Measured before the
    change: 150 of the 160 briefed assets carried evidence the model never saw.
    See DECISIONS.md D-049.

    Quotes are verbatim and already checked against the source note by
    `extract.parse_and_validate`, so nothing here is a paraphrase.
    """
    if asset["extraction_status"] == "failed":
        # Distinct from having no defects. A failed read is an absence of
        # knowledge, and a brief that treats it as a clean asset is wrong.
        return ("Recorded condition: the inspection notes for this asset could not be "
                "read reliably, so no condition findings are available. Do not treat "
                "this as an asset in good condition.")
    if not asset["evidence"]:
        return "Recorded condition: no outstanding defects at the last inspections."
    # The inspection id is deliberately absent. Supplied in v3, it was written
    # into 13 of 160 briefs as though it were a procedure reference — "clear the
    # radiator fins, as specified in INS-340-2" — which inverts what an
    # inspection is: it recorded the defect, it did not specify the remedy. The
    # v3 system prompt forbade this in as many words and the model did it anyway,
    # so the token is withheld rather than discouraged. Same reasoning as the
    # applicability filter in D-037: remove it from the candidate set rather than
    # rank it and hope. The date carries what the id was useful for — that a
    # finding was recorded at a particular visit — and the asset page shows the
    # ids beside the quotes regardless.
    lines = [
        f"- {e['flag'].replace('_', ' ')}, recorded {e['date']} — \"{e['text']}\""
        for e in asset["evidence"]
    ]
    return "Recorded condition, from the last inspections:\n" + "\n".join(lines)


def build_brief_prompt(asset, contributions, retrieved, documents_by_id):
    """Asset facts, recorded condition, why it ranked, then the documents in full."""
    # Every positive contribution, not the leading few. Truncating this list was
    # cutting a condition flag out of 149 of the 160 briefs — the flags sit below
    # cooling type and the maintenance interval in contribution order, so the
    # actionable findings were reliably the ones dropped. Ordered by effect, so
    # the ordering carries the priority instead of a cut-off doing it.
    # `reading` rather than `value`, for the same reason the asset page shows it:
    # "value 0" for a cooling type and "value 20.34" for an interaction are not
    # readings of anything, and a brief written from them cannot state a figure a
    # supervisor could check against the unit.
    reasons = [
        f"- {c['label']}: {c['reading']}"
        for c in contributions if c["contribution"] > 0
    ]

    document_blocks = []
    for hit in retrieved:
        document = documents_by_id[hit["doc_id"]]
        document_blocks.append(
            f"[{document['doc_id']}] {document['title']}\n\n{document['body']}")

    return (
        f"Asset: {asset['name']} ({asset['asset_id']})\n"
        f"Cooling type: {asset['cooling_type']}\n"
        f"Age: {int(_age_of(contributions))} years\n"
        f"Customers served: {asset['customers_served']:,}\n"
        f"Criticality: {asset['criticality']} of 5\n\n"
        + build_condition_block(asset) + "\n\n"
        f"Why this asset ranked where it did, in descending order of effect:\n"
        + "\n".join(reasons) + "\n\n"
        f"Procedure documents:\n\n" + "\n\n---\n\n".join(document_blocks)
    )


def _age_of(contributions):
    for contribution in contributions:
        if contribution["feature"] == "age_years":
            return contribution["value"]
    raise KeyError("age_years missing from contributions")


def generate_brief(asset, contributions, retrieved, documents_by_id,
                   scenario_id, model, offline):
    """One brief, cached by asset, scenario and the documents it was given."""
    if not retrieved:
        # No call is made, so the zeros are real rather than unknown.
        return {"brief": config.NO_MATCH_BRIEF, "cited_doc_ids": [], "status": "no_match",
                "input_tokens": 0, "output_tokens": 0}

    # Keyed on the prompt itself, as extraction is keyed on the note text. The
    # key used to be asset + scenario + doc ids + version, which named the
    # *inputs to selection* rather than the content sent — so a change to what
    # the prompt says about an asset, its recorded condition included, produced
    # the same key and silently served a brief written without it. Content
    # addressing removes that failure mode rather than relying on remembering to
    # bump a version. The version stays in the key because the system prompt is
    # not part of `prompt`, and the model because two models must not share an
    # answer. See DECISIONS.md D-049.
    prompt = build_brief_prompt(asset, contributions, retrieved, documents_by_id)
    key = llm.cache_key(prompt + config.BRIEF_PROMPT_VERSION + model)
    cached = llm.read_cache(config.BRIEF_CACHE_DIR, key)
    if cached is not None:
        return cached

    if offline:
        raise RuntimeError(
            f"offline mode: no cached brief for {asset['asset_id']} in {scenario_id} "
            f"(key {key}). Run without --offline and with an API key to populate the cache."
        )

    retrieved_ids = {hit["doc_id"] for hit in retrieved}

    input_tokens = 0
    output_tokens = 0
    for _ in range(config.LLM_MAX_RETRIES + 1):
        raw_text, used_in, used_out = llm.call_llm(
            BRIEF_SYSTEM_PROMPT, prompt, model, config.BRIEF_MAX_TOKENS)
        # Accumulated across retries, because a retry is billed like any other
        # call and a cost figure that ignored them would understate the corpus.
        input_tokens += used_in
        output_tokens += used_out
        body, _ = llm.strip_code_fences(raw_text)
        try:
            parsed = json.loads(body)
            brief = str(parsed["brief"])
            cited = [str(d) for d in parsed["cited_doc_ids"]]
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
        if not set(cited) <= retrieved_ids:
            # A citation to a document the model was not given is the failure
            # mode this layer exists to prevent; retry rather than accept it.
            continue
        # The same check against the prose. Validating only the cited_doc_ids
        # array left a gap: a brief could name a procedure in a sentence that was
        # never retrieved, and the array beside it would still be a clean subset.
        # Measured before this check existed, 2 of 160 briefs did exactly that.
        if not set(DOC_ID_IN_TEXT.findall(brief)) <= retrieved_ids:
            continue
        record = {"brief": brief, "cited_doc_ids": cited, "status": "ok",
                  "input_tokens": input_tokens, "output_tokens": output_tokens}
        llm.write_cache(config.BRIEF_CACHE_DIR, key, record)
        return record

    # Fall back to naming the retrieved documents rather than inventing guidance.
    titles = "; ".join(f"{hit['doc_id']} {hit['title']}" for hit in retrieved)
    record = {
        "brief": f"Brief generation failed. Applicable procedures: {titles}.",
        "cited_doc_ids": [hit["doc_id"] for hit in retrieved],
        "status": "fallback",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    llm.write_cache(config.BRIEF_CACHE_DIR, key, record)
    return record


def write_score_distribution(all_top_scores, path):
    """The evidence behind BM25_FLOOR_PER_TERM, written out so it can be checked.

    Both the total and the per-term score, because the total is what a reader
    sees on a brief and the per-term figure is what the floor actually tests.
    """
    totals = sorted(s for s, _ in all_top_scores)
    per_term = sorted(s / n if n else 0.0 for s, n in all_top_scores)
    fired = sum(1 for value in per_term if value < config.BM25_FLOOR_PER_TERM)
    lines = [
        "Top BM25 score per query, across all generated queries.",
        f"queries: {len(totals)}",
        f"floor in force: {config.BM25_FLOOR_PER_TERM} per query term",
        f"triggered on: {fired} of {len(totals)} queries",
        "",
        "The floor is per term because a BM25 total scales with query length:",
        "measured, the total correlates +0.87 with the number of terms. A short",
        "query is not a bad one, and an absolute floor would reject it for being",
        "short. See DECISIONS.md D-041.",
        "",
        f"total score   minimum {totals[0]:.4f}  median {totals[len(totals) // 2]:.4f}  "
        f"maximum {totals[-1]:.4f}",
        f"per-term      minimum {per_term[0]:.4f}  median {per_term[len(per_term) // 2]:.4f}  "
        f"maximum {per_term[-1]:.4f}",
        "",
        "per-term scores, sorted ascending:",
    ]
    lines += [f"  {value:.4f}" for value in per_term]
    path.write_text("\n".join(lines) + "\n")


def write_brief_cost(briefs_by_scenario, model, path):
    """What the brief corpus cost to produce, alongside the extraction figure.

    Only the extraction layer reported its tokens, which left the system's total
    LLM cost unrecorded — a gap that showed up the moment someone needed a cost
    line rather than a metric. Counts are stored per cached brief when it is
    written, so like the extraction figure they survive a replay.

    Entries cached before this accounting existed carry no counts. They are
    reported as unknown rather than as zero: a missing measurement and a measured
    zero are different things, and only the `no_match` path is a real zero.
    """
    records = [r for briefs in briefs_by_scenario.values() for r in briefs.values()]
    measured = [r for r in records if "input_tokens" in r]
    unknown = len(records) - len(measured)
    called = [r for r in measured if r["status"] != "no_match"]

    lines = [
        "Brief generation",
        f"model: {model}",
        f"prompt version: {config.BRIEF_PROMPT_VERSION}",
        f"scenarios: {len(briefs_by_scenario)}",
        f"briefs: {len(records)} ({config.BRIEF_TOP_N} per scenario)",
        f"reaching the no_match path, so making no call: "
        f"{sum(1 for r in measured if r['status'] == 'no_match')}",
        "",
        "Cost of building the brief corpus, whether or not this run paid it.",
        f"briefs with token counts recorded: {len(measured)} of {len(records)}",
        f"input tokens: {sum(r['input_tokens'] for r in measured)}",
        f"output tokens: {sum(r['output_tokens'] for r in measured)}",
        f"calls to rebuild from empty: {len(called)}",
    ]
    if unknown:
        lines += [
            "",
            f"NOT COUNTED: {unknown} briefs were cached before token accounting was",
            "added and carry no counts. The totals above cover the rest. Regenerate",
            "with a bumped BRIEF_PROMPT_VERSION, or delete cache/briefs/, to measure",
            "the whole corpus.",
        ]
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:6]))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--offline", action="store_true",
                        help="read the cache only; fail loudly on a miss")
    parser.add_argument("--scores-only", action="store_true",
                        help="build queries and retrieve, but write no briefs; used to "
                             "calibrate BM25_FLOOR_PER_TERM before any LLM call is made")
    args = parser.parse_args()

    model = config.BRIEF_MODEL
    documents = load_corpus()
    documents_by_id = {d["doc_id"]: d for d in documents}
    index = build_index(documents)

    all_top_scores = []
    briefs_by_scenario = {}
    no_match_count = 0

    for scenario_id, _, _, _, _, _ in config.SCENARIOS:
        scored = json.loads((config.OUTPUT_DIR / f"scored_{scenario_id}.json").read_text())
        briefs = {}
        for asset in scored["assets"][:config.BRIEF_TOP_N]:
            query_terms = build_query(
                asset["contributions"], scored["hazard"]["peak_temp_c"])
            retrieved, status, top_score = retrieve(
                index, documents, query_terms, asset["cooling_type"])
            all_top_scores.append((top_score, len(query_terms)))

            if status == "no_match":
                no_match_count += 1
                brief = {"brief": config.NO_MATCH_BRIEF, "cited_doc_ids": [],
                         "status": "no_match"}
            elif args.scores_only:
                brief = {"brief": None, "cited_doc_ids": [], "status": "not_generated"}
            else:
                brief = generate_brief(asset, asset["contributions"], retrieved,
                                       documents_by_id, scenario_id, model, args.offline)

            briefs[asset["asset_id"]] = {
                "query_terms": query_terms,
                "retrieved": retrieved,
                **brief,
            }

        briefs_by_scenario[scenario_id] = briefs
        if not args.scores_only:
            path = config.OUTPUT_DIR / f"briefs_{scenario_id}.json"
            path.write_text(json.dumps(briefs, indent=2) + "\n")
            print(f"wrote {path.name}: {len(briefs)} briefs")

    write_brief_cost(briefs_by_scenario, model, config.OUTPUT_DIR / "brief_cost.txt")
    write_score_distribution(all_top_scores, config.OUTPUT_DIR / "bm25_scores.txt")
    totals = [s for s, _ in all_top_scores]
    per_term = [s / n if n else 0.0 for s, n in all_top_scores]
    print(f"queries: {len(totals)}, top score range {min(totals):.3f} to {max(totals):.3f}, "
          f"per-term {min(per_term):.3f} to {max(per_term):.3f}")
    print(f"below the floor of {config.BM25_FLOOR_PER_TERM} per term: {no_match_count}")

    # Build spec section 5.4 asks that the floor trigger on at least one asset.
    # It does not, and the value was not raised into the main cluster to make it.
    # Reported here and in output/bm25_scores.txt so the fact is visible rather
    # than implied by an assertion that quietly passes. See DECISIONS.md D-018.
    if no_match_count == 0:
        print(f"note: the floor of {config.BM25_FLOOR_PER_TERM} per term did not trigger "
              f"on any of {len(totals)} queries (lowest per-term {min(per_term):.3f}); "
              f"the no_match path is untaken.")


if __name__ == "__main__":
    main()
