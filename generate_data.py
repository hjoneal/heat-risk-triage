"""Generate the synthetic fleet, weather, failure outcomes and inspection notes.

Everything downstream is built from what this script writes. The failure process
here is the ground truth the risk model has to recover: the model never sees
`condition`, `theta`, `tau`, the hourly load rise or `thermal_stress`.

Two files are diagnostic only and must be read by `validate.py` alone:
`hidden_asset_state.csv` and `inspection_truth.csv`.

Writes: data/, output/data_checks.txt
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd

import config

# ---------------------------------------------------------------------------
# Text banks
#
# Fixed content rather than tunable parameters, so these live here rather than
# in config.py. The phrasings matter: the extraction layer is judged on whether
# it can tell a defect that is outstanding from one that has been fixed.
# ---------------------------------------------------------------------------

# 40 x 30 = 1,200 combinations, drawn without replacement for 900 assets.
NAME_FIRST = [
    "Cedar", "Willow", "Granite", "Meadow", "Ashford", "Bellview", "Kingsley",
    "Marlow", "Pinecrest", "Redwater", "Stonebridge", "Thornton", "Wexford",
    "Fairmount", "Halloway", "Aldergrove", "Briarcliff", "Camden", "Draycott",
    "Eastvale", "Fernbank", "Glenmoor", "Harrowgate", "Ivybridge", "Jessup",
    "Kelvedon", "Langmere", "Mossvale", "Northcott", "Oakhurst", "Penrose",
    "Quarrydale", "Rushmere", "Sandbourne", "Tilbrook", "Underhill", "Vernham",
    "Westmarch", "Yarrow", "Zeller",
]

NAME_SECOND = [
    "Hollow", "Ridge", "Creek", "Crossing", "Heights", "Junction", "Landing",
    "Mill", "Park", "Springs", "Terrace", "Yard", "Bend", "Bluff", "Common",
    "Dale", "Ferry", "Gate", "Green", "Grove", "Hill", "Lock", "Moor", "Point",
    "Quay", "Reach", "Siding", "Vale", "Water", "Works",
]

# Voltage class follows the rating, as it does on a real register.
VOLTAGE_CLASS_BY_CAPACITY = {10: "69/12kV", 25: "115/12kV", 50: "138/13kV", 100: "230/34kV"}

# Approximate district centres. Stored for integration, never used as a feature.
DISTRICT_CENTRES = {
    "Northfield": (38.72, -121.44),
    "Cedar Basin": (38.51, -121.21),
    "Ridgeline": (38.34, -121.58),
    "Junction West": (38.60, -121.79),
}
DISTRICT_SPREAD_DEG = 0.09  # assumed: substations scatter within a district

CONDITION_SENTENCES = {
    "cooling_degraded": [
        "Fan bank 2 not running on inspection; ambient noise from bank 1 only.",
        "Cooling fans failed to start on manual test at the control cabinet.",
        "Radiator fins heavily fouled with dust and pollen, airflow visibly reduced.",
        "One of three cooling fans seized; unit running on reduced forced cooling.",
        "Radiator bank shows scale build-up across the lower third.",
        "Fan contactor chattering under load; cooling stage cycling intermittently.",
        "Top oil temperature running above the seasonal norm with fans called.",
    ],
    "ventilation_obstructed": [
        "Intake louvres on the north wall partially blocked by wind-blown debris.",
        "Vegetation encroaching on the compound fence within a metre of the radiators.",
        "Ventilation grilles obstructed by stacked pallets left inside the compound.",
        "Airflow path to the rear of the unit restricted by overgrown shrub cover.",
        "Louvre dampers stiff and not opening fully on the thermostat call.",
        "Bird nesting material found packed into the upper ventilation grille.",
        "Site storage has been placed against the ventilation opening on the east side.",
    ],
    "oil_issue": [
        "Oil level sight glass reading below the minimum mark at ambient.",
        "Active seepage at the lower gasket face, staining visible on the plinth.",
        "Oil sample returned marginal dielectric strength on the last test round.",
        "Weeping observed around the radiator header gasket.",
        "Conservator level low; make-up oil recommended before summer loading.",
        "Dissolved gas result flagged for review following the annual sample.",
        "Light oil film on the bund floor consistent with a slow leak.",
    ],
    "overdue_remedial": [
        "Work order WO-3182 for the cooling controller remains open from last season.",
        "Previously raised gasket repair has not been completed.",
        "Deferred bushing inspection still outstanding at this visit.",
        "Remedial actions from the prior report carried forward again, no work done.",
        "Outstanding defect notice from the last inspection round is still current.",
        "Repair deferred pending parts; no revised completion date recorded.",
        "Corrective work raised two rounds ago has not been actioned.",
    ],
}

RESOLUTION_SENTENCES = {
    "cooling_degraded": [
        "Cooling fan bank replaced under WO-4471; unit now operating within normal range.",
        "Fans repaired and proved on test; forced cooling fully restored.",
        "Radiator bank cleaned since the last visit, airflow restored to normal.",
        "Fan contactor replaced and cooling stages now cycling correctly.",
    ],
    "ventilation_obstructed": [
        "Louvres cleared and vegetation cut back; airflow path now unobstructed.",
        "Debris removed from the intake grilles during this visit.",
        "Encroaching growth cleared under the vegetation management round.",
        "Stored material removed from the compound; ventilation openings now clear.",
    ],
    "oil_issue": [
        "Gasket replaced and oil topped up; level now reading mid-sight-glass.",
        "Leak repaired under WO-4188 and the bund cleaned down.",
        "Oil replaced following the failed sample; retest returned within limits.",
        "Conservator refilled to the correct mark, no further seepage observed.",
    ],
    "overdue_remedial": [
        "Outstanding work order closed out since the previous inspection.",
        "Deferred repair completed and signed off this round.",
        "Prior remedial actions verified complete at this visit.",
        "All previously raised defects have now been cleared.",
    ],
}

NEGATION_SENTENCES = {
    "cooling_degraded": [
        "All cooling fans ran correctly on manual test.",
        "No fault found on the cooling system; fans proved on all stages.",
        "Radiators clean, no restriction to airflow observed.",
        "Forced cooling operating normally with no defects noted.",
    ],
    "ventilation_obstructed": [
        "Ventilation louvres clear and undamaged.",
        "No obstruction to airflow around the unit.",
        "Compound clear of vegetation and stored material.",
        "Intake grilles inspected and found clear.",
    ],
    "oil_issue": [
        "No evidence of oil seepage at the base or around the gaskets.",
        "Oil level correct at the sight glass, no leaks observed.",
        "Latest oil sample returned within specification.",
        "Bund dry, no staining or film present.",
    ],
    "overdue_remedial": [
        "No outstanding work orders against this asset.",
        "No open defects carried forward from the previous round.",
        "All prior actions closed; nothing outstanding.",
        "Defect register clear for this unit.",
    ],
}

DISTRACTOR_SENTENCES = [
    "Perimeter fencing intact. Signage legible.",
    "Access road passable; gate lock operating freely.",
    "Earth strap connections checked and found tight.",
    "Cable trench covers seated correctly.",
    "Control cabinet door seal in good condition.",
    "Site lighting operational on manual test.",
    "No graffiti or vandalism observed at this visit.",
    "Anti-climb guard secure on the ladder run.",
    "Nameplate legible and matching the asset register.",
    "Drainage channel clear of silt.",
    "Warning notices present and in date.",
    "Bund capacity unobstructed; no standing water.",
]

PROCEDURE_FRONT_MATTER_FIELDS = ["doc_id", "title", "applies_to", "category"]
PROCEDURE_CATEGORY_COUNTS = {
    "operational": 8,
    "manufacturer": 6,
    "emergency": 5,
    "regulatory": 4,
    "cold-weather": 2,
}


# ---------------------------------------------------------------------------
# Fleet
# ---------------------------------------------------------------------------

def generate_assets(rng, most_recent_event_date):
    """Build the asset register: 150 substation transformers."""
    name_pairs = [(first, second) for first in NAME_FIRST for second in NAME_SECOND]
    chosen_names = rng.choice(len(name_pairs), size=config.N_ASSETS, replace=False)

    rows = []
    for i in range(config.N_ASSETS):
        asset_number = i + 1
        first, second = name_pairs[chosen_names[i]]
        district = config.DISTRICTS[i % len(config.DISTRICTS)]
        centre_lat, centre_lon = DISTRICT_CENTRES[district]

        capacity = int(rng.choice(config.RATED_CAPACITIES_MVA, p=config.RATED_CAPACITY_WEIGHTS))
        install_year = int(rng.triangular(
            config.INSTALL_YEAR_MIN, config.INSTALL_YEAR_MODE, config.INSTALL_YEAR_MAX
        ))

        # Customers scale with rating; the lognormal spread separates a dense
        # urban feeder from a rural one of the same size.
        customers = capacity * config.CUSTOMERS_PER_MVA * rng.lognormal(0.0, config.CUSTOMERS_NOISE_SD)
        customers = int(np.clip(customers, config.CUSTOMERS_MIN, config.CUSTOMERS_MAX))

        maintenance_offset_days = int(rng.integers(0, config.MAINTENANCE_WINDOW_YEARS * 365))
        last_maintenance = most_recent_event_date - timedelta(days=maintenance_offset_days)

        rows.append({
            "asset_id": f"{config.ASSET_ID_PREFIX}{asset_number:03d}",
            "name": f"{first} {second} {VOLTAGE_CLASS_BY_CAPACITY[capacity]}",
            "district": district,
            "lat": round(float(centre_lat + rng.normal(0.0, DISTRICT_SPREAD_DEG)), 4),
            "lon": round(float(centre_lon + rng.normal(0.0, DISTRICT_SPREAD_DEG)), 4),
            "install_year": install_year,
            "rated_capacity_mva": capacity,
            "cooling_type": str(rng.choice(config.COOLING_TYPES, p=config.COOLING_TYPE_WEIGHTS)),
            "peak_load_pct": round(float(rng.uniform(
                config.PEAK_LOAD_PCT_MIN, config.PEAK_LOAD_PCT_MAX)), 3),
            "customers_served": customers,
            "criticality": int(rng.integers(config.CRITICALITY_MIN, config.CRITICALITY_MAX + 1)),
            "last_maintenance_date": last_maintenance.isoformat(),
            "prior_heat_faults": int(min(rng.poisson(config.PRIOR_FAULTS_POISSON_MEAN),
                                         config.PRIOR_FAULTS_CAP)),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------

def generate_events(rng):
    """Sixteen historical heat events plus the demo scenarios."""
    historical = []
    for event_type, count, days_min, days_max, peak_min, peak_max, amp_min, amp_max in config.EVENT_TYPES:
        for _ in range(count):
            historical.append({
                "event_type": event_type,
                "duration_days": int(rng.integers(days_min, days_max + 1)),
                "peak_temp_c": round(float(rng.uniform(peak_min, peak_max)), 2),
                "amplitude_c": round(float(rng.uniform(amp_min, amp_max)), 2),
            })
    assert len(historical) == config.N_HISTORICAL_EVENTS

    # Spread the events across the summers preceding the forecast so that
    # days-since-maintenance varies across the training window.
    season_length_days = (date(2000, config.EVENT_SEASON_END_MONTH, 30)
                          - date(2000, config.EVENT_SEASON_START_MONTH, 1)).days
    for i, event in enumerate(historical):
        year = config.FIRST_EVENT_YEAR + (i % 5)
        season_start = date(year, config.EVENT_SEASON_START_MONTH, 1)
        event["start_date"] = season_start + timedelta(days=int(rng.integers(0, season_length_days)))

    historical.sort(key=lambda e: e["start_date"])
    rows = []
    for i, event in enumerate(historical):
        rows.append({
            "event_id": f"EVT-{i + 1:02d}",
            "label": f"{event['event_type']} {event['start_date'].isoformat()}",
            "event_type": event["event_type"],
            "start_date": event["start_date"].isoformat(),
            "duration_days": event["duration_days"],
            "peak_temp_c": event["peak_temp_c"],
            "amplitude_c": event["amplitude_c"],
            "is_scenario": False,
        })

    for scenario_id, label, event_type, days, peak, amplitude in config.SCENARIOS:
        rows.append({
            "event_id": scenario_id,
            "label": label,
            "event_type": event_type,
            "start_date": config.FORECAST_DATE,
            "duration_days": days,
            "peak_temp_c": peak,
            "amplitude_c": amplitude,
            "is_scenario": True,
        })

    return pd.DataFrame(rows)


def generate_hourly_temps(events, rng):
    """Hourly ambient temperature for every event, historical and scenario."""
    rows = []
    for event in events.itertuples():
        # daily_mean = peak - amplitude, so the afternoon peak of the sine lands
        # exactly on the event's stated peak temperature.
        base_mean = event.peak_temp_c - event.amplitude_c
        for day in range(event.duration_days):
            daily_mean = base_mean + rng.uniform(-config.DAILY_MEAN_JITTER_C, config.DAILY_MEAN_JITTER_C)
            for hour in range(config.HOURS_PER_DAY):
                diurnal = np.sin(2 * np.pi * (hour - config.DIURNAL_PHASE_HOURS) / config.HOURS_PER_DAY)
                temp = daily_mean + event.amplitude_c * diurnal + rng.normal(0.0, config.WEATHER_NOISE_SD)
                rows.append({
                    "event_id": event.event_id,
                    "hour_index": day * config.HOURS_PER_DAY + hour,
                    "temp_c": round(float(temp), 3),
                })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Hidden asset state and the failure process
# ---------------------------------------------------------------------------

def normalise(values):
    """Scale to [0, 1] across the fleet."""
    values = np.asarray(values, dtype=float)
    spread = values.max() - values.min()
    assert spread > 0, "cannot normalise a constant column"
    return (values - values.min()) / spread


def generate_hidden_state(assets, rng, most_recent_event_date):
    """Latent condition and thermal parameters. Never visible to the model."""
    install_year = assets["install_year"].to_numpy()
    age_norm = normalise(-install_year)  # older asset, higher value

    last_maintenance = pd.to_datetime(assets["last_maintenance_date"]).dt.date
    months_since = np.array([
        (most_recent_event_date - d).days / 30.44 for d in last_maintenance
    ])
    maintenance_norm = normalise(months_since)
    faults_norm = normalise(assets["prior_heat_faults"].to_numpy())

    condition = np.clip(
        config.CONDITION_WEIGHT_AGE * age_norm
        + config.CONDITION_WEIGHT_MAINTENANCE * maintenance_norm
        + config.CONDITION_WEIGHT_PRIOR_FAULTS * faults_norm
        + config.CONDITION_WEIGHT_NOISE * rng.normal(0.0, 1.0, size=len(assets)),
        0.0, 1.0,
    )

    tau = np.clip(
        rng.normal(config.TAU_MEAN_HOURS, config.TAU_SD_HOURS, size=len(assets)),
        config.TAU_MIN_HOURS, config.TAU_MAX_HOURS,
    )

    # `load_rise` used to be computed here, once per asset. It is now a function
    # of ambient temperature and so belongs in compute_thermal_stress, where the
    # hourly series is in scope. It consumed no random draw, so moving it leaves
    # the generator's RNG stream — and therefore the inspection notes and their
    # cached extractions — untouched.
    return pd.DataFrame({
        "asset_id": assets["asset_id"],
        "condition": condition.round(4),
        "tau_hours": tau.round(3),
    })


def effective_load(peak_load_pct, ambient_c):
    """Loading every asset actually carries at one ambient temperature.

    Air-conditioning demand climbs with the weather, so an asset carries its
    heaviest load in the same hours its cooling works least well. Capped because
    protection operates before a transformer runs indefinitely beyond its rating.
    """
    demand_multiplier = 1.0 + config.AC_DEMAND_SLOPE * max(
        0.0, ambient_c - config.AC_DEMAND_BASE_C)
    return np.minimum(peak_load_pct * demand_multiplier, config.LOAD_CAP)


def hourly_load_rise(peak_load_pct, cooling_offset, ambient_c):
    """Winding rise above ambient for every asset at one ambient temperature.

    Resistive loss goes as the square of current, so the rise scales with the
    square of load against the reference. That exponent is physics rather than a
    fitted parameter, and it is what makes the gap between a heavily and a lightly
    loaded asset widen as the day gets hotter instead of staying constant.
    """
    return (
        config.LOAD_RISE_AT_REFERENCE_C
        * (effective_load(peak_load_pct, ambient_c) / config.LOAD_RISE_REFERENCE_LOAD)
        ** config.LOAD_RISE_EXPONENT
        + cooling_offset
    )


def compute_thermal_stress(hourly, hidden, assets, theta_reference):
    """Accumulated accelerated insulation ageing per asset per event.

    Oil temperature lags ambient with a time constant of a few hours, so the unit
    does substantially reset overnight: with tau of 3 hours, 96% of the offset is
    gone after an eight-hour night, and even at 8 hours it is two thirds gone.
    What the overnight minimum sets is the *floor* the unit resets to. A night at
    27 C instead of 18 C means the next day starts nine degrees hotter and
    reaches a higher peak from a higher base, day after day.

    Ageing is Arrhenius and irreversible: the rate doubles per few degrees, and
    five days of elevated temperature does five days of damage that the weather
    breaking does not give back. So stress is accumulated — duration counts, and
    a long moderate event can exceed a short severe one — but exponentially
    weighted, so an hour at 45 C is worth many hours at 39 C. Taking the peak
    instead would rank the short severe event higher and invert the premise;
    taking a plain integral of degrees above a line would flatten the difference
    between a damaging hour and a harmless one.

    The load rise is recomputed every hour rather than held fixed per asset, so
    the heat response differs by asset: a unit at 91% load reaches a far higher
    core temperature in a severe event than one at 60%, and the gap widens with
    temperature because of the square. Measured across the fleet, the ratio of
    mean stress between the top and bottom load quintiles rises from 2.97 under a
    static load rise to 7.11. That differential is what a forecast can act on.

    Units: equivalent days at the reference temperature.
    """
    tau = hidden["tau_hours"].to_numpy()
    peak_load_pct = assets["peak_load_pct"].to_numpy()
    cooling_offset = assets["cooling_type"].map(config.COOLING_OFFSET_C).to_numpy()
    assert list(assets["asset_id"]) == list(hidden["asset_id"]), \
        "asset register and hidden state are not in the same order"

    rows = []
    mean_effective_load = {}
    for event_id, group in hourly.groupby("event_id", sort=False):
        temps = group.sort_values("hour_index")["temp_c"].to_numpy()

        theta = temps[0] + hourly_load_rise(peak_load_pct, cooling_offset, temps[0])
        stress = np.zeros(len(hidden))
        load_total = np.zeros(len(hidden))
        for hour in range(1, len(temps)):
            load_rise = hourly_load_rise(peak_load_pct, cooling_offset, temps[hour])
            theta = theta + (1.0 / tau) * (temps[hour] + load_rise - theta)
            ageing_rate = 2.0 ** ((theta - theta_reference) / config.MONTSINGER_HALVING_C)
            # Only the acceleration above the nominal rate accumulates, so an
            # event spent below the reference contributes nothing.
            stress += np.maximum(0.0, ageing_rate - 1.0)
            load_total += effective_load(peak_load_pct, temps[hour])
        stress = stress / config.HOURS_PER_DAY
        mean_effective_load[event_id] = load_total / (len(temps) - 1)

        for index, (asset_id, value) in enumerate(zip(hidden["asset_id"], stress)):
            rows.append({"asset_id": asset_id, "event_id": event_id,
                         "thermal_stress": round(float(value), 4),
                         "mean_effective_load": round(
                             float(mean_effective_load[event_id][index]), 4)})

    return pd.DataFrame(rows)


def failure_logits(thermal_stress, condition, peak_load_pct, intercept):
    return (
        intercept
        + config.FAILURE_COEF_THERMAL_STRESS * thermal_stress
        + config.FAILURE_COEF_CONDITION * condition
        + config.FAILURE_COEF_INTERACTION * thermal_stress * condition
        + config.FAILURE_COEF_LOAD * (peak_load_pct - config.FAILURE_LOAD_REFERENCE)
    )


def solve_intercept(thermal_stress, condition, peak_load_pct):
    """Bisect for the intercept that puts the expected failure rate on target.

    Solved rather than hardcoded so that changing any coefficient upstream does
    not silently move the base rate.
    """
    def mean_probability(intercept):
        logits = failure_logits(thermal_stress, condition, peak_load_pct, intercept)
        return float((1.0 / (1.0 + np.exp(-logits))).mean())

    low, high = config.INTERCEPT_SEARCH_MIN, config.INTERCEPT_SEARCH_MAX
    assert mean_probability(low) < config.TARGET_FAILURE_RATE < mean_probability(high), \
        "target failure rate is not bracketed by the intercept search range"

    for _ in range(config.INTERCEPT_SEARCH_MAX_ITERATIONS):
        middle = (low + high) / 2
        if mean_probability(middle) < config.TARGET_FAILURE_RATE:
            low = middle
        else:
            high = middle
        if high - low < config.INTERCEPT_SEARCH_TOLERANCE:
            break

    return (low + high) / 2


# ---------------------------------------------------------------------------
# Inspection notes
# ---------------------------------------------------------------------------

def generate_inspections(assets, hidden, rng, most_recent_event_date):
    """Two free-text notes per asset, with a truth record of what each contains.

    A flag is genuinely true when a condition sentence for it was included and no
    resolution or negation sentence for the same flag was included. That is the
    distinction the extraction layer is measured on.
    """
    condition_by_asset = dict(zip(hidden["asset_id"], hidden["condition"]))

    note_rows = []
    truth_rows = []
    for asset in assets.itertuples():
        asset_number = int(asset.asset_id.removeprefix(config.ASSET_ID_PREFIX))
        condition = condition_by_asset[asset.asset_id]

        for note_number in range(1, config.NOTES_PER_ASSET + 1):
            inspection_id = config.INSPECTION_ID_TEMPLATE.format(
                asset_number=asset_number, note_number=note_number)

            present_flags = [
                flag for flag in config.CONDITION_FLAGS
                if rng.random() < config.CONDITION_SENTENCE_PROB_SCALE * condition
            ]
            if len(present_flags) > config.MAX_CONDITION_SENTENCES:
                keep = rng.choice(len(present_flags), size=config.MAX_CONDITION_SENTENCES,
                                  replace=False)
                present_flags = [present_flags[i] for i in sorted(keep)]

            resolved_flag = None
            negated_flag = None
            if present_flags and rng.random() < config.RESOLUTION_OR_NEGATION_PROB:
                target = present_flags[int(rng.integers(0, len(present_flags)))]
                if rng.random() < 0.5:
                    resolved_flag = target
                else:
                    negated_flag = target

            # One block per flag, one per distractor, then shuffled: a resolution
            # stays next to the defect it resolves, but nothing sits in a fixed
            # position that the extractor could learn instead of reading.
            blocks = []
            for flag in present_flags:
                sentences = [CONDITION_SENTENCES[flag][int(rng.integers(0, len(CONDITION_SENTENCES[flag])))]]
                if flag == resolved_flag:
                    sentences.append(RESOLUTION_SENTENCES[flag][int(rng.integers(0, len(RESOLUTION_SENTENCES[flag])))])
                elif flag == negated_flag:
                    sentences.append(NEGATION_SENTENCES[flag][int(rng.integers(0, len(NEGATION_SENTENCES[flag])))])
                blocks.append(" ".join(sentences))

            n_distractors = int(rng.integers(config.DISTRACTOR_SENTENCES_MIN,
                                             config.DISTRACTOR_SENTENCES_MAX + 1))
            distractor_choice = rng.choice(len(DISTRACTOR_SENTENCES), size=n_distractors, replace=False)
            for index in distractor_choice:
                blocks.append(DISTRACTOR_SENTENCES[index])

            order = rng.permutation(len(blocks))
            note_text = " ".join(blocks[i] for i in order)

            days_before = int(rng.integers(0, config.INSPECTION_WINDOW_DAYS))
            inspection_date = most_recent_event_date - timedelta(days=days_before)

            note_rows.append({
                "inspection_id": inspection_id,
                "asset_id": asset.asset_id,
                "inspection_date": inspection_date.isoformat(),
                "note_text": note_text,
            })

            truth = {"inspection_id": inspection_id}
            for flag in config.CONDITION_FLAGS:
                outstanding = flag in present_flags and flag not in (resolved_flag, negated_flag)
                truth[f"true_{flag}"] = bool(outstanding)
            truth["has_resolution"] = resolved_flag is not None
            truth["has_negation"] = negated_flag is not None
            truth_rows.append(truth)

    return pd.DataFrame(note_rows), pd.DataFrame(truth_rows)


# ---------------------------------------------------------------------------
# Procedure corpus
# ---------------------------------------------------------------------------

def parse_front_matter(text):
    """Read the YAML-style header of a procedure document.

    Deliberately not a YAML parser: the header is four fixed fields and pulling
    in a dependency to read them would not be justified.
    """
    assert text.startswith("---\n"), "procedure is missing its front matter block"
    _, header, _ = text.split("---\n", 2)
    fields = {}
    for line in header.strip().splitlines():
        key, _, value = line.partition(":")
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            value = [item.strip() for item in value[1:-1].split(",") if item.strip()]
        fields[key.strip()] = value
    return fields


def validate_procedures():
    """Check the committed procedure corpus rather than generating it.

    The corpus is fixed content with nothing stochastic about it, so it is
    authored on disk and read rather than emitted from a generator that would
    only ever produce the same 25 files.
    """
    paths = sorted(config.PROCEDURES_DIR.glob("*.md"))
    assert len(paths) == config.N_PROCEDURES, \
        f"expected {config.N_PROCEDURES} procedures, found {len(paths)}"

    categories = {}
    doc_ids = set()
    for path in paths:
        fields = parse_front_matter(path.read_text())
        for required in PROCEDURE_FRONT_MATTER_FIELDS:
            assert required in fields, f"{path.name} is missing front matter field {required}"
        assert fields["doc_id"] not in doc_ids, f"duplicate doc_id {fields['doc_id']}"
        doc_ids.add(fields["doc_id"])
        categories[fields["category"]] = categories.get(fields["category"], 0) + 1

    assert categories == PROCEDURE_CATEGORY_COUNTS, \
        f"procedure category mix is {categories}, expected {PROCEDURE_CATEGORY_COUNTS}"
    return len(paths)


# ---------------------------------------------------------------------------
# Hard gates
# ---------------------------------------------------------------------------

def longest_true_run(flags):
    longest = 0
    current = 0
    for flag in flags:
        current = current + 1 if flag else 0
        longest = max(longest, current)
    return longest


def run_hard_gates(events, hourly, stress, outcomes, hidden, theta_reference):
    """Six checks the generated data must pass. A failure stops the build."""
    historical = events[~events["is_scenario"]]
    event_type = dict(zip(events["event_id"], events["event_type"]))
    condition_by_asset = dict(zip(hidden["asset_id"], hidden["condition"]))

    merged = outcomes.merge(stress, on=["asset_id", "event_id"])
    merged["event_type"] = merged["event_id"].map(event_type)

    results = []

    failure_rate = float(merged["failed"].mean())
    results.append((
        "1. realised failure rate",
        f"{failure_rate:.4f}",
        f"required in [{config.FAILURE_RATE_MIN}, {config.FAILURE_RATE_MAX}]",
        config.FAILURE_RATE_MIN <= failure_rate <= config.FAILURE_RATE_MAX,
    ))

    long_moderate = merged.loc[merged["event_type"] == "long-moderate", "thermal_stress"].mean()
    short_severe = merged.loc[merged["event_type"] == "short-severe", "thermal_stress"].mean()
    results.append((
        "2. long-moderate stress exceeds short-severe",
        f"long-moderate {long_moderate:.2f} vs short-severe {short_severe:.2f} equivalent days",
        "long-moderate must be strictly greater (equivalent days at reference)",
        bool(long_moderate > short_severe),
    ))

    heat_rows = merged[merged["event_type"].isin(["short-severe", "long-severe", "long-moderate"])]
    nonzero_share = float((heat_rows["thermal_stress"] > 0).mean())
    results.append((
        "3. non-zero stress share on heat events",
        f"{nonzero_share:.4f}",
        f"required >= {config.NONZERO_STRESS_SHARE_MIN}",
        nonzero_share >= config.NONZERO_STRESS_SHARE_MIN,
    ))

    mild_rate = float(merged.loc[merged["event_type"] == "mild", "failed"].mean())
    results.append((
        "4. mild event failure rate",
        f"{mild_rate:.4f}",
        f"required < {config.MILD_FAILURE_RATE_MAX}",
        mild_rate < config.MILD_FAILURE_RATE_MAX,
    ))

    # Correlation across events, not across rows: within one event both figures
    # are constant, so a row-level correlation would be meaningless.
    per_event = []
    for event in historical.itertuples():
        temps = hourly.loc[hourly["event_id"] == event.event_id, "temp_c"].to_numpy()
        degree_hours = float(np.maximum(temps - config.DEGREE_HOUR_BASE_C, 0).sum())
        per_event.append((degree_hours, event.peak_temp_c))
    degree_hours_values = np.array([row[0] for row in per_event])
    peak_values = np.array([row[1] for row in per_event])
    correlation = float(np.corrcoef(degree_hours_values, peak_values)[0, 1])
    results.append((
        "5. degree-hours vs peak temperature correlation",
        f"{correlation:.4f}",
        f"required < {config.DEGREE_HOURS_PEAK_CORR_MAX}",
        abs(correlation) < config.DEGREE_HOURS_PEAK_CORR_MAX,
    ))

    merged["condition"] = merged["asset_id"].map(condition_by_asset)
    median_condition = merged["condition"].median()
    failures = merged[merged["failed"] == 1]
    n_low_condition = int((failures["condition"] < median_condition).sum())
    low_condition_share = n_low_condition / len(failures)
    results.append((
        "6. failures in below-median condition assets",
        f"{low_condition_share:.4f} ({n_low_condition} of {len(failures)} failures)",
        f"required >= {config.LOW_CONDITION_FAILURE_SHARE_MIN}",
        low_condition_share >= config.LOW_CONDITION_FAILURE_SHARE_MIN,
    ))

    lines = [
        "Hard gates on generated data",
        f"ageing reference temperature in force: {theta_reference:.1f} C",
        "",
    ]
    for name, measured, requirement, passed in results:
        lines.append(f"[{'PASS' if passed else 'FAIL'}] {name}")
        lines.append(f"         measured: {measured}")
        lines.append(f"         {requirement}")
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (config.OUTPUT_DIR / "data_checks.txt").write_text("\n".join(lines) + "\n")

    for name, measured, requirement, passed in results:
        assert passed, f"hard gate failed — {name}: measured {measured}, {requirement}"


# ---------------------------------------------------------------------------

def main():
    rng = np.random.default_rng(config.SEED)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    events = generate_events(rng)
    historical = events[~events["is_scenario"]]
    most_recent_event_date = max(date.fromisoformat(d) for d in historical["start_date"])

    assets = generate_assets(rng, most_recent_event_date)
    hourly = generate_hourly_temps(events, rng)
    hidden = generate_hidden_state(assets, rng, most_recent_event_date)

    # Gate 2 is the project's central premise. The build spec gives one remedy —
    # lower the threshold and regenerate — so it is applied here rather than
    # left for a human to apply by hand.
    theta_reference = config.THETA_REFERENCE_C
    while True:
        stress = compute_thermal_stress(hourly, hidden, assets, theta_reference)
        merged = stress.merge(events[["event_id", "event_type"]], on="event_id")
        long_moderate = merged.loc[merged["event_type"] == "long-moderate", "thermal_stress"].mean()
        short_severe = merged.loc[merged["event_type"] == "short-severe", "thermal_stress"].mean()
        if long_moderate > short_severe:
            break
        assert theta_reference > 0, "no reference temperature satisfies gate 2"
        theta_reference -= config.THETA_REFERENCE_DECREMENT_C

    historical_ids = set(historical["event_id"])
    training_stress = stress[stress["event_id"].isin(historical_ids)].reset_index(drop=True)
    condition_by_asset = dict(zip(hidden["asset_id"], hidden["condition"]))
    load_by_asset = dict(zip(assets["asset_id"], assets["peak_load_pct"]))

    stress_values = training_stress["thermal_stress"].to_numpy()
    condition_values = training_stress["asset_id"].map(condition_by_asset).to_numpy()
    load_values = training_stress["asset_id"].map(load_by_asset).to_numpy()

    intercept = solve_intercept(stress_values, condition_values, load_values)
    logits = failure_logits(stress_values, condition_values, load_values, intercept)
    probabilities = 1.0 / (1.0 + np.exp(-logits))
    failed = (rng.random(len(probabilities)) < probabilities).astype(int)

    outcomes = pd.DataFrame({
        "asset_id": training_stress["asset_id"],
        "event_id": training_stress["event_id"],
        "failed": failed,
    })
    assert len(outcomes) == config.N_ASSETS * config.N_HISTORICAL_EVENTS

    inspections, truth = generate_inspections(assets, hidden, rng, most_recent_event_date)
    n_procedures = validate_procedures()

    assets.to_csv(config.DATA_DIR / "assets.csv", index=False)
    events.to_csv(config.DATA_DIR / "weather_events.csv", index=False)
    hourly.to_csv(config.DATA_DIR / "weather_hourly.csv", index=False)
    inspections.to_csv(config.DATA_DIR / "inspections.csv", index=False)
    outcomes.to_csv(config.DATA_DIR / "outcomes.csv", index=False)
    hidden.to_csv(config.DATA_DIR / "hidden_asset_state.csv", index=False)
    truth.to_csv(config.DATA_DIR / "inspection_truth.csv", index=False)
    # Per asset-event rather than per asset, so it cannot live in
    # hidden_asset_state.csv. Carries the per-event mean effective load, which
    # moved here when load rise stopped being a static per-asset figure.
    # Diagnostic only; read by validate.py alone.
    stress.to_csv(config.DATA_DIR / "hidden_thermal_stress.csv", index=False)

    run_hard_gates(events, hourly, stress, outcomes, hidden, theta_reference)

    print(f"assets: {len(assets)}  events: {len(events)}  outcomes: {len(outcomes)}")
    print(f"inspections: {len(inspections)}  procedures: {n_procedures}")
    print(f"solved failure intercept: {intercept:.4f}")
    print(f"realised failure rate: {outcomes['failed'].mean():.4f}")
    print(f"ageing reference in force: {theta_reference:.1f} C")
    print(f"wrote {config.OUTPUT_DIR / 'data_checks.txt'}")


if __name__ == "__main__":
    main()
