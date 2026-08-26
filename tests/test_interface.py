"""What the interface must not misreport.

The web layer is inert — it reads scored JSON and renders it — so these check
presentation invariants rather than behaviour: that a reading is never shown
without the means to interpret it, that sorting cannot make the page claim
something untrue, and that the raw model vocabulary never reaches the screen.
"""

import json
import math
import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

import config  # noqa: E402

app_module = pytest.importorskip("app")
client = TestClient(app_module.app)
SCENARIO = config.SCENARIOS[0][0]


def queue_html(query=""):
    response = client.get(f"/scenario/{SCENARIO}{query}")
    assert response.status_code == 200, response.status_code
    return response.text


def top_asset_id(scenario_id):
    path = config.OUTPUT_DIR / f"scored_{scenario_id}.json"
    if not path.exists():
        pytest.skip("scored output not present; run model.py first")
    return json.loads(path.read_text())["assets"][0]["asset_id"]


def capacity_within_the_visible_queue():
    """A crew capacity whose line lands among the rows the queue shows.

    The line now falls after the nth *crew* row, which sits further down than
    rank n — often past the end of the queue. Tests that need to see it drawn
    have to pick a capacity that puts it on screen rather than assume one.
    """
    path = config.OUTPUT_DIR / f"scored_{SCENARIO}.json"
    if not path.exists():
        pytest.skip("scored output not present; run model.py first")
    assets = json.loads(path.read_text())["assets"]
    for capacity in range(config.CREW_CAPACITY, config.CREW_CAPACITY_MIN - 1, -1):
        line = app_module.crew_capacity_line(assets, capacity)
        if line and line <= config.QUEUE_ROWS:
            return capacity
    pytest.skip("no capacity in range draws the line within the visible queue")


def reading_cells(html):
    """The visible text of every reading cell, tags stripped.

    Matching to the first tag inside the cell is not the same thing: a
    categorical feature's reading *is* markup — its levels — so that form of the
    check matched an empty string and passed on rows it was not reading at all.
    """
    cells = re.findall(r'<td class="reading">(.*?)</td>', html, flags=re.DOTALL)
    return [" ".join(re.sub(r"<[^>]+>", " ", cell).split()) for cell in cells]


def rank_column(html):
    body = html.split("<tbody>")[1]
    return [int(n) for n in re.findall(r'<td class="num rank">(\d+)</td>', body)]


@pytest.mark.parametrize("column", ["rank", "name", "risk", "customers", "priority", "criticality"])
@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_every_sortable_column_renders(column, direction):
    html = queue_html(f"?sort={column}&direction={direction}")
    assert "<tbody>" in html


def test_an_unknown_sort_falls_back_rather_than_erroring():
    """A hand-edited URL is not a reason to show a stack trace."""
    assert rank_column(queue_html("?sort=nonsense&direction=sideways")) == \
        rank_column(queue_html())


def test_the_rank_column_is_the_dispatch_position_not_the_row_number():
    """Renumbering per sort would make the column mean something different on
    every click, and the number is how a supervisor refers to an asset."""
    assert rank_column(queue_html()) == sorted(rank_column(queue_html()))
    resorted = rank_column(queue_html("?sort=customers&direction=desc"))
    assert resorted != sorted(resorted), "rank was renumbered to match the sort"


def test_the_capacity_line_appears_only_in_dispatch_order():
    """Drawn in any other order it would assert that the rows above it get
    visited, which is only true when the queue is in the order the crew works."""
    within = capacity_within_the_visible_queue()
    assert "capacity-rule" in queue_html(f"?capacity={within}")
    for query in ["?sort=customers&direction=desc", "?sort=risk&direction=asc",
                  "?sort=priority&direction=asc", "?sort=criticality&direction=desc"]:
        html = queue_html(f"{query}&capacity={within}")
        assert "capacity-rule" not in html, f"capacity line drawn for {query}"
        assert "not that order" in html, f"no explanation offered for {query}"


def test_the_capacity_line_falls_after_the_nth_crew_row_not_the_nth_row():
    """The substantive change. A load transfer is made from a desk and consumes
    no crew capacity, so counting it against the budget drew the line short."""
    scored = json.loads((config.OUTPUT_DIR / f"scored_{SCENARIO}.json").read_text())
    capacity = capacity_within_the_visible_queue()
    line = app_module.crew_capacity_line(scored["assets"], capacity)
    above = [a for a in scored["assets"] if a["rank"] <= line]
    assert sum(1 for a in above if a["intervention_type"] == "crew") == capacity
    assert above[-1]["intervention_type"] == "crew", "the line falls after a row needing no crew"
    assert line > capacity, \
        "no non-crew rows above the line; this scenario cannot demonstrate the change"

    html = queue_html(f"?capacity={capacity}")
    marked = re.search(r'<tr class="capacity-rule"><td[^>]*>(.*?)</td>', html, re.DOTALL)
    assert marked, "no capacity line drawn"
    assert f"rank {line}" in " ".join(marked.group(1).split())
    # And it sits after that rank's row, not after the capacity-th row.
    ranks = rank_column(html)
    drawn_after = html[:html.index('class="capacity-rule"')].count('<td class="num rank">')
    assert ranks[drawn_after - 1] == line, \
        f"line drawn after rank {ranks[drawn_after - 1]}, expected {line}"


def test_a_capacity_line_beyond_the_queue_is_explained_rather_than_omitted():
    """Silently drawing nothing looks like a defect. Where it falls is also the
    finding: the crew budget reaches much further down than its own number."""
    scored = json.loads((config.OUTPUT_DIR / f"scored_{SCENARIO}.json").read_text())
    beyond = next(
        (c for c in range(config.CREW_CAPACITY_MIN, config.CREW_CAPACITY_MAX + 1)
         if (app_module.crew_capacity_line(scored["assets"], c) or config.QUEUE_ROWS + 1)
         > config.QUEUE_ROWS),
        None)
    assert beyond, "no capacity puts the line past the visible queue"
    html = queue_html(f"?capacity={beyond}")
    assert "capacity-rule" not in html
    line = app_module.crew_capacity_line(scored["assets"], beyond)
    assert f"rank {line}" in html, "the page does not say where the line fell"


def test_the_forecast_chart_carries_both_axes():
    """A temperature trace without a scale is decoration."""
    html = queue_html()
    assert "axis-label" in html and "gridline" in html
    assert "°C" in html
    assert re.search(r">\d{2}:00<", html), "no hour-of-day labels"
    assert "Day 1" in html


def test_the_capacity_control_works_without_scripting():
    """The script only removes the button; it must still be in the markup."""
    html = queue_html()
    assert 'data-fallback-submit' in html
    assert '<button type="submit"' in html
    assert client.get("/static/app.js").status_code == 200


@pytest.mark.parametrize("scenario_id", [s[0] for s in config.SCENARIOS])
def test_no_reading_is_shown_as_a_bare_uninterpretable_number(scenario_id):
    """Every factor needs a reading a supervisor could check against the asset.

    An interaction's own value is a product of two centred quantities; shown
    raw it was a number with no referent, which is what this guards against.
    """
    asset_id = top_asset_id(scenario_id)
    html = client.get(f"/scenario/{scenario_id}/asset/{asset_id}").text
    readings = reading_cells(html)
    assert len(readings) == len(config.FEATURES) + 1, \
        f"{len(readings)} reading cells for {len(config.FEATURES)} features and a baseline row"
    for reading in readings[:len(config.FEATURES)]:
        assert reading, "a factor was shown with no reading at all"
        # The comparison note sits in the same cell; the reading is what precedes it.
        head = reading.split(" of the fleet")[0].split(" among ")[0].split(" about ")[0]
        assert not re.fullmatch(r"-?[\d.,]+", head.strip()), \
            f"{head!r} is a bare number with no unit or referent"


def test_the_odds_multiplier_matches_the_log_odds_contribution(scenario_id=None):
    """The operator-facing figure must be the exact exponential of the model's
    own, not a separate approximation that could drift from it."""
    scenario_id = scenario_id or SCENARIO
    asset_id = top_asset_id(scenario_id)
    scored = json.loads((config.OUTPUT_DIR / f"scored_{scenario_id}.json").read_text())
    asset = next(a for a in scored["assets"] if a["asset_id"] == asset_id)
    for c in asset["contributions"]:
        assert math.isclose(c["odds_multiplier"], math.exp(c["contribution"]), rel_tol=1e-3), \
            f"{c['feature']}: x{c['odds_multiplier']} != exp({c['contribution']})"


@pytest.mark.parametrize("scenario_id", [s[0] for s in config.SCENARIOS])
def test_contributions_are_ordered_by_effect_on_odds(scenario_id):
    """The table reads straight down from the largest raiser to the largest
    reducer. Ordering by magnitude interleaved the two."""
    scored = json.loads((config.OUTPUT_DIR / f"scored_{scenario_id}.json").read_text())
    for asset in scored["assets"][:20]:
        multipliers = [c["odds_multiplier"] for c in asset["contributions"]]
        assert multipliers == sorted(multipliers, reverse=True), \
            f"{asset['asset_id']} contributions are not in descending order of effect"


def test_the_log_odds_figure_is_not_shown_to_the_reader():
    """Two columns saying the same thing in different units is one too many.

    The arithmetic is still checked — assert_contributions_sum holds it to 1e-6
    at score time and the raw contribution stays in the scored JSON — but the
    screen carries the readable unit only.
    """
    asset_id = top_asset_id(SCENARIO)
    html = client.get(f"/scenario/{SCENARIO}/asset/{asset_id}").text
    assert "log-odds" not in html
    assert "logodds" not in html
    scored = json.loads((config.OUTPUT_DIR / f"scored_{SCENARIO}.json").read_text())
    asset = next(a for a in scored["assets"] if a["asset_id"] == asset_id)
    assert all("contribution" in c for c in asset["contributions"]), \
        "the raw contribution must survive in the record even though it is not displayed"


def test_multipliers_compose_to_the_score(scenario_id=None):
    """The property that makes the table trustworthy: the multipliers and the
    baseline reproduce the risk shown at the top of the page."""
    scenario_id = scenario_id or SCENARIO
    scored = json.loads((config.OUTPUT_DIR / f"scored_{scenario_id}.json").read_text())
    baseline_odds = math.exp(scored["intercept"])
    for asset in scored["assets"][:10]:
        product = baseline_odds
        for c in asset["contributions"]:
            product *= c["odds_multiplier"]
        actual = asset["risk"] / (1 - asset["risk"])
        assert math.isclose(product, actual, rel_tol=1e-3), \
            f"{asset['asset_id']}: multipliers give odds {product}, score implies {actual}"


def test_every_capacity_the_slider_offers_has_rows_and_a_brief_behind_it():
    """The slider, the queue and the brief coverage must agree.

    A capacity the slider can reach but the queue cannot show would put the line
    nowhere, and a visible row without a brief breaks the invariant that every
    row the crew can be sent to carries guidance.
    """
    assert config.CREW_CAPACITY_MAX <= config.QUEUE_ROWS <= config.BRIEF_TOP_N
    assert max(config.CAPACITY_SWEEP) <= config.CREW_CAPACITY_MAX
    assert config.CREW_CAPACITY in config.CAPACITY_SWEEP, \
        "the default capacity must appear in the sweep it is reported against"

    html = queue_html(f"?capacity={config.CREW_CAPACITY_MAX}")
    rows = re.findall(r'/asset/(SUB-SGW-\d+)', html.split("<tbody>")[1])
    assert len(set(rows)) >= config.CREW_CAPACITY_MAX
    briefs = json.loads((config.OUTPUT_DIR / f"briefs_{SCENARIO}.json").read_text())
    missing = [a for a in rows[:config.CREW_CAPACITY_MAX] if a not in briefs]
    assert not missing, f"visible rows with no brief: {missing}"


def test_a_capacity_beyond_the_range_is_clamped_not_rejected():
    for value in ("99", "0", "-4"):
        assert client.get(f"/scenario/{SCENARIO}?capacity={value}").status_code == 200


def test_the_baseline_row_is_shown_as_a_probability():
    """`-5.827 log-odds` is the model's unit and means nothing on its own."""
    asset_id = top_asset_id(SCENARIO)
    html = client.get(f"/scenario/{SCENARIO}/asset/{asset_id}").text
    match = re.search(r"(\d+\.\d+)% — the failure rate", html)
    assert match, "baseline is not expressed as a percentage"
    scored = json.loads((config.OUTPUT_DIR / f"scored_{SCENARIO}.json").read_text())
    expected = 100.0 / (1.0 + math.exp(-scored["intercept"]))
    assert math.isclose(float(match.group(1)), expected, abs_tol=0.01)


@pytest.mark.parametrize("scenario_id", [s[0] for s in config.SCENARIOS])
def test_no_raw_feature_name_reaches_the_screen(scenario_id):
    """FEATURE_LABELS is the only path to the interface."""
    asset_id = top_asset_id(scenario_id)
    for path in [f"/scenario/{scenario_id}", f"/scenario/{scenario_id}/asset/{asset_id}"]:
        html = client.get(path).text
        leaked = [name for name in config.FEATURES if name in html]
        assert not leaked, f"{path} leaked {leaked}"


@pytest.mark.parametrize("scenario_id", [s[0] for s in config.SCENARIOS])
def test_a_categorical_feature_is_never_shown_on_a_continuous_scale(scenario_id):
    """A gradient bar asserts a continuum, and cooling type does not have one.

    The page used to draw one for all fifteen features, which put ONAN — the
    lowest of three levels and the one that raises an asset's odds most — at 40%
    of the way along a green-to-red bar and captioned it "about typical".
    """
    asset_id = top_asset_id(scenario_id)
    html = client.get(f"/scenario/{scenario_id}/asset/{asset_id}").text
    n_categorical = len(config.FEATURE_STATES)
    assert html.count('class="states"') == n_categorical
    assert html.count('class="rangebar"') == len(config.FEATURES) - n_categorical


def test_every_level_of_a_categorical_is_shown_not_just_the_asset_s_own():
    """Three cooling types are three segments, whichever one the asset is.

    Showing only the reading leaves the reader unable to tell whether ONAN is
    one of two options or one of five.
    """
    asset_id = top_asset_id(SCENARIO)
    html = client.get(f"/scenario/{SCENARIO}/asset/{asset_id}").text
    # From each states block to the comparison note that closes the cell; the
    # inner spans make a match on the outer closing tag unreliable.
    blocks = re.findall(r'<span class="states".*?(?=<span class="rangenote")',
                        html, flags=re.DOTALL)
    assert len(blocks) == len(config.FEATURE_STATES)
    # The marker naming the asset's own level is for screen readers, not for the
    # list of levels; drop it before reading the labels off.
    spoken = re.compile(r'<span class="visually-hidden">[^<]*</span>')
    rendered = [re.findall(r'<span class="state[^"]*">([^<]*)</span>', spoken.sub("", block))
                for block in blocks]
    assert config.FEATURE_STATES["cooling_type_ordinal"] in rendered, \
        f"the three cooling types are not shown as three levels: {rendered}"
    for block, labels in zip(blocks, rendered):
        assert block.count("is-current") == 1, \
            f"exactly one level is the asset's own, found {block.count('is-current')} in {labels}"


@pytest.mark.parametrize("scenario_id", [s[0] for s in config.SCENARIOS])
def test_a_categorical_s_reading_is_shown_once_not_twice(scenario_id):
    """The levels are the reading, so printing the reading beside them said the
    same word twice — the page read "ONAN ONAN ONAF OFAF"."""
    asset_id = top_asset_id(scenario_id)
    html = client.get(f"/scenario/{scenario_id}/asset/{asset_id}").text
    scored = json.loads((config.OUTPUT_DIR / f"scored_{scenario_id}.json").read_text())
    asset = next(a for a in scored["assets"] if a["asset_id"] == asset_id)
    cells = dict(zip([c["label"] for c in asset["contributions"]], reading_cells(html)))
    for c in asset["contributions"]:
        if c["feature"] not in config.FEATURE_STATES:
            continue
        current = config.FEATURE_STATES[c["feature"]][c["state_index"]]
        # "this asset" is the screen-reader marker on the current level and is
        # part of neither the reading nor the levels.
        text = cells[c["label"]].replace("— this asset", "")
        occurrences = len(re.findall(rf"\b{re.escape(current)}\b", text))
        assert occurrences == 1, \
            f"{c['label']}: {current!r} appears {occurrences} times in {text!r}"


@pytest.mark.parametrize("scenario_id", [s[0] for s in config.SCENARIOS])
def test_a_categorical_carries_a_fleet_share_and_no_percentile(scenario_id):
    """The two comparators are not interchangeable, and the wrong one for a
    category is the one that misled. A template reaching for `percentile` on a
    cooling type should fail rather than render a cumulative share of levels."""
    scored = json.loads((config.OUTPUT_DIR / f"scored_{scenario_id}.json").read_text())
    for asset in scored["assets"][:20]:
        for c in asset["contributions"]:
            if c["feature"] in config.FEATURE_STATES:
                assert "percentile" not in c, f"{c['feature']} carries a percentile"
                assert 0.0 < c["state_share"] <= 1.0
                assert 0 <= c["state_index"] < len(config.FEATURE_STATES[c["feature"]])
            else:
                assert "state_share" not in c, f"{c['feature']} carries a state share"
                assert 0.0 <= c["percentile"] <= 1.0


def test_the_fleet_share_shown_for_a_state_is_the_share_of_the_fleet_in_it():
    """Measured against the register rather than trusted from the JSON."""
    import pandas as pd
    scored = json.loads((config.OUTPUT_DIR / f"scored_{SCENARIO}.json").read_text())
    assets = pd.read_csv(config.DATA_DIR / "assets.csv")
    observed = assets["cooling_type"].value_counts(normalize=True)
    for asset in scored["assets"][:20]:
        c = next(c for c in asset["contributions"] if c["feature"] == "cooling_type_ordinal")
        state = config.FEATURE_STATES["cooling_type_ordinal"][c["state_index"]]
        assert state == asset["cooling_type"]
        assert math.isclose(c["state_share"], observed[state], abs_tol=0.01), \
            f"{state}: JSON says {c['state_share']:.2%}, register says {observed[state]:.2%}"

    # And that the figure survives the trip to the page. Checking only the JSON
    # left the rendered wording free to say anything at all.
    asset = scored["assets"][0]
    html = client.get(f"/scenario/{SCENARIO}/asset/{asset['asset_id']}").text
    label = config.FEATURE_LABELS["cooling_type_ordinal"]
    row = re.search(
        rf'{re.escape(label)}</td>.*?<span class="rangenote">([^<]+)</span>',
        html, flags=re.DOTALL)
    assert row, "no cooling type row on the page"
    expected = f"{observed[asset['cooling_type']]:.0%} of the fleet"
    assert row.group(1).strip() == expected, f"page reads {row.group(1)!r}, expected {expected!r}"


def test_sorting_a_column_is_a_plain_link_that_works_without_scripting():
    """The script keeps the reader's place across a sort; it must not be what
    makes the sort happen. Every header is an ordinary GET that stands alone."""
    html = queue_html()
    hrefs = re.findall(r'<a class="sort[^"]*"\s+href="([^"]+)"', html)
    assert len(hrefs) == len(app_module.sort_columns())
    for href in hrefs:
        assert href.startswith(f"/scenario/{SCENARIO}?sort="), href
        assert client.get(href.replace("&amp;", "&")).status_code == 200


@pytest.mark.parametrize("scenario_id", [s[0] for s in config.SCENARIOS])
def test_every_asset_carries_an_intervention_type_backed_by_its_own_contributions(scenario_id):
    """The label is derived from the contributions on the same page, so it can be
    checked against them. A driver that is not raising this asset's risk would be
    an assertion with nothing behind it."""
    scored = json.loads((config.OUTPUT_DIR / f"scored_{scenario_id}.json").read_text())
    for asset in scored["assets"]:
        assert asset["intervention_type"] in config.INTERVENTION_LABELS, asset["intervention_type"]
        driver = asset["intervention_driver"]
        if driver is None:
            assert asset["intervention_type"] == "monitor"
            continue
        contribution = next(c for c in asset["contributions"] if c["feature"] == driver)
        assert contribution["contribution"] > 0, \
            f"{asset['asset_id']}: {driver} is not raising its risk"
        assert driver in config.CREW_DRIVERS | config.REMOTE_DRIVERS


@pytest.mark.parametrize("scenario_id", [s[0] for s in config.SCENARIOS])
def test_the_classification_is_reproducible_from_the_stored_contributions(scenario_id):
    """Re-running the rule over the scored JSON must give back what is stored.
    A derived field that cannot be re-derived is a second source of truth."""
    model = pytest.importorskip("model")
    scored = json.loads((config.OUTPUT_DIR / f"scored_{scenario_id}.json").read_text())
    for asset in scored["assets"]:
        recomputed = model.classify_intervention(asset["contributions"])
        assert recomputed == (asset["intervention_type"], asset["intervention_driver"]), \
            f"{asset['asset_id']}: stored {asset['intervention_type']}, rule gives {recomputed[0]}"


@pytest.mark.parametrize("scenario_id", [s[0] for s in config.SCENARIOS])
def test_no_raw_intervention_value_reaches_the_screen(scenario_id):
    """INTERVENTION_LABELS is the only path to the interface, as FEATURE_LABELS
    is for feature names. The badge's style hook is an index for this reason —
    a class of `intervention-crew` would put the stored token on the page."""
    asset_id = top_asset_id(scenario_id)
    for path in [f"/scenario/{scenario_id}", f"/scenario/{scenario_id}/asset/{asset_id}"]:
        html = client.get(path).text
        # "crew" is ordinary prose on this page; a stored value appearing inside
        # a markup attribute is what would be a leak.
        attributes = re.findall(r'\w+="([^"]*)"', html)
        for value in config.INTERVENTION_LABELS:
            leaked = [a for a in attributes if re.search(rf"\b{value}\b", a)]
            assert not leaked, f"{path} leaked {value!r} in {leaked}"
        assert "intervention_type" not in html and "intervention_driver" not in html


def test_the_interface_says_load_transfer_and_never_de_rating():
    """A load transfer moves demand to an adjacent feeder without interrupting
    supply. Restricting throughput can mean shedding customers, which is the
    outcome the system exists to avoid. The two must not be conflated in text
    this project writes — a cited procedure may use its own vocabulary."""
    written = list(config.INTERVENTION_LABELS.values()) + list(config.INTERVENTION_NOTES.values())
    written.append(config.INTERVENTION_NO_DRIVER_NOTE)
    for text in written:
        lowered = text.lower()
        for forbidden in ("de-rat", "derat", "load restriction", "load shed", "curtail"):
            assert forbidden not in lowered, f"{forbidden!r} in {text!r}"
    assert config.INTERVENTION_LABELS["remote"] == "Load transfer"


def test_the_coverage_figure_counts_interventions_not_rows():
    """It reports the crew visits and the load transfers alongside them. Counting
    every row above the line would credit the monitor rows, which get nothing."""
    scored = json.loads((config.OUTPUT_DIR / f"scored_{SCENARIO}.json").read_text())
    capacity = capacity_within_the_visible_queue()
    covered = app_module.coverage(scored["assets"], capacity)
    assert covered["crew_count"] == capacity
    assert covered["covered_count"] == covered["crew_count"] + covered["remote_count"]

    above = [a for a in scored["assets"] if a["rank"] <= covered["line"]]
    monitors = [a for a in above if a["intervention_type"] == "monitor"]
    assert covered["covered_count"] == len(above) - len(monitors)
    expected = sum(a["risk"] for a in above if a["intervention_type"] != "monitor")
    assert math.isclose(covered["intercepted"], expected, rel_tol=1e-9)

    html = queue_html(f"?capacity={capacity}")
    assert f">{covered['remote_count']}</strong> load transfers" in html
