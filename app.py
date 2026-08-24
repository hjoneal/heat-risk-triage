"""The queue an operations manager reads 72 hours before the event.

Serve time is inert. Every scored file and every brief is loaded into memory at
startup; a request runs no model, opens no socket and fetches no asset from a
third party. The only write is appending a decision to output/decisions.jsonl.

That property is why `llm.py`, `model.py` and `retrieve.py` are not imported
here, and why there is no code path in this file that could reach an API.
"""

import json
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


def sparkline(hourly_temps):
    """Server-rendered polyline points, plus the overnight minimum markers.

    Built here rather than in the template so the template holds no arithmetic,
    and no JavaScript is involved at all.
    """
    width, height, pad = 720.0, 64.0, 4.0
    lowest, highest = min(hourly_temps), max(hourly_temps)
    span = highest - lowest or 1.0

    def x_at(index):
        return pad + (width - 2 * pad) * index / max(len(hourly_temps) - 1, 1)

    def y_at(value):
        return pad + (height - 2 * pad) * (1 - (value - lowest) / span)

    points = " ".join(f"{x_at(i):.1f},{y_at(t):.1f}" for i, t in enumerate(hourly_temps))

    markers = []
    for day_start in range(0, len(hourly_temps), config.HOURS_PER_DAY):
        night = hourly_temps[day_start:day_start + config.OVERNIGHT_HOURS]
        if not night:
            continue
        index = day_start + night.index(min(night))
        markers.append({"x": round(x_at(index), 1), "y": round(y_at(hourly_temps[index]), 1),
                        "temp": round(hourly_temps[index], 1)})

    return {"points": points, "markers": markers, "width": width, "height": height,
            "low": round(lowest, 1), "high": round(highest, 1)}


def reason_line(asset):
    """The one or two factors that pushed this asset up, in human labels.

    FEATURE_LABELS is the only path from a feature name to the screen; a raw
    name reaching a template is a bug.
    """
    positive = [c for c in asset["contributions"] if c["contribution"] > 0]
    return [c["label"] for c in positive[:2]]


def queue_rows(scenario_id, sort_key):
    scored = SCORED[scenario_id]
    assets = scored["assets"][:config.QUEUE_ROWS]
    if sort_key == "risk":
        assets = sorted(assets, key=lambda a: -a["risk"])

    decisions = read_decisions()
    highest_risk = max((a["risk"] for a in assets), default=1.0) or 1.0

    rows = []
    for position, asset in enumerate(assets, start=1):
        decision = decisions.get((scenario_id, asset["asset_id"]))
        share = asset["risk"] / highest_risk
        # Four steps of the thermal ramp, relative to the highest-risk asset in
        # this scenario. The step is decoration on a bar that already sits beside
        # the number; nothing is communicated by colour alone.
        heat_step = min(int(share * 4) + 1, 4)
        rows.append({
            **asset,
            "position": position,
            "reasons": reason_line(asset),
            "risk_pct": asset["risk"] * 100,
            "bar_pct": 100.0 * share,
            "heat_step": heat_step,
            "decision": decision["decision"] if decision else None,
        })
    return rows


@app.get("/")
def index():
    return RedirectResponse(f"/scenario/{config.SCENARIOS[0][0]}")


@app.get("/scenario/{scenario_id}")
def queue(request: Request, scenario_id: str, sort: str = "priority"):
    scored = SCORED[scenario_id]
    return templates.TemplateResponse("queue.html", {
        "request": request,
        "scenario": scored,
        "scenario_id": scenario_id,
        "scenarios": SCENARIO_LINKS,
        "rows": queue_rows(scenario_id, sort),
        "sort": sort,
        "sparkline": sparkline(scored["hourly_temps"]),
        "crew_capacity": config.CREW_CAPACITY,
    })


@app.get("/scenario/{scenario_id}/asset/{asset_id}")
def asset_detail(request: Request, scenario_id: str, asset_id: str):
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
        "asset": asset,
        "brief": brief,
        "cited": cited,
        "decision": decision,
        "intercept": scored["intercept"],
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
                    decision: str = Form(...), reason: str = Form("")):
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

    return RedirectResponse(f"/scenario/{scenario_id}", status_code=303)
