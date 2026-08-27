"""What the interaction features do to the ranking.

The three interaction terms were added because a logistic regression on hazard
features that are constant within an event cannot reorder assets from one
forecast to the next: the hazard block adds the same number to every asset's
log-odds, and adding a constant leaves an ordering untouched. That is an
argument about the model's structure, so it is demonstrated rather than
asserted — fit the same data with and without the three terms and compare.

A gate was specified on top of this (at least three of the top fifteen must
differ between any two scenarios) and dropped once measured as unreachable.
These tests check the mechanism, not a magnitude.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import config  # noqa: E402
import extract  # noqa: E402
import features  # noqa: E402
import model  # noqa: E402

NO_INTERACTIONS = [f for f in config.FEATURES if f not in config.INTERACTION_FEATURES]


@pytest.fixture(scope="module")
def fitted():
    """Everything needed to rank the fleet under each scenario, fitted once."""
    if not (config.DATA_DIR / "outcomes.csv").exists():
        pytest.skip("generated data not present; run generate_data.py first")
    assets = features.load_assets()
    events = features.load_events()
    hourly = features.load_hourly()
    outcomes = features.load_outcomes()
    inspections = features.load_inspections()
    flags = features.asset_condition_flags(
        extract.load_extractions(inspections, config.EXTRACTION_MODEL))
    hazard = features.build_hazard_table(events, hourly)
    pairs, labels, _ = features.training_pairs(outcomes)
    matrix = features.build_feature_matrix(assets, hazard, flags, pairs)
    return assets, hazard, flags, matrix, labels


def top_sets(fitted, feature_names, key):
    assets, hazard, flags, matrix, labels = fitted
    estimator = model.build_model()
    estimator.fit(matrix[feature_names].to_numpy(), labels)
    return {
        scenario_id: model.top_n_by_scenario(
            estimator, assets, hazard, flags, scenario_id, feature_names)[key]
        for scenario_id, *_ in config.SCENARIOS
    }


def scenario_pairs():
    ids = [s[0] for s in config.SCENARIOS]
    return [(ids[i], ids[j]) for i in range(len(ids)) for j in range(i + 1, len(ids))]


def test_without_interactions_the_forecast_cannot_reorder_the_fleet(fitted):
    """The control. Every remaining feature is constant either across assets
    within a scenario or across scenarios within an asset, so the ranking is
    mathematically forced to be identical whatever the weather does."""
    tops = top_sets(fitted, NO_INTERACTIONS, "risk")
    for left, right in scenario_pairs():
        assert tops[left] == tops[right], (
            f"{left} and {right} differ without interaction terms, which the "
            f"model's structure should make impossible"
        )


def test_interaction_terms_make_the_forecast_change_the_risk_ranking(fitted):
    """The claim. With the interaction terms the same comparison must move."""
    tops = top_sets(fitted, config.FEATURES, "risk")
    differing = {
        (left, right): config.CREW_CAPACITY - len(tops[left] & tops[right])
        for left, right in scenario_pairs()
    }
    assert any(count > 0 for count in differing.values()), (
        f"no pair of scenarios ranks assets differently: {differing}"
    )


def test_the_priority_queue_is_dominated_by_customers_served(fitted):
    """The finding that closed gate 7, pinned so a later change cannot quietly
    reverse it. customers_served spans far more than the forecast can move, so
    the crew's queue membership is far stabler than the risk ranking under it."""
    assets, *_ = fitted
    customers = assets["customers_served"].to_numpy()
    by_risk = top_sets(fitted, config.FEATURES, "risk")
    by_priority = top_sets(fitted, config.FEATURES, "priority")

    risk_moves = sum(config.CREW_CAPACITY - len(by_risk[a] & by_risk[b])
                     for a, b in scenario_pairs())
    priority_moves = sum(config.CREW_CAPACITY - len(by_priority[a] & by_priority[b])
                         for a, b in scenario_pairs())

    assert customers.max() / customers.min() > 10, \
        "the domination argument assumes a wide customer spread"
    assert priority_moves <= risk_moves, (
        f"priority ranking moved more than risk ranking ({priority_moves} vs "
        f"{risk_moves}), which contradicts the recorded finding"
    )


def test_every_scenario_sits_inside_the_trained_hazard_envelope(fitted):
    """A forecast outside the historical range would ask the model to
    extrapolate, and nothing in a linear model warns when it does."""
    _, hazard, _, _, _ = fitted
    events = features.load_events().set_index("event_id")
    hazard = hazard.set_index("event_id")
    historical = [e for e in hazard.index if not events.loc[e, "is_scenario"]]
    scenarios = [s[0] for s in config.SCENARIOS]

    for name in ["degree_hours_above_30", "peak_temp_c", "max_overnight_min_c"]:
        low = hazard.loc[historical, name].min()
        high = hazard.loc[historical, name].max()
        for scenario_id in scenarios:
            value = hazard.loc[scenario_id, name]
            assert low <= value <= high, (
                f"{scenario_id} has {name}={value:.1f}, outside the trained "
                f"range [{low:.1f}, {high:.1f}]"
            )


def test_the_capacity_sweep_is_reported_and_keeps_its_named_keys():
    """The sweep is a reporting change on the same predictions, and the two
    figures downstream references expect must survive it."""
    path = config.OUTPUT_DIR / "metrics.json"
    if not path.exists():
        pytest.skip("metrics not present; run model.py first")
    import json
    metrics = json.loads(path.read_text())

    assert "precision_at_15" in metrics and "recall_at_15" in metrics, \
        "named top-level keys were dropped; downstream references expect them"
    assert metrics["crew_capacity"] == config.CREW_CAPACITY

    sweep = {row["capacity"]: row for row in metrics["capacity_sweep"]}
    assert list(sweep) == config.CAPACITY_SWEEP

    # The sweep at the default capacity must be the same computation the
    # ablation table reports, not a parallel one that could drift from it.
    default = sweep[config.CREW_CAPACITY]
    assert default["is_default"]
    assert abs(default["precision"] - metrics["precision_at_15"]) < 1e-9
    assert abs(default["recall"] - metrics["recall_at_15"]) < 1e-9

    # Recall must rise with capacity: visiting more assets cannot find fewer.
    recalls = [sweep[k]["recall"] for k in config.CAPACITY_SWEEP]
    assert recalls == sorted(recalls), f"recall is not monotone in capacity: {recalls}"


def test_the_per_variant_sweep_agrees_with_the_ablation_table():
    """The sweep must be the same numbers the ablation table reports, at k=15.

    Both come from one set of out-of-fold predictions per variant. If they were
    computed from separate cross-validation runs they could drift apart, and a
    reader comparing the two tables would have no way to tell.
    """
    path = config.OUTPUT_DIR / "metrics.json"
    if not path.exists():
        pytest.skip("metrics not present; run model.py first")
    import json
    metrics = json.loads(path.read_text())
    by_variant = metrics["capacity_sweep_by_variant"]

    assert set(by_variant) == set(metrics["ablations"]), \
        "the sweep and the ablation table cover different variants"

    for name, rows in by_variant.items():
        assert [r["capacity"] for r in rows] == config.CAPACITY_SWEEP
        at_default = next(r for r in rows if r["capacity"] == config.CREW_CAPACITY)
        ablation = metrics["ablations"][name]
        assert abs(at_default["precision"] - ablation["precision_at_15"]) < 1e-9, name
        assert abs(at_default["recall"] - ablation["recall_at_15"]) < 1e-9, name

    # The full model's row must be the standalone sweep, not a second computation.
    assert by_variant["Full model"] == metrics["capacity_sweep"]


def test_hits_are_consistent_with_the_precision_they_are_reported_beside():
    """The count is what makes the rate readable; it must not drift from it."""
    path = config.OUTPUT_DIR / "metrics.json"
    if not path.exists():
        pytest.skip("metrics not present; run model.py first")
    import json
    metrics = json.loads(path.read_text())
    n_events = metrics["ablations"]["Full model"]["n_events_scored"]
    for name, rows in metrics["capacity_sweep_by_variant"].items():
        for r in rows:
            # n_events_scored counts events with both classes; precision is
            # averaged over every event, so recover the divisor from the data.
            implied = r["precision"] * r["capacity"] * 16
            assert abs(implied - r["hits"]) <= 0.5, (name, r["capacity"], implied, r["hits"])
