"""The queue an operations manager reads 72 hours before the event.

Serve time is inert. Every scored file and every brief is loaded into memory at
startup; a request runs no model, opens no socket and fetches no asset from a
third party. The only write is appending a decision to output/decisions.jsonl.

That property is why `llm.py`, `model.py` and `retrieve.py` are not imported
here, and why there is no code path in this file that could reach an API.
"""

import json
import math
from collections import Counter
from datetime import datetime, timezone

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import config

app = FastAPI(title="Heat risk triage")
app.mount("/static", StaticFiles(directory=config.REPO_ROOT / "static"), name="static")
templates = Jinja2Templates(directory=str(config.TEMPLATES_DIR))


def load_scored():
    scored = {}
    for scenario_id, _, _, _, _, _ in config.SCENARIOS:
        path = config.OUTPUT_DIR / f"scored_{scenario_id}.json"
        assert path.exists(), f"{path} is missing; run model.py before serving"
        scored[scenario_id] = json.loads(path.read_text())
    return scored


def load_briefs():
    briefs = {}
    for scenario_id, _, _, _, _, _ in config.SCENARIOS:
        path = config.OUTPUT_DIR / f"briefs_{scenario_id}.json"
        assert path.exists(), f"{path} is missing; run retrieve.py before serving"
        briefs[scenario_id] = json.loads(path.read_text())
    return briefs


def load_procedures():
    procedures = {}
    for path in sorted(config.PROCEDURES_DIR.glob("*.md")):
        text = path.read_text()
        _, header, body = text.split("---\n", 2)
        fields = {}
        for line in header.strip().splitlines():
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
        procedures[fields["doc_id"]] = {
            "doc_id": fields["doc_id"],
            "title": fields["title"],
            "category": fields["category"],
            "applies_to": fields["applies_to"],
            "body": body.strip(),
        }
    return procedures


SCORED = load_scored()
BRIEFS = load_briefs()
PROCEDURES = load_procedures()
SCENARIO_LINKS = [
    {"scenario_id": s[0], "label": s[1]} for s in config.SCENARIOS
]


def read_decisions():
    """Decisions are read per request so a newly recorded one shows immediately.

    The only file the application reads after startup, and the only one it
    writes. Kept out of the in-memory load deliberately.
    """
    decisions = {}
    if not config.DECISIONS_LOG.exists():
        return decisions
    for line in config.DECISIONS_LOG.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        decisions[(record["scenario"], record["asset_id"])] = record
    return decisions


def axis_ticks(low, high):
    """Temperature gridlines at a round interval covering the observed range.

    Chosen from a fixed ladder rather than computed, so the axis never lands on
    an interval like 3.7 °C. Whichever step first yields at most the target
    number of lines wins.
    """
    for step in config.CHART_TEMPERATURE_STEPS_C:
        first = math.floor(low / step) * step
        last = math.ceil(high / step) * step
        count = int(round((last - first) / step)) + 1
        if count <= config.CHART_MAX_TEMPERATURE_LINES:
            return [first + i * step for i in range(count)]
    return [low, high]


def sparkline(hourly_temps):
    """Server-rendered temperature chart: polyline, axes, gridlines and markers.

    Built here rather than in the template so the template holds no arithmetic,
    and no JavaScript is involved at all. The curve carried no axes until a
    reader pointed out that a shape without a scale is decoration — an hourly
    temperature trace is only readable against the degrees it spans and the hour
    of day it peaks at.
    """
    width, height = float(config.CHART_WIDTH), float(config.CHART_HEIGHT)
    left, right = float(config.CHART_PAD_LEFT), float(config.CHART_PAD_RIGHT)
    top, bottom = float(config.CHART_PAD_TOP), float(config.CHART_PAD_BOTTOM)

    lowest, highest = min(hourly_temps), max(hourly_temps)
    ticks = axis_ticks(lowest, highest)
    # The plotted band is the gridlines' span, so the curve never runs off an axis.
    floor_c, ceiling_c = min(ticks[0], lowest), max(ticks[-1], highest)
    span = ceiling_c - floor_c or 1.0
    plot_width, plot_height = width - left - right, height - top - bottom

    def x_at(index):
        return left + plot_width * index / max(len(hourly_temps) - 1, 1)

    def y_at(value):
        return top + plot_height * (1 - (value - floor_c) / span)

    points = " ".join(f"{x_at(i):.1f},{y_at(t):.1f}" for i, t in enumerate(hourly_temps))

    markers = []
    for day_start in range(0, len(hourly_temps), config.HOURS_PER_DAY):
        night = hourly_temps[day_start:day_start + config.OVERNIGHT_HOURS]
        if not night:
            continue
        index = day_start + night.index(min(night))
        markers.append({"x": round(x_at(index), 1), "y": round(y_at(hourly_temps[index]), 1),
                        "temp": round(hourly_temps[index], 1)})

    # Hour-of-day labels at midnight and midday. Every tick would be unreadable
    # over a six-day event, and the two that matter are when it is coldest and
    # when the load peaks.
    hours = []
    for index in range(0, len(hourly_temps), config.CHART_HOUR_TICK_INTERVAL):
        hour_of_day = index % config.HOURS_PER_DAY
        hours.append({
            "x": round(x_at(index), 1),
            "label": f"{hour_of_day:02d}:00",
            "major": hour_of_day == 0,
            "day": index // config.HOURS_PER_DAY + 1,
        })

    return {
        "points": points,
        "markers": markers,
        "width": width, "height": height,
        "plot_left": left, "plot_right": width - right,
        "plot_top": top, "plot_bottom": height - bottom,
        "temperatures": [{"value": round(t, 1), "y": round(y_at(t), 1)} for t in ticks],
        "hours": hours,
        "days": len(hourly_temps) // config.HOURS_PER_DAY,
        "low": round(lowest, 1), "high": round(highest, 1),
    }


def reason_line(asset):
    """The one or two factors that pushed this asset up, in human labels.

    FEATURE_LABELS is the only path from a feature name to the screen; a raw
    name reaching a template is a bug.
    """
    positive = [c for c in asset["contributions"] if c["contribution"] > 0]
    return [c["label"] for c in positive[:2]]


def percentile_label(share):
    """A percentile as a phrase, because "0.94" is not a comparison anyone makes.

    Bands rather than a figure: the reference set is 14,400 synthetic asset-event
    rows, which does not support a claim finer than this.
    """
    for threshold, label in config.PERCENTILE_BANDS:
        if share >= threshold:
            return label
    return config.PERCENTILE_BANDS[-1][1]


def intervention_view(asset):
    """What put this asset in the queue, in one line.

    Not what to do about it — that is the brief's job, and the brief can say
    "transfer load and repair the fan" where this cannot. The badge names the
    driver class; the note names the driver itself, because without it the badge
    is an assertion the reader has to take on trust and the whole point of the
    contribution table below is that they do not have to.
    """
    intervention_type = asset["intervention_type"]
    driver = asset["intervention_driver"]
    return {
        "label": config.INTERVENTION_LABELS[intervention_type],
        "step": config.INTERVENTION_STYLE_INDEX[intervention_type],
        "note": (
            config.INTERVENTION_NOTES[intervention_type].format(
                driver=config.FEATURE_LABELS[driver].lower())
            if driver else config.INTERVENTION_NO_DRIVER_NOTE
        ),
    }


def state_view(contribution):
    """A categorical feature's levels, with the asset's own marked.

    Two levels for a condition flag, three for cooling type, and the reader can
    see which it is out of how many. The alternative the page used to show — a
    green-to-red gradient with a marker on it — asserts that the levels lie on a
    continuum and that the reading sits somewhere along it, which is true of a
    temperature and false of a cooling type.
    """
    states = config.FEATURE_STATES[contribution["feature"]]
    return [
        {"label": label, "is_current": index == contribution["state_index"]}
        for index, label in enumerate(states)
    ]


def contribution_view(contributions):
    """Add the display-only fields the contribution table needs.

    Kept out of the scored JSON because these are presentation choices — a bar
    length and a form of words — and the JSON is the record of what the model
    computed. `odds_multiplier`, `reading`, `percentile` and `state_share` are
    in the JSON because they are properties of the fit, not of this page.
    """
    widest = max((abs(c["contribution"]) for c in contributions), default=1.0) or 1.0
    rows = []
    for c in contributions:
        is_state = c["feature"] in config.FEATURE_STATES
        rows.append({
            **c,
            "is_state": is_state,
            "states": state_view(c) if is_state else None,
            # How much of the fleet reads the same way. For a category that is
            # the share in this state; for everything else it is a band, because
            # the reference set is 14,400 synthetic asset-event rows and does not
            # support a claim finer than one.
            "comparison": (
                f"{c['state_share']:.0%} of the fleet" if is_state
                else percentile_label(c["percentile"])
            ),
            "effect_pct": 100.0 * abs(c["contribution"]) / widest,
            # Only where the factor is raising the odds: a flag reading "no"
            # lowers them and has no repair to offer.
            "remedy": remedy_view(c),
        })
    return rows


def remedy_view(contribution):
    """Which kind of action addresses this factor, or None if nothing does.

    The same two driver sets the badge is derived from, read one row at a time.
    Nine of the fifteen features are addressable by nothing inside 72 hours —
    the weather, the asset's age, its cooling design — and saying so against
    each of them is the honest half of the table: it shows how much of an
    asset's risk the crew budget cannot touch at all.
    """
    if contribution["contribution"] <= 0:
        return None
    feature = contribution["feature"]
    for key, drivers in (("crew", config.CREW_DRIVERS), ("remote", config.REMOTE_DRIVERS)):
        if feature in drivers:
            return {"label": config.REMEDY_LABELS[key],
                    "step": config.INTERVENTION_STYLE_INDEX[key]}
    return None


def clamp_capacity(value):
    """Keep the capacity control inside the range the queue can display.

    A capacity beyond the rows on screen would put the line nowhere, and a
    negative one would put it before the first row.
    """
    try:
        capacity = int(value)
    except (TypeError, ValueError):
        return config.CREW_CAPACITY
    return max(config.CREW_CAPACITY_MIN, min(config.CREW_CAPACITY_MAX, capacity))


def sort_columns():
    """Which queue columns can be sorted, and how each reads out of a row.

    The queue used to carry two buttons offering the only two orders anyone had
    anticipated. Every column a supervisor can see is a question they might be
    asking — who serves the most customers, what is most critical — so each
    header sorts instead.
    """
    return {
        "rank": lambda a: a["rank"],
        "name": lambda a: a["name"].lower(),
        "risk": lambda a: a["risk"],
        "customers": lambda a: a["customers_served"],
        "priority": lambda a: a["priority"],
    }


def queue_filters(assets):
    """The view filters, with the count behind each.

    Built from INTERVENTION_LABELS rather than listed here, so an intervention
    type cannot appear on a badge and be missing from the filter that would find
    it. The count matters as much as the label: "Crew visit (366)" says something
    about this forecast that "Crew visit" does not.
    """
    counts = Counter(a["intervention_type"] for a in assets)
    filters = [{"key": config.QUEUE_FILTER_ALL, "label": "All", "count": len(assets)}]
    filters += [
        {"key": key, "label": label, "count": counts.get(key, 0)}
        for key, label in config.INTERVENTION_LABELS.items()
    ]
    return filters


def queue_rows(scenario_id, sort_key, descending, intervention_filter):
    scored = SCORED[scenario_id]
    assets = scored["assets"]
    if intervention_filter != config.QUEUE_FILTER_ALL:
        assets = [a for a in assets if a["intervention_type"] == intervention_filter]
    columns = sort_columns()
    key = columns.get(sort_key, columns[config.QUEUE_DEFAULT_SORT])
    assets = sorted(assets, key=key, reverse=descending)

    decisions = read_decisions()
    highest_risk = max((a["risk"] for a in assets), default=1.0) or 1.0

    rows = []
    for asset in assets:
        decision = decisions.get((scenario_id, asset["asset_id"]))
        share = asset["risk"] / highest_risk
        # Four steps of the thermal ramp, relative to the highest-risk asset in
        # this scenario. The step is decoration on a bar that already sits beside
        # the number; nothing is communicated by colour alone.
        heat_step = min(int(share * 4) + 1, 4)
        rows.append({
            **asset,
            # The asset's place in the dispatch order, not its position in
            # whatever order the reader has sorted into. Renumbering it per sort
            # would make the column mean something different on every click.
            "position": asset["rank"],
            "reasons": reason_line(asset),
            "risk_pct": asset["risk"] * 100,
            "bar_pct": 100.0 * share,
            "heat_step": heat_step,
            # INTERVENTION_LABELS is the only path from the stored value to the
            # page, and the style hook is an index rather than the value itself,
            # so the raw token has no route to the screen at all.
            "intervention_label": config.INTERVENTION_LABELS[asset["intervention_type"]],
            "intervention_step": config.INTERVENTION_STYLE_INDEX[asset["intervention_type"]],
            # Briefs cover the top BRIEF_TOP_N. Now that the queue shows the whole
            # fleet, most rows have none, and the reader should know that before
            # following the link rather than after.
            "has_brief": asset["rank"] <= config.BRIEF_TOP_N,
            "decision": decision["decision"] if decision else None,
        })
    return rows


def crew_capacity_line(assets, crew_capacity):
    """Where the crew stops, counting only the rows that need a crew.

    The line used to fall after row *n*. Rows whose risk is driven by loading are
    remedied from a desk and consume no crew capacity, so counting them against
    the budget drew the line short — often far short. Returns the dispatch rank
    the *n*th crew row sits at, or None when the ranking does not contain that
    many, which is a thing the page has to say rather than silently omit.
    """
    crew_ranks = [a["rank"] for a in assets if a["intervention_type"] == "crew"]
    return crew_ranks[crew_capacity - 1] if len(crew_ranks) >= crew_capacity else None


def coverage(assets, crew_capacity):
    """What the crew budget actually reaches, once the queue is read this way.

    Everything above the line that carries an intervention — the crew rows and
    the load transfers alongside them — not merely the first *n* rows. Monitor
    rows sit above the line at their correct rank and receive nothing, so they
    are excluded from the covered set and from the count it is compared against.

    All sums of already-scored probabilities. No model runs here.
    """
    line = crew_capacity_line(assets, crew_capacity)
    # With fewer crew rows than the budget, the crew never stops: the whole
    # ranking is within reach and the covered set is every actionable row in it.
    covered = [a for a in assets
               if (line is None or a["rank"] <= line)
               and a["intervention_type"] in ("crew", "remote")]
    crew = [a for a in covered if a["intervention_type"] == "crew"]
    remote = [a for a in covered if a["intervention_type"] == "remote"]

    intercepted = sum(a["risk"] for a in covered)
    fleet_expected = sum(a["risk"] for a in assets)
    fleet_size = len(assets)
    risk_share = intercepted / fleet_expected if fleet_expected else 0.0
    fleet_share = len(covered) / fleet_size if fleet_size else 0.0
    return {
        "line": line,
        "crew_count": len(crew),
        "remote_count": len(remote),
        "covered_count": len(covered),
        "intercepted": intercepted,
        "fleet_expected": fleet_expected,
        "fleet_size": fleet_size,
        "risk_share": risk_share,
        "fleet_share": fleet_share,
        # How much better than choosing the same number of assets at random.
        "concentration": risk_share / fleet_share if fleet_share else 0.0,
    }


@app.get("/")
def index():
    return RedirectResponse(f"/scenario/{config.SCENARIOS[0][0]}")


@app.get("/scenario/{scenario_id}")
def queue(request: Request, scenario_id: str, sort: str = config.QUEUE_DEFAULT_SORT,
          direction: str = "desc", capacity: str = str(config.CREW_CAPACITY),
          view: str = config.QUEUE_FILTER_ALL):
    scored = SCORED[scenario_id]
    crew_capacity = clamp_capacity(capacity)
    if sort not in sort_columns():
        sort = config.QUEUE_DEFAULT_SORT
    # An unknown filter falls back rather than erroring, as an unknown sort does:
    # a mistyped URL should show the queue, not a stack trace.
    if view != config.QUEUE_FILTER_ALL and view not in config.INTERVENTION_LABELS:
        view = config.QUEUE_FILTER_ALL
    descending = direction != "asc"
    rows = queue_rows(scenario_id, sort, descending, view)

    # The capacity line marks where the crew stops working down the dispatch
    # order. Drawn in any other order it would say that the fifteen rows above it
    # get visited, which is only true when the queue is in that order.
    dispatch_order = sort in (config.QUEUE_DEFAULT_SORT, "rank") and (
        descending if sort == config.QUEUE_DEFAULT_SORT else not descending)

    # The concentration the ranking achieves, not a claim about prevention. The
    # first figure is the expected failures sitting among the assets the crew
    # would reach; the second is the expected total across all 900. Both are sums
    # of already-scored probabilities — no model runs here.
    #
    # It is reported as a share and a lift because the raw pair reads as failure:
    # "0.5 of 5.0" sounds like the tool missing nine tenths of the problem, when
    # it is the arithmetic of visiting 2.8% of a fleet whose mean risk is under
    # 1%. What the ranking can be judged on is how much better than 2.8% of the
    # risk those visits carry.
    covered = coverage(scored["assets"], crew_capacity)

    return templates.TemplateResponse("queue.html", {
        "request": request,
        "scenario": scored,
        "scenario_id": scenario_id,
        "scenarios": SCENARIO_LINKS,
        "rows": rows,
        "sort": sort,
        "direction": "desc" if descending else "asc",
        "view": view,
        "filters": queue_filters(scored["assets"]),
        "intervention_heading": config.INTERVENTION_COLUMN_HEADING,
        "brief_top_n": config.BRIEF_TOP_N,
        "dispatch_order": dispatch_order,
        "sparkline": sparkline(scored["hourly_temps"]),
        "crew_capacity": crew_capacity,
        "capacity_min": config.CREW_CAPACITY_MIN,
        "capacity_max": config.CREW_CAPACITY_MAX,
        "covered": covered,
        # The line falls after the nth crew row, which is usually further down
        # than the nth row. Beyond the visible queue it cannot be drawn, and the
        # page says where it fell instead.
        "capacity_line_row": next(
            (i for i, row in enumerate(rows, start=1) if row["position"] == covered["line"]),
            None),
    })


@app.get("/scenario/{scenario_id}/asset/{asset_id}")
def asset_detail(request: Request, scenario_id: str, asset_id: str,
                 capacity: str = str(config.CREW_CAPACITY)):
    scored = SCORED[scenario_id]
    asset = next(a for a in scored["assets"] if a["asset_id"] == asset_id)
    brief = BRIEFS[scenario_id].get(asset_id)

    cited = []
    if brief:
        cited = [PROCEDURES[doc_id] for doc_id in brief["cited_doc_ids"] if doc_id in PROCEDURES]

    decision = read_decisions().get((scenario_id, asset_id))

    return templates.TemplateResponse("asset.html", {
        "request": request,
        "scenario": scored,
        "scenario_id": scenario_id,
        "scenarios": SCENARIO_LINKS,
        "asset": {**asset, "contributions": contribution_view(asset["contributions"])},
        "intervention": intervention_view(asset),
        "remedy_heading": config.REMEDY_COLUMN_HEADING,
        "remedy_none": config.REMEDY_NONE_LABEL,
        "brief": brief,
        "brief_top_n": config.BRIEF_TOP_N,
        "cited": cited,
        "decision": decision,
        "intercept": scored["intercept"],
        # The intercept as a probability. A bare -5.83 log-odds told a reader
        # nothing; 0.29% is the same number in the units the rest of the page uses.
        "baseline_pct": 100.0 / (1.0 + math.exp(-scored["intercept"])),
        "crew_capacity": clamp_capacity(capacity),
    })


@app.get("/procedure/{doc_id}")
def procedure(request: Request, doc_id: str):
    return templates.TemplateResponse("procedure.html", {
        "request": request,
        "scenarios": SCENARIO_LINKS,
        "scenario_id": config.SCENARIOS[0][0],
        "procedure": PROCEDURES[doc_id],
    })


@app.post("/scenario/{scenario_id}/asset/{asset_id}/decision")
def record_decision(scenario_id: str, asset_id: str,
                    decision: str = Form(...), reason: str = Form(""),
                    capacity: str = Form(str(config.CREW_CAPACITY))):
    scored = SCORED[scenario_id]
    asset = next(a for a in scored["assets"] if a["asset_id"] == asset_id)

    record = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scenario": scenario_id,
        "asset_id": asset_id,
        "decision": decision,
        "reason": reason,
        "rank_shown": asset["rank"],
        "risk_shown": asset["risk"],
    }
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with config.DECISIONS_LOG.open("a") as handle:
        handle.write(json.dumps(record) + "\n")

    return RedirectResponse(
        f"/scenario/{scenario_id}?capacity={clamp_capacity(capacity)}", status_code=303)
