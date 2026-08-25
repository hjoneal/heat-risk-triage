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
    assert "capacity-rule" in queue_html()
    for query in ["?sort=customers&direction=desc", "?sort=risk&direction=asc",
                  "?sort=priority&direction=asc", "?sort=criticality&direction=desc"]:
        html = queue_html(query)
        assert "capacity-rule" not in html, f"capacity line drawn for {query}"
        assert "not that order" in html, f"no explanation offered for {query}"


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
    readings = re.findall(r'<td class="reading">\s*([^<\n]+?)\s*<', html)
    assert len(readings) >= len(config.FEATURES)
    for reading in readings[:len(config.FEATURES)]:
        assert not re.fullmatch(r"-?[\d.,]+", reading), \
            f"{reading!r} is a bare number with no unit or referent"


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
