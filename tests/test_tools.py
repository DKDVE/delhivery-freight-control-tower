"""Unit tests for app/tools.py. Several tests re-score real test-period rows through the tools' own code path and
compare with the prediction CSVs saved by the notebooks, so a mismatch means the app would show a different number
from the report."""
import ast
import os

import numpy as np
import pandas as pd
import pytest

from app import tools as T

PROC = T.PROC


def _csv(name):
    return pd.read_csv(os.path.join(PROC, name))


def test_every_schema_has_a_function_and_valid_shape():
    names = [s["function"]["name"] for s in T.TOOL_SCHEMAS]
    assert sorted(names) == sorted(T.TOOLS)
    for s in T.TOOL_SCHEMAS:
        f = s["function"]
        assert s["type"] == "function" and f["description"]
        p = f["parameters"]
        assert p["type"] == "object" and set(p["required"]) <= set(p["properties"])


def test_no_eval_exec_or_sql_in_tools_module():
    tree = ast.parse(open(T.__file__, encoding="utf8").read())
    calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not calls & {"eval", "exec", "compile", "__import__"}
    src = open(T.__file__, encoding="utf8").read().lower()
    assert "read_sql" not in src and "sqlite" not in src


def test_call_tool_unknown_and_bad_arguments():
    assert "error" in T.call_tool("delete_vendor", {})
    assert "error" in T.call_tool("vendor_profile", {"wrong": 1})


def test_v2_scoring_matches_pred_v2():
    pred = _csv("pred_V2.csv")
    pred = pred[~pred["record_id"].duplicated(keep=False)].sample(30, random_state=0)  # delivery_id repeats for 505 exact-duplicate LRs
    lr = T._t("vendor_lr_clean.parquet")
    ship = T._t("vendor_shipments.parquet")[["shipment_no", "n_lrs", "utilisation"]].rename(columns={"utilisation": "shipment_utilisation"})
    v = lr[lr["delivery_id"].isin(pred["record_id"])].merge(ship, on="shipment_no", how="left")
    v["month"], v["weekday"] = v["lr_date"].dt.month, v["lr_date"].dt.weekday
    assert len(v) == 30, "test rows must be found"
    p = np.array([T._score_cls("V2_LightGBM", v.iloc[[i]])[0] for i in range(len(v))])
    ref = pred.set_index("record_id").loc[v["delivery_id"], "probability"].values
    np.testing.assert_allclose(p, ref, atol=1e-6)


def test_v3_and_v1_scoring_match_saved_predictions():
    ship = T._t("vendor_shipments.parquet")
    p3 = _csv("pred_V3.csv").sample(30, random_state=0)
    s = ship[ship["shipment_no"].isin(p3["record_id"])]
    assert len(s) == 30
    got = np.array([T._score_cls("V3_LightGBM", s.iloc[[i]])[0] for i in range(len(s))])
    np.testing.assert_allclose(got, p3.set_index("record_id").loc[s["shipment_no"], "probability"].values, atol=1e-6)
    p1 = _csv("pred_V1.csv").sample(30, random_state=0)
    s = ship[ship["shipment_no"].isin(p1["record_id"])]
    assert len(s) == 30
    got = np.array([T._score_v1(s.iloc[[i]])["fair_rs"] for i in range(len(s))])
    np.testing.assert_allclose(got, p1.set_index("record_id").loc[s["shipment_no"], "predicted"].values, rtol=1e-6)


def test_check_trip_matches_m1_m2_predictions():
    p1, p2 = _csv("pred_M1.csv").set_index("record_id"), _csv("pred_M2.csv").set_index("record_id")
    for tid in p1.sample(15, random_state=1).index:
        r = T.check_trip({"trip_id": tid})
        assert r["status"] == "ok" and r["in_test_period"] is True
        assert r["predicted_min"] == pytest.approx(p1.loc[tid, "predicted"], rel=1e-6)
        assert r["severe_delay"]["probability"] == pytest.approx(p2.loc[tid, "probability"], abs=1e-6)
        assert r["severe_delay"]["flag"] == bool(p2.loc[tid, "predicted_label"])
        assert r["predicted_low_min"] <= r["predicted_min"] <= r["predicted_high_min"]
        assert len(r["severe_delay"]["top_drivers"]) == 3


@pytest.fixture(scope="module")
def lane():
    ov = _csv("vendor_overpay.csv").sort_values("excess_rs", ascending=False).iloc[0]
    return {"route": ov.route, "vehicle_type": ov.vehicle_type, "vendor": ov.vendor}


def test_check_booking_structure_and_consistency(lane):
    r = T.check_booking({**lane, "qty": 8.0, "month": 2, "quoted_rs": 30000})
    v2, v1, v3 = r["v2_loss_risk"], r["v1_fair_price"], r["v3_underload_risk"]
    for blk in (v2, v3):
        assert 0 <= blk["probability"] <= 1 and blk["flag"] == (blk["probability"] >= blk["threshold"])
    assert len(v2["top_drivers"]) == 3 and all({"feature", "value", "shap", "effect"} <= set(d) for d in v2["top_drivers"])
    assert v1["fair_rs"] > 0 and v1["fair_low_rs"] <= v1["fair_rs"] <= v1["fair_high_rs"]
    assert v1["overpay_pct"] == pytest.approx((30000 - v1["fair_rs"]) / v1["fair_rs"] * 100)
    assert v1["excess_rs"] == pytest.approx(max(30000 - v1["fair_rs"], 0))
    assert v3["expected_utilisation_pct"] == pytest.approx(8.0 / r["inputs"]["vehicle_capacity"] * 100)
    assert r["assumed_defaults"]


def test_check_booking_without_quote_has_no_overpay(lane):
    r = T.check_booking({**lane, "qty": 8.0, "month": 2})
    assert "overpay_pct" not in r["v1_fair_price"]


def test_check_booking_unknown_route_warns(lane):
    r = T.check_booking({**lane, "route": "NOSUCHROUTE", "qty": 8.0, "month": 2, "vehicle_capacity": 10})
    assert any("not seen" in w for w in r["warnings"]) and 0 <= r["v2_loss_risk"]["probability"] <= 1


@pytest.mark.parametrize("bad", [{"qty": 0}, {"qty": -3}, {"qty": "abc"}, {"month": 13}, {"month": None}, {"route": ""}, {"vendor": None}])
def test_check_booking_rejects_bad_input(lane, bad):
    b = {**lane, "qty": 8.0, "month": 2, **bad}
    assert "error" in T.call_tool("check_booking", {"booking": b})


def test_check_booking_flags_overfull_vehicle(lane):
    r = T.check_booking({**lane, "qty": 40.0, "month": 2, "vehicle_capacity": 10})
    assert any("capacity" in w for w in r["warnings"])


def test_find_alternative_vendors_sorted_and_filtered():
    lane = _csv("lane_vendor_rates.csv")
    cnt = lane.groupby(["route", "vehicle_type"]).size()
    route, vt = cnt[cnt >= 4].index[0]
    r = T.find_alternative_vendors(route, vt, k=3)
    rates = [a["rs_per_mt"] for a in r["alternatives"]]
    assert r["status"] == "ok" and len(rates) == 3 and rates == sorted(rates)
    assert all(a["lr_count"] >= 10 for a in r["alternatives"])
    ex = r["alternatives"][0]["vendor_name"]
    r2 = T.find_alternative_vendors(route, vt, k=3, exclude_vendor=ex.lower())
    assert ex not in [a["vendor_name"] for a in r2["alternatives"]]


def test_find_alternative_vendors_no_data():
    r = T.find_alternative_vendors("NOSUCHROUTE", "SB02")
    assert r["status"] == "insufficient data" and r["alternatives"] == []


def test_vendor_profile_matches_independent_calculation(lane):
    lr = T._t("vendor_lr_clean.parquet")
    v = lr[(lr.vendor_name == lane["vendor"]) & (lr.r2_freight > 0) & (lr.gta_invoice_value > 0)]
    r = T.vendor_profile(lane["vendor"].lower())
    assert r["status"] == "ok" and r["vendor"] == lane["vendor"]
    assert r["lrs"] == len(v)
    assert r["spend_rs"] == pytest.approx(v.r2_freight.sum())
    assert r["margin_pct"] == pytest.approx(v.margin.sum() / v.gta_invoice_value.sum() * 100)
    assert r["loss_share_pct"] == pytest.approx((v.margin < 0).mean() * 100)
    s = _csv("vendor_overpay_summary.csv").set_index("vendor").loc[lane["vendor"]]
    assert r["overpay_pct"] == pytest.approx((s.actual_rs - s.fair_rs) / s.fair_rs * 100)
    assert len(r["worst_lanes"]) <= 3 and all(w["lrs"] >= T.MIN_LRS for w in r["worst_lanes"])
    assert r["worst_lanes"] == sorted(r["worst_lanes"], key=lambda w: -w["loss_rs"])


def test_vendor_profile_unknown_vendor_suggests_matches(lane):
    r = T.vendor_profile(lane["vendor"][:-1] + "Z")
    assert r["status"] == "insufficient data" and r["did_you_mean"] and all(c.startswith("V") for c in r["did_you_mean"])
    assert T.vendor_profile("ZZZ NOT A VENDOR")["did_you_mean"] == []


def test_check_trip_manual_input_and_validation():
    r = T.check_trip({"osrm_time_total": 120, "osrm_distance_total": 130, "route_type": "FTL"})
    assert r["status"] == "ok" and r["predicted_min"] > 0 and r["warnings"] and "recorded_actual_min" not in r
    assert "error" in T.call_tool("check_trip", {"trip": {"osrm_time_total": 120, "osrm_distance_total": 130, "route_type": "Rail"}})
    assert "error" in T.call_tool("check_trip", {"trip": {"osrm_time_total": -5, "osrm_distance_total": 130, "route_type": "FTL"}})
    assert T.check_trip({"trip_id": "trip-does-not-exist"})["status"] == "insufficient data"


def test_hub_report_ranking_and_lookup():
    r = T.hub_report(top=5)
    ex = [h["excess_hours"] for h in r["hubs"]]
    assert len(ex) == 5 and ex == sorted(ex, reverse=True) and r["network_median_handling_min"] > 0
    first = r["hubs"][0]
    assert T.hub_report(first["source_center"])["hubs"][0]["source_name"] == first["source_name"]
    assert T.hub_report(first["source_name"][:6].lower())["status"] == "ok"
    assert T.hub_report("zzzz-no-such-hub")["status"] == "insufficient data"


def test_hub_report_unranked_centre_uses_all_period_figures():
    legs = T._t("middle_mile_legs.parquet")
    ranked = set(_csv("hub_handling_ranking.csv")["source_center"])
    other = legs[~legs["source_center"].isin(ranked)]["source_center"].iloc[0]
    r = T.hub_report(other)
    assert r["status"] == "not ranked" and r["legs"] >= 1


@pytest.mark.parametrize("qid", list(T.QUERIES))
def test_every_whitelisted_query_runs_with_defaults(qid):
    r = T.query_data(qid)
    assert r["status"] == "ok" and r["n_rows"] > 0 and r["query_id"] == qid


def test_query_parameters_are_applied_and_validated():
    r = T.query_data("vendors_by_loss", {"zone": "north", "quarter": "2025Q4", "top": 3})
    assert r["params"]["zone"] == "NORTH" and r["n_rows"] <= 3
    loss = [x["loss_rs"] for x in r["rows"]]
    assert loss == sorted(loss, reverse=True) and all(x["lrs"] >= T.MIN_LRS for x in r["rows"])
    for bad in ({"zone": "Atlantis"}, {"quarter": "2099Q1"}, {"top": 999}, {"top": "1; DROP TABLE x"}, {"top": "__import__('os')"}, {"nope": 1}):
        assert "error" in T.call_tool("query_data", {"query_id": "vendors_by_loss", "params": bad})


def test_unsupported_queries_are_refused_with_list():
    for q in ("what_is_the_weather", "SELECT * FROM lr", "__import__('os').system('dir')", ""):
        r = T.query_data(q)
        assert r["status"] == "unsupported" and "can't answer" in r["message"]
        assert {x["query_id"] for x in r["available_queries"]} == set(T.QUERIES)


def test_query_results_match_source_tables():
    top = T.query_data("top_overpaying_lanes", {"top": 1})["rows"][0]
    ref = _csv("vendor_overpay.csv").sort_values("excess_rs", ascending=False).iloc[0]
    assert top["vendor"] == ref.vendor and top["excess_rs"] == pytest.approx(ref.excess_rs)
    m = T.query_data("monthly_margin_trend")["rows"]
    assert len(m) == 12 and sum(x["lrs"] for x in m) == len(T._lr_valid())


def test_all_ten_templates_render_with_no_placeholders_left():
    tpls = T.list_templates()
    assert len(tpls) == 10
    for t in tpls:
        fields = {f: (12.5 if i % 3 else "X") for i, f in enumerate(t["fields"])}
        r = T.draft_message(t["template_id"], fields)
        assert "{{" not in r["text"] and r["numbers_inserted_by_code"] and r["polished"] is False


def test_draft_message_missing_field_errors():
    t = T.list_templates()[0]
    assert "error" in T.call_tool("draft_message", {"template_id": t["template_id"], "fields": {}})
    assert "error" in T.call_tool("draft_message", {"template_id": "no_such_template", "fields": {}})


def test_polisher_cannot_change_numbers_or_placeholders():
    t = T.list_templates()[0]
    fields = {f: "VAL" + str(i) for i, f in enumerate(t["fields"])}
    ok = T.draft_message(t["template_id"], fields, polisher=lambda s: s.replace("Dear", "Hello"))
    assert ok["polished"] is True and "Hello" in ok["text"] and "VAL0" in ok["text"]
    dropped = T.draft_message(t["template_id"], fields, polisher=lambda s: s.replace("{{" + t["fields"][0] + "}}", "99999"))
    assert dropped["polished"] is False and "99999" not in dropped["text"] and "rejected" in dropped["note"]
    boom = T.draft_message(t["template_id"], fields, polisher=lambda s: 1 / 0)
    assert boom["polished"] is False and "failed" in boom["note"]
