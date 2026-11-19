"""Lane finder logic (app/lanes.py). Rules are tested on small synthetic frames; the data-backed tests assert properties
that must hold for any lane (so they do not depend on one lane's numbers)."""
import json

import pandas as pd
import pytest

from app import lanes as L
from app import tools as T
from backend import vendor_codes as vc

NAMES = vc.names()


def no_names(payload):
    low = json.dumps(payload, default=str).casefold()
    leaked = [n for n in NAMES if n.casefold() in low]
    assert not leaked, f"vendor name(s) leaked: {leaked[:3]}"


def rows(rates, lrs=50, days=5):
    n = len(rates)
    lrs = lrs if isinstance(lrs, list) else [lrs] * n
    days = days if isinstance(days, list) else [days] * n
    return pd.DataFrame({"vendor_code": [f"V{i + 1:03d}" for i in range(n)], "rs_per_mt": rates, "lrs": lrs, "days_since_last_use": days, "total_mt": [100.0] * n})


def test_tags_follow_the_ten_percent_rule():
    t = L.tag_group(rows([100, 105, 108, 120])).set_index("vendor_code")  # median of the reliable vendors = 106.5; Costly above 117.15
    assert t.loc["V001", "tag"] == "Cheapest"
    assert t.loc["V002", "tag"] == "Fair" and t.loc["V003", "tag"] == "Fair"
    assert t.loc["V004", "tag"] == "Costly"
    assert t["rank"].tolist() == [1, 2, 3, 4]


def test_exactly_ten_percent_above_the_median_is_still_fair():
    t = L.tag_group(rows([90, 100, 110])).set_index("vendor_code")  # median 100; 110 is exactly +10%, not more than 10%
    assert t.loc["V003", "tag"] == "Fair"
    assert L.tag_group(rows([90, 100, 110.5])).set_index("vendor_code").loc["V003", "tag"] == "Costly"


def test_thin_data_vendors_get_no_tag_and_no_rank_and_do_not_set_the_median():
    t = L.tag_group(rows([100, 110, 50, 60], lrs=[40, 40, 9, 40], days=[5, 5, 5, 91])).set_index("vendor_code")
    assert t.loc["V003", "thin"] and t.loc["V003", "tag"] is None and pd.isna(t.loc["V003", "rank"])      # fewer than 10 LRs
    assert t.loc["V004", "thin"] and t.loc["V004", "tag"] is None and "90 days" in t.loc["V004", "thin_reason"]  # not used for 91 days
    assert t.loc["V001", "tag"] == "Cheapest"  # the 50 and 60 Rs/MT thin vendors cannot take the Cheapest tag
    assert t["lane_median_rs_per_mt"].iloc[0] == 105


def test_a_vendor_with_exactly_ten_lrs_used_90_days_ago_is_not_thin():
    t = L.tag_group(rows([100, 120], lrs=[10, 10], days=[90, 0]))
    assert not t["thin"].any()


def test_all_thin_means_no_tags_at_all():
    t = L.tag_group(rows([100, 120], lrs=[3, 4]))
    assert t["tag"].isna().all() and t["rank"].isna().all()


def test_saving_if_switched_is_the_rate_gap_times_that_vendors_tonnes():
    t = L.tag_group(rows([100, 130, 160])).set_index("vendor_code")
    assert t.loc["V001", "saving_if_switched_rs"] == 0
    assert t.loc["V002", "saving_if_switched_rs"] == pytest.approx(30 * 100)
    assert t.loc["V003", "saving_if_switched_rs"] == pytest.approx(60 * 100)


def _legs(direct_rate, via_rates):
    ex = [("A", "B", direct_rate)] if direct_rate else []
    ex += [("A", "X", via_rates[0]), ("X", "B", via_rates[1])]
    return pd.DataFrame([{"dep": a, "dest": b, "vehicle_type": "SB04", "vendor_code": "V001", "rs_per_mt": r, "lrs": 30} for a, b, r in ex])


@pytest.fixture
def toy(monkeypatch):
    monkeypatch.setattr(L, "_places", lambda: (["A", "X"], ["B", "X"]))
    return monkeypatch


def test_two_leg_shown_only_when_at_least_ten_percent_cheaper(toy):
    toy.setattr(L, "_best_legs", lambda m, v: _legs(230, (100, 100)))   # 200 vs 230: 13% cheaper
    r = L.two_leg_options("A", "B")
    assert len(r["options"]) == 1 and r["options"][0]["via"] == "X" and r["options"][0]["saving_pct"] == pytest.approx(100 * 30 / 230)
    assert r["options"][0]["total_rs_per_mt"] == 200 and "excludes handling" in r["options"][0]["label"] and "indicative" in r["label"]
    toy.setattr(L, "_best_legs", lambda m, v: _legs(210, (100, 100)))   # 200 vs 210: 4.8% cheaper -> not shown
    assert L.two_leg_options("A", "B")["options"] == []
    toy.setattr(L, "_best_legs", lambda m, v: _legs(222.22, (100, 100)))  # about 10.0%: just under or at the line
    assert len(L.two_leg_options("A", "B")["options"]) == (1 if (222.22 - 200) / 222.22 * 100 >= 10 else 0)


def test_two_leg_with_no_direct_lane_lists_paths_without_a_saving(toy):
    toy.setattr(L, "_best_legs", lambda m, v: _legs(None, (100, 120)))
    r = L.two_leg_options("A", "B")
    assert r["has_direct"] is False and r["options"][0]["total_rs_per_mt"] == 220 and r["options"][0]["saving_pct"] is None


def test_two_leg_never_replaces_the_direct_recommendation():
    r = L.lane_search("HAZIRA", "KHADOLI")
    rec = r["recommendation"]
    assert rec is None or (rec["vendor_code"] and rec["vehicle_type"])          # the recommendation is always a direct vendor + vehicle
    assert "two_leg" in r and all("indicative" in o["label"] for o in r["two_leg"]["options"])


@pytest.fixture(scope="module")
def hk():
    return L.lane_options("HAZIRA", "KHADOLI", months=6)


def test_each_group_is_ranked_tagged_and_ordered(hk):
    assert hk["has_direct"] and hk["groups"]
    for g in hk["groups"]:
        vs = g["vendors"]
        ok = [v for v in vs if not v["thin"]]
        assert [v["rank"] for v in ok] == list(range(1, len(ok) + 1))
        assert [v["rs_per_mt"] for v in ok] == sorted(v["rs_per_mt"] for v in ok)
        if ok:
            assert ok[0]["tag"] == "Cheapest" and g["min_rs_per_mt"] == ok[0]["rs_per_mt"]
            for v in ok[1:]:
                assert v["tag"] == ("Costly" if v["rs_per_mt"] > g["lane_median_rs_per_mt"] * 1.10 else "Fair")
        for v in vs:
            if v["thin"]:
                assert v["tag"] is None and v["rank"] is None
                assert v["lrs"] < 10 or v["days_since_last_use"] > 90
            else:
                assert v["lrs"] >= 10 and v["days_since_last_use"] <= 90


def test_savings_in_the_payload_match_the_formula(hk):
    for g in hk["groups"]:
        ok = [v for v in g["vendors"] if not v["thin"]]
        if not ok:
            continue
        best = ok[0]["rs_per_mt"]
        for v in g["vendors"]:
            assert v["saving_if_switched_rs"] == pytest.approx(max(v["rs_per_mt"] - best, 0) * v["total_mt"], rel=1e-9)


def test_recommendation_is_the_cheapest_reliable_vendor_and_smallest_vehicle_that_fits():
    for qty in (3, 9, 18):
        r = L.lane_options("HAZIRA", "KHADOLI", qty_mt=qty, months=12)
        rec = r["recommendation"]
        if rec is None:
            continue
        assert rec["capacity_mt"] >= qty and rec["qty_mt"] == qty
        smaller = [g for g in r["groups"] if g["capacity_mt"] and qty <= g["capacity_mt"] < rec["capacity_mt"] and not g["thin_only"]]
        assert not smaller, "a smaller vehicle with reliable vendors also fits"
        g = next(g for g in r["groups"] if g["vehicle_type"] == rec["vehicle_type"] and g["basis"] == rec["basis"])
        assert rec["vendor_code"] == next(v for v in g["vendors"] if not v["thin"])["vendor_code"]
        assert rec["booking_prefill"]["vendor"] == rec["vendor_code"] and rec["booking_prefill"]["route"] == "HZKHA1"


def test_a_load_bigger_than_any_truck_gets_no_recommendation_and_says_why():
    r = L.lane_options("HAZIRA", "KHADOLI", qty_mt=99, months=12)
    assert r["recommendation"] is None and any("99" in n for n in r["notes"])


def test_vehicle_type_filter_and_period_are_applied():
    r = L.lane_options("HAZIRA", "KHADOLI", vehicle_type="SB04", months=3)
    assert {g["vehicle_type"] for g in r["groups"]} == {"SB04"}
    assert r["summary"]["lrs"] <= L.lane_options("HAZIRA", "KHADOLI", vehicle_type="SB04", months=12)["summary"]["lrs"]
    assert r["summary"]["full_year_lrs"] >= r["summary"]["lrs"]


def test_gap_to_fair_needs_ten_covered_shipments_and_flags_extremes_without_touching_tags():
    for lane in (("HAZIRA", "KHADOLI"), ("PATALGANGA", "D-NIYOL"), ("HAZIRA", "KARANJ")):
        r = L.lane_options(*lane, months=6)
        for g in r["groups"]:
            for v in g["vendors"]:
                assert (v["gap_to_fair_pct"] is None) == (v["n_gap_shipments"] < 10)
                assert v["check_data"] == (v["gap_to_fair_pct"] is not None and abs(v["gap_to_fair_pct"]) > 30)
    # tags depend on rupees per MT only: the same rate with and without a wild gap gets the same tag
    a = rows([100, 105, 120]); a["gap_to_fair_pct"] = [0.0, 0.0, 0.0]
    b = rows([100, 105, 120]); b["gap_to_fair_pct"] = [-45.0, 60.0, None]
    assert L.tag_group(a)["tag"].tolist() == L.tag_group(b)["tag"].tolist()


def test_estimate_falls_back_to_rate_x_load_when_the_gap_fails_the_ten_shipment_rule():
    thin_gap = {"vendor_code": "V001", "gap_to_fair_pct": None}
    est = L._estimate("HZKHA1", "SB04", thin_gap, 15)
    assert est["estimate_rs"] is None and est["vendor_gap_pct"] is None and "Rate x load" in est["basis"] and est["fair_rs"] > 0
    ok = L._estimate("HZKHA1", "SB04", {"vendor_code": "V001", "gap_to_fair_pct": -10.0}, 15)
    assert ok["estimate_rs"] == pytest.approx(ok["fair_rs"] * 0.9)


def test_recommendation_estimate_only_uses_a_gap_with_enough_evidence():
    for lane in (("HAZIRA", "KHADOLI"), ("PATALGANGA", "D-NIYOL"), ("HAZIRA", "KARANJ")):
        rec = L.lane_options(*lane, qty_mt=10, months=6)["recommendation"]
        if rec and rec["estimate"]:
            assert (rec["estimate"]["estimate_rs"] is None) == (rec["gap_to_fair_pct"] is None)
            assert rec["rate_based_rs"] > 0


def test_unknown_places_raise_with_suggestions():
    with pytest.raises(L.LaneNotFound) as e:
        L.lane_options("HAZIR", "KHADOLI")
    assert e.value.suggestions == ["HAZIRA"] and e.value.kind == "origin"
    with pytest.raises(L.LaneNotFound) as e:
        L.lane_options("HAZIRA", "KHADOL")
    assert e.value.suggestions == ["KHADOLI"] and e.value.kind == "destination"
    with pytest.raises(L.LaneNotFound):
        L.lane_options("KHADOLI", "HAZIRA")  # KHADOLI is a destination only; the lane has no origin by that name


def test_known_places_with_no_direct_lane_is_an_empty_state_not_an_error():
    pts = L.lane_points()
    o = pts["origins"][-1]
    reach = {d["name"] for d in o["destinations"]}
    other = next(d for d in L._places()[1] if d not in reach)
    r = L.lane_search(o["name"], other)
    assert r["has_direct"] is False and r["groups"] == [] and any("No direct lane" in n for n in r["notes"])


def test_points_dropdown_only_lists_reachable_destinations():
    pts = L.lane_points()
    h = next(o for o in pts["origins"] if o["name"] == "HAZIRA")
    assert any(d["name"] == "KHADOLI" for d in h["destinations"]) and len(h["destinations"]) < pts["n_destinations"]
    assert pts["n_lanes"] == sum(len(o["destinations"]) for o in pts["origins"])


def test_vendor_compare_uses_only_shared_lanes_with_enough_lrs():
    a, b = L.best_pair()
    r = L.vendor_compare([a, b])
    assert r["n_shared_lanes"] > 0
    for x in r["lanes"]:
        assert set(x["rs_per_mt"]) == {a, b} and all(n >= 10 for n in x["lrs"].values())
        assert x["cheaper"] == min(x["rs_per_mt"], key=x["rs_per_mt"].get)
    assert sum(v["lanes_won"] for v in r["vendors"]) == r["n_shared_lanes"]
    assert all(v["index_vs_lane_median"] and v["extra_cost_vs_cheapest_rs"] >= 0 for v in r["vendors"])
    assert min(v["extra_cost_vs_cheapest_rs"] for v in r["vendors"]) >= 0


def test_vendor_compare_validates_codes():
    with pytest.raises(T.ToolError):
        L.vendor_compare(["V001"])
    with pytest.raises(T.ToolError):
        L.vendor_compare("V001,V002,V003,V004,V005")
    with pytest.raises(L.VendorNotFound):
        L.vendor_compare("V001,V999")


def test_no_vendor_names_in_any_lane_output():
    a, b = L.best_pair()
    for payload in (L.lane_points(), L.lane_search("HAZIRA", "KHADOLI", qty_mt=10), L.lane_search("SILVASSA", "KHADOLI"), L.vendor_compare([a, b]),
                    T.query_data("lane_vendor_ranking", {}), T.query_data("vendor_head_to_head", {})):
        no_names(payload)


def test_lane_vendor_ranking_query_is_cheapest_first_and_head_to_head_is_shared_only():
    r = T.query_data("lane_vendor_ranking", {"origin": "hazira", "destination": "Khadoli", "vehicle_type": "SB04"})
    rs = [x["rs_per_mt"] for x in r["rows"]]
    assert r["status"] == "ok" and rs == sorted(rs) and all(x["vehicle_type"] == "SB04" for x in r["rows"])
    h = T.query_data("vendor_head_to_head", {"vendor_a": "V013", "vendor_b": "V023"})
    assert h["rows"] and all(x["cheaper_vendor"] in ("V013", "V023") for x in h["rows"])
    for bad in ({"origin": "NOWHERE", "destination": "KHADOLI"}, {"months": 99}):
        with pytest.raises(T.ToolError):
            T.query_data("lane_vendor_ranking", bad)
    with pytest.raises(T.ToolError):
        T.query_data("vendor_head_to_head", {"vendor_a": "V013", "vendor_b": "V9999"})


def test_router_sends_lane_and_head_to_head_questions_to_the_new_queries():
    from app import agent as A
    assert A.route_question("cheapest vendor from Hazira to Khadoli") == ("lane_vendor_ranking", {"origin": "HAZIRA", "destination": "KHADOLI"})
    assert A.route_question("who is the cheapest from hazira to khadoli with SB04?")[1]["vehicle_type"] == "SB04"
    assert A.route_question("compare V013 vs V023") == ("vendor_head_to_head", {"vendor_a": "V013", "vendor_b": "V023"})
    assert A.route_question("cheapest vendor from Atlantis to Khadoli")[0] is None
    assert A.route_question("which lanes have the widest price gap")[0] == "vendor_price_gap_lanes"


def test_suggestions_are_prefix_or_very_close_matches_only():
    deps, dests = L._places()
    assert L._suggest("HAZIR", deps) == ["HAZIRA"]                       # prefix
    assert L._suggest("HAZRA", deps) == ["HAZIRA"]                       # similarity >= 0.8
    assert L._suggest("QQQQQ", deps) == [] and L._suggest("", deps) == []  # nothing qualifies: empty, not a guess
    for typed in ("HAZIR", "KHADOL", "SILVAS", "PATALG"):
        for n in L._suggest(typed, deps + dests):
            import difflib
            assert n.startswith(typed) or difflib.SequenceMatcher(None, typed, n).ratio() >= 0.8
    with pytest.raises(L.LaneNotFound) as e:
        L.lane_options("QQQQQ", "KHADOLI")
    assert e.value.suggestions == []


def test_llm_query_picker_is_told_the_param_names_so_lane_questions_get_their_places():
    schema = next(t for t in T.TOOL_SCHEMAS if t["function"]["name"] == "query_data")
    desc = schema["function"]["parameters"]["properties"]["params"]["description"]
    assert "lane_vendor_ranking: origin, destination, vehicle_type, months" in desc and "vendor_head_to_head: vendor_a, vendor_b" in desc
