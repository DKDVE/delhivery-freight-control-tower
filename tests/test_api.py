"""API tests: every endpoint, in DEMO mode, with the audit log redirected to a temp file."""
import json
import os

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend import vendor_codes as vc


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("DEMO_MODE", "1")
    mp.setenv("AUDIT_LOG_DIR", str(tmp_path_factory.mktemp("api")))
    from backend.main import app
    with TestClient(app) as c:
        yield c
    mp.undo()


BOOKING = {"route": "AFBAN1", "vehicle_type": "OB22", "vendor": "V002", "qty": 3, "month": 3}
NAMES = vc.names()


def no_names(text: str):
    low = text.casefold()
    leaked = [n for n in NAMES if n.casefold() in low]
    assert not leaked, f"vendor name(s) leaked: {leaked[:3]}"


def sse(resp) -> list[tuple[str, dict]]:
    out = []
    for block in resp.text.strip().split("\n\n"):
        ev, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        out.append((ev, data))
    return out


def test_vendor_codes_cover_every_vendor_and_follow_spend_order():
    t = vc.table()
    lr = pd.read_parquet(os.path.join(vc.ROOT, "data", "processed", "vendor_lr_clean.parquet"), columns=["vendor_name"])
    assert set(t["vendor_code"]) == set(lr["vendor_name"]) and t["vendor_code"].is_unique
    assert t["vendor_code"].iloc[0] == "V001" and t["vendor_code"].iloc[-1] == f"V{len(t):03d}"
    assert t["spend_rs"].is_monotonic_decreasing
    assert vc.name_for("v001") == "V001" and vc.name_for("V999") is None


def test_scrub_is_identity_on_codes():
    assert vc.scrub_text("Dear V002 team") == "Dear V002 team"
    assert vc.scrub({"V002": [{"v": "V002"}]}) == {"V002": [{"v": "V002"}]}


def test_health_demo_mode(client):
    r = client.get("/api/health").json()
    assert r["status"] == "ok" and r["mode"] == "DEMO" and r["label"] == "DEMO" and r["pinned_model"] == "gpt-oss-120b"
    assert "rule-based" in r["banner"] and r["data_as_of"]["vendor_data"]["from"] == "2025-04-01" and r["last_routed_via"] is None


def test_health_live_label(client, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "0")
    monkeypatch.setenv("LLM_API_KEY", "x")
    r = client.get("/api/health").json()
    assert r["mode"] == "LIVE" and r["label"] == "LIVE · gpt-oss-120b" and r["banner"] is None


def test_ready_after_warmup(client):
    """The client fixture opens TestClient as a context manager, which runs the lifespan startup (tables, model
    pipelines, SHAP explainers and the cached Overview/Vendors/Hubs payloads) before any test runs."""
    r = client.get("/api/ready")
    body = r.json()
    assert r.status_code == 200 and body["status"] == "ready" and body["cold_start_ms"] > 0
    assert set(body["first_request_ms"]) == {"GET /api/overview", "GET /api/vendors", "GET /api/hubs"}


def test_parse_allowed_hosts():
    from backend.main import parse_allowed_hosts
    assert parse_allowed_hosts(None) == [] and parse_allowed_hosts("*") == [] and parse_allowed_hosts("") == []
    assert parse_allowed_hosts("fct-app.example.io, localhost") == ["fct-app.example.io", "localhost"]


def test_meta_options(client):
    m = client.get("/api/meta/options").json()
    assert len(m["vendors"]) == len(vc.table()) and m["vendors"][0]["vendor_code"] == "V001"
    assert any(r["route"] == "AFBAN1" and "OB22" in r["vehicle_types"] for r in m["routes"])
    assert m["route_types"] == ["FTL", "Carting"] and len(m["months"]) == 12
    no_names(json.dumps(m))


def test_openapi_lists_all_endpoints(client):
    paths = client.get("/api/openapi.json").json()["paths"]
    for p in ["/api/health", "/api/meta/options", "/api/overview", "/api/booking/check", "/api/vendors", "/api/vendors/{code}", "/api/lanes/{route}/alternatives", "/api/trips",
              "/api/trips/{trip_id}", "/api/hubs", "/api/ask", "/api/prompts", "/api/drafts", "/api/decisions", "/api/audit", "/api/models"]:
        assert p in paths, p
    assert client.get("/api/docs").status_code == 200


def test_unknown_api_path_is_404_json(client):
    r = client.get("/api/nope")
    assert r.status_code == 404 and "no such endpoint" in r.json()["detail"]


def test_overview(client):
    o = client.get("/api/overview").json()
    assert [k["id"] for k in o["kpis"]] == ["spend", "margin", "loss_share", "loss_rs", "excess"]
    assert all(len(k["sparkline"]) == 12 for k in o["kpis"][:4]) and o["kpis"][0]["change_pct"] is not None
    assert len(o["monthly_margin"]) == 12 and len(o["top_lanes_by_loss"]) == 5
    losses = [x["loss_rs"] for x in o["top_lanes_by_loss"]]
    assert losses == sorted(losses, reverse=True)
    assert o["quarter"] == "2026Q1" and o["previous_quarter"] == "2025Q4"
    assert {a["kind"] for a in o["alerts"]} == {"trip", "vendor"}
    no_names(json.dumps(o))


def test_booking_check_returns_scores_recommendation_and_no_names(client):
    r = client.post("/api/booking/check", json={**BOOKING, "quoted_rs": 20000})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["recommended_action"] in ("downsize vehicle", "reassign vendor", "renegotiate rate", "reprice or top up", "verify quote", "proceed")
    assert b["policy"]["signal"] in ("underload_smaller_vehicle", "quote_above_fair", "loss_risk", "quote_below_fair", "none")
    assert 0 <= b["loss_risk"]["probability"] <= 1 and b["loss_risk"]["level"] in ("low", "medium", "high") and len(b["loss_risk"]["top_drivers"]) == 3
    assert b["fair_price"]["fair_low_rs"] <= b["fair_price"]["fair_rs"] <= b["fair_price"]["fair_high_rs"] and "overpay_pct" in b["fair_price"]
    assert b["mode"] == "DEMO" and b["guardrail_status"] == "passed" and b["run_id"]
    assert b["inputs"]["vendor"] == "V002"
    no_names(r.text)


def test_booking_check_matches_the_tools_directly(client):
    from app import tools as T
    name = vc.name_for("V002")
    direct = T.check_booking({**BOOKING, "vendor": name})
    api = client.post("/api/booking/check", json=BOOKING).json()
    assert api["loss_risk"]["probability"] == pytest.approx(direct["v2_loss_risk"]["probability"])
    assert api["fair_price"]["fair_rs"] == pytest.approx(direct["v1_fair_price"]["fair_rs"])


def test_booking_check_with_alternatives_policy(client):
    b = client.post("/api/booking/check", json={**BOOKING, "quoted_rs": 60000}).json()
    assert b["policy"]["over_flag"] is True and b["policy"]["flagged"] is True
    assert b["recommended_action"] in ("downsize vehicle", "reassign vendor", "renegotiate rate")
    assert b["draft"]  # a draft is made for every action except proceed and verify quote
    for a in b["alternatives"]:
        assert "vendor_name" not in a and a["vendor_code"].startswith("V") and a["rs_per_mt"] > 0


@pytest.mark.parametrize("patch,status", [({"vendor": "V999"}, 404), ({"vendor": "Some Transport Co"}, 422), ({"month": 13}, 422), ({"qty": 0}, 422), ({"qty": -2}, 422),
                                          ({"route": ""}, 422), ({"quoted_rs": -5}, 422)])
def test_booking_check_rejects_bad_input(client, patch, status):
    r = client.post("/api/booking/check", json={**BOOKING, **patch})
    assert r.status_code == status, r.text
    assert "detail" in r.json()


def test_booking_check_unseen_route_scores_with_a_warning(client):
    """Existing tool behaviour is kept: an unseen route is scored, with a visible warning that the result is less reliable."""
    b = client.post("/api/booking/check", json={**BOOKING, "route": "NOSUCH1"}).json()
    assert any("not seen in training" in w for w in b["warnings"])


def test_booking_check_missing_field(client):
    r = client.post("/api/booking/check", json={"route": "AFBAN1"})
    assert r.status_code == 422 and any(e["field"] == "vendor" for e in r.json()["errors"])


def test_lane_alternatives(client):
    r = client.get("/api/lanes/AFAHM1/alternatives", params={"vehicle_type": "OB22", "exclude_vendor": "V002", "k": 3})
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "ok" and 1 <= len(j["alternatives"]) <= 3
    assert all("vendor_name" not in a and a["vendor_code"].startswith("V") for a in j["alternatives"])
    rates = [a["rs_per_mt"] for a in j["alternatives"]]
    assert rates == sorted(rates)
    no_names(r.text)
    assert client.get("/api/lanes/AFAHM1/alternatives").status_code == 422
    assert client.get("/api/lanes/NOSUCH/alternatives", params={"vehicle_type": "OB22"}).json()["status"] == "insufficient data"


def test_vendor_list_paging_sorting_and_filters(client):
    j = client.get("/api/vendors", params={"page_size": 10, "sort": "loss_rs", "order": "desc"}).json()
    assert len(j["items"]) == 10 and j["total"] > 50
    v = [x["loss_rs"] for x in j["items"]]
    assert v == sorted(v, reverse=True)
    assert all(x["vendor_code"].startswith("V") for x in j["items"])
    p2 = client.get("/api/vendors", params={"page_size": 10, "sort": "loss_rs", "page": 2}).json()
    assert p2["items"][0]["vendor_code"] != j["items"][0]["vendor_code"]
    assert client.get("/api/vendors", params={"q": "v002"}).json()["items"][0]["vendor_code"] == "V002"
    assert all(x["lrs"] >= 500 for x in client.get("/api/vendors", params={"min_lrs": 500}).json()["items"])
    fl = client.get("/api/vendors", params={"flagged": True, "page_size": 200}).json()
    assert all(x["loss_share_pct"] > 25 or (x["overpay_pct"] or 0) > 10 for x in fl["items"])
    assert client.get("/api/vendors", params={"sort": "name"}).status_code == 400
    no_names(json.dumps(j) + json.dumps(fl))


def test_vendor_detail_and_deep_link_errors(client):
    r = client.get("/api/vendors/V002")
    assert r.status_code == 200
    d = r.json()
    assert d["vendor_code"] == "V002" and d["lrs"] > 1000 and 1 <= len(d["worst_lanes"]) <= 5 and len(d["monthly"]) == 12
    no_names(r.text)
    assert client.get("/api/vendors/v002").status_code == 200  # code is case-insensitive
    assert client.get("/api/vendors/V999").status_code == 404
    assert client.get("/api/vendors/Chetek").status_code == 404


def test_trip_list_and_filters(client):
    j = client.get("/api/trips", params={"risk": "high", "page_size": 5}).json()
    assert j["total"] == j["high_risk_total"] and len(j["items"]) == 5 and all(t["risk"] == "high" for t in j["items"])
    probs = [t["severe_probability"] for t in j["items"]]
    assert probs == sorted(probs, reverse=True) and all(p >= t["threshold"] for p, t in zip(probs, j["items"]))
    lo = client.get("/api/trips", params={"risk": "low", "page_size": 3}).json()
    assert all(t["risk"] == "low" for t in lo["items"])
    allr = client.get("/api/trips", params={"risk": "all", "page_size": 1}).json()
    assert allr["total"] == j["total"] + lo["total"] == 4163
    assert client.get("/api/trips", params={"risk": "bogus"}).status_code == 400
    assert client.get("/api/trips", params={"q": j["items"][0]["trip_id"][:20]}).json()["total"] >= 1
    assert client.get("/api/trips", params={"page": 0}).status_code == 422


def test_trip_detail(client):
    tid = client.get("/api/trips", params={"risk": "high", "page_size": 1}).json()["items"][0]["trip_id"]
    r = client.get(f"/api/trips/{tid}")
    assert r.status_code == 200
    d = r.json()
    assert d["trip_id"] == tid and d["predicted_min"] > 0 and d["severe_delay"]["flag"] is True
    assert len(d["legs"]) >= 1 and d["legs"][0]["leg"] == 1 and len(d["severe_delay"]["top_drivers"]) == 3
    assert client.get("/api/trips/trip-does-not-exist").status_code == 404


def test_hubs(client):
    h = client.get("/api/hubs", params={"top": 10}).json()
    assert h["status"] == "ok" and len(h["hubs"]) == 10 and h["network_median_handling_min"] > 0
    ex = [x["excess_hours"] for x in h["hubs"]]
    assert ex == sorted(ex, reverse=True)
    assert client.get("/api/hubs", params={"top": 99}).status_code == 422


def test_ask_catalogue_endpoint_lists_17_queries(client):
    r = client.get("/api/ask/catalogue")
    assert r.status_code == 200
    cat = r.json()
    assert len(cat) == 17
    assert {c["id"] for c in cat} >= {"vendors_by_loss", "monthly_margin_trend", "vendor_quadrant_counts"}


def test_ask_streams_analysis_result_chart_summary_followups_done(client):
    r = client.post("/api/ask", json={"question": "Which vendors lose the most money?"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    ev = sse(r)
    names = [e for e, _ in ev]
    assert names == ["analysis", "result", "chart", "summary", "followups", "done"]
    analysis = ev[0][1]
    assert analysis["query_id"] == "vendors_by_loss"
    result = ev[1][1]
    assert result["row_count"] >= 1 and result["rows"][0]["vendor_name"].startswith("V")
    assert result["sql_equivalent"]
    chart = ev[2][1]
    assert chart["type"] in ("bar", "stacked100", "kpi_tile", "line", "bar_dot", "scatter", "table")
    summary = ev[3][1]
    assert summary["guardrail_status"] == "passed" and summary["text"]
    followups = ev[4][1]["items"]
    assert 1 <= len(followups) <= 3
    assert ev[5][1]["run_id"]
    no_names(r.text)


def test_ask_pipeline_result_carries_codes_only():
    """The shipped tables hold vendor codes, so the pipeline result has no real name even before the API boundary scrubs it."""
    from app import ask_pipeline as AP
    res = AP.run_ask_structured("Which vendors lose the most money?", [])
    assert res["kind"] == "ok" and res["query_id"] == "vendors_by_loss"
    text = json.dumps(res, default=str)
    assert not [n for n in NAMES if n in text]


def test_ask_refuses_actions_and_unsupported_questions(client):
    for q, key in [("Send an email to V001 asking for a 10% discount.", "action"), ("What will the weather be in Delhi tomorrow?", "unsupported")]:
        ev = sse(client.post("/api/ask", json={"question": q}))
        names = [e for e, _ in ev]
        assert names == ["refusal", "done"], key
        refusal = ev[0][1]
        assert len(refusal["closest"]) == 6, key
        no_names(json.dumps(refusal))


def test_ask_validates_question(client):
    assert client.post("/api/ask", json={"question": "hi"}).status_code == 422
    assert client.post("/api/ask", json={"question": "x" * 501}).status_code == 422


def test_ask_rerun_is_deterministic_and_matches_the_original(client):
    ev = sse(client.post("/api/ask", json={"question": "Which hubs are slowest at handling?"}))
    run_id = next(d for e, d in ev if e == "done")["run_id"]
    result = next(d for e, d in ev if e == "result")
    r = client.post("/api/ask/rerun", json={"run_id": run_id, "params": {}})
    assert r.status_code == 200
    assert r.json()["result"]["rows"] == result["rows"]


def test_ask_feedback_is_recorded_in_the_audit_log(client):
    ev = sse(client.post("/api/ask", json={"question": "Which hubs are slowest at handling?"}))
    run_id = next(d for e, d in ev if e == "done")["run_id"]
    r = client.post("/api/ask/feedback", json={"run_id": run_id, "kind": "fix_it", "reasons": ["wrong_chart"], "note": "test"})
    assert r.status_code == 200 and r.json()["recorded"] is True


def test_prompt_library(client):
    ps = client.get("/api/prompts").json()
    assert len(ps) == 10 and ps[0]["template_id"] == "vendor_renegotiation"
    for p in ps:
        assert p["purpose"] and p["fields"] and p["template"] and p["example_output"] and p["failure_modes"], p["template_id"]
    assert client.get("/api/prompts/06").json()["template_id"] == "hub_delay_alert"
    assert client.get("/api/prompts/vendor_renegotiation").status_code == 200
    assert client.get("/api/prompts/99").status_code == 404
    no_names(json.dumps(ps))


def test_draft_hub_alert_and_customer_eta_for_a_flagged_trip(client):
    tid = client.get("/api/trips", params={"risk": "high", "page_size": 1}).json()["items"][0]["trip_id"]
    a = client.post("/api/drafts", json={"kind": "hub_alert", "trip_id": tid})
    b = client.post("/api/drafts", json={"kind": "customer_eta", "trip_id": tid})
    assert a.status_code == 200 and b.status_code == 200, a.text + b.text
    assert a.json()["template_id"] == "hub_delay_alert" and b.json()["template_id"] == "customer_eta_note"
    assert "{{" not in a.json()["text"] and "Nothing is sent" in a.json()["note"] and a.json()["run_id"] != b.json()["run_id"]


def test_draft_refused_when_rules_see_no_alert(client):
    tid = client.get("/api/trips", params={"risk": "low", "page_size": 1}).json()["items"][0]["trip_id"]
    r = client.post("/api/drafts", json={"kind": "hub_alert", "trip_id": tid})
    assert r.status_code == 409 and "not raised" in r.json()["detail"]
    assert client.post("/api/drafts", json={"kind": "hub_alert"}).status_code == 422
    assert client.post("/api/drafts", json={"kind": "hub_alert", "trip_id": "nope"}).status_code == 404


def test_draft_renegotiation_uses_codes_and_a_benchmark(client):
    made = None
    for code in [v["vendor_code"] for v in client.get("/api/vendors", params={"page_size": 30, "sort": "spend_rs"}).json()["items"]]:
        r = client.post("/api/drafts", json={"kind": "renegotiation", "vendor": code})
        if r.status_code == 200:
            made = (code, r.json())
            break
        assert r.status_code == 409
    assert made, "no top-30 vendor had a cheaper benchmark on its worst lane"
    code, d = made
    assert d["template_id"] == "vendor_renegotiation" and f"Dear {code} team" in d["text"] and "₹" in d["text"]
    no_names(json.dumps(d))
    assert client.post("/api/drafts", json={"kind": "renegotiation"}).status_code == 422
    assert client.post("/api/drafts", json={"kind": "renegotiation", "vendor": "V999"}).status_code == 404


def test_decision_flow_and_audit_log(client):
    run = client.post("/api/booking/check", json={**BOOKING, "quoted_rs": 60000}).json()["run_id"]
    before = client.get("/api/audit", params={"decision": "pending"}).json()["total"]
    r = client.post("/api/decisions", json={"run_id": run, "decision": "approved", "final_text": "Edited text for V002"})
    assert r.status_code == 200 and r.json()["recorded"] is True
    j = client.get("/api/audit", params={"decision": "approved"}).json()
    row = next(x for x in j["items"] if x["run_id"] == run)
    assert row["decision"] == "approved" and row["final_text"] == "Edited text for V002" and row["page"] == "Booking check" and row["mode"] == "DEMO"
    assert client.get("/api/audit", params={"decision": "pending"}).json()["total"] == before - 1
    detail = client.get(f"/api/audit/{run}").json()
    assert [c["name"] for c in detail["tools_called"]][0] == "check_booking" and detail["inputs"]["vendor"] == "V002"
    no_names(json.dumps(detail))


def test_decision_validation(client):
    assert client.post("/api/decisions", json={"run_id": "abcdef123456", "decision": "approved"}).status_code == 404
    run = client.get("/api/audit").json()["items"][0]["run_id"]
    assert client.post("/api/decisions", json={"run_id": run, "decision": "sent"}).status_code == 422
    assert client.post("/api/decisions", json={"decision": "approved"}).status_code == 422


def test_audit_filters_paging_and_csv_export(client):
    client.post("/api/ask", json={"question": "How has margin trended by month?"})
    j = client.get("/api/audit", params={"page_size": 2}).json()
    assert len(j["items"]) == 2 and j["total"] >= 3 and "tools_called" not in j["items"][0]
    ts = [x["ts"] for x in client.get("/api/audit", params={"page_size": 50}).json()["items"]]
    assert ts == sorted(ts, reverse=True)
    ask = client.get("/api/audit", params={"screen": "Ask the data"}).json()
    assert ask["total"] >= 1 and all(x["page"] == "Ask the data" for x in ask["items"])
    assert client.get("/api/audit", params={"mode": "LIVE"}).json()["total"] == 0
    assert client.get("/api/audit", params={"q": "margin"}).json()["total"] >= 1
    assert client.get("/api/audit", params={"decision": "maybe"}).status_code == 422
    assert client.get("/api/audit", params={"date_from": "2999-01-01"}).json()["total"] == 0
    c = client.get("/api/audit/export.csv", params={"screen": "Ask the data"})
    assert c.status_code == 200 and c.headers["content-type"].startswith("text/csv") and "attachment" in c.headers["content-disposition"]
    import csv
    import io
    rows = list(csv.reader(io.StringIO(c.text)))
    assert rows[0][:4] == ["run_id", "ts", "page", "mode"] and len(rows) == ask["total"] + 1
    assert client.get("/api/audit/zzzzzzzzzzzz").status_code == 404
    no_names(c.text)


def test_model_cards(client):
    ms = client.get("/api/models").json()
    assert [m["task_id"] for m in ms] == ["V1", "V2", "V3", "M1", "M2", "M3"]
    for m in ms:
        assert m["winner"] and m["headline"]["winner_value"] is not None and m["headline"]["baseline"] and m["caveats"] and len(m["top_drivers"]) >= 5
        assert len(m["models"]) >= 3
        assert m["figures"]["shap_bar"] == f"/api/figures/{m['task_id']}_shap_bar.png"
    r = client.get("/api/figures/V2_shap_bar.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert client.get("/api/figures/nope.png").status_code == 404
    assert client.get("/api/figures/..%2Fsecret.png").status_code in (404, 422)
    no_names(json.dumps(ms))


def test_spa_fallback_when_not_built_or_built(client):
    r = client.get("/vendors/V012")
    assert r.status_code in (200, 404)
    if r.status_code == 404:
        assert "Frontend not built" in r.json()["detail"]


def test_booking_reason_names_the_signal_and_policy_is_exposed(client):
    b = client.post("/api/booking/check", json={**BOOKING, "quoted_rs": 20000}).json()
    sig = b["policy"]["signal"]
    key = {"underload_smaller_vehicle": "under-load flag is raised", "quote_above_fair": "above the fair price", "loss_risk": "loss-risk flag is raised",
           "quote_below_fair": "below the fair price", "none": "no rule fires"}[sig]
    assert key in b["summary"] and f"Recommendation: {b['recommended_action']}, because" in b["summary"]
    for k in ("signal", "flagged", "over_flag", "below_flag", "under_flag", "cheaper_alternative", "smaller_vehicle"):
        assert k in b["policy"]


def test_booking_verify_quote_has_no_draft(client):
    b = client.post("/api/booking/check", json={**BOOKING, "vendor": "V001", "route": "AFAHM1", "qty": 12, "quoted_rs": 1000}).json()
    if b["recommended_action"] == "verify quote":
        assert b["draft"] is None


def test_built_assets_are_served_as_javascript():
    import glob
    from backend.main import app as _app
    js = glob.glob(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "dist", "assets", "index-*.js"))
    if not js:
        return
    r = TestClient(_app).get("/assets/" + os.path.basename(js[0]))
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]
