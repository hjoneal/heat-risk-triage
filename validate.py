"""Deterministic validation of everything the pipeline produced.

This is the only module permitted to open `data/hidden_asset_state.csv`,
`data/hidden_thermal_stress.csv` and `data/inspection_truth.csv`. Nothing that
produces a model feature may read them; keeping them confined here is what makes
the leakage boundary checkable rather than merely asserted.

Writes: output/extraction_eval.md, output/validation.md
"""

import json

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

import config
import extract
import features
import generate_data
import model


def precision_recall(predicted, actual):
    """Precision and recall for one boolean flag."""
    predicted = np.asarray(predicted, dtype=bool)
    actual = np.asarray(actual, dtype=bool)
    true_positive = int((predicted & actual).sum())
    false_positive = int((predicted & ~actual).sum())
    false_negative = int((~predicted & actual).sum())
    precision = true_positive / (true_positive + false_positive) if predicted.any() else float("nan")
    recall = true_positive / (true_positive + false_negative) if actual.any() else float("nan")
    return {
        "precision": precision,
        "recall": recall,
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "actual_positive": int(actual.sum()),
    }


def note_category(truth_row):
    """Which of the four note shapes this is, for the error breakdown.

    Resolution and negation are the cases the keyword baseline cannot see, so
    they are counted separately rather than folded into an overall accuracy.
    """
    if truth_row["has_resolution"]:
        return "resolution present"
    if truth_row["has_negation"]:
        return "negation present"
    if any(truth_row[f"true_{flag}"] for flag in config.CONDITION_FLAGS):
        return "straightforward positive"
    return "distractors only"


def evaluate_extraction(inspections, truth, extractions):
    """LLM extraction against the keyword baseline, both against truth."""
    merged = (
        inspections
        .merge(truth, on="inspection_id")
        .merge(extractions, on=["inspection_id", "asset_id", "inspection_date"])
    )
    assert len(merged) == len(inspections), "lost a note joining notes, truth and extractions"

    merged["category"] = merged.apply(note_category, axis=1)
    baseline = merged["note_text"].apply(extract.keyword_baseline)
    for flag in config.CONDITION_FLAGS:
        merged[f"baseline_{flag}"] = baseline.apply(lambda flags, f=flag: flags[f])

    per_flag = {}
    for flag in config.CONDITION_FLAGS:
        actual = merged[f"true_{flag}"]
        per_flag[flag] = {
            "llm": precision_recall(merged[f"{flag}_present"], actual),
            "baseline": precision_recall(merged[f"baseline_{flag}"], actual),
        }

    by_category = {}
    for category, group in merged.groupby("category"):
        llm_errors = 0
        baseline_errors = 0
        for flag in config.CONDITION_FLAGS:
            actual = group[f"true_{flag}"].to_numpy(dtype=bool)
            llm_errors += int((group[f"{flag}_present"].to_numpy(dtype=bool) != actual).sum())
            baseline_errors += int((group[f"baseline_{flag}"].to_numpy(dtype=bool) != actual).sum())
        decisions = len(group) * len(config.CONDITION_FLAGS)
        by_category[category] = {
            "notes": len(group),
            "flag_decisions": decisions,
            "llm_errors": llm_errors,
            "baseline_errors": baseline_errors,
            "llm_error_rate": llm_errors / decisions,
            "baseline_error_rate": baseline_errors / decisions,
        }

    return merged, per_flag, by_category


def evidence_check(merged):
    """Every non-null quote must be a verbatim substring of its own note.

    extract.py already refuses to cache an extraction that fails this, so a
    non-zero count here means something got past that gate and is a defect, not
    a statistic.
    """
    failures = 0
    checked = 0
    for row in merged.itertuples():
        for flag in config.CONDITION_FLAGS:
            evidence = getattr(row, f"{flag}_evidence")
            if isinstance(evidence, str) and evidence:
                checked += 1
                if evidence not in row.note_text:
                    failures += 1
    return checked, failures


def leakage_check(assets, hazard_table, flags, outcomes):
    """The feature matrix must contain no proxy for the hidden state."""
    pairs, _, _ = features.training_pairs(outcomes)
    matrix = features.build_feature_matrix(assets, hazard_table, flags, pairs)

    assert list(matrix.columns) == config.FEATURES, \
        f"feature matrix columns are {list(matrix.columns)}, expected {config.FEATURES}"

    hidden = pd.read_csv(config.DATA_DIR / "hidden_asset_state.csv")
    hidden_stress = pd.read_csv(config.DATA_DIR / "hidden_thermal_stress.csv")

    condition_by_asset = dict(zip(hidden["asset_id"], hidden["condition"]))
    stress_by_pair = {
        (row.asset_id, row.event_id): row.thermal_stress
        for row in hidden_stress.itertuples()
    }
    condition = pairs["asset_id"].map(condition_by_asset).to_numpy(dtype=float)
    stress = np.array([stress_by_pair[(a, e)] for a, e in
                       zip(pairs["asset_id"], pairs["event_id"])], dtype=float)

    correlations = []
    for name in config.FEATURES:
        column = matrix[name].to_numpy(dtype=float)
        if column.std() == 0:
            correlations.append({"feature": name, "vs_condition": 0.0, "vs_thermal_stress": 0.0})
            continue
        correlations.append({
            "feature": name,
            "vs_condition": float(np.corrcoef(column, condition)[0, 1]),
            "vs_thermal_stress": float(np.corrcoef(column, stress)[0, 1]),
        })

    worst = max(
        max(abs(c["vs_condition"]), abs(c["vs_thermal_stress"])) for c in correlations
    )
    assert worst <= config.LEAKAGE_CORRELATION_MAX, (
        f"a feature correlates {worst:.4f} with hidden state, above "
        f"{config.LEAKAGE_CORRELATION_MAX}; that is leakage"
    )
    return correlations, worst


def bayes_ceiling(assets, hazard_table, flags, outcomes):
    """How close the model gets to the best any model could do.

    Ranking by the true generative probability is the ceiling: it uses the exact
    hidden state that produced the outcomes. Whatever it leaves on the table is
    irreducible — outcomes are Bernoulli draws at roughly 1%, so most of the
    variation is coin flip and no feature set can recover it.

    Without this, precision@15 of 0.079 reads as a weak model. Against the
    ceiling it reads as the problem being mostly noise.
    """
    hidden = pd.read_csv(config.DATA_DIR / "hidden_asset_state.csv")
    hidden_stress = pd.read_csv(config.DATA_DIR / "hidden_thermal_stress.csv")

    pairs, labels, groups = features.training_pairs(outcomes)
    matrix = features.build_feature_matrix(assets, hazard_table, flags, pairs)
    predicted = model.out_of_fold_predictions(matrix.to_numpy(), labels, groups)

    stress_by_pair = {(r.asset_id, r.event_id): r.thermal_stress
                      for r in hidden_stress.itertuples()}
    condition_by_asset = dict(zip(hidden["asset_id"], hidden["condition"]))
    load_by_asset = dict(zip(assets["asset_id"], assets["peak_load_pct"]))

    stress = np.array([stress_by_pair[(a, e)] for a, e in
                       zip(pairs["asset_id"], pairs["event_id"])])
    condition = pairs["asset_id"].map(condition_by_asset).to_numpy(dtype=float)
    load = pairs["asset_id"].map(load_by_asset).to_numpy(dtype=float)

    # Re-solved rather than stored, so it cannot drift from the generator.
    intercept = generate_data.solve_intercept(stress, condition, load)
    logits = generate_data.failure_logits(stress, condition, load, intercept)
    true_probability = 1.0 / (1.0 + np.exp(-logits))

    def summarise(scores):
        return {
            "pooled_auc": float(roc_auc_score(labels, scores)),
            "within_event_auc": model.within_event_auc(scores, labels, groups)[0],
            "precision_at_capacity": float(np.mean([
                labels[groups == e][np.argsort(-scores[groups == e])[:config.CREW_CAPACITY]].sum()
                / config.CREW_CAPACITY
                for e in pd.unique(groups) if labels[groups == e].sum() > 0
            ])),
        }

    return {"model": summarise(predicted), "ceiling": summarise(true_probability)}


def citation_integrity():
    """Pass rate across every brief, as a percentage."""
    total = 0
    passing = 0
    offenders = []
    for scenario_id, _, _, _, _, _ in config.SCENARIOS:
        path = config.OUTPUT_DIR / f"briefs_{scenario_id}.json"
        if not path.exists():
            return None
        for asset_id, record in json.loads(path.read_text()).items():
            total += 1
            retrieved = {hit["doc_id"] for hit in record["retrieved"]}
            cited = set(record["cited_doc_ids"])
            if cited <= retrieved:
                passing += 1
            else:
                offenders.append((scenario_id, asset_id, sorted(cited - retrieved)))
    return {"total": total, "passing": passing,
            "rate": 100.0 * passing / total if total else float("nan"),
            "offenders": offenders}


def write_extraction_eval(per_flag, by_category, checked, evidence_failures,
                          n_notes, n_failed, path):
    lines = [
        "# Extraction evaluation",
        "",
        f"Model: `{config.EXTRACTION_MODEL}`, prompt version `{config.PROMPT_VERSION}`.",
        f"Notes: {n_notes}. Compared against `data/inspection_truth.csv`.",
        "",
        "The keyword baseline is a deliberately naive comparator: per-flag term",
        "lists, case-insensitive substring match, no handling of a defect that",
        "was resolved or explicitly negated. The gap between the two is the",
        "value the extraction layer adds.",
        "",
        "## Per-flag precision and recall",
        "",
        "| Flag | Actual positives | LLM precision | LLM recall | Keyword precision | Keyword recall |",
        "|---|---|---|---|---|---|",
    ]
    for flag, result in per_flag.items():
        llm = result["llm"]
        base = result["baseline"]
        lines.append(
            f"| {config.FEATURE_LABELS['flag_' + flag]} | {llm['actual_positive']} | "
            f"{llm['precision']:.3f} | {llm['recall']:.3f} | "
            f"{base['precision']:.3f} | {base['recall']:.3f} |"
        )

    lines += [
        "",
        "## Errors by note category",
        "",
        "An error is one wrong flag decision; each note carries four.",
        "",
        "| Category | Notes | Flag decisions | LLM errors | LLM error rate | Keyword errors | Keyword error rate |",
        "|---|---|---|---|---|---|---|",
    ]
    for category in ["straightforward positive", "resolution present",
                     "negation present", "distractors only"]:
        if category not in by_category:
            continue
        row = by_category[category]
        lines.append(
            f"| {category} | {row['notes']} | {row['flag_decisions']} | "
            f"{row['llm_errors']} | {row['llm_error_rate']:.4f} | "
            f"{row['baseline_errors']} | {row['baseline_error_rate']:.4f} |"
        )

    lines += [
        "",
        "## Evidence and extraction failures",
        "",
        f"- Evidence quotes checked: {checked}",
        f"- Quotes not found verbatim in their note: {evidence_failures} "
        f"({100.0 * evidence_failures / checked if checked else 0:.2f}%)",
        f"- Extractions that failed after one retry: {n_failed} of {n_notes} "
        f"({100.0 * n_failed / n_notes:.2f}%)",
        "",
        "A failed extraction sets no flags and marks the asset",
        "`extraction_status = \"failed\"`; the interface then says the condition",
        "data is unavailable rather than showing four clean negatives.",
    ]
    path.write_text("\n".join(lines) + "\n")


def write_validation(correlations, worst, ceiling, citations, path):
    lines = [
        "# Validation",
        "",
        "## Leakage",
        "",
        f"Feature matrix columns equal `FEATURES` exactly, in order: {len(config.FEATURES)} columns.",
        "",
        "Hazard features derive from ambient temperature alone. `theta`, `tau`,",
        "the hourly load rise, `condition` and `thermal_stress` never enter the",
        "matrix. The three interaction columns are products of features already",
        "in it, so they cross no boundary the components did not already sit on:",
        "`peak_load_pct` and `age_years` come from the asset register, the",
        "condition flag count from the extracted notes, and the hazard terms from",
        "ambient temperature.",
        "The correlations below are computed against the hidden state that",
        "generated the outcomes, which only this module may read.",
        "",
        f"Highest absolute correlation with hidden state: **{worst:.4f}** "
        f"(threshold {config.LEAKAGE_CORRELATION_MAX}).",
        "",
        "| Feature | vs condition | vs thermal stress |",
        "|---|---|---|",
    ]
    for row in sorted(correlations,
                      key=lambda r: -max(abs(r["vs_condition"]), abs(r["vs_thermal_stress"]))):
        lines.append(
            f"| {config.FEATURE_LABELS[row['feature']]} | "
            f"{row['vs_condition']:+.4f} | {row['vs_thermal_stress']:+.4f} |"
        )

    lines += [
        "",
        "## Ranking quality against the Bayes ceiling",
        "",
        "Ranking by the true generative probability is the best any model could do:",
        "it uses the exact hidden state that produced the outcomes. Outcomes are",
        "Bernoulli draws at roughly 1%, so most of the remaining variation is",
        "irreducible.",
        "",
        "| Ranked by | Pooled AUC | Within-event AUC | Precision@15 |",
        "|---|---|---|---|",
        f"| The model (out-of-fold) | {ceiling['model']['pooled_auc']:.4f} | "
        f"{ceiling['model']['within_event_auc']:.4f} | "
        f"{ceiling['model']['precision_at_capacity']:.4f} |",
        f"| True generative probability | {ceiling['ceiling']['pooled_auc']:.4f} | "
        f"{ceiling['ceiling']['within_event_auc']:.4f} | "
        f"{ceiling['ceiling']['precision_at_capacity']:.4f} |",
        "",
        f"The model reaches "
        f"**{100 * ceiling['model']['within_event_auc'] / ceiling['ceiling']['within_event_auc']:.1f}%** "
        f"of the achievable within-event AUC and "
        f"**{100 * ceiling['model']['precision_at_capacity'] / ceiling['ceiling']['precision_at_capacity']:.1f}%** "
        f"of the achievable precision at the crew's capacity.",
        "",
        "## Citation integrity",
        "",
    ]
    if citations is None:
        lines.append("Briefs not present; run `retrieve.py` before validating citations.")
    else:
        lines.append(f"**{citations['rate']:.2f}%** of briefs cite only documents they were given "
                     f"({citations['passing']} of {citations['total']}).")
        if citations["offenders"]:
            lines.append("")
            lines.append("Briefs citing documents they were not given:")
            for scenario_id, asset_id, extra in citations["offenders"]:
                lines.append(f"- {scenario_id} / {asset_id}: {extra}")
    path.write_text("\n".join(lines) + "\n")


def main():
    inspections = features.load_inspections()
    assets = features.load_assets()
    events = features.load_events()
    hourly = features.load_hourly()
    outcomes = features.load_outcomes()
    truth = pd.read_csv(config.DATA_DIR / "inspection_truth.csv")

    extractions = extract.load_extractions(inspections)
    n_failed = int((extractions["extraction_status"] == "failed").sum())

    merged, per_flag, by_category = evaluate_extraction(inspections, truth, extractions)
    checked, evidence_failures = evidence_check(merged)

    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_extraction_eval(per_flag, by_category, checked, evidence_failures,
                          len(inspections), n_failed,
                          config.OUTPUT_DIR / "extraction_eval.md")

    flags = features.asset_condition_flags(extractions)
    hazard_table = features.build_hazard_table(events, hourly)
    correlations, worst = leakage_check(assets, hazard_table, flags, outcomes)
    ceiling = bayes_ceiling(assets, hazard_table, flags, outcomes)
    citations = citation_integrity()

    write_validation(correlations, worst, ceiling, citations, config.OUTPUT_DIR / "validation.md")

    print(f"extraction: {n_failed} failed of {len(inspections)}; "
          f"{evidence_failures} evidence quotes not verbatim of {checked} checked")
    print(f"leakage: highest |correlation| with hidden state {worst:.4f} "
          f"(threshold {config.LEAKAGE_CORRELATION_MAX})")
    print(f"ceiling: model reaches "
          f"{100 * ceiling['model']['within_event_auc'] / ceiling['ceiling']['within_event_auc']:.1f}% "
          f"of achievable within-event AUC, "
          f"{100 * ceiling['model']['precision_at_capacity'] / ceiling['ceiling']['precision_at_capacity']:.1f}% "
          f"of achievable precision@15")
    if citations:
        print(f"citations: {citations['rate']:.2f}% clean ({citations['passing']}/{citations['total']})")
    for flag, result in per_flag.items():
        print(f"  {flag}: LLM P={result['llm']['precision']:.3f} R={result['llm']['recall']:.3f} | "
              f"keyword P={result['baseline']['precision']:.3f} R={result['baseline']['recall']:.3f}")


if __name__ == "__main__":
    main()
