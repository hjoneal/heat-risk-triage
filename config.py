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

# The whole SGW substation transformer fleet, derived from the client brief
# rather than assumed: 8M residents / ~2.5 per household ~= 3.2M customer
# accounts; / ~8,000 customers per substation ~= 400 distribution substations;
# x ~2.2 transformers each ~= 900. See DECISIONS.md D-011.
N_ASSETS = 900  # derived
ASSET_ID_PREFIX = "SUB-SGW-"  # chosen

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
# versus rural feeders of the same size. Substations run N-1 — each transformer
# is sized to carry the full substation load alone — so customers per transformer
# sit well below what rated capacity alone suggests. 92 per MVA against a mean
# rating of 38.5 MVA gives a mean near 3,555, and 900 x 3,555 ~= 3.2M customers
# fleet-wide, which matches the derivation above. See DECISIONS.md D-013.
CUSTOMERS_PER_MVA = 92  # derived
CUSTOMERS_NOISE_SD = 0.35  # assumed: lognormal sigma on the per-MVA figure
CUSTOMERS_MIN = 400  # assumed
CUSTOMERS_MAX = 18_000  # assumed

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

# The demo scenarios are the forecast the tool is used against: 72 hours
# ahead of the event, hence a fixed near-future date.
FORECAST_DATE = "2026-08-25"  # chosen

# One of each shape the model was trained on, so the ranking can be compared
# across them. long-severe was added after the first three were found to span
# only 2.8 to 283.6 degree-hours against a training range of 0 to 787 — the demo
# omitted the most damaging event type in the bank, which is three of the sixteen
# training events. Every scenario sits inside the historical envelope for its
# type, asserted in tests/test_ranking.py; a forecast outside it would ask a
# linear model to extrapolate, and nothing in one warns when it does. That test
# earned its place immediately: long-severe was first set at amplitude 3.5, below
# every historical event of its type, which pushed the overnight minimum to
# 33.3 C against a trained maximum of 32.45. The amplitude here is mid-range of
# the three historical long-severe events rather than at an edge.
#
# One hourly temperature series applies to all 900 assets, so the scenario names
# carry no geography: a single series covering both coastal and inland areas
# would not be coherent. Hazard uniform across the fleet is a stated limitation,
# and it is why the forecast cannot reorder the queue by district. See D-026.
SCENARIOS = [
    # (scenario_id, label, event_type, days, peak_temp_c, amplitude_c)
    ("short-severe", "2-day severe spike", "short-severe", 2, 41.0, 8.0),
    ("long-moderate", "5-day moderate", "long-moderate", 5, 36.0, 4.0),
    ("long-severe", "5-day severe", "long-severe", 5, 39.8, 4.1),
    ("baseline-mild", "3-day mild baseline", "mild", 3, 30.5, 6.5),
]  # chosen

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

# Air-conditioning demand rises with ambient temperature, so an asset carries its
# heaviest loading at the same hours its cooling capacity is worst. Applying one
# static load figure to every hour of every event would make a mild day and a
# severe day impose identical loading, which contradicts the mechanism this
# project exists to model. Slope is per degree above the base.
AC_DEMAND_BASE_C = 25.0  # assumed: below this, cooling load is negligible
AC_DEMAND_SLOPE = 0.02  # assumed: +2% load per degree above the base
LOAD_CAP = 1.15  # assumed: protection operates beyond this

# Winding temperature rise above ambient. Resistive losses scale with the square
# of current, so a 20% load increase produces about 44% more heat from that
# source. The exponent is physics, not a fitted parameter.
LOAD_RISE_EXPONENT = 2.0  # chosen: I squared R
LOAD_RISE_AT_REFERENCE_C = 3.0  # assumed: rise at the reference load
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

# Re-derived after demand coupling, which raised fleet-mean thermal stress from
# 1.11 to 2.58 equivalent days and so required roughly a halving of this scale.
# The previous value of 2.4 was measured against a static per-asset load rise and
# does not carry over; nor did the 3.5 before it, measured at a 5% base rate.
#
# The bounds pull opposite ways. Gate 4 needs mild events near failure-free,
# which wants a steep hazard response — demand coupling makes heavily loaded
# assets accumulate real stress even at 30 C, so this binds harder than it did.
# The leakage ceiling on out-of-fold AUC caps the other end: a steeper response
# makes the outcomes too easy to predict from the hazard features alone.
#
# Measured across the bracket 0.20 to 3.00 on the final configuration: gate 4
# fails at 0.95 (six mild failures, rate 0.0022) and the AUC ceiling fails at
# 1.40 (0.9092 against a 0.90 limit), leaving a feasible band of [0.98, 1.35].
# This is its centre. Gate 6 has margin throughout the band and only binds
# outside it, at 1.50.
#
# Both lower-bound checks turn on single-figure failure counts, so the band moves
# with the generator's random stream: an earlier sweep that drew failures from a
# fresh stream rather than continuing the pipeline's own reported a band that did
# not exist, and concluded no value satisfied every gate. See DECISIONS.md D-027.
HAZARD_SCALE = 1.14  # measured

FAILURE_COEF_THERMAL_STRESS = SPEC_COEF_THERMAL_STRESS * HAZARD_SCALE
FAILURE_COEF_CONDITION = 1.60  # assumed
FAILURE_COEF_INTERACTION = SPEC_COEF_INTERACTION * HAZARD_SCALE
FAILURE_COEF_LOAD = 0.90  # assumed
FAILURE_LOAD_REFERENCE = 0.70  # chosen: mid-fleet load, so the term is centred

# Per asset *per event*, which is not an annual rate. CIGRE TB 642 (2015) puts
# the real annual major-failure rate below 1% — 0.8% for pre-1978 units — which
# implies roughly 0.0008 per asset-event for a heat-attributable share across
# four events a year. At that rate 14,400 rows would carry about a dozen
# positives, too few to fit 16 features, so this is inflated about twelvefold to
# roughly 144 positives. That scales predicted probabilities but preserves
# ranking, and the system consumes a ranking. See DECISIONS.md D-012.
TARGET_FAILURE_RATE = 0.01  # chosen
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

FAILURE_RATE_MIN = 0.008  # chosen: gate 1 lower bound
FAILURE_RATE_MAX = 0.013  # chosen: gate 1 upper bound
NONZERO_STRESS_SHARE_MIN = 0.80  # chosen: gate 3
# Moved down with the base rate: held at 0.01 against an overall rate of 0.01
# the gate would be vacuous.
MILD_FAILURE_RATE_MAX = 0.002  # chosen: gate 4
DEGREE_HOURS_PEAK_CORR_MAX = 0.85  # chosen: gate 5
LOW_CONDITION_FAILURE_SHARE_MIN = 0.20  # chosen: gate 6

# There is no gate 7. One was specified — the top 15 must differ by at least
# three assets between any two scenarios — and it was dropped after being
# measured as unreachable: with raw interaction products and three scenarios the
# pairwise maximum was 2, even with customers_served removed from the ranking
# entirely. Two later changes reversed that. A fourth scenario widened the range
# of forecasts on offer, and centring the interaction products stopped the model
# putting negative coefficients on its own hazard features. The divergence is now
# 3 to 9 assets, which the dropped gate would have passed.
#
# It is left dropped rather than reinstated at a threshold the system now happens
# to clear, because the reachable value was not known when the number was chosen
# and picking one afterwards is choosing a threshold to fit the data. The
# measurement is reported in output/ranking_divergence.txt. See D-026.

# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------

FEATURES = [
    # hazard (3) — ambient temperature only, never the asset's own thermal state.
    # `max_overnight_min_c` was a fourth and was dropped: at a variance inflation
    # factor of 49.4 it was 98% predictable from the rest, its coefficient flipped
    # sign between cross-validation folds, and the asset page was reporting that a
    # 32 C night had lowered an asset's risk. Removing it left every ranking
    # metric identical — a hazard feature is constant within an event and so
    # cannot reorder the assets in one — while taking the model's worst VIF to
    # 3.6. It is still computed and still shown on the forecast strip, because it
    # is a real property of the weather; it is no longer a model input.
    # See DECISIONS.md D-040.
    "peak_temp_c",
    "degree_hours_above_30",
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
    # interactions (3)
    "load_x_degree_hours",
    "condition_x_degree_hours",
    "age_x_warm_nights",
]
assert len(FEATURES) == 15

# Logistic regression is additive in the log-odds, so with hazard features that
# are constant within an event the hazard block adds the same number to every
# asset's logit and the forecast cannot reorder the queue however much the
# weather differs. An interaction term is asset value x event value, which
# rescales each asset's logit rather than offsetting all of them equally, and is
# the only way an additive model can represent "heavily loaded assets suffer more
# in a severe event". Each of the three corresponds to a mechanism now present in
# the generator. See DECISIONS.md D-024.
INTERACTION_FEATURES = [
    "load_x_degree_hours",
    "condition_x_degree_hours",
    "age_x_warm_nights",
]
assert set(INTERACTION_FEATURES) <= set(FEATURES)

# Each interaction is a product of *centred* components, not of raw values.
#
# Raw products are collinear with the features they are built from, and the
# consequence is not cosmetic: fitted on raw products the model puts a negative
# coefficient on degree-hours, peak temperature, warm nights and age, because the
# product term takes the shared signal and leaves the residual. The asset page
# then tells a supervisor that 40.7 C and a 33 C night *lowered* an asset's risk,
# and build_query — which reads only positive contributions — drops every
# heat-related term from the retrieval query. Golden rule 1 outranks the
# convenience of a raw product. Measured: centring takes the count of
# wrong-signed hazard and age coefficients from 5 to 2, moves degree-hours from
# -0.2388 to +1.0920, costs 0.014 of within-event AUC, and *increases* the
# forecast's effect on the ranking. See DECISIONS.md D-031.
#
# The centres are fixed rather than recomputed per call. A scenario matrix holds
# one event, so its own degree-hours mean is that event's value and a
# self-centred term would collapse to zero for every asset. Measured on the
# 14,400 training rows; model.py asserts they still match.
INTERACTION_CENTRE_PEAK_LOAD_PCT = 0.7546  # measured
INTERACTION_CENTRE_DEGREE_HOURS = 303.4492  # measured
INTERACTION_CENTRE_CONDITION_FLAGS = 1.6944  # measured
INTERACTION_CENTRE_AGE_YEARS = 32.5039  # measured
INTERACTION_CENTRE_WARM_NIGHTS = 3.0625  # measured
# The training means may drift if the generator changes; past this the stored
# centres are stale and the interaction terms no longer mean what they say.
INTERACTION_CENTRE_TOLERANCE = 0.01  # chosen: relative

# The ablation variant: everything the register knows, nothing the notes add.
# `condition_x_degree_hours` is built from the extracted flags, so leaving it in
# would leak note-derived information into the no-notes arm and understate the
# uplift the extraction layer is credited with.
NO_NOTES_FEATURES = [
    name for name in FEATURES
    if not name.startswith("flag_") and name != "condition_x_degree_hours"
]
assert len(NO_NOTES_FEATURES) == 10

# The only path from a feature name to anything a human reads.
FEATURE_LABELS = {
    "peak_temp_c": "Peak temperature",
    "degree_hours_above_30": "Accumulated heat above 30°C",
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
    "load_x_degree_hours": "Heavy load through sustained heat",
    "condition_x_degree_hours": "Known defects through sustained heat",
    "age_x_warm_nights": "Ageing asset with warm nights",
}
assert set(FEATURE_LABELS) == set(FEATURES)

# How a feature's reading is written on screen. FEATURE_LABELS gives the name; a
# reading without its unit is not a reading, and several of these were being
# shown as bare numbers whose meaning a crew supervisor could not recover.
FEATURE_UNITS = {
    "peak_temp_c": "°C",
    "degree_hours_above_30": "°C·h",
    "consecutive_warm_nights": "nights",
    "age_years": "years",
    "cooling_type_ordinal": "",  # rendered as the cooling type's name
    "peak_load_pct": "%",  # rendered as a percentage of rating
    "days_since_maintenance": "days",
    "prior_heat_faults": "recorded",
    "flag_cooling_degraded": "",  # rendered yes/no
    "flag_ventilation_obstructed": "",
    "flag_oil_issue": "",
    "flag_overdue_remedial": "",
    "load_x_degree_hours": "",  # rendered as its two components
    "condition_x_degree_hours": "",
    "age_x_warm_nights": "",
}
assert set(FEATURE_UNITS) == set(FEATURES)

# An interaction's own value is a product of two centred numbers and means
# nothing read on its own — "20.34" for an ageing asset in warm nights is not a
# quantity anyone can check. The components are shown instead. The flag count is
# not itself a feature, so it carries its own name here.
CONDITION_FLAG_COUNT = "n_condition_flags"  # chosen: pseudo-feature, display only
INTERACTION_COMPONENTS = {
    "load_x_degree_hours": ("peak_load_pct", "degree_hours_above_30"),
    "condition_x_degree_hours": (CONDITION_FLAG_COUNT, "degree_hours_above_30"),
    "age_x_warm_nights": ("age_years", "consecutive_warm_nights"),
}
assert set(INTERACTION_COMPONENTS) == set(INTERACTION_FEATURES)

# Each component's own phrasing, because composing a unit with a label gives
# "43 years age x 5 nights warm nights". A cell reading "Peak load 91% x
# Accumulated heat above 30°C 774 °C·h" is also worse than the number it
# replaced, so these are deliberately short.
COMPONENT_DISPLAY = {
    "peak_load_pct": "{value:.0%} load",
    "degree_hours_above_30": "{value:,.0f} °C·h heat",
    "age_years": "{value:,.0f} years old",
    "consecutive_warm_nights": "{value:.0f} warm nights",
    CONDITION_FLAG_COUNT: "{value:.0f} recorded defects",
}

DEGREE_HOUR_BASE_C = 30.0  # chosen: conventional degree-hour base for heat stress
# Overnight minimum is taken from the first six hours of the day, which is when
# ambient bottoms out under the diurnal curve above.
OVERNIGHT_HOURS = 6  # chosen
WARM_NIGHT_C = 24.0  # assumed: overnight minimum above which an asset fails to reset

# Ordered by cooling capability, so the coefficient has a readable sign.
COOLING_TYPE_ORDINAL = {"ONAN": 0, "ONAF": 1, "OFAF": 2}  # chosen
COOLING_TYPE_BY_ORDINAL = {value: name for name, value in COOLING_TYPE_ORDINAL.items()}

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

# How many assets a crew can reach in the 72 hours before the event begins, which
# is what makes precision and recall at that number the metrics that matter
# operationally — and therefore the least-grounded assumption in the build to
# leave unexamined.
#
# Utilities do not publish pre-event inspection capacity, so there is no figure to
# cite. Inspection *cadence* is documented, and capacity follows from it: 400
# substations on a monthly visual cadence over ~21 working days is ~19 substation
# visits a day, ~57 across the 72-hour window, of which perhaps half is divertible
# from routine walk-rounds and event-readiness staging to targeted pre-event work
# — roughly 30 transformer interventions. Cadence basis: IEEE C57 and NFPA 70B for
# condition assessment, NERC PRC-005 for protection intervals. The divertible
# share is assumed. Plausible range 20 to 50, centred near 30.
#
# It stays at 15 regardless. Raising it to the derived figure would roughly double
# reported recall, which is the reason not to raise it in the same change that
# discovered the derivation. 15 is the conservative reported case and the range is
# reported beside it as a sweep. See DECISIONS.md D-036.
CREW_CAPACITY = 15  # chosen

# Reported across a range rather than at a single value, because pre-event
# capacity is a client operating parameter and not a property of the system.
CAPACITY_SWEEP = [10, 15, 20, 25, 30, 40]  # chosen

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

# The floor is a per-term score, not a total. BM25 sums a contribution per query
# term, so a total scales with how many terms the query has: measured across 160
# queries, total score correlates +0.87 with query length. An absolute floor on
# that quantity tests how long the query was, not how well it matched — which is
# why the previous value needed re-deriving after every change to build_query,
# four times, each one moving a threshold that had stopped meaning what it said.
#
# It caught a real false positive on the fifth. Dropping max_overnight_min_c
# removed three terms from every query that carried it, and one asset's 15-term
# query fell to a total of 12.03 against a floor of 14.0 — suppressing three
# genuinely applicable procedures (insulation ageing, loading de-rating,
# maintenance compliance) for an old, heavily loaded, overdue asset. On per-term
# quality that same query ranked 62nd of 160: mid-pack, not degenerate.
#
# Per-term score across the 160 queries runs 0.589 to 1.360, median 0.845. 0.45
# sits 24% below the observed minimum and does not move when query length does.
# See DECISIONS.md D-018 and D-041.
BM25_FLOOR_PER_TERM = 0.45  # measured

# Covers the top of CAPACITY_SWEEP, so every row reachable at any reported
# capacity carries an action brief rather than only those inside the default 15.
BRIEF_TOP_N = 40  # chosen
# Above this ambient peak the query picks up de-rating guidance regardless of
# which asset-specific factors are driving the ranking.
HIGH_AMBIENT_QUERY_C = 35.0  # chosen

QUERY_TERMS = {
    "flag_cooling_degraded": ["cooling", "fan", "fans", "radiator", "inspection"],
    "flag_ventilation_obstructed": ["ventilation", "louvre", "airflow", "vegetation"],
    "flag_oil_issue": ["oil", "level", "seepage", "sampling"],
    "flag_overdue_remedial": ["work", "order", "outstanding", "deferred"],
    "consecutive_warm_nights": ["sustained", "overnight", "thermal", "loading"],
    "age_years": ["ageing", "insulation", "end-of-life"],
    "peak_load_pct": ["loading", "capacity", "de-rating"],
    "degree_hours_above_30": ["sustained", "high", "ambient"],
    "peak_temp_c": ["high", "ambient", "temperature"],
    "cooling_type_ordinal": [],
    "days_since_maintenance": ["maintenance", "overdue", "schedule"],
    "prior_heat_faults": ["recurring", "fault", "history"],
    "load_x_degree_hours": ["loading", "capacity", "de-rating", "sustained"],
    "condition_x_degree_hours": ["inspection", "defect", "pre-event"],
    "age_x_warm_nights": ["ageing", "insulation", "overnight"],
}
assert set(QUERY_TERMS) == set(FEATURES)

HIGH_AMBIENT_QUERY_TERMS = ["de-rating", "high", "ambient", "temperature"]  # chosen

# Applicability is a filter, not a query term. Two of the 25 procedures — fan
# control and pre-event cooling inspection — apply only to ONAF and OFAF units,
# because an ONAN transformer has no forced-air cooling system to inspect. That is
# a hard constraint on what may be put in front of a crew, and BM25 relevance
# cannot express it: measured before this filter existed, SOP-014 reached rank 4
# for 9 of the 135 briefed ONAN assets, one place outside the top-3 cut.
#
# The asset's cooling type was previously appended to every query instead, which
# did not do this job. `applies_to` is not indexed, so the term could only match
# cooling types written into a document's prose: "onan" appears in exactly one
# document and "onaf"/"ofaf" in none, making the term inert for 81 of 200 assets
# and a high-IDF accident for the rest. See DECISIONS.md D-037.
FILTER_BY_COOLING_TYPE = True  # chosen

# Doc ids as they appear in a brief's prose, so a reference the model wrote into
# a sentence can be checked the same way its cited_doc_ids array is.
DOC_ID_PATTERN = r"\b(?:SOP|MG|ERP|REG)-\d{3}\b"  # chosen

NO_MATCH_BRIEF = (
    "No specific procedure matched this asset's condition profile. "
    "Recommend general pre-event inspection."
)  # chosen: fixed text, no LLM call

# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------

# Smallest model first, per the build spec; escalate only if the extraction
# evaluation shows it is not good enough.
# Flash-lite does not emit reasoning tokens, which for a four-flag classification
# is latency and cost spent for nothing: measured against gemini-2.5-flash on the
# same note, 0.8 s and 0 thinking tokens versus 3.2 s and 593. A pinned id rather
# than gemini-flash-lite-latest, so the committed cache stays reproducible.
EXTRACTION_MODEL = "gemini-3.5-flash-lite"  # measured
BRIEF_MODEL = "gemini-3.5-flash-lite"  # measured

# Bumping either version invalidates every cache key built with it, which is how
# a prompt change is forced to re-run rather than silently reuse old output.
PROMPT_VERSION = "v1"  # chosen
BRIEF_PROMPT_VERSION = "v2"  # chosen: v2 forbids naming an unsupplied doc in the prose

LLM_TEMPERATURE = 0.0  # chosen: deterministic decoding
EXTRACTION_MAX_TOKENS = 1024  # chosen: four flags with short quotes
BRIEF_MAX_TOKENS = 1024  # chosen: three or four sentences plus citations
# One retry on a parse or validation failure, then record the failure. Guessing
# at a defect the model could not read is worse than recording that it failed.
LLM_MAX_RETRIES = 1  # chosen
CACHE_KEY_LENGTH = 16  # chosen: 64 bits of sha256 is ample for a few hundred keys
# Neither SDK stalls visibly on a dead socket, and a 300-call batch that hangs
# looks identical to one that is merely slow. Measured call latency is around a
# second, so anything past this is a stall, not slowness.
LLM_REQUEST_TIMEOUT_S = 60  # chosen
LLM_PROGRESS_EVERY = 25  # chosen: how often a long extraction run reports progress
# A stalled socket or a 429 is a transport problem, not a bad answer, and is
# retried separately from LLM_MAX_RETRIES: that one exists for a model that
# returned something unusable, and re-sending on a timeout would otherwise be
# scored as the model failing. Both are counted and reported.
LLM_TRANSPORT_RETRIES = 3  # chosen
LLM_TRANSPORT_BACKOFF_S = 2.0  # chosen: doubles each attempt

# ---------------------------------------------------------------------------
# Web application
# ---------------------------------------------------------------------------

# Raised from 25 with BRIEF_TOP_N, so that every capacity the sweep reports and
# the slider offers has rows to draw a line across.
QUEUE_ROWS = 40  # chosen: the briefed assets, so every visible row has a brief

# The forecast chart. Rendered as SVG on the server, so the page still carries no
# plotting library and no request-time computation. The left pad holds the
# temperature scale and the bottom pad the hour-of-day labels.
CHART_WIDTH = 720  # chosen
CHART_HEIGHT = 190  # chosen
CHART_PAD_LEFT = 44  # chosen: room for a "40 °C" label
CHART_PAD_RIGHT = 10  # chosen
CHART_PAD_TOP = 10  # chosen
CHART_PAD_BOTTOM = 34  # chosen: room for two rows of hour labels
CHART_TEMPERATURE_STEPS_C = [2, 5, 10]  # chosen: the ladder of round gridline intervals
CHART_MAX_TEMPERATURE_LINES = 7  # chosen
CHART_HOUR_TICK_INTERVAL = 12  # chosen: midnight and midday only

# The queue's default order. Every column sorts, so this is only the order the
# page opens in — expected impact, because that is what the crew is dispatched on.
QUEUE_DEFAULT_SORT = "priority"  # chosen

# A reading's place among the 14,400 training asset-event rows, as a phrase. The
# reference set is synthetic and does not support a claim finer than a band.
PERCENTILE_BANDS = [
    (0.90, "among the highest in the fleet"),
    (0.70, "higher than most"),
    (0.30, "about typical"),
    (0.10, "lower than most"),
    (0.00, "among the lowest in the fleet"),
]  # chosen

# The crew-capacity control's range. Capacity is the one input that genuinely
# changes which assets get visited: the ranking is fixed for a given forecast,
# and capacity decides where the line is drawn across it. Capped at QUEUE_ROWS
# because past that the queue has no rows left to draw the line after.
CREW_CAPACITY_MIN = 5  # chosen
CREW_CAPACITY_MAX = 40  # chosen: the top of CAPACITY_SWEEP, and of QUEUE_ROWS
DECISIONS_LOG = OUTPUT_DIR / "decisions.jsonl"
