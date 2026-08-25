"""Retrieval assertions.

Ten fixed queries with expected results, plus the one that matters most: no
heat-related query may return a cold-weather document. That is checked across
every query the pipeline actually generates, not only the fixed set, because a
fixed set only tests the cases someone thought of.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import config  # noqa: E402
import retrieve  # noqa: E402

DOCUMENTS = retrieve.load_corpus()
INDEX = retrieve.build_index(DOCUMENTS)
COLD_WEATHER_IDS = {d["doc_id"] for d in DOCUMENTS if d["category"] == "cold-weather"}


def top_doc_ids(query, k=config.BM25_TOP_K):
    scores = INDEX.get_scores(retrieve.tokenise(query))
    ranked = sorted(range(len(DOCUMENTS)), key=lambda i: -scores[i])[:k]
    return [DOCUMENTS[i]["doc_id"] for i in ranked]


# (name, query, doc_ids that must appear in the top 3)
FIXED_QUERIES = [
    ("cooling fan failure on a forced-air unit",
     "cooling fan fans radiator inspection ONAF de-rating high ambient temperature",
     {"SOP-014", "MG-021"}),
    ("oil seepage",
     "oil level seepage sampling",
     {"SOP-013"}),
    ("sustained high ambient temperature",
     "sustained high ambient de-rating loading capacity",
     {"MG-021"}),
    ("obstructed ventilation",
     "ventilation louvre airflow vegetation",
     {"SOP-015"}),
    ("vegetation encroachment",
     "ventilation louvre airflow vegetation ONAN",
     {"SOP-011"}),
    ("outstanding work orders",
     "work order outstanding deferred",
     {"SOP-016"}),
    ("warm nights and sustained loading",
     "sustained overnight thermal loading ageing insulation",
     {"MG-025"}),
    ("ageing insulation",
     "ageing insulation end-of-life",
     {"MG-023"}),
    ("overdue maintenance",
     "maintenance overdue schedule",
     {"REG-042"}),
    ("oil defect on an asset with outstanding work",
     "oil level seepage sampling work order outstanding deferred OFAF",
     {"SOP-013", "SOP-016"}),
]


@pytest.mark.parametrize("name,query,expected", FIXED_QUERIES,
                         ids=[q[0] for q in FIXED_QUERIES])
def test_fixed_query_returns_expected_documents(name, query, expected):
    returned = set(top_doc_ids(query))
    assert expected <= returned, \
        f"{name}: expected {sorted(expected)} in the top {config.BM25_TOP_K}, got {sorted(returned)}"


# Everything build_query can ever emit: the per-feature term lists, the
# high-ambient terms, and the cooling type appended to every query.
QUERY_VOCABULARY = sorted(
    {term for terms in config.QUERY_TERMS.values() for term in terms}
    | set(config.HIGH_AMBIENT_QUERY_TERMS)
    | set(config.COOLING_TYPES)
)


QUERY_ONLY = [(name, query) for name, query, _ in FIXED_QUERIES]


@pytest.mark.parametrize("name,query", QUERY_ONLY, ids=[q[0] for q in QUERY_ONLY])
def test_fixed_queries_use_only_generatable_vocabulary(name, query):
    """The fixed set must be reachable by build_query.

    A hand-written query containing words the system cannot emit tests the
    corpus, not the system. `vegetation clearance compound airflow` puts a
    cold-weather document in the top three on the strength of "clearance" and
    "compound", and neither word is in the vocabulary below.
    """
    unknown = [t for t in query.split() if t not in QUERY_VOCABULARY]
    assert not unknown, f"{name}: {unknown} cannot be produced by build_query"


@pytest.mark.parametrize("term", QUERY_VOCABULARY)
def test_no_single_query_term_surfaces_a_cold_weather_document(term):
    """Exhaustive over the query atoms, rather than over queries someone imagined."""
    returned = set(top_doc_ids(term))
    assert not (returned & COLD_WEATHER_IDS), \
        f"{term!r} returned cold-weather document(s) {sorted(returned & COLD_WEATHER_IDS)}"


@pytest.mark.parametrize("name,query", QUERY_ONLY, ids=[q[0] for q in QUERY_ONLY])
def test_fixed_query_returns_no_cold_weather_document(name, query):
    returned = set(top_doc_ids(query))
    assert not (returned & COLD_WEATHER_IDS), \
        f"{name}: returned cold-weather document(s) {sorted(returned & COLD_WEATHER_IDS)}"


def test_tokeniser_preserves_hyphenated_identifiers():
    """`sop-014` and `de-rating` must survive as single tokens.

    A `\\w+` tokeniser splits both, which silently breaks every cross-reference
    in the corpus and every de-rating query.
    """
    tokens = retrieve.tokenise("See SOP-014 for de-rating at high ambient.")
    assert "sop-014" in tokens
    assert "de-rating" in tokens
    assert "sop" not in tokens
    assert "014" not in tokens


def test_corpus_shape():
    assert len(DOCUMENTS) == config.N_PROCEDURES
    assert len(COLD_WEATHER_IDS) == 2
    assert len({d["doc_id"] for d in DOCUMENTS}) == config.N_PROCEDURES


def generated_queries():
    """Every query the pipeline actually built, read back from the briefs."""
    queries = []
    for scenario_id, _, _, _, _, _ in config.SCENARIOS:
        path = config.OUTPUT_DIR / f"briefs_{scenario_id}.json"
        if not path.exists():
            pytest.skip(f"{path.name} not present; run retrieve.py first")
        for asset_id, record in json.loads(path.read_text()).items():
            queries.append((scenario_id, asset_id, record))
    return queries


def test_no_generated_query_returns_a_cold_weather_document():
    """The assertion across every generated query, not just the fixed set."""
    offenders = []
    for scenario_id, asset_id, record in generated_queries():
        returned = {hit["doc_id"] for hit in record["retrieved"]}
        if returned & COLD_WEATHER_IDS:
            offenders.append((scenario_id, asset_id, sorted(returned & COLD_WEATHER_IDS)))
    assert not offenders, f"cold-weather documents returned for heat queries: {offenders}"


def test_every_generated_query_has_terms():
    for scenario_id, asset_id, record in generated_queries():
        assert record["query_terms"], f"{scenario_id}/{asset_id} produced an empty query"


def test_retrieved_documents_respect_the_floor():
    for scenario_id, asset_id, record in generated_queries():
        if record["status"] == "no_match":
            assert record["retrieved"] == [], \
                f"{scenario_id}/{asset_id} is no_match but carries retrieved documents"
        else:
            assert record["retrieved"], f"{scenario_id}/{asset_id} returned nothing but is not no_match"
            per_term = record["retrieved"][0]["score"] / len(record["query_terms"])
            assert per_term >= config.BM25_FLOOR_PER_TERM, \
                f"{scenario_id}/{asset_id} scored {per_term:.3f} per term but was returned"


def test_no_match_branch_fires_on_a_degenerate_query():
    """Exercise the floor's branch directly, since no real query reaches it.

    `BM25_FLOOR_PER_TERM` is set below the whole observed distribution and never
    fires on any generated query (DECISIONS.md D-018), so without this the branch
    and its fixed text would be unexecuted code. A one-term query against
    vocabulary the corpus barely contains is the cheap way to reach it without
    moving the production threshold to manufacture a trigger.
    """
    hits, status, top_score = retrieve.retrieve(INDEX, DOCUMENTS, ["zzzznonsense"])
    assert status == "no_match"
    assert hits == []
    assert top_score / 1 < config.BM25_FLOOR_PER_TERM


def test_no_match_produces_the_fixed_brief_and_makes_no_call():
    """A no_match asset gets fixed text and no LLM call.

    `offline=True` would raise on a cache miss, so reaching the fixed text
    without raising proves no call was attempted.
    """
    brief = retrieve.generate_brief(
        asset={"asset_id": "SUB-SGW-001", "name": "x", "cooling_type": "ONAN",
               "customers_served": 1000, "criticality": 3},
        contributions=[], retrieved=[], documents_by_id={},
        scenario_id="short-severe", model="unused", offline=True,
    )
    assert brief["status"] == "no_match"
    assert brief["brief"] == config.NO_MATCH_BRIEF
    assert brief["cited_doc_ids"] == []


# --- Applicability -------------------------------------------------------

APPLIES_TO = {d["doc_id"]: set(d["applies_to"]) for d in DOCUMENTS}
RESTRICTED = {doc_id for doc_id, types in APPLIES_TO.items()
              if types != set(config.COOLING_TYPES)}


def test_the_corpus_actually_has_something_to_filter():
    """If every document applied to every cooling type the filter would be
    machinery for a case that does not exist. Two documents do not."""
    assert RESTRICTED, "no document is cooling-type restricted; the filter is pointless"
    for doc_id in RESTRICTED:
        assert "ONAN" not in APPLIES_TO[doc_id], \
            "the restriction this filter was written for is ONAN having no fans"


@pytest.mark.parametrize("cooling_type", config.COOLING_TYPES)
def test_retrieval_never_returns_an_inapplicable_procedure(cooling_type):
    """A procedure for a forced-air cooling system does not become relevant to a
    naturally-cooled unit by scoring well. Checked against a query built from the
    terms most likely to surface those documents."""
    query = (config.QUERY_TERMS["flag_cooling_degraded"]
             + config.QUERY_TERMS["flag_ventilation_obstructed"]
             + config.QUERY_TERMS["consecutive_warm_nights"]
             + config.QUERY_TERMS["peak_load_pct"]
             + config.HIGH_AMBIENT_QUERY_TERMS)
    hits, _, _ = retrieve.retrieve(INDEX, DOCUMENTS, query, cooling_type)
    for hit in hits:
        assert cooling_type in APPLIES_TO[hit["doc_id"]], \
            f"{hit['doc_id']} does not apply to {cooling_type} but was returned"


def test_the_filter_changes_what_ONAN_units_can_receive():
    """Guard against the filter being a no-op: without it the restricted
    documents must be reachable, otherwise this test proves nothing."""
    query = (config.QUERY_TERMS["flag_cooling_degraded"] * 4
             + config.QUERY_TERMS["consecutive_warm_nights"])
    unfiltered, _, _ = retrieve.retrieve(INDEX, DOCUMENTS, query, None)
    filtered, _, _ = retrieve.retrieve(INDEX, DOCUMENTS, query, "ONAN")
    assert {h["doc_id"] for h in unfiltered} & RESTRICTED, \
        "the probe query does not reach a restricted document, so it tests nothing"
    assert not {h["doc_id"] for h in filtered} & RESTRICTED


def test_the_cooling_type_is_no_longer_a_query_term():
    """It could not do the job: applies_to is not indexed, so as a term it only
    matched cooling types written into a document's prose."""
    import json
    path = config.OUTPUT_DIR / f"scored_{config.SCENARIOS[0][0]}.json"
    if not path.exists():
        pytest.skip("scored output not present")
    scored = json.loads(path.read_text())
    asset = scored["assets"][0]
    terms = retrieve.build_query(asset["contributions"], scored["hazard"]["peak_temp_c"])
    assert not set(terms) & set(config.COOLING_TYPES)


def test_the_floor_does_not_reject_a_query_for_being_short():
    """The defect the per-term floor exists to prevent.

    A short query scores a low BM25 *total* simply because there are fewer terms
    to accumulate over — total score correlates +0.87 with query length. Under an
    absolute floor a well-matched 15-term query was rejected while a poorly
    matched 44-term one passed. Same terms, repeated: the match quality per term
    is identical, so the floor's verdict must be too.
    """
    short = config.QUERY_TERMS["peak_load_pct"] + config.QUERY_TERMS["age_years"]
    long = short * 3
    _, short_status, short_score = retrieve.retrieve(INDEX, DOCUMENTS, short)
    _, long_status, long_score = retrieve.retrieve(INDEX, DOCUMENTS, long)
    assert long_score > short_score, "the long query should score a higher total"
    assert short_status == long_status, (
        f"the same match quality was judged {short_status} at {len(short)} terms and "
        f"{long_status} at {len(long)} terms"
    )
