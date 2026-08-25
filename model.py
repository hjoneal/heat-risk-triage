"""Layer 2 — train, evaluate and score.

Standardise, then logistic regression. Grouped on `event_id` because every asset
in one event shares all four hazard features, so a random split would put the
same weather on both sides of the fold and the reported AUC would be a fiction.

Calibration is load-bearing: priority is `risk x customers_served`, which is only
an expected number of customers if the probability means what it says. That rules
out class weighting, resampling, and any post-hoc calibration layer.

Writes: output/scored_*.json, output/metrics.json, output/metrics.md,
output/calibration.png
"""

import argparse
import json

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

import config
import extract
import features


def build_model(regularisation_c=config.LOGREG_C):
    """Standardise then fit. No class weighting — it would break calibration."""
    return Pipeline([
        ("scale", StandardScaler()),
        ("clf", LogisticRegression(
            penalty=config.LOGREG_PENALTY,
            C=regularisation_c,
            max_iter=config.LOGREG_MAX_ITER,
        )),
    ])


def out_of_fold_predictions(X, y, groups, regularisation_c=config.LOGREG_C):
    """Pooled out-of-fold probabilities, one per training row."""
    predictions = np.zeros(len(y))
    splitter = GroupKFold(n_splits=config.CV_FOLDS)
    for train_index, test_index in splitter.split(X, y, groups=groups):
        model = build_model(regularisation_c)
        model.fit(X[train_index], y[train_index])
        predictions[test_index] = model.predict_proba(X[test_index])[:, 1]
    assert (predictions > 0).all(), "a row was never held out"
    return predictions


def assert_interaction_centres(X_frame):
    """The stored interaction centres must still be the training means.

    They are constants because a scenario matrix cannot supply them (one event,
    so its own degree-hours mean is that event's value). Constants go stale when
    the generator changes, and a stale centre does not fail — it quietly shifts
    what the interaction term represents. Checked here, where the training matrix
    is in scope, rather than in features.py, which cannot tell training from
    scenario.
    """
    flag_count = X_frame[[f"flag_{flag}" for flag in config.CONDITION_FLAGS]].sum(axis=1)
    for name, measured, stored in [
        ("peak_load_pct", X_frame["peak_load_pct"].mean(), config.INTERACTION_CENTRE_PEAK_LOAD_PCT),
        ("degree_hours_above_30", X_frame["degree_hours_above_30"].mean(),
         config.INTERACTION_CENTRE_DEGREE_HOURS),
        ("condition flag count", flag_count.mean(), config.INTERACTION_CENTRE_CONDITION_FLAGS),
        ("age_years", X_frame["age_years"].mean(), config.INTERACTION_CENTRE_AGE_YEARS),
        ("consecutive_warm_nights", X_frame["consecutive_warm_nights"].mean(),
         config.INTERACTION_CENTRE_WARM_NIGHTS),
    ]:
        drift = abs(measured - stored) / abs(stored)
        assert drift <= config.INTERACTION_CENTRE_TOLERANCE, (
            f"interaction centre for {name} is stale: stored {stored}, training "
            f"mean is now {measured:.4f} ({drift:.1%} drift). Update config."
        )


def fold_coefficients(X, y, groups):
    """Each feature's coefficient in every cross-validation fold.

    Interaction terms are correlated with the features they are built from, so a
    coefficient can be large and unstable while the pair's joint effect is
    steady. Reporting the spread across folds makes that visible instead of
    letting a single refit-on-everything number imply a precision the data does
    not support. A sign that flips between folds is information about the model,
    not a defect to suppress.
    """
    per_fold = []
    splitter = GroupKFold(n_splits=config.CV_FOLDS)
    for train_index, _ in splitter.split(X, y, groups=groups):
        model = build_model()
        model.fit(X[train_index], y[train_index])
        per_fold.append(model["clf"].coef_[0])
    matrix = np.array(per_fold)
    return {
        name: {
            "mean": float(matrix[:, i].mean()),
            "sd": float(matrix[:, i].std(ddof=1)),
            "sign_flips": bool(matrix[:, i].min() < 0 < matrix[:, i].max()),
        }
        for i, name in enumerate(config.FEATURES)
    }


def top_n_by_scenario(model, assets, hazard_table, flags, scenario_id, feature_names):
    """The top CREW_CAPACITY assets under one forecast, by risk and by priority.

    Both are returned because they answer different questions. Risk is what the
    model predicts and what the interaction features act on; priority is
    risk x customers_served, the order the crew actually works down. They diverge
    sharply, which is the finding recorded in D-026.

    `feature_names` selects the columns to fit and score on, so the same function
    serves the reported measurement and the tests that compare the ranking with
    and without the interaction terms.
    """
    pairs = features.scenario_pairs(assets, scenario_id)
    X = features.build_feature_matrix(assets, hazard_table, flags, pairs)[feature_names].to_numpy()
    risk = model.predict_proba(X)[:, 1]
    customers = assets.set_index("asset_id").loc[pairs["asset_id"], "customers_served"].to_numpy()
    asset_ids = pairs["asset_id"].to_numpy()
    return {
        "risk": set(asset_ids[np.argsort(-risk)[:config.CREW_CAPACITY]]),
        "priority": set(asset_ids[np.argsort(-(risk * customers))[:config.CREW_CAPACITY]]),
    }


def ranking_divergence(model, assets, hazard_table, flags, feature_names):
    """How far the top-15 moves between each pair of demo scenarios.

    Reported on both rankings rather than one, because the difference between
    them is the whole finding: the model does respond to the forecast, and is
    then overwhelmed by a static term in the ranking built on top of it.
    """
    tops = {
        scenario_id: top_n_by_scenario(
            model, assets, hazard_table, flags, scenario_id, feature_names)
        for scenario_id, *_ in config.SCENARIOS
    }
    ids = list(tops)
    rows = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            rows.append((
                ids[i], ids[j],
                config.CREW_CAPACITY - len(tops[ids[i]]["priority"] & tops[ids[j]]["priority"]),
                config.CREW_CAPACITY - len(tops[ids[i]]["risk"] & tops[ids[j]]["risk"]),
            ))
    return rows


def write_ranking_divergence(pairs, path):
    """How far the queue moves between forecasts. A measurement, not a gate.

    A gate was specified here — at least three of the top fifteen must differ
    between any two scenarios — and dropped once it was measured as unreachable.
    Across five scenario configurations spanning the whole historical envelope,
    and with customers_served removed from the ranking entirely, the pairwise
    maximum was two. Setting a threshold the system cannot meet, or lowering one
    until it passes, would both be worse than recording what happens. See D-026.
    """
    lines = [
        "Ranking divergence across forecast scenarios",
        "",
        f"Top {config.CREW_CAPACITY} assets, compared pairwise across the "
        f"{len(config.SCENARIOS)} demo scenarios.",
        "By priority is the crew's queue order (risk x customers_served); by risk",
        "is the model's own ranking, which is what the interaction features act on.",
        "",
    ]
    for left, right, by_priority, by_risk in pairs:
        lines.append(f"  {left} vs {right}: {by_priority} of "
                     f"{config.CREW_CAPACITY} differ by priority, "
                     f"{by_risk} by risk alone")
    by_priority = [row[2] for row in pairs]
    by_risk = [row[3] for row in pairs]
    lines += [
        "",
        f"Priority divergence runs {min(by_priority)} to {max(by_priority)} against "
        f"{min(by_risk)} to {max(by_risk)} by risk. Priority is damped because",
        "customers_served spans 45x across the fleet, far more than the forecast",
        "re-weights any asset's risk, so the queue is stabler than the ranking",
        "underneath it. It is no longer frozen: an earlier build measured 0 here,",
        "with three scenarios and uncentred interaction products. See D-026, D-031.",
    ]
    path.write_text("\n".join(lines) + "\n")


def precision_recall_at_capacity(scores, labels, groups):
    """Precision and recall at the crew's capacity, per event then averaged.

    Pooling across events would let a single severe event's ranking stand in for
    the average one, and the crew is despatched per event.
    """
    precisions = []
    recalls = []
    for event_id in pd.unique(groups):
        mask = groups == event_id
        event_scores = scores[mask]
        event_labels = labels[mask]
        top = np.argsort(-event_scores)[:config.CREW_CAPACITY]
        hits = int(event_labels[top].sum())
        total_failures = int(event_labels.sum())
        precisions.append(hits / config.CREW_CAPACITY)
        recalls.append(hits / total_failures if total_failures else np.nan)
    return float(np.mean(precisions)), float(np.nanmean(recalls))


def within_event_auc(scores, labels, groups):
    """AUC computed inside each event, then averaged over events.

    The pooled figure answers a question the crew never asks. Pooling puts a mild
    event's rows beside a severe event's, so a model scores well partly by
    telling those apart — which the hazard features make trivial and which the
    supervisor already knows from the forecast sitting on their desk. Within an
    event the hazard block is constant for every asset, so this measures only
    what the tool is for: ranking 900 assets against one another under one
    forecast. It runs about 0.20 below the pooled number.

    Pooling also interacts badly with GroupKFold: each fold's intercept is fitted
    to the events *not* held out, so a low-risk event held out is scored by a
    model calibrated on higher-risk ones. A feature set with no hazard features
    cannot correct for that, which is how four genuinely informative condition
    flags score 0.4374 pooled and 0.6171 here.
    """
    values = []
    for event in np.unique(groups):
        mask = groups == event
        if 0 < labels[mask].sum() < mask.sum():
            values.append(roc_auc_score(labels[mask], scores[mask]))
    assert values, "no event contained both a failure and a survival"
    return float(np.mean(values)), len(values)


def evaluate(scores, labels, groups):
    """AUC pooled and within-event, plus precision and recall at crew capacity."""
    precision, recall = precision_recall_at_capacity(scores, labels, groups)
    within, n_events = within_event_auc(scores, labels, groups)
    return {
        "auc": float(roc_auc_score(labels, scores)),
        "within_event_auc": within,
        "n_events_scored": n_events,
        "precision_at_15": precision,
        "recall_at_15": recall,
    }


def heuristic_scores(X):
    """Rank by peak temperature times age. Nothing is fitted."""
    peak = X[:, config.FEATURES.index("peak_temp_c")]
    age = X[:, config.FEATURES.index("age_years")]
    return peak * age


def explain(model, x_row):
    """Contributions to the log-odds: coefficient times standardised value.

    Eight lines, exact, and the same arithmetic the model itself does. A linear
    model does not need SHAP to say what it did.

    Ordered by signed contribution rather than by magnitude, so the table reads
    straight down from the factor that raised this asset's odds most to the one
    that lowered them most. Sorting by magnitude interleaved the two, putting a
    strong reducer between two weak raisers for no reason a reader could see.
    Among the positive contributions the two orderings are identical, which is
    why `build_query` and `build_brief_prompt` — both of which take the leading
    positives — are unaffected.
    """
    z = model["scale"].transform(x_row.reshape(1, -1))[0]
    b = model["clf"].coef_[0]
    contributions = b * z
    order = np.argsort(-contributions)
    return [(config.FEATURES[i], float(x_row[i]), float(contributions[i])) for i in order]


def format_reading(name, value, components):
    """One feature's value as a crew supervisor would write it down.

    A contribution table is only an explanation if the reading beside it can be
    checked against the asset. A bare `0.91` for peak load, `0` for cooling type
    or `20.34` for an interaction cannot be, so each is rendered in the terms the
    register uses. `components` supplies the raw values behind an interaction,
    whose own number is a product of two centred quantities and is not a reading
    of anything.
    """
    if name in config.INTERACTION_COMPONENTS:
        return " × ".join(
            config.COMPONENT_DISPLAY[part].format(value=components[part])
            for part in config.INTERACTION_COMPONENTS[name]
        )

    unit = config.FEATURE_UNITS[name]
    if name.startswith("flag_"):
        return "yes" if value >= 0.5 else "no"
    if name == "cooling_type_ordinal":
        return config.COOLING_TYPE_BY_ORDINAL[int(round(value))]
    if name == "peak_load_pct":
        return f"{value * 100:.0f}{unit}"
    if name in ("consecutive_warm_nights", "prior_heat_faults", "days_since_maintenance",
                "age_years", "degree_hours_above_30"):
        return f"{value:,.0f} {unit}".strip()
    return f"{value:.1f} {unit}".strip()


def feature_percentiles(X_frame):
    """Sorted training values per feature, for placing a reading in context.

    "774 °C·h" says nothing without knowing that the fleet has seen 0 to 787.
    The reference is the whole training matrix rather than the current scenario,
    because within one scenario every asset shares an identical hazard reading
    and the comparison would be vacuous for four of the sixteen features.
    """
    return {name: np.sort(X_frame[name].to_numpy()) for name in config.FEATURES}


def percentile_of(sorted_values, value):
    """Share of the training set at or below this reading, 0 to 1."""
    return float(np.searchsorted(sorted_values, value, side="right") / len(sorted_values))


def assert_contributions_sum(model, X, probabilities):
    """Contributions must sum to logit(p) minus the intercept, exactly."""
    intercept = float(model["clf"].intercept_[0])
    for row_index in range(len(X)):
        contributions = sum(c for _, _, c in explain(model, X[row_index]))
        p = probabilities[row_index]
        logit = float(np.log(p / (1 - p)))
        assert abs(contributions - (logit - intercept)) < config.CONTRIBUTION_SUM_TOLERANCE, \
            f"row {row_index}: contributions {contributions} != logit-intercept {logit - intercept}"


def calibration_plot(labels, predictions, path):
    """Reliability diagram: mean predicted against observed, ten equal bins."""
    edges = np.linspace(0.0, 1.0, config.CALIBRATION_BINS + 1)
    mean_predicted = []
    observed = []
    counts = []
    for lower, upper in zip(edges[:-1], edges[1:]):
        mask = (predictions >= lower) & (predictions < upper)
        if not mask.any():
            continue
        mean_predicted.append(float(predictions[mask].mean()))
        observed.append(float(labels[mask].mean()))
        counts.append(int(mask.sum()))

    figure, axes = plt.subplots(figsize=(5, 5))
    axes.plot([0, 1], [0, 1], linestyle="--", color="grey", label="perfect calibration")
    axes.plot(mean_predicted, observed, marker="o", color="black", label="out-of-fold")
    axes.set_xlabel("mean predicted probability")
    axes.set_ylabel("observed failure rate")
    axes.set_title("Reliability, pooled out-of-fold predictions")
    axes.legend()
    figure.tight_layout()
    figure.savefig(path, dpi=150)
    plt.close(figure)

    return [
        {"mean_predicted": m, "observed": o, "count": c}
        for m, o, c in zip(mean_predicted, observed, counts)
    ]


def score_scenario(model, assets, hazard_table, flags, scenario_id, scenario_label,
                   hourly, extraction_model, training_percentiles):
    """Score every asset against one forecast scenario and rank by priority."""
    pairs = features.scenario_pairs(assets, scenario_id)
    X_frame = features.build_feature_matrix(assets, hazard_table, flags, pairs)
    X = X_frame.to_numpy()
    risk = model.predict_proba(X)[:, 1]

    assert_contributions_sum(model, X, risk)

    asset_rows = assets.set_index("asset_id")
    flag_rows = flags.set_index("asset_id")
    hazard = hazard_table.set_index("event_id").loc[scenario_id]

    # Expected customers affected. customers_served ranks but never predicts, so
    # it appears here and never in FEATURES.
    priority = risk * asset_rows.loc[pairs["asset_id"], "customers_served"].to_numpy()

    order = np.argsort(-priority)
    scored_assets = []
    for rank, row_index in enumerate(order, start=1):
        asset_id = pairs["asset_id"].iloc[row_index]
        asset = asset_rows.loc[asset_id]
        flag_row = flag_rows.loc[asset_id]
        # Raw values of everything an interaction is built from, so its reading
        # can be shown as its components rather than as a product of two centred
        # numbers, which is not a reading of anything.
        components = {
            name: float(X_frame[name].iloc[row_index])
            for name in ("peak_load_pct", "degree_hours_above_30",
                         "age_years", "consecutive_warm_nights")
        }
        components[config.CONDITION_FLAG_COUNT] = float(sum(
            X_frame[f"flag_{flag}"].iloc[row_index] for flag in config.CONDITION_FLAGS))

        contributions = [
            {
                "feature": name,
                "label": config.FEATURE_LABELS[name],
                "value": value,
                "reading": format_reading(name, value, components),
                # Where this reading sits among everything the model was trained
                # on. A temperature means little without the range behind it.
                "percentile": round(percentile_of(training_percentiles[name], value), 4),
                # The contribution is a log-odds term, which is the honest unit
                # and an unreadable one. Its exponential is the factor this
                # feature multiplies the asset's odds of failure by — exactly
                # equivalent, and a number an operator can act on.
                "odds_multiplier": round(float(np.exp(contribution)), 4),
                "contribution": contribution,
            }
            for name, value, contribution in explain(model, X[row_index])
        ]
        scored_assets.append({
            "asset_id": asset_id,
            "name": asset["name"],
            "district": asset["district"],
            "rank": rank,
            "risk": round(float(risk[row_index]), 6),
            "customers_served": int(asset["customers_served"]),
            "criticality": int(asset["criticality"]),
            "cooling_type": asset["cooling_type"],
            "priority": round(float(priority[row_index]), 2),
            "extraction_status": flag_row["extraction_status"],
            "contributions": contributions,
            "evidence": flag_row["evidence"],
        })

    temps = hourly.loc[hourly["event_id"] == scenario_id].sort_values("hour_index")["temp_c"]

    return {
        "scenario_id": scenario_id,
        "scenario_label": scenario_label,
        "generated_at": config.RUN_TIMESTAMP,
        "extraction_model": extraction_model,
        "hazard": {
            "peak_temp_c": round(float(hazard["peak_temp_c"]), 2),
            "degree_hours_above_30": round(float(hazard["degree_hours_above_30"]), 1),
            "max_overnight_min_c": round(float(hazard["max_overnight_min_c"]), 2),
            "consecutive_warm_nights": int(hazard["consecutive_warm_nights"]),
            "event_duration_days": int(hazard["event_duration_days"]),
        },
        "hourly_temps": [round(float(t), 2) for t in temps],
        "intercept": round(float(model["clf"].intercept_[0]), 6),
        "assets": scored_assets,
    }


def write_metrics_markdown(metrics, path):
    lines = [
        "# Model metrics",
        "",
        f"Extraction model: `{metrics['extraction_model']}`",
        f"Training rows: {metrics['n_rows']}, failures: {metrics['n_failures']} "
        f"({metrics['base_rate']:.4f})",
        f"Cross-validation: GroupKFold({config.CV_FOLDS}) grouped on event_id",
        "",
        "## Ablations",
        "",
        "Every variant goes through the same grouped cross-validation loop. The two",
        "middle rows differ from the full model in one respect each, so the notes",
        "and the interaction terms can be credited separately rather than jointly.",
        "",
        "Within-event AUC ranks assets against each other under one forecast, which",
        "is the only comparison the crew makes. The pooled figure also rewards",
        "telling a severe event from a mild one — easy from the hazard features and",
        "already known from the forecast — and so runs well above it.",
        "",
        "| Variant | Features | Within-event AUC | Pooled AUC | Precision@15 | Recall@15 |",
        "|---|---|---|---|---|---|",
    ]
    for name, result in metrics["ablations"].items():
        lines.append(
            f"| {name} | {result['n_features']} | {result['within_event_auc']:.4f} | "
            f"{result['auc']:.4f} | "
            f"{result['precision_at_15']:.4f} | {result['recall_at_15']:.4f} |"
        )

    lines += [
        "",
        "## Calibration",
        "",
        f"| Brier score, full model | {metrics['brier']['model']:.5f} |",
        "|---|---|",
        f"| Brier score, base rate only | {metrics['brier']['base_rate']:.5f} |",
        f"| Improvement | {metrics['brier']['improvement']:.5f} |",
        "",
        "## Regularisation sweep",
        "",
        "| C | Out-of-fold AUC |",
        "|---|---|",
    ]
    for entry in metrics["c_sweep"]:
        lines.append(f"| {entry['C']} | {entry['auc']:.4f} |")

    lines += [
        "",
        "## Coefficients",
        "",
        "Fitted on all events, on standardised features. Sign is the direction of",
        "the effect on the log-odds of failure. The fold mean and standard",
        "deviation come from the same GroupKFold split used above: interaction",
        "terms are correlated with the features they are built from, so a wide",
        "spread there is expected and is reported rather than smoothed away.",
        "",
        "| Feature | Coefficient | Fold mean | Fold SD | Sign flips |",
        "|---|---|---|---|---|",
    ]
    for name, value in metrics["coefficients"].items():
        fold = metrics["fold_coefficients"][name]
        lines.append(
            f"| {config.FEATURE_LABELS[name]} | {value:+.4f} | {fold['mean']:+.4f} | "
            f"{fold['sd']:.4f} | {'yes' if fold['sign_flips'] else 'no'} |"
        )
    lines.append(f"| _intercept_ | {metrics['intercept']:+.4f} | | | |")

    lines += [
        "",
        "## Ranking divergence across scenarios",
        "",
        f"Assets differing in the top {config.CREW_CAPACITY}, pairwise. Priority is",
        "the crew's queue order; risk is the model's own ranking. Measured, not gated.",
        "",
        "| Scenario | Scenario | Differing by priority | Differing by risk |",
        "|---|---|---|---|",
    ]
    for left, right, by_priority, by_risk in metrics["ranking_divergence"]:
        lines.append(f"| {left} | {right} | {by_priority} | {by_risk} |")

    path.write_text("\n".join(lines) + "\n")


def main():
    # Headless only when run as a script. Setting this at import time would
    # hijack the backend for anything that imports this module, which silently
    # stops the notebook rendering its plots.
    import matplotlib
    matplotlib.use("Agg")

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args()

    extraction_model = config.EXTRACTION_MODEL

    assets = features.load_assets()
    events = features.load_events()
    hourly = features.load_hourly()
    outcomes = features.load_outcomes()
    inspections = features.load_inspections()

    extractions = extract.load_extractions(inspections, extraction_model)
    flags = features.asset_condition_flags(extractions)
    hazard_table = features.build_hazard_table(events, hourly)

    pairs, labels, groups = features.training_pairs(outcomes)
    X_frame = features.build_feature_matrix(assets, hazard_table, flags, pairs)
    X = X_frame.to_numpy()

    assert_interaction_centres(X_frame)

    predictions = out_of_fold_predictions(X, labels, groups)
    full_result = evaluate(predictions, labels, groups)

    # Build spec section 9: anything above this line is evidence of leakage, not
    # of a good model. See DECISIONS.md D-006.
    assert full_result["auc"] < config.LEAKAGE_AUC_THRESHOLD, (
        f"out-of-fold AUC {full_result['auc']:.4f} is above "
        f"{config.LEAKAGE_AUC_THRESHOLD}, which indicates leakage. Investigate before accepting."
    )

    no_notes_index = [config.FEATURES.index(name) for name in config.NO_NOTES_FEATURES]
    no_notes_predictions = out_of_fold_predictions(X[:, no_notes_index], labels, groups)
    heuristic = heuristic_scores(X)

    # A ladder rather than a pair, so the notes and the interaction terms can be
    # credited separately. The two middle rungs differ from the full model in
    # exactly one respect each, which is the only way to say what either bought.
    plain = [f for f in config.FEATURES if f not in config.INTERACTION_FEATURES]
    register_only = [f for f in plain if not f.startswith("flag_")]

    def variant(feature_names):
        index = [config.FEATURES.index(name) for name in feature_names]
        return {"n_features": len(feature_names),
                **evaluate(out_of_fold_predictions(X[:, index], labels, groups),
                           labels, groups)}

    ablations = {
        "Heuristic baseline (peak temp x age)": {
            "n_features": 2, **evaluate(heuristic, labels, groups)},
        "Register only": variant(register_only),
        "Register + notes": variant(plain),
        "Register + interactions (no notes)": {
            "n_features": len(config.NO_NOTES_FEATURES),
            **evaluate(no_notes_predictions, labels, groups)},
        "Full model": {"n_features": len(config.FEATURES), **full_result},
    }

    c_sweep = [
        {"C": value, "auc": float(roc_auc_score(
            labels, out_of_fold_predictions(X, labels, groups, value)))}
        for value in config.C_SWEEP
    ]

    base_rate = float(labels.mean())
    brier = {
        "model": float(brier_score_loss(labels, predictions)),
        "base_rate": float(brier_score_loss(labels, np.full(len(labels), base_rate))),
    }
    brier["improvement"] = brier["base_rate"] - brier["model"]

    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    calibration_bins = calibration_plot(
        labels, predictions, config.OUTPUT_DIR / "calibration.png")

    # Refit on every event to score the forecasts.
    final_model = build_model()
    final_model.fit(X, labels)
    coefficients = {
        name: float(value)
        for name, value in zip(config.FEATURES, final_model["clf"].coef_[0])
    }

    fold_coefs = fold_coefficients(X, labels, groups)

    # The interaction features exist so that a different forecast can rank the
    # assets differently; without them every hazard feature is constant within a
    # scenario and the ranking cannot move at all. tests/test_ranking.py holds
    # that comparison. Recorded here as a measurement rather than gated.
    divergence = ranking_divergence(final_model, assets, hazard_table, flags, config.FEATURES)
    write_ranking_divergence(divergence, config.OUTPUT_DIR / "ranking_divergence.txt")

    metrics = {
        "extraction_model": extraction_model,
        "n_rows": int(len(labels)),
        "n_failures": int(labels.sum()),
        "base_rate": base_rate,
        "ablations": ablations,
        "c_sweep": c_sweep,
        "brier": brier,
        "calibration_bins": calibration_bins,
        "coefficients": coefficients,
        "fold_coefficients": fold_coefs,
        "ranking_divergence": divergence,
        "intercept": float(final_model["clf"].intercept_[0]),
    }
    (config.OUTPUT_DIR / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
    write_metrics_markdown(metrics, config.OUTPUT_DIR / "metrics.md")

    training_percentiles = feature_percentiles(X_frame)
    for scenario_id, scenario_label, _, _, _, _ in config.SCENARIOS:
        scored = score_scenario(final_model, assets, hazard_table, flags,
                                scenario_id, scenario_label, hourly, extraction_model,
                                training_percentiles)
        path = config.OUTPUT_DIR / f"scored_{scenario_id}.json"
        path.write_text(json.dumps(scored, indent=2) + "\n")
        print(f"wrote {path.name}: top asset {scored['assets'][0]['asset_id']} "
              f"risk {scored['assets'][0]['risk']:.4f}")

    print(f"\nout-of-fold AUC (full model): {full_result['auc']:.4f}")
    print(f"out-of-fold AUC (no notes):     "
          f"{ablations['Register + interactions (no notes)']['auc']:.4f}")
    print(f"out-of-fold AUC (heuristic): {ablations['Heuristic baseline (peak temp x age)']['auc']:.4f}")
    print(f"Brier: {brier['model']:.5f} against base-rate {brier['base_rate']:.5f}")

    print("\ntop-15 assets differing between scenarios:")
    for left, right, by_priority, by_risk in divergence:
        print(f"  {left} vs {right}: {by_priority} by priority, {by_risk} by risk alone")


if __name__ == "__main__":
    main()
