"""Freight Control Tower API.  Run:  uvicorn backend.main:app

The API is a thin layer over app/ (tools, agent, guardrails). Models, rules and guardrails are unchanged.
Vendors are exposed by code only (V001...), never by name: inbound codes are translated before a tool runs, outbound payloads are scrubbed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import re
import sys
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

# Windows registries can map .js to text/plain, which browsers refuse for module scripts
for _ext, _mt in ((".js", "text/javascript"), (".mjs", "text/javascript"), (".css", "text/css"), (".woff2", "font/woff2"), (".svg", "image/svg+xml")):
    mimetypes.add_type(_mt, _ext)

from app import agent
from app import ask_pipeline as AP
from app import guardrails as G
from app import lanes as LN
from app import llm as L
from app import tools as T
from app.queries import catalogue as CAT
from backend import access_gate as AG
from backend import schemas as S
from backend import services as svc
from backend import vendor_codes as vc

ROOT = T.ROOT
DIST = os.path.join(ROOT, "frontend", "dist")
DEMO_BANNER = "Demo mode: explanations are rule-based; models and data are real"
BLOCKING = {"hallucination_blocked", "unknown_placeholder", "answer_without_slots", "no_tool_answer_blocked", "action_claim_blocked", "empty_reply"}

PERF_TARGET_MS = 300.0

# Plain-message lines on stdout: Container Apps ships stdout to Log Analytics, and infra/queries parse the [reqlog] lines.
log = logging.getLogger("fct")
log.setLevel(logging.INFO)
log.propagate = False
if not log.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(_h)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Load every table, model pipeline and SHAP explainer, and precompute the Overview/Vendors/Hubs payloads,
    before the app accepts traffic. Then time one call to each of those three endpoints to confirm they are now
    served from memory. Function names (overview/vendors/hubs) resolve at call time, once the whole module -
    including the route handlers defined below - has finished loading."""
    t0 = time.perf_counter()
    await run_in_threadpool(svc.warmup)
    cold_start_ms = (time.perf_counter() - t0) * 1000
    _state["cold_start_ms"] = cold_start_ms
    log.info("[startup] cold start (tables, %d model pipelines, SHAP explainers and cached payloads): %.0f ms", len(svc.MODEL_FILES), cold_start_ms)

    checks = [("GET /api/overview", lambda: overview()),
              ("GET /api/vendors", lambda: vendors(q=None, min_lrs=10, flagged=False, sort="spend_rs", order="desc", page=1, page_size=25)),
              ("GET /api/hubs", lambda: hubs(top=25))]
    for label, fn in checks:
        t1 = time.perf_counter()
        await run_in_threadpool(fn)
        ms = (time.perf_counter() - t1) * 1000
        flag = "OK" if ms < PERF_TARGET_MS else f"SLOW (target < {PERF_TARGET_MS:.0f} ms)"
        _state["first_request_ms"][label] = ms
        log.info("[startup] first-request latency %s: %.1f ms [%s]", label, ms, flag)
    _state["ready"] = True
    yield


app = FastAPI(title="Freight Control Tower API", version="1.0.0", docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json",
              description="Student capstone prototype, not an official Delhivery system. The models and rules decide; the LLM only explains and drafts. "
                          "A person approves every action. Vendors are anonymised codes.", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=800)  # event streams are skipped by Starlette, so Ask still streams


def parse_allowed_hosts(value: str | None) -> list[str]:
    """ALLOWED_HOSTS: comma-separated host/FQDN allow-list (e.g. "fct-app.<region>.azurecontainerapps.io").
    Unset, empty or "*" means no restriction (local dev): returns []."""
    hosts = [h.strip() for h in (value or "*").split(",") if h.strip()]
    return [] if hosts in ([], ["*"]) else hosts


_allowed_hosts = parse_allowed_hosts(os.environ.get("ALLOWED_HOSTS"))
if _allowed_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_allowed_hosts)

# Shared-password gate: a no-op unless APP_ACCESS_CODE is set, so local dev and tests
# are unaffected. /api/health and /api/ready always stay open for Container Apps probes.
app.add_middleware(AG.AccessGateMiddleware)


@app.get("/signin", include_in_schema=False)
async def signin_get(request: Request):
    return await AG.signin_page(request)


@app.post("/signin", include_in_schema=False)
async def signin_post(request: Request):
    return await AG.signin_submit(request)


@app.middleware("http")
async def _cache_hashed_assets(request: Request, call_next):
    """Vite file names carry a content hash, so built assets can be cached for a year."""
    resp = await call_next(request)
    if request.url.path.startswith("/assets/"):
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


@app.middleware("http")
async def _request_log(request: Request, call_next):
    """A one-line structured log per request, to stdout. Container Apps ships stdout to Log Analytics
    (ContainerAppConsoleLogs_CL), which is what the README's "slowest requests" saved query parses."""
    t0 = time.perf_counter()
    resp = await call_next(request)
    ms = (time.perf_counter() - t0) * 1000
    log.info("[reqlog] %s %s %s %.1fms", request.method, request.url.path, resp.status_code, ms)
    return resp


_state = {"last_routed_via": None, "first_request_ms": {}, "ready": False}
_run_lock = threading.Lock()  # the agent, SHAP explainers and the audit file are shared; one run at a time keeps them simple and safe


@app.exception_handler(svc.NotFound)
async def _nf(_: Request, e: svc.NotFound):
    return JSONResponse({"detail": str(e)}, status_code=404)


@app.exception_handler(LN.LaneNotFound)
async def _lnf(_: Request, e: LN.LaneNotFound):
    return JSONResponse(out({"detail": f"{e}." + (f" Did you mean: {', '.join(e.suggestions)}?" if e.suggestions else " Pick one from the list."), "kind": e.kind, "suggestions": e.suggestions}), status_code=404)


@app.exception_handler(LN.VendorNotFound)
async def _vnf(_: Request, e: LN.VendorNotFound):
    return JSONResponse({"detail": str(e)}, status_code=404)


@app.exception_handler(svc.BadRequest)
async def _br(_: Request, e: svc.BadRequest):
    return JSONResponse({"detail": str(e)}, status_code=400)


@app.exception_handler(T.ToolError)
async def _te(_: Request, e: T.ToolError):
    return JSONResponse({"detail": str(e)}, status_code=422)


@app.exception_handler(RequestValidationError)
async def _ve(_: Request, e: RequestValidationError):
    errs = [{"field": ".".join(str(p) for p in x["loc"][1:]), "message": x["msg"]} for x in e.errors()]
    return JSONResponse({"detail": "Invalid request", "errors": errs}, status_code=422)


def out(o):
    """Every payload leaves through here: JSON-safe and with vendor names replaced by codes."""
    return vc.scrub(svc.clean(o))


def _vendor_codes(rows: list[dict]) -> list[dict]:
    """Alternative-vendor rows carry vendor_code (the tools call the field vendor_name; names never leave the API)."""
    return [{("vendor_code" if k == "vendor_name" else k): v for k, v in r.items()} for r in rows]


def get_llm() -> L.LLM:
    return L.LLM()


def _remember(result: dict) -> None:
    if result.get("mode") in ("LIVE", "LIVE (guardrail fallback)") and result.get("model_used"):
        _state["last_routed_via"] = result["model_used"]


@app.get("/api/health", response_model=S.Health, tags=["system"])
def health():
    """Liveness probe. No table reads or model calls, so it answers in well under 100 ms even on a cold container."""
    llm = get_llm()
    live = not llm.demo
    return {"status": "ok", "mode": "LIVE" if live else "DEMO", "label": f"LIVE · {llm.model}" if live else "DEMO", "pinned_model": llm.model,
            "last_routed_via": _state["last_routed_via"], "data_as_of": svc.data_as_of(), "banner": None if live else DEMO_BANNER}


@app.get("/api/ready", tags=["system"])
def ready():
    """Readiness probe: 200 only once the lifespan warmup (tables, model pipelines, SHAP explainers, cached
    Overview/Vendors/Hubs payloads) has finished. Before that, 503 so Container Apps holds traffic back."""
    if not _state["ready"]:
        raise HTTPException(503, "warming up: tables, models and cached payloads are still loading")
    return {"status": "ready", "cold_start_ms": _state.get("cold_start_ms"), "first_request_ms": _state["first_request_ms"]}


@app.get("/api/meta/options", tags=["system"])
def meta_options():
    return svc.meta_options()


@app.get("/api/figures/{name}", tags=["models"])
def figure(name: str):
    if not re.fullmatch(r"[\w.-]+\.png", name):
        raise svc.NotFound("figure not found")
    p = os.path.join(svc.FIG_DIR, name)
    if not os.path.isfile(p):
        raise svc.NotFound("figure not found")
    return FileResponse(p, media_type="image/png")


@app.get("/api/overview", tags=["overview"])
def overview():
    """Five KPI tiles (value, change vs the previous quarter, monthly sparkline), the monthly margin trend, the top 5 lanes by loss, and alerts needing a decision."""
    return out(svc.overview())


@app.post("/api/booking/check", response_model=S.BookingResponse, tags=["booking"])
def booking_check(req: S.BookingRequest):
    """V1 fair price, V2 loss risk and V3 under-load risk for one planned booking, one recommended action (chosen by rules) and a draft message."""
    name = svc.vendor_name(req.vendor)
    booking = {k: v for k, v in req.model_dump().items() if v is not None}
    booking["vendor"] = name
    with _run_lock:
        res = agent.run_booking(booking, get_llm())
    _remember(res)
    if res["recommended_action"] is None:
        raise HTTPException(422, vc.scrub(res["text"]))
    calls = {c["name"]: c["output"] for c in res["tools_called"]}
    check, alts, cur = calls["check_booking"], calls.get("find_alternative_vendors"), calls.get("lane_rate")
    cur = cur if cur and cur.get("rs_per_mt") else None
    d = res["policy"]
    v2 = check["v2_loss_risk"]
    ratio = v2["probability"] / v2["threshold"] if v2["threshold"] else 0
    level = "high" if v2["flag"] else ("medium" if ratio >= 0.5 else "low")
    blocked = any(e.get("event") in BLOCKING for e in res["guardrail_events"])
    draft_call = next((c["output"] for c in res["tools_called"] if c["name"] == "draft_message"), None)
    return out({
        "run_id": res["run_id"], "recommended_action": res["recommended_action"], "summary": res["text"], "draft": res["draft"],
        "draft_template": draft_call.get("template_id") if draft_call else None, "banner": res["banner"], "mode": res["mode"], "model_used": res["model_used"],
        "guardrail_events": res["guardrail_events"], "guardrail_status": "replaced" if blocked else "passed", "inputs": check["inputs"],
        "assumed_defaults": check.get("assumed_defaults", {}), "warnings": check.get("warnings", []),
        "loss_risk": {**v2, "level": level}, "fair_price": check["v1_fair_price"], "underload": check["v3_underload_risk"],
        "alternatives": _vendor_codes((alts or {}).get("alternatives", [])), "current_rs_per_mt": d["current_rs_per_mt"],
        "policy": {"signal": d["signal"], "flagged": d["flagged"], "over_flag": d["over_flag"], "below_flag": d["below_flag"], "under_flag": d["under_flag"],
                   "cheaper_alternative": d["cheaper_alternative"], "smaller_vehicle": d["smaller_vehicle"], "lane_median_rs_per_mt": (alts or {}).get("lane_median_rs_per_mt")}})


@app.get("/api/lanes/{route}/alternatives", tags=["booking"])
def lane_alternatives(route: str, vehicle_type: str = Query(min_length=1), exclude_vendor: str | None = Query(None, pattern=r"^[Vv]\d{3,}$"), k: int = Query(5, ge=1, le=10)):
    """Cheapest vendors on a lane and vehicle type with at least 10 LRs (Rs per MT)."""
    excl = svc.vendor_name(exclude_vendor) if exclude_vendor else None
    res = T.find_alternative_vendors(route, vehicle_type, k, excl)
    return out({**res, "alternatives": _vendor_codes(res.get("alternatives", []))})


@app.get("/api/lanes/points", tags=["lanes"])
def lane_points():
    """Origins and, for each origin, the destinations reachable by a direct lane (for dependent dropdowns)."""
    return out(LN.lane_points())


@app.get("/api/lanes/search", tags=["lanes"])
def lane_search(origin: str = Query(alias="from", min_length=1, max_length=60), to: str = Query(min_length=1, max_length=60), qty: float | None = Query(None, gt=0, le=100),
                vehicle_type: str | None = Query(None, max_length=12), months: int = Query(6, ge=1, le=12)):
    """Vendors on one lane A -> B ranked from cheapest to costliest per vehicle type, a recommendation, savings if switched and indicative two-leg options."""
    return out(LN.lane_search(origin, to, qty, vehicle_type, months))


# /api/vendors/compare must be registered before /api/vendors/{code}, or "compare" is read as a vendor code
@app.get("/api/vendors/compare", tags=["vendors"])
def vendors_compare(codes: str = Query(max_length=60, description="2 to 4 vendor codes, comma separated, e.g. V001,V005"), vehicle_type: str | None = Query(None, max_length=12),
                    months: int = Query(12, ge=1, le=12)):
    """Head to head on the lanes the chosen vendors share (10+ LRs each): Rs/MT each, the cheaper vendor, a volume-weighted index and lanes won."""
    return out(LN.vendor_compare(codes, vehicle_type, months))


@app.get("/api/vendors", tags=["vendors"])
def vendors(q: str | None = Query(None, max_length=10, description="Part of a vendor code"), min_lrs: int = Query(10, ge=1, le=100000),
            flagged: bool = Query(False, description="Only vendors loss-making on more than 25% of LRs or more than 10% above the fair price"),
            sort: str = "spend_rs", order: str = Query("desc", pattern="^(asc|desc)$"), page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200)):
    return out(svc.list_vendors(q, min_lrs, sort, order, page, page_size, flagged))


@app.get("/api/vendors/{code}", tags=["vendors"])
def vendor(code: str):
    """Profile, worst lanes and monthly trend for one vendor code."""
    return out(svc.vendor_detail(code))


@app.get("/api/trips", tags=["trips"])
def trips(risk: str = Query("all", description="all, high or low (high = M2 severe-delay flag raised)"), q: str | None = Query(None, max_length=60), corridor: str | None = None,
          sort: str = "severe_probability", order: str = Query("desc", pattern="^(asc|desc)$"), page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200)):
    """Test-period trips (27 Sep to 3 Oct 2018 onwards) with the M2 severe-delay probability."""
    return out(svc.list_trips(risk, q, corridor, sort, order, page, page_size))


@app.get("/api/trips/{trip_id}", tags=["trips"])
def trip(trip_id: str):
    """M1 predicted duration and range, M2 severe-delay risk with drivers, the leg timeline and the origin hub."""
    return out(svc.trip_detail(trip_id))


@app.get("/api/hubs", tags=["trips"])
def hubs(top: int = Query(25, ge=1, le=25)):
    """Hub handling-time ranking (hubs with 30+ test legs) with excess hours and rupees."""
    return out(T.hub_report(None, top))


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(out(data), ensure_ascii=False, default=str)}\n\n"


def _chunks(text: str, n: int = 6):
    words = re.findall(r"\S+\s*|\n", text)
    for i in range(0, len(words), n):
        yield "".join(words[i:i + n])


@app.get("/api/ask/catalogue", tags=["ask"])
def ask_catalogue():
    """The ~15 trusted-asset query specs: id, title, params, columns (role/unit/decimals), data_scope."""
    return out(CAT.list_catalogue())


@app.post("/api/ask", tags=["ask"], responses={200: {"content": {"text/event-stream": {}}, "description": "Server-sent events, in order: analysis, result, chart, summary, followups, done (or refused/empty/error)"}})
async def ask(req: S.AskRequest):
    """Answer card pipeline: analysis -> result -> chart -> summary -> followups -> done, each a
    server-sent event the UI renders as it arrives. Code picks the data, the chart and the numbers; only the
    summary's wording may ever have come from an LLM (DEMO mode: never - a code-written template)."""
    history = [{"question": t.question, "query_id": t.query_id, "params": t.params} for t in req.history if t.query_id]

    async def gen():
        try:
            def work():
                with _run_lock:
                    return AP.run_ask_structured(req.question, history, llm=get_llm())
            res = await run_in_threadpool(work)
            kind = res["kind"]
            # Every payload below carries data straight from app.tools/app.ask_pipeline, which work in real vendor
            # names by design (backend/vendor_codes.py's own docstring); out() is what turns a name into its code
            # everywhere else in this file, and the SSE stream is no exception - skipping it here would leak
            # names through the one endpoint that forgot to call it.
            if kind == "refused":
                yield _sse("refusal", out({"reason": res["reason"], "closest": res["closest"]}))
                yield _sse("done", {"run_id": None})
                return
            if kind == "error":
                yield _sse("error", out({"message": res["message"]}))
                return
            if kind == "empty":
                yield _sse("analysis", out(res["analysis"]))
                yield _sse("empty", out({"filters_applied": res["filters_applied"], "query_id": res["query_id"], "notes": res.get("notes") or []}))
                yield _sse("done", {"run_id": None})
                return
            _state["last_routed_via"] = res.get("model_used") or _state["last_routed_via"]
            yield _sse("analysis", out(res["analysis"]))
            await asyncio.sleep(0)
            yield _sse("result", out(res["result"]))
            await asyncio.sleep(0)
            yield _sse("chart", out(res["chart"]))
            await asyncio.sleep(0)
            yield _sse("summary", out({"text": res["summary"], "source": res["summary_source"], "guardrail_status": res["guardrail_status"],
                                       "guardrail_events": [e.get("event") for e in res["guardrail_events"]], "mode": res["mode"]}))
            await asyncio.sleep(0)
            yield _sse("followups", out({"items": res["followups"]}))
            yield _sse("done", {"run_id": res["run_id"]})
        except Exception as e:  # noqa: BLE001
            yield _sse("error", {"message": f"{type(e).__name__}: {e}"})
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/ask/rerun", tags=["ask"])
def ask_rerun(req: S.AskRerunRequest):
    """Same query, edited filters, no LLM call: fast and deterministic (Genie's "Run")."""
    try:
        res = AP.rerun(req.run_id, req.params)
    except KeyError as e:
        raise svc.NotFound(str(e))
    except T.ToolError as e:
        raise HTTPException(422, str(e))
    if res["kind"] == "error":
        raise HTTPException(422, res["message"])
    return out(res)


@app.post("/api/ask/feedback", tags=["ask"])
def ask_feedback(req: S.AskFeedbackRequest):
    """Yes / Fix it / Request review on one answer card. Recorded in the audit log only."""
    if req.run_id not in {r.get("run_id") for r in G.read_audit()}:
        raise svc.NotFound(f"unknown run '{req.run_id}'")
    G.log_feedback(req.run_id, req.kind, req.reasons, req.note)
    return {"run_id": req.run_id, "recorded": True}


@app.get("/api/ask/runs/{run_id}/csv", tags=["ask"])
def ask_run_csv(run_id: str):
    """The full result (not just the first 50 rows shown in the card) for one Ask run, as CSV."""
    for r in G.read_audit():
        if r.get("type") == "run" and r.get("run_id") == run_id:
            qid, params = r.get("query_id"), r.get("params", {})
            break
    else:
        raise svc.NotFound(f"unknown run '{run_id}'")
    if not qid:
        raise svc.NotFound(f"run '{run_id}' has no query to export")
    res = T.query_data(qid, params)
    rows = vc.scrub(svc.clean(res.get("rows", [])))
    csv_text = svc.rows_to_csv(rows)
    return Response(csv_text, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="ask_{run_id}.csv"'})


@app.get("/api/prompts", tags=["prompts"])
def prompts():
    """The ten message templates: purpose, input fields, template text, worked example and known failure modes."""
    return out(svc.prompts())


@app.get("/api/prompts/{template_id}", tags=["prompts"])
def prompt(template_id: str):
    for p in svc.prompts():
        if template_id in (p["template_id"], p["number"]):
            return out(p)
    raise svc.NotFound(f"unknown template '{template_id}'")


def _draft_from_trip(req: S.DraftRequest) -> dict:
    if not req.trip_id:
        raise HTTPException(422, "trip_id is required for this draft")
    with _run_lock:
        res = agent.run_trip({"trip_id": req.trip_id}, get_llm())
    _remember(res)
    if res["recommended_action"] is None:
        raise svc.NotFound(vc.scrub(res["text"]))
    if res["recommended_action"] != "alert":
        raise HTTPException(409, "The severe-delay flag is not raised for this trip, so the rules do not call for an alert. No draft was made.")
    want = "hub_delay_alert" if req.kind == "hub_alert" else "customer_eta_note"
    d = next((c["output"] for c in res["tools_called"] if c["name"] == "draft_message" and c["output"].get("template_id") == want), None)
    if not d or d.get("status") != "ok":
        raise HTTPException(409, "No draft is available for this trip (for example, the origin hub is not in the ranking).")
    return {"run_id": res["run_id"], "kind": req.kind, "template_id": d["template_id"], "title": d["title"], "text": d["text"], "mode": res["mode"], "banner": res["banner"]}


def _draft_renegotiation(req: S.DraftRequest) -> dict:
    if not req.vendor:
        raise HTTPException(422, "vendor is required for a renegotiation draft")
    name = svc.vendor_name(req.vendor)
    detail = svc.vendor_detail(req.vendor)
    lane = {"route": req.route, "vehicle_type": req.vehicle_type} if req.route and req.vehicle_type else (detail["worst_lanes"][0] if detail["worst_lanes"] else None)
    if not lane:
        raise HTTPException(409, "This vendor has no lane with enough history (10+ LRs) to benchmark.")
    llm = get_llm()
    turn = agent.Turn()
    with _run_lock:
        alts = turn.call("find_alternative_vendors", {"route": lane["route"], "vehicle_type": lane["vehicle_type"], "k": 3, "exclude_vendor": name})
        cur = turn.call("lane_rate", {"route": lane["route"], "vehicle_type": lane["vehicle_type"], "vendor": name}, fn=T.lane_rate)
        best = (alts.get("alternatives") or [None])[0]
        if not cur.get("rs_per_mt") or not best or best["rs_per_mt"] >= cur["rs_per_mt"]:
            raise HTTPException(409, "No cheaper benchmark with 10+ LRs exists on this lane, so the rules do not support a rate request here.")
        d = turn.call("draft_message", {"template_id": "01", "fields": {
            "vendor": name, "route": lane["route"], "vehicle_type": lane["vehicle_type"], "current_rate": agent.rs(cur["rs_per_mt"]),
            "benchmark_rate": agent.rs(best["rs_per_mt"]), "lr_count": int(cur["lr_count"]), "period": detail["period"]}}, fn=T.draft_message, polisher=llm.polisher())
        banner = L.BANNER if str(d.get("note") or "").startswith("polish failed") else None
        run_id = G.log_run("Vendor profile", {"vendor": name, **lane}, turn.calls, "rules (DEMO_MODE)" if llm.demo else llm.model, d["text"], [], "DEMO" if llm.demo else "LIVE",
                           banner, extra={"recommended_action": "renegotiate rate"})
    return {"run_id": run_id, "kind": "renegotiation", "template_id": d["template_id"], "title": d["title"], "text": d["text"], "mode": "DEMO" if llm.demo else "LIVE", "banner": banner}


@app.post("/api/drafts", response_model=S.DraftResponse, tags=["prompts"])
def drafts(req: S.DraftRequest):
    """Make a draft message (hub alert, customer ETA note, rate renegotiation) for a person to review. Logged as a run; nothing is sent."""
    return out(_draft_renegotiation(req) if req.kind == "renegotiation" else _draft_from_trip(req))


@app.post("/api/decisions", response_model=S.DecisionResponse, tags=["audit"])
def decisions(req: S.DecisionRequest):
    """Record a person's decision on a run (approved, rejected or edited). Append-only; nothing is sent or changed."""
    if req.run_id not in {r["run_id"] for r in svc.audit_records()}:
        raise svc.NotFound(f"unknown run '{req.run_id}'")
    G.log_decision(req.run_id, req.decision, req.final_text)
    return {"run_id": req.run_id, "decision": req.decision, "recorded": True}


def _audit_filters(page_name, decision, mode, q, date_from, date_to):
    return svc.audit_query(page_name, decision, mode, q, date_from, date_to)


@app.get("/api/audit/export.csv", tags=["audit"])
def audit_export(page_name: str | None = Query(None, alias="screen"), decision: str | None = Query(None, pattern="^(approved|rejected|edited|pending)$"),
                 mode: str | None = Query(None, pattern="^(DEMO|LIVE)$"), q: str | None = None, date_from: str | None = Query(None, pattern=r"^\d{4}-\d\d-\d\d$"),
                 date_to: str | None = Query(None, pattern=r"^\d{4}-\d\d-\d\d$")):
    csv_text = svc.audit_csv(_audit_filters(page_name, decision, mode, q, date_from, date_to))
    return Response(csv_text, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="audit_log.csv"'})


@app.get("/api/audit", tags=["audit"])
def audit(page_name: str | None = Query(None, alias="screen", description="Booking check, Trip alert, Ask the data or Vendor profile"),
          decision: str | None = Query(None, pattern="^(approved|rejected|edited|pending)$"), mode: str | None = Query(None, pattern="^(DEMO|LIVE)$"),
          q: str | None = Query(None, max_length=100), date_from: str | None = Query(None, pattern=r"^\d{4}-\d\d-\d\d$"), date_to: str | None = Query(None, pattern=r"^\d{4}-\d\d-\d\d$"),
          page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200)):
    rows = _audit_filters(page_name, decision, mode, q, date_from, date_to)
    items = [{k: v for k, v in r.items() if k not in ("tools_called", "inputs", "run_text")} for r in rows[(page - 1) * page_size: page * page_size]]
    return out({"total": len(rows), "page": page, "page_size": page_size, "items": items})


@app.get("/api/audit/{run_id}", tags=["audit"])
def audit_run(run_id: str):
    for r in svc.audit_records():
        if r["run_id"] == run_id:
            return out(r)
    raise svc.NotFound(f"unknown run '{run_id}'")


@app.get("/api/models", tags=["models"])
def models():
    """The six model cards (V1-V3, M1-M3) built from reports/metrics/*.json."""
    return svc.model_cards()


@app.get("/api/{rest:path}", include_in_schema=False)
def api_not_found(rest: str):
    raise HTTPException(404, f"no such endpoint: /api/{rest}")


if os.path.isdir(os.path.join(DIST, "assets")):
    app.mount("/assets", StaticFiles(directory=os.path.join(DIST, "assets")), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    """Serve the React build; unknown paths fall back to index.html so deep links such as /vendors/V012 work."""
    index = os.path.join(DIST, "index.html")
    if not os.path.isfile(index):
        return JSONResponse({"detail": "Frontend not built yet. API docs: /api/docs"}, status_code=404)
    f = os.path.normpath(os.path.join(DIST, path))
    if path and f.startswith(DIST) and os.path.isfile(f):
        return FileResponse(f)
    return FileResponse(index)
