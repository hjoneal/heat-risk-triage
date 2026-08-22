"""Citation integrity.

A brief may cite only the documents it was given. A citation to anything else is
the failure mode the retrieval layer exists to prevent, and it is checked across
every brief rather than sampled.
"""

import json
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
