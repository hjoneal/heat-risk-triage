"""Build the 16-column feature matrix from the asset register, the weather and
the extracted condition flags.

The leakage boundary lives here. Hazard features derive from ambient temperature
alone; `theta`, `tau`, the hourly load rise and `condition` are never read by
this module.
Nothing in this file opens a file whose name begins with `hidden_`.
"""

from datetime import date

import numpy as np
import pandas as pd

import config


def load_assets():
    return pd.read_csv(config.DATA_DIR / "assets.csv")


def load_events():
    return pd.read_csv(config.DATA_DIR / "weather_events.csv")


def load_hourly():
    return pd.read_csv(config.DATA_DIR / "weather_hourly.csv")


def load_inspections():
    return pd.read_csv(config.DATA_DIR / "inspections.csv")


def load_outcomes():
    return pd.read_csv(config.DATA_DIR / "outcomes.csv")


def longest_run(flags):
    """Length of the longest unbroken run of True."""
    longest = 0
    current = 0
    for flag in flags:
        current = current + 1 if flag else 0
        longest = max(longest, current)
    return longest


def hazard_features(temps):
    """The four hazard features, from ambient temperature only.

    Overnight minimum is read from the first six hours of each day, where the
    diurnal curve bottoms out. Peak temperature alone would rank a short severe
    event above a long moderate one, which is the inversion the accumulated
    degree-hours and the warm-night count exist to correct.
    """
    days = temps.reshape(-1, config.HOURS_PER_DAY)
    overnight_mins = days[:, 0:config.OVERNIGHT_HOURS].min(axis=1)
    return {
        "peak_temp_c": float(temps.max()),
        "degree_hours_above_30": float(np.maximum(temps - config.DEGREE_HOUR_BASE_C, 0).sum()),
        "max_overnight_min_c": float(overnight_mins.max()),
        "consecutive_warm_nights": float(longest_run(overnight_mins > config.WARM_NIGHT_C)),
    }


def build_hazard_table(events, hourly):
    """One row of hazard features per event, historical and scenario alike."""
    rows = []
    for event in events.itertuples():
        temps = hourly.loc[hourly["event_id"] == event.event_id].sort_values("hour_index")["temp_c"].to_numpy()
        assert len(temps) == event.duration_days * config.HOURS_PER_DAY, \
            f"{event.event_id} has {len(temps)} hourly readings, expected a whole number of days"
        row = {"event_id": event.event_id, "event_duration_days": event.duration_days}
        row.update(hazard_features(temps))
        rows.append(row)
    return pd.DataFrame(rows)


def asset_condition_flags(extractions):
    """Collapse the per-note extractions into one condition record per asset.

    A flag is set when any of the asset's inspections recorded that defect as
    outstanding. The two notes per asset are independent observations of the same
    latent condition rather than a time series of a changing asset, so taking the
    union uses both; taking only the later one would discard half the evidence
    for no gain. See DECISIONS.md D-007.

    An asset whose extraction failed on any note carries `extraction_status` of
    "failed": the flags for that asset are not trustworthy and the interface says
    so rather than showing four clean falses.
    """
    records = {}
    for row in extractions.itertuples():
        record = records.setdefault(row.asset_id, {
            "asset_id": row.asset_id,
            "extraction_status": "ok",
            "evidence": [],
            **{f"flag_{flag}": 0.0 for flag in config.CONDITION_FLAGS},
        })

        if row.extraction_status == "failed":
            record["extraction_status"] = "failed"
            continue

        for flag in config.CONDITION_FLAGS:
            if getattr(row, f"{flag}_present"):
                record[f"flag_{flag}"] = 1.0
                evidence = getattr(row, f"{flag}_evidence")
                if isinstance(evidence, str) and evidence:
                    record["evidence"].append({
                        "flag": flag,
                        "inspection_id": row.inspection_id,
                        "date": row.inspection_date,
                        "text": evidence,
                    })

    return pd.DataFrame(list(records.values()))


def build_feature_matrix(assets, hazard_table, flags, pairs):
    """Assemble the feature matrix for a set of (asset_id, event_id) pairs.

    `pairs` is a DataFrame with those two columns; the returned matrix has one
    row per pair, in the same order, with columns exactly `config.FEATURES`.
    """
    events = pd.read_csv(config.DATA_DIR / "weather_events.csv")
    event_year = {
        event.event_id: date.fromisoformat(event.start_date).year
        for event in events.itertuples()
    }

    # Condition features describe the asset as recorded on the register, not as
    # it stood on the date of each historical event: the register holds a single
    # maintenance date and the notes are undated relative to past events. See
    # DECISIONS.md D-008.
    reference_date = max(
        date.fromisoformat(event.start_date)
        for event in events.itertuples() if not event.is_scenario
    )
    days_since_maintenance = {
        asset.asset_id: (reference_date - date.fromisoformat(asset.last_maintenance_date)).days
        for asset in assets.itertuples()
    }

    merged = (
        pairs
        .merge(assets, on="asset_id", how="left")
        .merge(hazard_table, on="event_id", how="left")
        .merge(flags, on="asset_id", how="left")
    )
    assert len(merged) == len(pairs), "join changed the row count"

    matrix = pd.DataFrame(index=merged.index)
    matrix["peak_temp_c"] = merged["peak_temp_c"]
    matrix["degree_hours_above_30"] = merged["degree_hours_above_30"]
    matrix["max_overnight_min_c"] = merged["max_overnight_min_c"]
    matrix["consecutive_warm_nights"] = merged["consecutive_warm_nights"]
    matrix["age_years"] = merged["event_id"].map(event_year) - merged["install_year"]
    matrix["cooling_type_ordinal"] = merged["cooling_type"].map(config.COOLING_TYPE_ORDINAL)
    matrix["peak_load_pct"] = merged["peak_load_pct"]
    matrix["days_since_maintenance"] = merged["asset_id"].map(days_since_maintenance)
    matrix["prior_heat_faults"] = merged["prior_heat_faults"]
    for flag in config.CONDITION_FLAGS:
        matrix[f"flag_{flag}"] = merged[f"flag_{flag}"]

    # Interactions. Every one is asset value x event value, which is what lets an
    # additive model in the log-odds represent a hazard that lands unevenly on the
    # fleet: without them the hazard block contributes the same number to every
    # asset in a scenario and the forecast cannot reorder the queue at all.
    #
    # Both sides are centred on a fixed training mean before multiplying. A raw
    # product is collinear enough with its own components to drive their
    # coefficients negative, which makes the asset page claim that heat lowered
    # an asset's risk; see config.INTERACTION_CENTRE_* and DECISIONS.md D-031.
    # The centres are constants rather than column means because a scenario
    # matrix holds a single event, whose own mean degree-hours is that event's
    # value — self-centring would zero the term for every asset.
    n_condition_flags = sum(merged[f"flag_{flag}"] for flag in config.CONDITION_FLAGS)
    degree_hours = merged["degree_hours_above_30"] - config.INTERACTION_CENTRE_DEGREE_HOURS
    matrix["load_x_degree_hours"] = (
        merged["peak_load_pct"] - config.INTERACTION_CENTRE_PEAK_LOAD_PCT) * degree_hours
    matrix["condition_x_degree_hours"] = (
        n_condition_flags - config.INTERACTION_CENTRE_CONDITION_FLAGS) * degree_hours
    matrix["age_x_warm_nights"] = (
        (matrix["age_years"] - config.INTERACTION_CENTRE_AGE_YEARS)
        * (merged["consecutive_warm_nights"] - config.INTERACTION_CENTRE_WARM_NIGHTS))

    # The leakage assertion. Columns equal FEATURES exactly, in order, no nulls.
    assert list(matrix.columns) == config.FEATURES, \
        f"feature matrix columns are {list(matrix.columns)}, expected {config.FEATURES}"
    assert not matrix.isnull().any().any(), \
        f"feature matrix has nulls in {list(matrix.columns[matrix.isnull().any()])}"
    assert (matrix["age_years"] > 0).all(), "an asset was installed after an event it is scored on"

    return matrix.astype(float)


def training_pairs(outcomes):
    """Every asset-event row, with its labels, in a fixed order."""
    pairs = outcomes[["asset_id", "event_id"]].copy()
    labels = outcomes["failed"].to_numpy()
    groups = outcomes["event_id"].to_numpy()
    return pairs, labels, groups


def scenario_pairs(assets, scenario_id):
    """Every asset scored against one demo scenario."""
    return pd.DataFrame({
        "asset_id": assets["asset_id"],
        "event_id": scenario_id,
    })
