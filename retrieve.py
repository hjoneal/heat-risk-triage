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
            "applies_to": fields["applies_to"],
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


def build_query(cooling_type, contributions, peak_temp_c):
    """Assemble query terms from the contributions that pushed risk up.

    Only positive contributions: a feature that lowered this asset's risk is not
    a reason to send a crew, and including it would retrieve procedures for a
    problem the asset does not have.
    """
    terms = []
    for contribution in contributions:
        if contribution["contribution"] > 0:
            terms += config.QUERY_TERMS[contribution["feature"]]
    terms.append(cooling_type)
    if peak_temp_c > config.HIGH_AMBIENT_QUERY_C:
        terms += config.HIGH_AMBIENT_QUERY_TERMS
    return terms


def retrieve(index, documents, query_terms):
    """Top-k documents above the score floor.

    Below the floor there is no useful match, and returning the least-bad of a
    bad set would put a procedure in front of a crew that does not apply.
    """
    scores = index.get_scores([term.lower() for term in query_terms])
    ranked = sorted(range(len(documents)), key=lambda i: -scores[i])[:config.BM25_TOP_K]
    top_score = float(scores[ranked[0]]) if ranked else 0.0

    if top_score < config.BM25_FLOOR:
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
- Use only the supplied asset facts and the supplied procedure documents. Do not \
introduce any procedure, threshold or figure that is not in them.
- Cite the doc_id of every document you draw on.
- If the supplied documents do not cover the situation, say so plainly rather \
than improvising guidance.

Return a single JSON object with keys "brief" (string) and "cited_doc_ids" \
(array of strings). Output the JSON only. No markdown fences, no commentary."""


def build_brief_prompt(asset, contributions, retrieved, documents_by_id):
    """Asset facts, then why it ranked, then the documents in full."""
    top_reasons = [
        f"- {c['label']} (value {c['value']:.4g})"
        for c in contributions if c["contribution"] > 0
    ][:config.BM25_TOP_K]

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
        f"Why this asset ranked where it did:\n" + "\n".join(top_reasons) + "\n\n"
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
        return {"brief": config.NO_MATCH_BRIEF, "cited_doc_ids": [], "status": "no_match"}

    doc_ids = "".join(hit["doc_id"] for hit in retrieved)
    key = llm.cache_key(asset["asset_id"] + scenario_id + doc_ids
                        + config.BRIEF_PROMPT_VERSION + model)
    cached = llm.read_cache(config.BRIEF_CACHE_DIR, key)
    if cached is not None:
        return cached

    if offline:
        raise RuntimeError(
            f"offline mode: no cached brief for {asset['asset_id']} in {scenario_id} "
            f"(key {key}). Run without --offline and with an API key to populate the cache."
        )

    prompt = build_brief_prompt(asset, contributions, retrieved, documents_by_id)
    retrieved_ids = {hit["doc_id"] for hit in retrieved}

    for _ in range(config.LLM_MAX_RETRIES + 1):
        raw_text, _, _ = llm.call_llm(
            BRIEF_SYSTEM_PROMPT, prompt, model, config.BRIEF_MAX_TOKENS)
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
        record = {"brief": brief, "cited_doc_ids": cited, "status": "ok"}
        llm.write_cache(config.BRIEF_CACHE_DIR, key, record)
        return record

    # Fall back to naming the retrieved documents rather than inventing guidance.
    titles = "; ".join(f"{hit['doc_id']} {hit['title']}" for hit in retrieved)
    record = {
        "brief": f"Brief generation failed. Applicable procedures: {titles}.",
        "cited_doc_ids": [hit["doc_id"] for hit in retrieved],
        "status": "fallback",
    }
    llm.write_cache(config.BRIEF_CACHE_DIR, key, record)
    return record


def write_score_distribution(all_top_scores, path):
    """The evidence behind BM25_FLOOR, written out so the value can be checked."""
    ordered = sorted(all_top_scores)
    lines = [
        "Top BM25 score per query, across all generated queries.",
        f"queries: {len(ordered)}",
        f"floor in force: {config.BM25_FLOOR}",
        f"triggered on: {sum(1 for s in ordered if s < config.BM25_FLOOR)} of {len(ordered)} queries",
        f"minimum: {ordered[0]:.4f}",
        f"maximum: {ordered[-1]:.4f}",
        f"median:  {ordered[len(ordered) // 2]:.4f}",
        "",
        "sorted ascending:",
    ]
    lines += [f"  {score:.4f}" for score in ordered]
    path.write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--offline", action="store_true",
                        help="read the cache only; fail loudly on a miss")
    parser.add_argument("--scores-only", action="store_true",
                        help="build queries and retrieve, but write no briefs; used to "
                             "calibrate BM25_FLOOR before any LLM call is made")
    args = parser.parse_args()

    model = config.BRIEF_MODEL
    documents = load_corpus()
    documents_by_id = {d["doc_id"]: d for d in documents}
    index = build_index(documents)

    all_top_scores = []
    no_match_count = 0

    for scenario_id, _, _, _, _, _ in config.SCENARIOS:
        scored = json.loads((config.OUTPUT_DIR / f"scored_{scenario_id}.json").read_text())
        briefs = {}
        for asset in scored["assets"][:config.BRIEF_TOP_N]:
            query_terms = build_query(
                asset["cooling_type"], asset["contributions"], scored["hazard"]["peak_temp_c"])
            retrieved, status, top_score = retrieve(index, documents, query_terms)
            all_top_scores.append(top_score)

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

        if not args.scores_only:
            path = config.OUTPUT_DIR / f"briefs_{scenario_id}.json"
            path.write_text(json.dumps(briefs, indent=2) + "\n")
            print(f"wrote {path.name}: {len(briefs)} briefs")

    write_score_distribution(all_top_scores, config.OUTPUT_DIR / "bm25_scores.txt")
    print(f"queries: {len(all_top_scores)}, top score range "
          f"{min(all_top_scores):.3f} to {max(all_top_scores):.3f}")
    print(f"below the floor of {config.BM25_FLOOR}: {no_match_count}")

    # Build spec section 5.4 asks that the floor trigger on at least one asset.
    # It does not, and the value was not raised into the main cluster to make it.
    # Reported here and in output/bm25_scores.txt so the fact is visible rather
    # than implied by an assertion that quietly passes. See DECISIONS.md D-018.
    if no_match_count == 0:
        print(f"note: the floor of {config.BM25_FLOOR} did not trigger on any of "
              f"{len(all_top_scores)} queries (lowest top score "
              f"{min(all_top_scores):.2f}); the no_match path is untaken.")


if __name__ == "__main__":
    main()
