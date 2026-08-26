"""Citation integrity.

A brief may reference only the documents it was given. A reference to anything
else is the failure mode the retrieval layer exists to prevent, and it is checked
across every brief rather than sampled.

Two checks: the `cited_doc_ids` array, and every doc id written into the prose.
The second exists because the first reported 100% clean while two briefs named a
procedure in a sentence that had never been supplied. See DECISIONS.md D-038.
"""

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import config  # noqa: E402
import retrieve  # noqa: E402

VALID_DOC_IDS = {d["doc_id"] for d in retrieve.load_corpus()}


def all_briefs():
    briefs = []
    for scenario_id, _, _, _, _, _ in config.SCENARIOS:
        path = config.OUTPUT_DIR / f"briefs_{scenario_id}.json"
        if not path.exists():
            pytest.skip(f"{path.name} not present; run retrieve.py first")
        for asset_id, record in json.loads(path.read_text()).items():
            briefs.append((scenario_id, asset_id, record))
    return briefs


def test_brief_count():
    briefs = all_briefs()
    expected = config.BRIEF_TOP_N * len(config.SCENARIOS)
    assert len(briefs) == expected, f"expected {expected} briefs, found {len(briefs)}"


def test_citations_are_a_subset_of_retrieved():
    offenders = []
    for scenario_id, asset_id, record in all_briefs():
        retrieved = {hit["doc_id"] for hit in record["retrieved"]}
        cited = set(record["cited_doc_ids"])
        if not cited <= retrieved:
            offenders.append((scenario_id, asset_id, sorted(cited - retrieved)))
    assert not offenders, f"briefs citing documents they were not given: {offenders}"


def test_every_citation_names_a_real_document():
    for scenario_id, asset_id, record in all_briefs():
        for doc_id in record["cited_doc_ids"]:
            assert doc_id in VALID_DOC_IDS, \
                f"{scenario_id}/{asset_id} cites {doc_id}, which is not in the corpus"


def test_no_match_briefs_use_the_fixed_text_and_cite_nothing():
    for scenario_id, asset_id, record in all_briefs():
        if record["status"] == "no_match":
            assert record["brief"] == config.NO_MATCH_BRIEF
            assert record["cited_doc_ids"] == []


def test_briefs_are_not_empty():
    for scenario_id, asset_id, record in all_briefs():
        assert record["brief"] and record["brief"].strip(), \
            f"{scenario_id}/{asset_id} has an empty brief"


DOC_ID_IN_TEXT = re.compile(config.DOC_ID_PATTERN)


@pytest.mark.parametrize("scenario_id,asset_id,record", all_briefs(),
                         ids=[f"{s}/{a}" for s, a, _ in all_briefs()])
def test_no_brief_names_a_document_it_was_not_given(scenario_id, asset_id, record):
    """The gap the cited_doc_ids check left open.

    A brief could name a procedure in a sentence while its citation array stayed
    a clean subset of what was retrieved — the array was valid and the reference
    invented. Two of 160 did exactly that. A supplied document may cite another
    procedure by id, and passing that id on reads to a supervisor as a document
    they were handed.
    """
    retrieved = {hit["doc_id"] for hit in record["retrieved"]}
    mentioned = set(DOC_ID_IN_TEXT.findall(record["brief"]))
    assert mentioned <= retrieved, \
        f"names {sorted(mentioned - retrieved)}, was given {sorted(retrieved)}"


def scored_assets(scenario_id):
    path = config.OUTPUT_DIR / f"scored_{scenario_id}.json"
    if not path.exists():
        pytest.skip(f"{path.name} not present; run model.py first")
    return json.loads(path.read_text())["assets"]


@pytest.mark.parametrize("scenario_id", [s[0] for s in config.SCENARIOS])
def test_the_prompt_carries_every_inspection_finding_the_page_shows(scenario_id):
    """The brief and the evidence sit side by side on the asset page, and the
    brief was being written without ever seeing the evidence. 150 of the 160
    briefed assets carried findings the model was not given."""
    documents = {d["doc_id"]: d for d in retrieve.load_corpus()}
    briefs = json.loads((config.OUTPUT_DIR / f"briefs_{scenario_id}.json").read_text())
    checked = 0
    for asset in scored_assets(scenario_id)[:config.BRIEF_TOP_N]:
        record = briefs[asset["asset_id"]]
        if record["status"] == "no_match":
            continue
        prompt = retrieve.build_brief_prompt(
            asset, asset["contributions"], record["retrieved"], documents)
        for evidence in asset["evidence"]:
            assert evidence["text"] in prompt, \
                f"{asset['asset_id']}: {evidence['text'][:40]!r} never reached the model"
            assert evidence["date"] in prompt
            # Withheld on purpose — see build_condition_block.
            assert evidence["inspection_id"] not in prompt
        checked += 1
    assert checked, "no briefs checked"


@pytest.mark.parametrize("scenario_id", [s[0] for s in config.SCENARIOS])
def test_no_positive_driver_is_dropped_from_the_prompt(scenario_id):
    """The list used to be truncated at BM25_TOP_K — a constant about how many
    procedures a supervisor reads, reused for how many risk drivers to state.
    Condition flags sit below cooling type and the maintenance interval in
    contribution order, so the actionable findings were reliably the ones cut:
    149 of 160 briefs lost at least one."""
    documents = {d["doc_id"]: d for d in retrieve.load_corpus()}
    briefs = json.loads((config.OUTPUT_DIR / f"briefs_{scenario_id}.json").read_text())
    for asset in scored_assets(scenario_id)[:config.BRIEF_TOP_N]:
        record = briefs[asset["asset_id"]]
        if record["status"] == "no_match":
            continue
        prompt = retrieve.build_brief_prompt(
            asset, asset["contributions"], record["retrieved"], documents)
        drivers = prompt.split("in descending order of effect:\n")[1].split("\n\nProcedure")[0]
        positive = [c for c in asset["contributions"] if c["contribution"] > 0]
        assert len(drivers.strip().splitlines()) == len(positive), \
            f"{asset['asset_id']}: {len(positive)} positive drivers, prompt lists " \
            f"{len(drivers.strip().splitlines())}"
        for c in positive:
            assert c["label"] in drivers


@pytest.mark.parametrize("scenario_id", [s[0] for s in config.SCENARIOS])
def test_a_failed_extraction_is_not_presented_to_the_model_as_a_clean_asset(scenario_id):
    """An unreadable note is an absence of knowledge, not an absence of defects,
    and the two must not produce the same brief."""
    documents = {d["doc_id"]: d for d in retrieve.load_corpus()}
    briefs = json.loads((config.OUTPUT_DIR / f"briefs_{scenario_id}.json").read_text())
    for asset in scored_assets(scenario_id)[:config.BRIEF_TOP_N]:
        record = briefs[asset["asset_id"]]
        if record["status"] == "no_match":
            continue
        prompt = retrieve.build_brief_prompt(
            asset, asset["contributions"], record["retrieved"], documents)
        if asset["extraction_status"] == "failed":
            assert "could not be read reliably" in prompt
            assert "no outstanding defects" not in prompt.lower()
        elif not asset["evidence"]:
            assert "no outstanding defects" in prompt.lower()


def test_an_inspection_id_is_never_cited_as_a_procedure():
    """An inspection recorded a defect; it did not specify a remedy. v3 supplied
    the ids and forbade the misuse in as many words, and 13 of 160 briefs wrote
    "as specified in INS-340-2" anyway. v4 withholds the id, so this asserts
    something the prompt makes impossible rather than something it asks for."""
    pattern = re.compile(config.DOC_ID_PATTERN)
    for scenario_id, asset_id, record in all_briefs():
        for cited in record["cited_doc_ids"]:
            assert pattern.fullmatch(cited), f"{scenario_id}/{asset_id} cited {cited!r}"
        assert not re.search(r"\bINS-\d+-\d+\b", record["brief"]), \
            f"{scenario_id}/{asset_id} cites an inspection id in its prose"


def test_the_cache_key_changes_when_the_prompt_changes():
    """Keyed on identifiers, the key stayed the same when the prompt's content
    changed — so a brief written before the inspection findings were supplied
    would have been served as though it had seen them."""
    import llm
    documents = {d["doc_id"]: d for d in retrieve.load_corpus()}
    scenario_id = config.SCENARIOS[0][0]
    briefs = json.loads((config.OUTPUT_DIR / f"briefs_{scenario_id}.json").read_text())
    asset = next(a for a in scored_assets(scenario_id)[:config.BRIEF_TOP_N]
                 if a["evidence"] and briefs[a["asset_id"]]["status"] != "no_match")
    record = briefs[asset["asset_id"]]

    def key_for(a):
        prompt = retrieve.build_brief_prompt(a, a["contributions"], record["retrieved"], documents)
        return llm.cache_key(prompt + config.BRIEF_PROMPT_VERSION + config.BRIEF_MODEL)

    stripped = {**asset, "evidence": []}
    assert key_for(asset) != key_for(stripped), \
        "dropping every inspection finding did not change the cache key"
