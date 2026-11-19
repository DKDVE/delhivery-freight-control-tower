"""Phase 6 ROI inputs. Data only: every rupee and count the strategies need, from the test period and the saved models.

    PYTHONPATH=. python scripts/roi_inputs.py            (about 8 minutes; most of it is the 2,000-booking policy sample)

Writes reports/roi_inputs.json and CSVs in reports/roi/, then prints a summary table. Vendors appear by code only.
It does not choose capture rates or costs and it does not write documents: the team applies those in the ROI model.

Ownership rule: each shipment's benefit belongs to one strategy, in the order S3 -> S1 -> S2. Later strategies are recomputed
without the shipments an earlier one has claimed. Gross figures are before that exclusion, net figures after.
"""
from __future__ import annotations

import json
import os
import sys
import time

import joblib
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import agent, tools as T  # noqa: E402
from backend import vendor_codes as vc  # noqa: E402
from src import config, prep  # noqa: E402
from src import vendor_pipeline as vp  # noqa: E402

PROC = os.path.join(ROOT, "data", "processed")
ROI_DIR = os.path.join(ROOT, "reports", "roi")
OUT_JSON = os.path.join(ROOT, "reports", "roi_inputs.json")

SEED = 42
MIN_CELL = 10          # shipments per vendor x route x vehicle_type cell in the test period
MIN_LANE_LRS = 10      # LRs per vendor on a lane in training, for lever B
TARGET_PCT = 0.05      # lever A target: cell actual / fair - 1 above 5%
SENS_PCT = (0.03, 0.10)
FRACS = (0.20, 0.35, 0.50)  # share of the costlier vendors' volume moved (lever B scenarios)
MID = 0.35             # the middle scenario is the one used for claims and for the single headline figure
POLICY_SAMPLE = 2000
WEEKS_PER_YEAR = 52
MM_LABEL = "2018 network scale, illustrative"


def pct_key(f: float) -> str:
    return f"{int(round(f * 100))}"


def cr(v: float) -> str:
    a = abs(v)
    s = "-" if v < 0 else ""
    if a >= 1e7:
        return f"{s}₹{a / 1e7:.1f} Cr"
    if a >= 1e5:
        return f"{s}₹{a / 1e5:.1f} lakh"
    return f"{s}₹{a:,.0f}"


def load() -> dict:
    ship = pd.read_parquet(os.path.join(PROC, "vendor_shipments.parquet"))
    lr = pd.read_parquet(os.path.join(PROC, "vendor_lr_clean.parquet"))
    codes = vc.table().set_index("vendor_name")["vendor_code"]
    ship["vendor_code"] = ship["vendor"].map(codes)
    lr["vendor_code"] = lr["vendor_name"].map(codes)
    assert ship["vendor_code"].notna().all() and lr["vendor_code"].notna().all(), "every vendor needs a code"
    return {
        "ship": ship, "lr": lr,
        "p1": pd.read_csv(os.path.join(PROC, "pred_V1.csv")), "p2": pd.read_csv(os.path.join(PROC, "pred_V2.csv")),
        "p3": pd.read_csv(os.path.join(PROC, "pred_V3.csv")),
        "trips": pd.read_parquet(os.path.join(PROC, "middle_mile_trips.parquet")), "legs": pd.read_parquet(os.path.join(PROC, "middle_mile_legs.parquet")),
        "p_m1": pd.read_csv(os.path.join(PROC, "pred_M1.csv")), "p_m2": pd.read_csv(os.path.join(PROC, "pred_M2.csv")),
        "hub": pd.read_csv(os.path.join(PROC, "hub_handling_ranking.csv")),
        "dup": pd.read_csv(os.path.join(PROC, "audit_duplicate_rows.csv"), encoding="utf-8-sig"),
    }


def context(d: dict) -> dict:
    lr, ship = d["lr"], d["ship"]
    v = lr[(lr["r2_freight"] > 0) & (lr["gta_invoice_value"] > 0)]
    test = v[v["lr_date"] >= prep.TEST_START]

    def blk(x):
        return {"valid_freight_rs": float(x["r2_freight"].sum()), "billed_rs": float(x["gta_invoice_value"].sum()), "margin_rs": float(x["margin"].sum()),
                "margin_pct": float(x["margin"].sum() / x["gta_invoice_value"].sum() * 100), "valid_lrs": int(len(x))}

    year, q = blk(v), blk(test)
    return {
        "year": {**blk(v), "period": "2025-04-01 to 2026-03-31"}, "test_quarter": {**q, "period": "2026-01-01 to 2026-03-31"},
        "annual_factor": year["valid_freight_rs"] / q["valid_freight_rs"], "annual_factor_x4": 4.0,
        "annual_factor_definition": "valid freight Apr 2025-Mar 2026 / valid freight Jan-Mar 2026 (valid = LRs with freight > 0 and billed value > 0)",
        "counts": {"lrs_in_clean_table": int(len(lr)), "valid_cost_lrs": int(len(v)), "shipments": int(len(ship)), "valid_cost_shipments": int(ship["valid_cost"].sum()),
                   "vendors": int(lr["vendor_code"].nunique()), "vendors_with_10plus_lrs": int((lr.groupby("vendor_code").size() >= 10).sum()),
                   "routes": int(lr["route"].nunique()), "lanes_route_x_vehicle_type": int(lr.groupby(["route", "vehicle_type"]).ngroups),
                   "plants": int(lr["origin_plant"].nunique())},
    }


def s3(d: dict) -> tuple[dict, set, pd.DataFrame]:
    ship = d["ship"]
    v3 = ship[ship["valid_capacity"] & ~ship["util_outlier"]].copy()
    m3 = v3[v3["test"]].merge(d["p3"][["record_id", "predicted_label"]], left_on="shipment_no", right_on="record_id", how="inner")
    assert len(m3) == len(d["p3"]) and m3["shipment_no"].is_unique
    flagged = m3[m3["predicted_label"] == 1]
    elig = flagged[flagged["all_cost_valid"] & (flagged["distance"] > 0) & (flagged["total_qty"] > 0) & (flagged["freight"] > 0)]
    bundle = joblib.load(os.path.join(ROOT, "models", "V1_RandomForest.joblib"))
    cf = vp.counterfactual_saving(elig, v3[v3["train"]], bundle["pipeline"], bundle["cats"], bundle["nums"])
    saved = json.load(open(os.path.join(ROOT, "reports", "metrics", "V3.json"), encoding="utf8"))["business_impact_inputs"]["counterfactual_saving"]
    assert abs(cf["saving"].sum() - saved["total_saving_rs"]) < 1.0, "counterfactual no longer reproduces the saved V3 figure"
    pos = cf[cf["saving"] > 0]
    by_route = pos.groupby("route").agg(shipments=("saving", "size"), saving_rs=("saving", "sum"), freight_rs=("freight", "sum")).sort_values("saving_rs", ascending=False)
    by_route["saving_pct"] = by_route["saving_rs"] / by_route["freight_rs"] * 100
    by_route.head(10).reset_index().to_csv(os.path.join(ROI_DIR, "s3_top_routes.csv"), index=False)
    out = {
        "claim_rule": "S3 claims every flagged shipment the counterfactual covers (a right-sizing action applies to the whole shipment)",
        "flagged_shipments": int(len(flagged)), "shipments_covered": int(len(cf)), "shipments_with_positive_saving": int(len(pos)),
        "saving_rs_gross": float(cf["saving"].sum()), "saving_rs_net": float(cf["saving"].sum()),
        "actual_freight_on_covered_rs": float(cf["freight"].sum()),
        "median_saving_pct_all_covered": float(cf["saving_pct"].median()), "median_saving_pct_where_positive": float(pos["saving_pct"].median()),
        "distinct_routes_affected": int(pos["route"].nunique()), "distinct_plants_affected": int(pos["origin_plant"].nunique()),
        "top10_routes_by_saving": by_route.head(10).reset_index().round(2).to_dict("records"),
        "top10_share_of_saving": float(by_route.head(10)["saving_rs"].sum() / by_route["saving_rs"].sum()),
        "reconciles_with": "reports/metrics/V3.json counterfactual_saving.total_saving_rs",
        "caveat": "Indicative: V1 is a market-median price, capacities come from training history on the same route.",
    }
    return out, set(cf["shipment_no"]), cf


def lane_rates(ship: pd.DataFrame) -> pd.DataFrame:
    """Training-period volume-weighted Rs per MT for each vendor on each route x vehicle_type, vendors with 10+ LRs; lanes with 2+ such vendors."""
    t = ship[ship["train"] & ship["all_cost_valid"] & (ship["total_qty"] > 0) & (ship["freight"] > 0)]
    g = t.groupby(["route", "vehicle_type", "vendor_code"]).agg(freight=("freight", "sum"), qty=("total_qty", "sum"), lrs=("n_lrs", "sum")).reset_index()
    g = g[g["lrs"] >= MIN_LANE_LRS].copy()
    g["rate"] = g["freight"] / g["qty"]
    n = g.groupby(["route", "vehicle_type"])["vendor_code"].transform("size")
    g = g[n >= 2].copy()
    g["cheapest"] = g.groupby(["route", "vehicle_type"])["rate"].transform("min")
    return g


def s1_frame(d: dict) -> pd.DataFrame:
    ship = d["ship"]
    u = ship[ship["test"]].merge(d["p1"][["record_id", "actual", "predicted"]], left_on="shipment_no", right_on="record_id", how="inner")
    assert len(u) == len(d["p1"]) and np.allclose(u["freight"], u["actual"])
    u = u.rename(columns={"predicted": "fair", "total_qty": "qty"})[["shipment_no", "vendor_code", "route", "vehicle_type", "qty", "actual", "fair", "origin_plant"]]
    lr_ = lane_rates(ship)
    u = u.merge(lr_[["route", "vehicle_type", "vendor_code", "rate", "cheapest"]], on=["route", "vehicle_type", "vendor_code"], how="left")
    u["b_full"] = ((u["rate"] - u["cheapest"]).clip(lower=0) * u["qty"]).fillna(0.0)  # saving if ALL of this shipment's volume moved to the cheapest vendor
    return u


def cell_table(u: pd.DataFrame, cells: pd.DataFrame, thr: float = TARGET_PCT) -> pd.DataFrame:
    """Per-cell figures from the shipments in `u`; `cells` fixes which cells exist and (via gross_target) which are targeted."""
    g = u.groupby(["vendor_code", "route", "vehicle_type"]).agg(shipments=("shipment_no", "size"), actual=("actual", "sum"), fair=("fair", "sum"), b_full=("b_full", "sum")).reset_index()
    t = cells[["vendor_code", "route", "vehicle_type", "gross_target"]].merge(g, on=["vendor_code", "route", "vehicle_type"], how="left").fillna({"shipments": 0, "actual": 0, "fair": 0, "b_full": 0})
    t["overpay_pct"] = np.where(t["fair"] > 0, (t["actual"] / t["fair"] - 1) * 100, np.nan)
    t["lever_a"] = np.where(t["gross_target"], t["actual"] - t["fair"], 0.0)
    for f in FRACS:
        t[f"lever_b_{pct_key(f)}"] = t["b_full"] * f
        t[f"best_{pct_key(f)}"] = np.maximum(t["lever_a"], t[f"lever_b_{pct_key(f)}"])  # no double counting: the larger lever per cell
    return t.drop(columns=["b_full"])


def s1(d: dict, u: pd.DataFrame, s3_claims: set) -> tuple[dict, set, pd.DataFrame]:
    n = u.groupby(["vendor_code", "route", "vehicle_type"]).agg(shipments=("shipment_no", "size"), actual=("actual", "sum"), fair=("fair", "sum")).reset_index()
    cells = n[n["shipments"] >= MIN_CELL].copy()
    cells["gross_target"] = cells["actual"] / cells["fair"] - 1 > TARGET_PCT
    in_cells = u.merge(cells[["vendor_code", "route", "vehicle_type"]], on=["vendor_code", "route", "vehicle_type"])
    gross = cell_table(in_cells, cells)
    net_u = in_cells[~in_cells["shipment_no"].isin(s3_claims)]
    net = cell_table(net_u, cells)  # same cells and same targets; S3-claimed shipments removed from the sums

    # lever A sensitivity on the gross cells (3 / 5 / 10 percent)
    sens = {}
    for thr in (SENS_PCT[0], TARGET_PCT, SENS_PCT[1]):
        tg = gross["actual"] / gross["fair"] - 1 > thr
        sens[f"{int(thr * 100)}pct"] = {"target_cells": int(tg.sum()), "gap_rs": float((gross["actual"] - gross["fair"])[tg].sum())}

    tgt = gross[gross["gross_target"]]
    gap = (tgt["actual"] - tgt["fair"]).sort_values(ascending=False)
    a_gross = float(tgt["lever_a"].sum())
    b_gross = {pct_key(f): float(gross[f"lever_b_{pct_key(f)}"].sum()) for f in FRACS}
    best_gross = {pct_key(f): float(gross[f"best_{pct_key(f)}"].sum()) for f in FRACS}
    best_net = {pct_key(f): float(net[f"best_{pct_key(f)}"].sum()) for f in FRACS}

    # claims at the middle scenario: a cell whose winning lever is A claims every remaining shipment in it (the rate applies to the cell);
    # a cell won by B claims only the remaining shipments on which a cheaper vendor exists
    mk = pct_key(MID)
    win_a = (net["lever_a"] >= net[f"lever_b_{mk}"]) & (net["lever_a"] > 0)
    win_b = (~win_a) & (net[f"lever_b_{mk}"] > 0)
    claim = set()
    keys = ["vendor_code", "route", "vehicle_type"]
    tag = net_u.merge(net[keys].assign(win_a=win_a.values, win_b=win_b.values), on=keys)
    claim |= set(tag.loc[tag["win_a"], "shipment_no"])
    claim |= set(tag.loc[tag["win_b"] & (tag["b_full"] > 0), "shipment_no"])
    assert not (claim & s3_claims), "S1 must not claim a shipment S3 owns"

    out_csv = gross.rename(columns={"actual": "actual_rs", "fair": "fair_rs", "gross_target": "target_cell"})
    out_csv = out_csv.merge(net[keys + [f"best_{mk}"]].rename(columns={f"best_{mk}": f"net_best_{mk}_after_S3"}), on=keys)
    out_csv.round(2).sort_values("lever_a", ascending=False).to_csv(os.path.join(ROI_DIR, "s1_cells.csv"), index=False)

    lanes = lane_rates(d["ship"])
    out = {
        "cells_with_10plus_test_shipments": int(len(cells)), "target_cells": int(len(tgt)), "target_vendors": int(tgt["vendor_code"].nunique()),
        "target_lanes_route_x_vehicle_type": int(tgt.groupby(["route", "vehicle_type"]).ngroups),
        "actual_rs_in_cells": float(gross["actual"].sum()), "fair_rs_in_cells": float(gross["fair"].sum()),
        "lever_a_gap_rs_gross": a_gross, "lever_a_sensitivity": sens,
        "top10_cells_share_of_gap": float(gap.head(10).sum() / gap.sum()),
        "lever_b_rs_gross_by_share_moved_pct": b_gross,
        "lanes_eligible_for_reallocation": int(lanes.groupby(["route", "vehicle_type"]).ngroups),
        "cells_with_a_cheaper_vendor_available": int((gross["lever_b_20"] > 0).sum()),
        "s1_rs_gross_by_share_moved_pct": best_gross, "s1_rs_net_by_share_moved_pct": best_net,
        "headline_share_moved_pct": int(MID * 100),
        "s1_rs_gross": best_gross[mk], "s1_rs_net": best_net[mk],
        "shipments_claimed_by_s1": len(claim),
        "definition": "per cell the larger of lever A (actual - fair, target cells above 5%) and lever B (share moved x (vendor rate - cheapest rate) x qty, training rates); target cells fixed on the gross cell",
        "claim_rule": "S1 claims all remaining shipments in cells won by lever A and the shipments with a cheaper vendor in cells won by lever B (middle scenario); S3 shipments are excluded first",
        "caveat": "Lever A is the gap to a market-median model price; lever B assumes the cheapest training rate on the lane is available at the same service quality.",
    }
    return out, claim, out_csv


def s2(d: dict, claimed: set) -> tuple[dict, pd.DataFrame]:
    lr, p2, ship = d["lr"], d["p2"], d["ship"]
    v2 = lr[(lr["r2_freight"] > 0) & (lr["gta_invoice_value"] > 0)].reset_index(drop=True)
    m = v2[v2["lr_date"] >= prep.TEST_START].reset_index(drop=True)
    assert len(m) == len(p2) and (m["delivery_id"].values == p2["record_id"].values).all(), "V2 test rows no longer line up with pred_V2"
    m["flag"] = p2["predicted_label"].values
    m["loss"] = (m["margin"] < 0).astype(int)
    assert (m["loss"].values == p2["actual"].values).all()
    m["abs_margin"] = m["margin"].abs()
    tp, fp, miss = m[(m.flag == 1) & (m.loss == 1)], m[(m.flag == 1) & (m.loss == 0)], m[(m.flag == 0) & (m.loss == 1)]
    saved = json.load(open(os.path.join(ROOT, "reports", "metrics", "V2.json"), encoding="utf8"))["business_impact_inputs"]
    assert abs(tp["abs_margin"].sum() - saved["loss_on_true_positives_rs"]) < 1.0 and len(tp) == saved["true_positives"]
    alerts = int(m["flag"].sum())
    cal_days = (pd.Timestamp("2026-03-31") - prep.TEST_START).days + 1
    work_days = int(m["lr_date"].nunique())

    # recommended-action mix: the booking policy of app/agent.py on a sample of flagged LRs (shipment-level booking inputs)
    flagged = m[m["flag"] == 1].merge(ship[["shipment_no", "route", "vehicle_type", "vehicle_capacity", "total_qty", "freight", "all_cost_valid", "distance", "vendor"]]
                                      .rename(columns={"route": "s_route", "vehicle_type": "s_vt", "vehicle_capacity": "s_cap", "total_qty": "s_qty", "freight": "s_freight",
                                                       "distance": "s_dist", "vendor": "s_vendor"}), on="shipment_no", how="left")
    usable = flagged[flagged["s_qty"].gt(0) & flagged["s_route"].notna() & flagged["s_vt"].notna()]
    sample = usable.sample(min(POLICY_SAMPLE, len(usable)), random_state=SEED)
    from functools import lru_cache
    T._t = lru_cache(maxsize=None)(T._t)                 # the lookup files do not change during the run
    T._top_shap = lambda *a, **k: []                      # the policy does not read the SHAP drivers; skipping them keeps the run short
    counts, errors, t0 = {}, 0, time.time()
    for r in sample.itertuples():
        b = {"route": r.s_route, "vehicle_type": r.s_vt, "vendor": r.s_vendor, "qty": float(r.s_qty), "month": int(r.lr_date.month), "weekday": int(r.lr_date.weekday())}
        if r.s_cap and r.s_cap > 0:
            b["vehicle_capacity"] = float(r.s_cap)
        if r.s_dist and r.s_dist > 0:
            b["distance"] = float(r.s_dist)
        if r.all_cost_valid and r.s_freight > 0:
            b["quoted_rs"] = float(r.s_freight)
        try:
            check = T.check_booking(b)
            alts = T.find_alternative_vendors(b["route"], b["vehicle_type"], k=3, exclude_vendor=b["vendor"])
            cur = T.lane_rate(b["route"], b["vehicle_type"], b["vendor"])
            smaller = T.smallest_capacity_for(b["route"], b["qty"]) if check["v3_underload_risk"]["flag"] else None
            act = agent.decide_booking(check, alts, cur, smaller)["action"]
        except Exception:
            errors += 1
            continue
        counts[act] = counts.get(act, 0) + 1
    scored = sum(counts.values())
    shares = {a: counts.get(a, 0) / scored for a in agent.ACTIONS}
    print(f"   policy sample: {scored} bookings scored, {errors} errors, {time.time() - t0:.0f}s")

    net_tp = tp[~tp["shipment_no"].isin(claimed)]
    out = {
        "threshold": float(p2["threshold"].iloc[0]), "alerts": alerts, "calendar_days": cal_days, "working_days_with_lrs": work_days,
        "alerts_per_calendar_day": alerts / cal_days, "alerts_per_working_day": alerts / work_days,
        "true_positives": int(len(tp)), "false_positives": int(len(fp)), "missed_loss_makers": int(len(miss)), "actual_loss_makers": int(m["loss"].sum()),
        "precision": float(len(tp) / alerts), "recall": float(len(tp) / m["loss"].sum()),
        "loss_on_true_positives_rs_gross": float(tp["abs_margin"].sum()), "loss_missed_rs": float(miss["abs_margin"].sum()),
        "avg_loss_per_true_positive_rs": float(tp["abs_margin"].mean()),
        "loss_on_true_positives_rs_net": float(net_tp["abs_margin"].sum()), "true_positives_net": int(len(net_tp)),
        "shipments_claimed_by_s2": int(net_tp["shipment_no"].nunique()),
        "action_mix": {"sample_size": scored, "sample_requested": POLICY_SAMPLE, "errors": errors, "counts": counts, "shares": shares,
                       "note": "agent.decide_booking on shipment-level inputs (route, vehicle, qty, month, quote = shipment freight). Lane vendor rates behind 'reassign' include the test months."},
        "reconciles_with": "reports/metrics/V2.json business_impact_inputs",
        "claim_rule": "net = true-positive LRs whose shipment is not claimed by S3 or S1",
    }
    return out, net_tp


def s4(d: dict) -> dict:
    trips = d["trips"].set_index("trip_uuid")
    base = config.RATES_PER_HOUR["base"]
    m2 = d["p_m2"].join(trips[["actual_min", "osrm_time_total", "route_type"]], on="record_id")
    assert m2["osrm_time_total"].notna().all()
    m2["tp"] = (m2["predicted_label"] == 1) & (m2["actual"] == 1)
    m2["exc_over_2x_h"] = (m2["actual_min"] - 2.0 * m2["osrm_time_total"]).clip(lower=0) / 60
    m2["exc_over_plan_h"] = (m2["actual_min"] - m2["osrm_time_total"]).clip(lower=0) / 60
    saved = json.load(open(os.path.join(ROOT, "reports", "metrics", "M2.json"), encoding="utf8"))["business_impact_inputs"]
    assert abs(m2.loc[m2.tp, "exc_over_plan_h"].sum() - saved["excess_hours_on_true_positives"]) < 0.01
    rows = {}
    for rt in ("FTL", "Carting"):
        x = m2[m2["route_type"] == rt]
        rows[rt] = {"trips": int(len(x)), "alerts": int((x["predicted_label"] == 1).sum()), "true_positives": int(x["tp"].sum()), "severe_trips": int(x["actual"].sum()),
                    "excess_hours_above_2x_plan_on_true_positives": float(x.loc[x.tp, "exc_over_2x_h"].sum()),
                    "excess_hours_above_plan_on_true_positives": float(x.loc[x.tp, "exc_over_plan_h"].sum())}
    m2_tot = {k: sum(r[k] for r in rows.values()) for k in rows["FTL"]}

    # hub fixes: same definition as the hub ranking (hours above the network median handling time of the test legs)
    legs = d["legs"]
    te = legs[legs["test"] & legs["handling_min"].notna()].copy()
    med = float(te["handling_min"].median())
    te["exc_h"] = (te["handling_min"] - med).clip(lower=0) / 60
    hub = d["hub"].sort_values("excess_hours", ascending=False).reset_index(drop=True)
    top10 = hub.head(10)
    hb = te[te["source_center"].isin(top10["source_center"])].groupby(["source_center", "route_type"])["exc_h"].sum().unstack(fill_value=0.0)
    for rt in ("FTL", "Carting"):
        if rt not in hb:
            hb[rt] = 0.0
    chk = hb.sum(axis=1).reindex(top10["source_center"]).values
    assert np.allclose(chk, top10["excess_hours"].values, atol=0.01), "hub excess hours no longer reproduce the ranking file"
    big = te.groupby("source_center").filter(lambda g: len(g) >= 30)
    hubs_out = {
        "network_median_handling_min": med, "hubs_ranked": int(len(hub)),
        "total_excess_hours_hubs_30plus_legs": float(hub["excess_hours"].sum()), "total_excess_hours_all_test_legs": float(te["exc_h"].sum()),
        "total_excess_hours_by_route_type": {rt: float(big.loc[big["route_type"] == rt, "exc_h"].sum()) for rt in ("FTL", "Carting")},
        "top10_excess_hours": float(top10["excess_hours"].sum()), "top10_share_of_excess": float(top10["excess_hours"].sum() / hub["excess_hours"].sum()),
        "top10_excess_hours_by_route_type": {rt: float(hb[rt].sum()) for rt in ("FTL", "Carting")},
        "top10_hubs": [{"hub": r.source_center, "name": r.source_name, "legs": int(r.legs), "excess_hours": float(r.excess_hours),
                        "FTL_hours": float(hb.loc[r.source_center, "FTL"]), "Carting_hours": float(hb.loc[r.source_center, "Carting"])} for r in top10.itertuples()],
    }
    pd.DataFrame(hubs_out["top10_hubs"]).round(2).to_csv(os.path.join(ROI_DIR, "s4_top10_hubs.csv"), index=False)

    # M1 planning gap: how far schedules are from reality with the plan vs with M1
    m1 = d["p_m1"].join(trips[["actual_min", "osrm_time_total", "route_type"]], on="record_id")
    ratio = json.load(open(os.path.join(ROOT, "reports", "metrics", "M1.json"), encoding="utf8"))["base_rate_or_baseline"]["median_training_ratio"]
    m1["gap_plan"] = (m1["actual_min"] - m1["osrm_time_total"]).abs() / 60
    m1["gap_plan_scaled"] = (m1["actual_min"] - ratio * m1["osrm_time_total"]).abs() / 60
    m1["gap_m1"] = (m1["actual_min"] - m1["predicted"]).abs() / 60
    gap = {}
    for rt in ("FTL", "Carting", "All"):
        x = m1 if rt == "All" else m1[m1["route_type"] == rt]
        gap[rt] = {"trips": int(len(x)), "abs_gap_hours_plan_as_is": float(x["gap_plan"].sum()), "abs_gap_hours_plan_scaled_by_training_ratio": float(x["gap_plan_scaled"].sum()),
                   "abs_gap_hours_m1": float(x["gap_m1"].sum()),
                   "hours_better_than_plan": float(x["gap_plan"].sum() - x["gap_m1"].sum()), "hours_better_than_scaled_plan": float(x["gap_plan_scaled"].sum() - x["gap_m1"].sum())}
    assert abs(gap["All"]["abs_gap_hours_m1"] - m1["abs_error"].sum() / 60) < 0.01

    def at_base(h_by_rt):
        return float(sum(h_by_rt[rt] * base[rt] for rt in ("FTL", "Carting")))

    hours_m2 = {rt: rows[rt]["excess_hours_above_2x_plan_on_true_positives"] for rt in rows}
    hours_m2_plan = {rt: rows[rt]["excess_hours_above_plan_on_true_positives"] for rt in rows}
    return {
        "scale_label": MM_LABEL, "weeks_per_year": WEEKS_PER_YEAR, "test_window": "trips created 2018-09-27 to 2018-10-03 (7 days)",
        "rates_for_convenience_rs_per_hour_base": base, "rate_source": config.RATE_SOURCE + " (the team sets the rates in the ROI model)",
        "m2_alerts": {"by_route_type": rows, "total": m2_tot, "base_rate_rs_on_hours_above_2x_plan": at_base(hours_m2), "base_rate_rs_on_hours_above_plan": at_base(hours_m2_plan),
                      "threshold": float(d["p_m2"]["threshold"].iloc[0]), "reconciles_with": "reports/metrics/M2.json (hours above plan)"},
        "hub_fixes": {**hubs_out, "base_rate_rs_top10": at_base(hubs_out["top10_excess_hours_by_route_type"]),
                      "base_rate_rs_network": at_base(hubs_out["total_excess_hours_by_route_type"])},
        "m1_planning_gap": {"by_route_type": gap, "median_training_ratio": ratio,
                            "base_rate_rs_hours_better_than_plan": at_base({rt: gap[rt]["hours_better_than_plan"] for rt in ("FTL", "Carting")}),
                            "base_rate_rs_hours_better_than_scaled_plan": at_base({rt: gap[rt]["hours_better_than_scaled_plan"] for rt in ("FTL", "Carting")})},
        "annualised_hours_x52": {"m2_true_positive_hours_above_2x_plan": m2_tot["excess_hours_above_2x_plan_on_true_positives"] * WEEKS_PER_YEAR,
                                 "hub_top10_excess_hours": hubs_out["top10_excess_hours"] * WEEKS_PER_YEAR,
                                 "m1_hours_better_than_plan": gap["All"]["hours_better_than_plan"] * WEEKS_PER_YEAR},
    }


def duplicates(d: dict) -> dict:
    x = d["dup"].copy()
    x["vendor_code"] = x["vendor_name"].map(vc.table().set_index("vendor_name")["vendor_code"])
    assert x["vendor_code"].notna().all()
    x["month"] = pd.to_datetime(x["lr_date"]).dt.to_period("M").astype(str)
    g = x.groupby(["vendor_code", "month"]).agg(rows=("delivery_id", "size"), freight_rs=("r2_freight", "sum"), billed_rs=("gta_invoice_value", "sum")).reset_index()
    g.round(2).to_csv(os.path.join(ROI_DIR, "duplicate_rows_by_vendor_month.csv"), index=False)
    bv = x.groupby("vendor_code").agg(rows=("delivery_id", "size"), freight_rs=("r2_freight", "sum")).sort_values("freight_rs", ascending=False)
    bm = x.groupby("month").agg(rows=("delivery_id", "size"), freight_rs=("r2_freight", "sum"))
    return {
        "rows": int(len(x)), "freight_rs": float(x["r2_freight"].sum()), "billed_rs": float(x["gta_invoice_value"].sum()),
        "shipments_affected": int(x["shipment_no"].nunique()), "vendors_affected": int(x["vendor_code"].nunique()),
        "by_vendor_top10": bv.head(10).round(2).reset_index().to_dict("records"), "top3_vendor_share_of_freight": float(bv["freight_rs"].head(3).sum() / bv["freight_rs"].sum()),
        "by_month": bm.round(2).reset_index().to_dict("records"),
        "full_table": "reports/roi/duplicate_rows_by_vendor_month.csv",
        "note": "Rows that are exact repeats of another LR across all 69 columns; they were removed before modelling. Whether any was paid twice needs checking against payment records.",
    }


def main() -> dict:
    os.makedirs(ROI_DIR, exist_ok=True)
    print("loading ...")
    d = load()
    ctx = context(d)
    af = ctx["annual_factor"]
    print(f"annual factor {af:.3f} (x4 alternative 4.0)")

    print("S3 ...")
    o3, claim3, _ = s3(d)
    print("S1 ...")
    u = s1_frame(d)
    o1, claim1, _ = s1(d, u, claim3)
    print("S2 (policy sample) ...")
    o2, net_tp = s2(d, claim3 | claim1)
    claim2 = set(net_tp["shipment_no"].dropna())
    print("S4 ...")
    o4 = s4(d)
    dup = duplicates(d)

    # ownership and gross/net checks
    assert not (claim3 & claim1) and not (claim3 & claim2) and not (claim1 & claim2), "a shipment is owned by two strategies"
    gross = {"S3": o3["saving_rs_gross"], "S1": o1["s1_rs_gross"], "S2": o2["loss_on_true_positives_rs_gross"]}
    net = {"S3": o3["saving_rs_net"], "S1": o1["s1_rs_net"], "S2": o2["loss_on_true_positives_rs_net"]}
    for k in gross:
        assert net[k] <= gross[k] + 1e-6, f"{k}: net exceeds gross"
    assert sum(net.values()) <= sum(gross.values()) + 1e-6
    ownership = {"order": ["S3", "S1", "S2"], "shipments_claimed": {"S3": len(claim3), "S1": len(claim1), "S2": len(claim2)}, "overlap_shipments": 0,
                 "gross_rs": gross, "net_rs": net, "gross_sum_rs": sum(gross.values()), "net_sum_rs": sum(net.values()),
                 "s2_note": "S2 gross is the loss on true-positive LRs (the exposure the alert watches), not a saving; the team applies a capture rate."}

    for k in ("S1", "S3", "S2"):
        ownership.setdefault("annualised_net_rs", {})[k] = net[k] * af
        ownership.setdefault("annualised_net_rs_x4", {})[k] = net[k] * 4
    res = {
        "meta": {"generated": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"), "vendor_test_period": "2026-01-01 to 2026-03-31", "middle_mile_test_period": "2018-09-27 to 2018-10-03",
                 "vendors": "codes only", "capture_rates_and_costs": "not chosen here; the team sets them in the ROI model",
                 "s1_headline_scenario": f"{int(MID * 100)}% of the costlier vendors' volume moved (20% and 50% also given)"},
        "context": ctx, "ownership": ownership, "S1": o1, "S2": o2, "S3": o3, "S4": o4, "duplicate_payment_audit": dup,
    }
    text = json.dumps(res, indent=2, default=float)
    names = vc.table()["vendor_name"].tolist()
    blob = text + "".join(open(os.path.join(ROI_DIR, f), encoding="utf8").read() for f in os.listdir(ROI_DIR))
    leaked = [n for n in names if n.casefold() in blob.casefold()]
    assert not leaked, f"vendor names found in outputs: {leaked[:3]}"
    with open(OUT_JSON, "w", encoding="utf8") as f:
        f.write(text)
    return res


def summary(res: dict) -> str:
    c, o, a = res["context"], res["ownership"], res["context"]["annual_factor"]
    s1, s2, s3, s4, dup = res["S1"], res["S2"], res["S3"], res["S4"], res["duplicate_payment_audit"]
    rows = [
        ("S1 renegotiate + reallocate", f"{cr(o['gross_rs']['S1'])} / {cr(o['net_rs']['S1'])}", cr(o["annualised_net_rs"]["S1"]),
         f"{s1['target_cells']} target cells, {s1['target_vendors']} vendors, {s1['target_lanes_route_x_vehicle_type']} lanes; top-10 cells hold {s1['top10_cells_share_of_gap']:.0%} of the gap"),
        ("S2 margin check at dispatch (loss exposure)", f"{cr(o['gross_rs']['S2'])} / {cr(o['net_rs']['S2'])}", cr(o["annualised_net_rs"]["S2"]),
         f"{s2['alerts']:,} alerts ({s2['alerts_per_working_day']:.0f}/working day), {s2['true_positives']:,} TP, {s2['false_positives']:,} FP, {s2['missed_loss_makers']:,} missed"),
        ("S3 right-size + consolidate", f"{cr(o['gross_rs']['S3'])} / {cr(o['net_rs']['S3'])}", cr(o["annualised_net_rs"]["S3"]),
         f"{s3['flagged_shipments']:,} flagged, {s3['shipments_covered']:,} covered, {s3['distinct_routes_affected']} routes, {s3['distinct_plants_affected']} plants"),
    ]
    mm = s4["m2_alerts"]["total"]
    h = s4["hub_fixes"]
    g = s4["m1_planning_gap"]["by_route_type"]["All"]
    out = ["", "ROI INPUTS: summary (test period; vendor codes only)", "=" * 110,
           f"Annual factor {a:.3f} (valid freight Apr25-Mar26 / Jan-Mar26); x4 alternative = 4.0. Annualised = net x factor.",
           f"{'Strategy':<46}{'Test ₹ gross / net':<26}{'Annualised net':<16}Key counts", "-" * 110]
    for r in rows:
        out.append(f"{r[0]:<46}{r[1]:<26}{r[2]:<16}{r[3]}")
    out += ["-" * 110, f"{'Total (S2 is exposure, not a saving)':<46}{cr(o['gross_sum_rs']) + ' / ' + cr(o['net_sum_rs']):<26}{cr(sum(o['annualised_net_rs'].values()))}",
            "", "S1 net at 20 / 35 / 50% volume moved: " + " / ".join(cr(s1["s1_rs_net_by_share_moved_pct"][k]) for k in ("20", "35", "50")) + f"   (gross {' / '.join(cr(s1['s1_rs_gross_by_share_moved_pct'][k]) for k in ('20', '35', '50'))})",
            "S1 lever A gap at 3 / 5 / 10%: " + " / ".join(cr(s1["lever_a_sensitivity"][k]["gap_rs"]) for k in ("3pct", "5pct", "10pct")),
            f"S2 action mix (n={s2['action_mix']['sample_size']:,}): " + ", ".join(f"{k} {v:.0%}" for k, v in s2["action_mix"]["shares"].items()),
            "", f"S4 middle mile ({MM_LABEL}; hours only, test week; x52 for a year)",
            f"  M2 alerts {mm['alerts']:,}, true positives {mm['true_positives']:,}; excess hours above 2.0x plan on TPs {mm['excess_hours_above_2x_plan_on_true_positives']:,.0f} h "
            f"(FTL {s4['m2_alerts']['by_route_type']['FTL']['excess_hours_above_2x_plan_on_true_positives']:,.0f}, Carting {s4['m2_alerts']['by_route_type']['Carting']['excess_hours_above_2x_plan_on_true_positives']:,.0f})",
            f"  Hub fixes: top-10 hubs {h['top10_excess_hours']:,.0f} h of {h['total_excess_hours_hubs_30plus_legs']:,.0f} h network-wide "
            f"(FTL {h['top10_excess_hours_by_route_type']['FTL']:,.0f}, Carting {h['top10_excess_hours_by_route_type']['Carting']:,.0f})",
            f"  M1 planning gap: plan {g['abs_gap_hours_plan_as_is']:,.0f} h vs M1 {g['abs_gap_hours_m1']:,.0f} h of absolute error ({g['hours_better_than_plan']:,.0f} h better; vs ratio-scaled plan {g['hours_better_than_scaled_plan']:,.0f} h better)",
            "", f"Duplicate-payment audit: {dup['rows']:,} rows, {cr(dup['freight_rs'])} freight, {dup['vendors_affected']} vendors, {dup['shipments_affected']} shipments",
            f"Context: year freight {cr(c['year']['valid_freight_rs'])} (margin {c['year']['margin_pct']:.1f}%), test quarter {cr(c['test_quarter']['valid_freight_rs'])} (margin {c['test_quarter']['margin_pct']:.1f}%); "
            f"{c['counts']['vendors']} vendors, {c['counts']['routes']} routes, {c['counts']['lanes_route_x_vehicle_type']} lanes, {c['counts']['plants']} plants, "
            f"{c['counts']['valid_cost_lrs']:,} valid LRs, {c['counts']['shipments']:,} shipments",
            f"Shipments owned: S3 {o['shipments_claimed']['S3']:,}, S1 {o['shipments_claimed']['S1']:,}, S2 {o['shipments_claimed']['S2']:,}; overlap 0.", "", "Written: reports/roi_inputs.json, reports/roi/*.csv"]
    return "\n".join(out)


if __name__ == "__main__":
    print(summary(main()))
