"""All constants for the heat risk triage pipeline.

Every threshold, count and coefficient in the project lives here. Each carries a
one-line comment stating whether the value was *chosen* (a design decision),
*measured* (read off a run and recorded), or *assumed* (a stand-in for a number
that would come from real operational data).

A number appearing inline in any other module is a defect.
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

SEED = 42  # chosen: single seed, used by every generator in the project

# Written into scored output as `generated_at`. Fixed rather than read from the
# clock so that re-running the pipeline produces byte-identical files; a moving
# timestamp would break the determinism guarantee for no analytical gain.
RUN_TIMESTAMP = "2026-08-22T00:00:00Z"  # chosen

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent
DATA_DIR = REPO_ROOT / "data"
PROCEDURES_DIR = DATA_DIR / "procedures"
OUTPUT_DIR = REPO_ROOT / "output"
CACHE_DIR = REPO_ROOT / "cache"
EXTRACTION_CACHE_DIR = CACHE_DIR / "extractions"
BRIEF_CACHE_DIR = CACHE_DIR / "briefs"
TEMPLATES_DIR = REPO_ROOT / "templates"

# ---------------------------------------------------------------------------
# Fleet
# ---------------------------------------------------------------------------

# The utility operates roughly 400 transformers in total; this prototype models
# the 150-asset inland corridor and 150 is the number used everywhere in code.
N_ASSETS = 150  # chosen
ASSET_ID_PREFIX = "SUB-INL-"  # chosen

DISTRICTS = ["Northfield", "Cedar Basin", "Ridgeline", "Junction West"]  # chosen

INSTALL_YEAR_MIN = 1968  # assumed: oldest units still in service
INSTALL_YEAR_MAX = 2019  # assumed: newest units in the corridor
# Triangular rather than uniform so the fleet skews old, as a real ageing
# distribution network does. The mode is the peak build-out decade.
INSTALL_YEAR_MODE = 1985  # assumed

RATED_CAPACITIES_MVA = [10, 25, 50, 100]  # chosen: standard distribution ratings
RATED_CAPACITY_WEIGHTS = [0.30, 0.30, 0.25, 0.15]  # assumed: small units are commoner

COOLING_TYPES = ["ONAN", "ONAF", "OFAF"]  # chosen: oil-natural through oil-forced
COOLING_TYPE_WEIGHTS = [0.40, 0.40, 0.20]  # assumed

PEAK_LOAD_PCT_MIN = 0.55  # assumed: lightly loaded unit
PEAK_LOAD_PCT_MAX = 0.95  # assumed: unit running close to nameplate

# Customers behind an asset scale with its rating; the spread reflects urban
# versus rural feeders of the same size.
CUSTOMERS_PER_MVA = 430  # assumed
CUSTOMERS_NOISE_SD = 0.35  # assumed: lognormal sigma on the per-MVA figure
CUSTOMERS_MIN = 800  # assumed
CUSTOMERS_MAX = 45_000  # assumed

CRITICALITY_MIN = 1  # chosen: 1 = routine, 5 = serves a designated critical load
CRITICALITY_MAX = 5  # chosen

MAINTENANCE_WINDOW_YEARS = 4  # assumed: last maintenance falls in this window
PRIOR_FAULTS_POISSON_MEAN = 0.8  # assumed
PRIOR_FAULTS_CAP = 4  # assumed

# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------

HOURS_PER_DAY = 24  # chosen: hourly resolution throughout
# Peak of the diurnal sine falls mid-afternoon; hour 9 is the phase offset that
# puts the maximum at hour 15.
DIURNAL_PHASE_HOURS = 9  # chosen
WEATHER_NOISE_SD = 0.4  # assumed: hour-to-hour measurement noise, degrees C
DAILY_MEAN_JITTER_C = 0.5  # assumed: day-to-day drift within one event

# Historical event bank. Duration and amplitude ranges are inclusive.
# Amplitude sets how cold the nights get, and the overnight minimum sets the
# floor the asset resets down to — not whether it resets. A low amplitude means
# warm nights, so each day starts from a higher base than the last.
EVENT_TYPES = [
    # (name, count, days_min, days_max, peak_min, peak_max, amp_min, amp_max)
    ("short-severe", 5, 2, 2, 40.0, 42.0, 7.5, 8.5),  # assumed
    ("long-moderate", 5, 5, 6, 35.0, 37.0, 3.5, 4.5),  # assumed
    ("long-severe", 3, 5, 5, 39.0, 41.0, 3.5, 4.5),  # assumed
    ("mild", 3, 3, 3, 29.0, 32.0, 6.0, 7.0),  # assumed
]
N_HISTORICAL_EVENTS = 16  # chosen: sum of the counts above

# Historical events are dated across the summers preceding the forecast so that
# `days_since_maintenance` has a plausible spread.
FIRST_EVENT_YEAR = 2021  # chosen
EVENT_SEASON_START_MONTH = 6  # chosen: events fall between June and September
EVENT_SEASON_END_MONTH = 9  # chosen

# The three demo scenarios are the forecast the tool is used against: 72 hours
# ahead of the event, hence a fixed near-future date.
FORECAST_DATE = "2026-08-25"  # chosen

SCENARIOS = [
    # (scenario_id, label, event_type, days, peak_temp_c, amplitude_c)
    ("coastal-short-severe", "Coastal · 2-day severe", "short-severe", 2, 41.0, 8.0),
    ("inland-long-moderate", "Inland · 5-day moderate", "long-moderate", 5, 36.0, 4.0),
    ("baseline-mild", "Baseline · 3-day mild", "mild", 3, 30.5, 6.5),
]  # chosen: one of each shape, so the ranking can be compared across them

# ---------------------------------------------------------------------------
# Hidden failure process
#
# None of this is visible to the model. It is the ground truth the risk model
# has to recover from ambient temperature, the asset register and the notes.
# ---------------------------------------------------------------------------

# Latent condition is a weighted blend of age, maintenance lag and fault history
# plus unexplained variation. The weights sum to 1.0.
CONDITION_WEIGHT_AGE = 0.35  # assumed
CONDITION_WEIGHT_MAINTENANCE = 0.30  # assumed
CONDITION_WEIGHT_PRIOR_FAULTS = 0.20  # assumed
CONDITION_WEIGHT_NOISE = 0.15  # assumed: condition the register does not capture

# Forced cooling sheds heat, natural cooling does not.
COOLING_OFFSET_C = {"ONAN": 2.0, "ONAF": 0.0, "OFAF": -2.0}  # assumed

# Winding temperature rise above ambient at peak load.
LOAD_RISE_BASE_C = 4.0  # assumed
LOAD_RISE_SLOPE_C = 8.0  # assumed: degrees per unit of load above the reference
LOAD_RISE_REFERENCE_LOAD = 0.55  # chosen: the lightest loaded asset in the fleet

# Thermal time constant of the bulk oil, not the winding: the winding responds in
# minutes, the oil mass in hours. Even at the slow end the unit sheds most of its
# offset over a night (at tau 3 h, 96% after eight hours; at 8 h, 66%), so the
# overnight minimum matters as the floor it returns to, not as a failure to reset.
TAU_MEAN_HOURS = 5.0  # assumed
TAU_SD_HOURS = 1.0  # assumed
TAU_MIN_HOURS = 3.0  # assumed
TAU_MAX_HOURS = 8.0  # assumed

# Cellulose insulation ages by an Arrhenius process: the rate roughly doubles
# for every MONTSINGER_HALVING_C of sustained temperature rise (Montsinger,
# 1942; the literature range is 6-8 C). Thermal stress is the accumulated
# *accelerated* ageing over an event, in equivalent days at the reference
# temperature — an accumulation, so duration counts, but exponentially weighted,
# so a severe peak counts for more than a mild hour of the same length.
#
# The reference is expressed on this model's temperature scale, which tracks
# bulk oil rather than winding hot-spot: the standards' 110 C hot-spot reference
# sits above this by the winding gradient, which this model does not represent.
THETA_REFERENCE_C = 38.0  # assumed
MONTSINGER_HALVING_C = 6.0  # assumed: doubling per 6 C, the steep end of 6-8
# Hard gate 2 remedy: if long-moderate events do not accumulate more stress than
# short-severe ones, drop the reference by this much and regenerate.
THETA_REFERENCE_DECREMENT_C = 1.0  # chosen

# Failure log-odds. The interaction term is the mechanism the project exists to
# capture: heat and poor condition are worse together than either alone.
#
# The build spec's own coefficients on the two stress terms, kept named so the
# deviation below is visible rather than buried in a changed literal.
SPEC_COEF_THERMAL_STRESS = 0.15  # build spec section 2.5
SPEC_COEF_INTERACTION = 0.30  # build spec section 2.5

# Gate 4 requires mild events to be near failure-free, which needs a steep
# hazard response; build spec section 9 requires out-of-fold AUC below 0.90,
# which needs a shallow one. They bracket a narrow band, and this is the value
# inside it: at 3.0 the mild-event rate is 0.0133 and gate 4 fails, at 4.0 the
# AUC reaches 0.9103 and the leakage line fails. See DECISIONS.md D-007, D-008.
HAZARD_SCALE = 3.5  # measured

FAILURE_COEF_THERMAL_STRESS = SPEC_COEF_THERMAL_STRESS * HAZARD_SCALE
FAILURE_COEF_CONDITION = 1.60  # assumed
FAILURE_COEF_INTERACTION = SPEC_COEF_INTERACTION * HAZARD_SCALE
FAILURE_COEF_LOAD = 0.90  # assumed
FAILURE_LOAD_REFERENCE = 0.70  # chosen: mid-fleet load, so the term is centred

TARGET_FAILURE_RATE = 0.05  # chosen: ~120 failures across 2,400 rows
# The failure intercept is solved for by bisection rather than hardcoded, so the
# base rate stays at target if any upstream coefficient changes.
INTERCEPT_SEARCH_MIN = -30.0  # chosen: wide enough to bracket any plausible value
INTERCEPT_SEARCH_MAX = 10.0  # chosen
INTERCEPT_SEARCH_TOLERANCE = 1e-9  # chosen
INTERCEPT_SEARCH_MAX_ITERATIONS = 200  # chosen

# ---------------------------------------------------------------------------
# Inspection notes
# ---------------------------------------------------------------------------

NOTES_PER_ASSET = 2  # chosen: 300 notes across the fleet
INSPECTION_ID_TEMPLATE = "INS-{asset_number:03d}-{note_number}"  # chosen

# A defect is written up with probability proportional to condition; even a poor
# asset does not have every defect recorded at every visit.
CONDITION_SENTENCE_PROB_SCALE = 0.7  # assumed
MAX_CONDITION_SENTENCES = 3  # chosen: keeps notes to a realistic length
DISTRACTOR_SENTENCES_MIN = 1  # chosen
DISTRACTOR_SENTENCES_MAX = 2  # chosen
# Proportion of notes where a defect that would otherwise read as present is
# instead recorded as fixed or explicitly absent. This is the case the keyword
# baseline gets wrong and the extraction layer has to get right.
RESOLUTION_OR_NEGATION_PROB = 0.25  # chosen

# Inspections are dated before the most recent historical event, so no note can
# describe a condition observed after the training window closes.
INSPECTION_WINDOW_DAYS = 540  # assumed: roughly 18 months of inspection history

# ---------------------------------------------------------------------------
# Hard gates on generated data
# ---------------------------------------------------------------------------

FAILURE_RATE_MIN = 0.04  # chosen: gate 1 lower bound
FAILURE_RATE_MAX = 0.07  # chosen: gate 1 upper bound
NONZERO_STRESS_SHARE_MIN = 0.80  # chosen: gate 3
MILD_FAILURE_RATE_MAX = 0.01  # chosen: gate 4
DEGREE_HOURS_PEAK_CORR_MAX = 0.85  # chosen: gate 5
LOW_CONDITION_FAILURE_SHARE_MIN = 0.20  # chosen: gate 6

# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------

FEATURES = [
    # hazard (4) — ambient temperature only, never the asset's own thermal state
    "peak_temp_c",
    "degree_hours_above_30",
    "max_overnight_min_c",
    "consecutive_warm_nights",
    # asset static (3)
    "age_years",
    "cooling_type_ordinal",
    "peak_load_pct",
    # condition (6)
    "days_since_maintenance",
    "prior_heat_faults",
    "flag_cooling_degraded",
    "flag_ventilation_obstructed",
    "flag_oil_issue",
    "flag_overdue_remedial",
]
assert len(FEATURES) == 13

# The ablation variant: everything the register knows, nothing the notes add.
NO_NOTES_FEATURES = FEATURES[:9]

# The only path from a feature name to anything a human reads.
FEATURE_LABELS = {
    "peak_temp_c": "Peak temperature",
    "degree_hours_above_30": "Accumulated heat above 30°C",
    "max_overnight_min_c": "Warmest overnight minimum",
    "consecutive_warm_nights": "Consecutive warm nights",
    "age_years": "Age",
    "cooling_type_ordinal": "Cooling system type",
    "peak_load_pct": "Peak load",
    "days_since_maintenance": "Time since last maintenance",
    "prior_heat_faults": "Prior heat-related faults",
    "flag_cooling_degraded": "Cooling degraded (from inspection notes)",
    "flag_ventilation_obstructed": "Ventilation obstructed (from inspection notes)",
    "flag_oil_issue": "Oil issue (from inspection notes)",
    "flag_overdue_remedial": "Outstanding remedial work (from inspection notes)",
}
assert set(FEATURE_LABELS) == set(FEATURES)

DEGREE_HOUR_BASE_C = 30.0  # chosen: conventional degree-hour base for heat stress
# Overnight minimum is taken from the first six hours of the day, which is when
# ambient bottoms out under the diurnal curve above.
OVERNIGHT_HOURS = 6  # chosen
WARM_NIGHT_C = 24.0  # assumed: overnight minimum above which an asset fails to reset

# Ordered by cooling capability, so the coefficient has a readable sign.
COOLING_TYPE_ORDINAL = {"ONAN": 0, "ONAF": 1, "OFAF": 2}  # chosen

# ---------------------------------------------------------------------------
# Condition flags
# ---------------------------------------------------------------------------

CONDITION_FLAGS = [
    "cooling_degraded",
    "ventilation_obstructed",
    "oil_issue",
    "overdue_remedial",
]  # chosen: the four defects an inspector records that bear on heat tolerance

# ---------------------------------------------------------------------------
# Risk model
# ---------------------------------------------------------------------------

# Grouped on event, not row: every asset in one event shares all four hazard
# features, so a random split would put the same weather on both sides.
CV_FOLDS = 5  # chosen
LOGREG_PENALTY = "l2"  # chosen
LOGREG_C = 1.0  # chosen: scikit-learn default, confirmed by the sweep below
LOGREG_MAX_ITER = 1000  # chosen: enough for convergence at this size
C_SWEEP = [0.01, 0.1, 1.0, 10.0]  # chosen: one decade either side of the default

# The crew can reach 15 assets before the event begins, which is what makes
# precision and recall at 15 the metrics that matter operationally.
CREW_CAPACITY = 15  # chosen

CALIBRATION_BINS = 10  # chosen: equal-width bins over [0, 1]
# Contributions are coefficient x standardised value and must sum to
# logit(p) - intercept. Anything looser than this would hide a real error.
CONTRIBUTION_SUM_TOLERANCE = 1e-6  # chosen

# Leakage check: no feature may track the hidden state this closely.
LEAKAGE_CORRELATION_MAX = 0.95  # chosen
# Out-of-fold AUC above this is evidence of leakage, not of a good model.
LEAKAGE_AUC_THRESHOLD = 0.90  # chosen

# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

N_PROCEDURES = 25  # chosen
BM25_K1 = 1.5  # chosen: rank_bm25 default
BM25_B = 0.75  # chosen: rank_bm25 default
BM25_TOP_K = 3  # chosen: as many procedures as a supervisor will read

# Preserves hyphenated identifiers such as sop-014, which \w+ would split.
TOKEN_PATTERN = r"[a-z0-9]+(?:-[a-z0-9]+)*"  # chosen

# Set from the score distribution in output/bm25_scores.txt after the first run.
BM25_FLOOR = 0.0  # measured: placeholder until retrieve.py has been run once

BRIEF_TOP_N = 25  # chosen: the crew capacity of 15 plus a review margin
# Above this ambient peak the query picks up de-rating guidance regardless of
# which asset-specific factors are driving the ranking.
HIGH_AMBIENT_QUERY_C = 35.0  # chosen

QUERY_TERMS = {
    "flag_cooling_degraded": ["cooling", "fan", "fans", "radiator", "inspection"],
    "flag_ventilation_obstructed": ["ventilation", "louvre", "airflow", "vegetation"],
    "flag_oil_issue": ["oil", "level", "seepage", "sampling"],
    "flag_overdue_remedial": ["work", "order", "outstanding", "deferred"],
    "consecutive_warm_nights": ["sustained", "overnight", "thermal", "loading"],
    "max_overnight_min_c": ["overnight", "cooling", "recovery"],
    "age_years": ["ageing", "insulation", "end-of-life"],
    "peak_load_pct": ["loading", "capacity", "de-rating"],
    "degree_hours_above_30": ["sustained", "high", "ambient"],
    "peak_temp_c": ["high", "ambient", "temperature"],
    "cooling_type_ordinal": [],
    "days_since_maintenance": ["maintenance", "overdue", "schedule"],
    "prior_heat_faults": ["recurring", "fault", "history"],
}
assert set(QUERY_TERMS) == set(FEATURES)

HIGH_AMBIENT_QUERY_TERMS = ["de-rating", "high", "ambient", "temperature"]  # chosen

NO_MATCH_BRIEF = (
    "No specific procedure matched this asset's condition profile. "
    "Recommend general pre-event inspection."
)  # chosen: fixed text, no LLM call

# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------

# Smallest model first, per the build spec; escalate only if the extraction
# evaluation shows it is not good enough.
LLM_PROVIDER = "anthropic"  # chosen: the committed cache is the Anthropic one
# Model ids carry no date suffix; a suffixed id is rejected by the API.
EXTRACTION_MODEL = "claude-haiku-4-5"  # chosen
BRIEF_MODEL = "claude-haiku-4-5"  # chosen
# The Gemini path is written from the current SDK documentation but has not been
# executed; see DECISIONS.md D-009.
GEMINI_EXTRACTION_MODEL = "gemini-2.5-flash"  # chosen
GEMINI_BRIEF_MODEL = "gemini-2.5-flash"  # chosen

# Bumping either version invalidates every cache key built with it, which is how
# a prompt change is forced to re-run rather than silently reuse old output.
PROMPT_VERSION = "v1"  # chosen
BRIEF_PROMPT_VERSION = "v1"  # chosen

LLM_TEMPERATURE = 0.0  # chosen: deterministic decoding
EXTRACTION_MAX_TOKENS = 1024  # chosen: four flags with short quotes
BRIEF_MAX_TOKENS = 1024  # chosen: three or four sentences plus citations
# One retry on a parse or validation failure, then record the failure. Guessing
# at a defect the model could not read is worse than recording that it failed.
LLM_MAX_RETRIES = 1  # chosen
CACHE_KEY_LENGTH = 16  # chosen: 64 bits of sha256 is ample for a few hundred keys

# ---------------------------------------------------------------------------
# Web application
# ---------------------------------------------------------------------------

QUEUE_ROWS = 25  # chosen: the briefed assets, so every visible row has a brief
DECISIONS_LOG = OUTPUT_DIR / "decisions.jsonl"
