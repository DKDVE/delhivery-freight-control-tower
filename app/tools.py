"""Freight Control Tower tools: pure Python functions over the saved Phase 5 models and processed tables.

Every number the app shows must come from one of these functions. No eval, no generated code, no SQL.
"""
from __future__ import annotations

import difflib
import glob
import json
import math
import warnings as _warnings
import os
import re
import sys
from functools import lru_cache

import joblib
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.modeling import inner_pipeline  # noqa: E402  (needed so the saved pipelines unpickle)

PROC = os.path.join(ROOT, "data", "processed")
MODELS = os.path.join(ROOT, "models")
PROMPTS = os.path.join(ROOT, "prompts")
_warnings.filterwarnings("ignore", message="LightGBM binary classifier")
MIN_LRS = 10  # minimum evidence before a vendor / lane is ranked


class ToolError(ValueError):
    """Bad tool input; reported back to the agent as {'error': ...}."""


@lru_cache(maxsize=None)
def _model(name: str) -> dict:
    """Load a saved task model. `name` may be the task id ('V1') or a legacy 'V1_RandomForest'; the task's winner file is found by prefix."""
    hits = sorted(glob.glob(os.path.join(MODELS, f"{name.split('_')[0]}_*.joblib")))
    if len(hits) != 1:
        raise FileNotFoundError(f"expected exactly one saved model for task {name.split('_')[0]} in models/, found {len(hits)}")
    return joblib.load(hits[0])


@lru_cache(maxsize=None)
def _explainer(name: str):
    import shap
    return shap.TreeExplainer(inner_pipeline(_model(name)["pipeline"]).named_steps["model"])


@lru_cache(maxsize=None)
def _t(name: str) -> pd.DataFrame:
    path = os.path.join(PROC, name)
    return pd.read_parquet(path) if name.endswith(".parquet") else pd.read_csv(path)


def _mode(s: pd.Series):
    s = s.dropna()
    return s.value_counts().index[0] if len(s) else None


@lru_cache(maxsize=1)
def _defaults() -> dict:
    ship = _t("vendor_shipments.parquet")
    cols = ["origin_plant", "destination_point", "state_destination", "zone", "shipment_type", "inco_terms", "truck_load"]
    route = ship.groupby("route").agg({**{c: _mode for c in cols}, "distance": "median"})
    vcap = ship[ship["vehicle_capacity"] > 0].groupby("vehicle_type")["vehicle_capacity"].agg(_mode)
    lr = _t("vendor_lr_clean.parquet")
    return {"route": route, "vcap": vcap, "billed_uom": _mode(lr["billed_uom"]), "billing_type": _mode(lr["billing_type"]),
            "weekday": int(_mode(ship["weekday"])), "vehicle_types": sorted(vcap.index.astype(str))}


@lru_cache(maxsize=1)
def _lr_valid() -> pd.DataFrame:
    lr = _t("vendor_lr_clean.parquet")
    v = lr[(lr["r2_freight"] > 0) & (lr["gta_invoice_value"] > 0)].copy()
    v["quarter"] = v["lr_date"].dt.to_period("Q").astype(str)
    v["month"] = v["lr_date"].dt.month
    v["loss_rs"] = (-v["margin"]).clip(lower=0)
    v["is_loss"] = v["margin"] < 0
    return v


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return None if (isinstance(o, float) and math.isnan(o)) or (isinstance(o, np.floating) and np.isnan(o)) else float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, pd.Timestamp):
        return str(o.date())
    return o


def _num(v, name, lo=None, hi=None, required=True, default=None):
    if v is None or v == "":
        if required:
            raise ToolError(f"'{name}' is required")
        return default
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ToolError(f"'{name}' must be a number")
    if math.isnan(x) or (lo is not None and x < lo) or (hi is not None and x > hi):
        raise ToolError(f"'{name}' must be between {lo} and {hi}")
    return x


def _str(v, name, required=True):
    if v is None or str(v).strip() == "":
        if required:
            raise ToolError(f"'{name}' is required")
        return None
    return str(v).strip()


def _top_shap(model_name: str, X: pd.DataFrame, k: int = 3, up_word: str = "raises") -> list[dict]:
    pipe = inner_pipeline(_model(model_name)["pipeline"])
    Z = pipe.named_steps["prep"].transform(X)
    sv = _explainer(model_name).shap_values(Z, check_additivity=False)
    if isinstance(sv, list):
        sv = sv[1]
    sv = np.asarray(sv)
    if sv.ndim == 3:
        sv = sv[:, :, 1]
    row = sv[0]
    out = []
    for j in np.argsort(-np.abs(row))[:k]:
        f = Z.columns[j]
        out.append({"feature": f, "value": X.iloc[0][f], "shap": float(row[j]), "effect": up_word if row[j] > 0 else up_word.replace("raises", "lowers")})
    return out


def _booking_features(b: dict) -> tuple[pd.DataFrame, dict, list[str]]:
    route, vt, vendor = _str(b.get("route"), "route"), _str(b.get("vehicle_type"), "vehicle_type"), _str(b.get("vendor"), "vendor")
    qty = _num(b.get("qty"), "qty", lo=0.01, hi=100)
    month = int(_num(b.get("month"), "month", lo=1, hi=12))
    d = _defaults()
    warnings, assumed = [], {}
    rd = d["route"].loc[route].to_dict() if route in d["route"].index else None
    if rd is None:
        warnings.append(f"route '{route}' was not seen in training; lane attributes are unknown and the scores are less reliable")
        rd = {c: None for c in ["origin_plant", "destination_point", "state_destination", "zone", "shipment_type", "inco_terms", "truck_load"]}
        rd["distance"] = np.nan
    if vt not in d["vcap"].index:
        warnings.append(f"vehicle_type '{vt}' was not seen in training")
    cap = _num(b.get("vehicle_capacity"), "vehicle_capacity", lo=0.1, hi=100, required=False)
    if cap is None:
        cap = float(d["vcap"].get(vt, np.nan))
        assumed["vehicle_capacity"] = None if np.isnan(cap) else cap
    dist = _num(b.get("distance"), "distance", lo=1, hi=5000, required=False)
    if dist is None:
        dist = float(rd["distance"]) if rd["distance"] == rd["distance"] else np.nan
        assumed["distance"] = None if np.isnan(dist) else dist
    weekday = int(_num(b.get("weekday"), "weekday", lo=0, hi=6, required=False, default=d["weekday"]))
    if b.get("weekday") in (None, ""):
        assumed["weekday"] = weekday
    util = qty / cap * 100 if cap and cap == cap else np.nan
    if util == util and util > 150:
        warnings.append(f"quantity {qty:g} MT is {util:.0f}% of the vehicle capacity ({cap:g} MT); check the inputs")
    row = {**rd, "route": route, "vehicle_type": vt, "vendor": vendor, "vendor_name": vendor, "vehicle_capacity": cap, "distance": dist,
           "qty": qty, "lr_qty": qty, "total_qty": qty, "month": month, "weekday": weekday, "n_lrs": 1, "n_routes": 1,
           "shipment_utilisation": util, "billed_uom": d["billed_uom"], "billing_type": d["billing_type"]}
    for k, v in rd.items():
        if k != "distance" and v is not None:
            assumed.setdefault(k, v)
    return pd.DataFrame([row]), assumed, warnings


def _score_cls(name: str, X: pd.DataFrame) -> tuple[float, bool, float]:
    m = _model(name)
    p = float(m["pipeline"].predict_proba(X[m["cats"] + m["nums"]])[:, 1][0])
    return p, p >= m["threshold"], float(m["threshold"])


def _interval(m: dict, X: pd.DataFrame, point: float) -> tuple[float, float]:
    """80% interval = the saved quantile models widened by the split-conformal correction (log scale), always containing the point forecast."""
    lo, hi = float(m["q_low"].predict(X)[0]), float(m["q_high"].predict(X)[0])
    lo, hi = max(min(lo, hi), 0.0), max(lo, hi, 0.0)
    q = float(m.get("conformal_Q", 0.0))
    by_band = m.get("conformal_Q_by_band")
    if by_band and "total_qty" in X.columns:  # V1: the correction is calibrated per load band
        qty, edges = float(X["total_qty"].iloc[0]), m["qty_band_edges"]
        for i, lab in enumerate(m["qty_band_labels"]):
            if edges[i] <= qty < edges[i + 1]:
                q = float(by_band.get(lab, q))
    lo, hi = max(np.expm1(np.log1p(lo) - q), 0.0), np.expm1(np.log1p(hi) + q)
    lo, hi = min(lo, hi), max(lo, hi)
    return float(min(lo, point)), float(max(hi, point))


def _score_v1(X: pd.DataFrame) -> dict:
    m = _model("V1_RandomForest")
    cols = m["cats"] + m["nums"]
    fair = float(m["pipeline"].predict(X[cols])[0])
    lo, hi = _interval(m, X[cols], fair)
    return {"fair_rs": fair, "fair_low_rs": lo, "fair_high_rs": hi}


def check_booking(booking: dict) -> dict:
    """V2 loss risk, V1 fair price vs the quote, V3 under-load risk for one planned booking."""
    X, assumed, warnings = _booking_features(booking)
    p2, f2, t2 = _score_cls("V2_LightGBM", X)
    drivers = _top_shap("V2_LightGBM", X[_model("V2_LightGBM")["cats"] + _model("V2_LightGBM")["nums"]], 3, "raises loss risk")
    v1 = _score_v1(X)
    quoted = _num(booking.get("quoted_rs"), "quoted_rs", lo=1, required=False)
    if quoted is not None:
        v1.update({"quoted_rs": quoted, "overpay_pct": (quoted - v1["fair_rs"]) / v1["fair_rs"] * 100, "excess_rs": max(quoted - v1["fair_rs"], 0.0)})
    p3, f3, t3 = _score_cls("V3_LightGBM", X)
    row = X.iloc[0]
    return _clean({
        "inputs": {k: row[k] for k in ["route", "vehicle_type", "vendor", "qty", "vehicle_capacity", "distance", "month"]},
        "assumed_defaults": assumed, "warnings": warnings,
        "v2_loss_risk": {"probability": p2, "flag": f2, "threshold": t2, "top_drivers": drivers},
        "v1_fair_price": v1,
        "v3_underload_risk": {"probability": p3, "flag": f3, "threshold": t3, "expected_utilisation_pct": row["shipment_utilisation"]},
    })


def find_alternative_vendors(route: str, vehicle_type: str, k: int = 3, exclude_vendor: str | None = None) -> dict:
    """Cheapest vendors (Rs per MT) on the same route and vehicle with at least 10 LRs."""
    route, vt = _str(route, "route"), _str(vehicle_type, "vehicle_type")
    k = int(_num(k, "k", lo=1, hi=10, required=False, default=3))
    lane = _t("lane_vendor_rates.csv")
    lane = lane[(lane["route"] == route) & (lane["vehicle_type"] == vt) & (lane["lr_count"] >= MIN_LRS)]
    if exclude_vendor:
        lane = lane[lane["vendor_name"].str.casefold() != str(exclude_vendor).casefold()]
    if lane.empty:
        return {"status": "insufficient data", "message": f"no vendor has {MIN_LRS}+ LRs on {route} with {vt}", "alternatives": []}
    lane = lane.sort_values("rs_per_mt")
    return _clean({"status": "ok", "route": route, "vehicle_type": vt, "min_lrs": MIN_LRS, "vendors_on_lane": int(len(lane)),
                   "lane_median_rs_per_mt": float(lane["rs_per_mt"].median()),
                   "alternatives": lane.head(k)[["vendor_name", "rs_per_mt", "margin_pct", "lr_count"]].to_dict("records")})


def lane_rate(route: str, vehicle_type: str, vendor: str) -> dict | None:
    """The vendor's own Rs per MT on this lane (>= 10 LRs), or None."""
    lane = _t("lane_vendor_rates.csv")
    r = lane[(lane["route"] == route) & (lane["vehicle_type"] == vehicle_type) & (lane["vendor_name"].str.casefold() == str(vendor).casefold())
             & (lane["lr_count"] >= MIN_LRS)]
    return None if r.empty else _clean(r.iloc[0][["rs_per_mt", "lr_count"]].to_dict())


def smallest_capacity_for(route: str, qty: float) -> dict | None:
    """Smallest vehicle capacity (MT) seen in training shipments on this route that still fits the load, with its vehicle type."""
    s = _t("vendor_shipments.parquet")
    s = s[(s["route"] == route) & s["train"] & (s["vehicle_capacity"] >= qty)]
    if s.empty:
        return None
    cap = s["vehicle_capacity"].min()
    return _clean({"capacity": float(cap), "vehicle_type": _mode(s.loc[s["vehicle_capacity"] == cap, "vehicle_type"])})


def _match_vendor(vendor: str) -> str | None:
    names = _lr_valid()["vendor_name"].unique()
    low = {n.casefold(): n for n in names}
    return low.get(vendor.casefold())


def vendor_profile(vendor: str) -> dict:
    """Spend, margin %, loss-making share, overpay % and worst lanes for one vendor (valid-cost LRs only)."""
    vendor = _str(vendor, "vendor")
    name = _match_vendor(vendor)
    if name is None:
        close = difflib.get_close_matches(vendor, list(_lr_valid()["vendor_name"].unique()), n=3, cutoff=0.6)
        return {"status": "insufficient data", "message": f"vendor '{vendor}' not found", "did_you_mean": close}
    v = _lr_valid()
    v = v[v["vendor_name"] == name]
    summ = _t("vendor_overpay_summary.csv")
    s = summ[summ["vendor"] == name]
    lanes = (v.groupby(["route", "vehicle_type"], dropna=False).agg(lrs=("margin", "size"), loss_rs=("loss_rs", "sum"), loss_share=("is_loss", "mean"))
             .reset_index())
    lanes = lanes[lanes["lrs"] >= MIN_LRS].sort_values("loss_rs", ascending=False).head(3)
    out = {"status": "ok", "vendor": name, "lrs": int(len(v)), "rankable": bool(len(v) >= MIN_LRS), "min_lrs": MIN_LRS,
           "spend_rs": float(v["r2_freight"].sum()), "billed_rs": float(v["gta_invoice_value"].sum()),
           "margin_pct": float(v["margin"].sum() / v["gta_invoice_value"].sum() * 100),
           "loss_share_pct": float(v["is_loss"].mean() * 100), "loss_rs": float(v["loss_rs"].sum()),
           "period": f"{v['lr_date'].min().date()} to {v['lr_date'].max().date()}",
           "worst_lanes": lanes.to_dict("records")}
    if len(s):
        s = s.iloc[0]
        out.update({"overpay_pct": float((s["actual_rs"] - s["fair_rs"]) / s["fair_rs"] * 100), "excess_rs_test_quarter": float(s["excess_rs"]),
                    "overpay_shipments": int(s["shipments"])})
    else:
        out["overpay_pct"] = None
        out["overpay_note"] = "vendor has no shipments in the V1 test quarter"
    return _clean(out)


_TRIP_COLS_NUM = ["osrm_time_total", "osrm_distance_total", "n_legs", "creation_hour", "creation_weekday"]


def check_trip(trip: dict) -> dict:
    """M1 predicted duration vs the plan and M2 severe-delay risk for one trip (by trip_uuid, or by entering the fields)."""
    tid = _str(trip.get("trip_id") or trip.get("trip_uuid"), "trip_id", required=False)
    warnings = []
    if tid:
        tt = _t("middle_mile_trips.parquet")
        r = tt[tt["trip_uuid"] == tid]
        if r.empty:
            return {"status": "insufficient data", "message": f"trip '{tid}' not found"}
        row = r.iloc[0].to_dict()
        row["_recorded_actual_min"] = row["actual_min"]
    else:
        row = {"osrm_time_total": _num(trip.get("osrm_time_total"), "osrm_time_total", lo=1, hi=5000),
               "osrm_distance_total": _num(trip.get("osrm_distance_total"), "osrm_distance_total", lo=1, hi=5000),
               "route_type": _str(trip.get("route_type"), "route_type"), "n_legs": int(_num(trip.get("n_legs"), "n_legs", lo=1, hi=20, required=False, default=1)),
               "first_source_center": _str(trip.get("first_source_center"), "first_source_center", required=False) or "MISSING",
               "last_destination_center": _str(trip.get("last_destination_center"), "last_destination_center", required=False) or "MISSING",
               "corridor": _str(trip.get("corridor"), "corridor", required=False) or "MISSING",
               "route_schedule_uuid": _str(trip.get("route_schedule_uuid"), "route_schedule_uuid", required=False) or "MISSING",
               "creation_hour": int(_num(trip.get("creation_hour"), "creation_hour", lo=0, hi=23, required=False, default=12)),
               "creation_weekday": int(_num(trip.get("creation_weekday"), "creation_weekday", lo=0, hi=6, required=False, default=2))}
        if row["route_type"] not in ("FTL", "Carting"):
            raise ToolError("'route_type' must be FTL or Carting")
        warnings.append("manual trip: centres, corridor and schedule not given or unseen are scored as unknown")
    X = pd.DataFrame([row])
    m1, m2 = _model("M1_LightGBM"), _model("M2_RandomForest")
    c1 = m1["cats"] + m1["nums"]
    pred = float(m1["pipeline"].predict(X[c1])[0])
    lo, hi = _interval(m1, X[c1], pred)
    p, flag, thr = _score_cls("M2_RandomForest", X)
    drivers = _top_shap("M2_RandomForest", X[m2["cats"] + m2["nums"]], 3, "raises severe-delay risk")
    plan = float(row["osrm_time_total"])
    out = {"status": "ok", "trip_id": tid, "route_type": row["route_type"], "corridor": row["corridor"], "first_source_center": row.get("first_source_center"),
           "planned_min": plan, "predicted_min": pred, "predicted_vs_plan_ratio": pred / plan,
           "predicted_low_min": min(lo, pred), "predicted_high_min": max(hi, pred),
           "severe_delay": {"probability": p, "flag": flag, "threshold": thr, "top_drivers": drivers}, "warnings": warnings}
    if "_recorded_actual_min" in row:
        out["recorded_actual_min"] = float(row["_recorded_actual_min"])
        out["in_test_period"] = bool(row.get("test", False))
    return _clean(out)


@lru_cache(maxsize=32)
def hub_report(center: str | None = None, top: int = 10) -> dict:
    """Handling-time ranking (hubs with 30+ test legs) with excess hours and Rs; or one centre by code / name."""
    top = int(_num(top, "top", lo=1, hi=25, required=False, default=10))
    hub = _t("hub_handling_ranking.csv")
    if not center:
        return _clean({"status": "ok", "network_median_handling_min": float(_t("middle_mile_legs.parquet").query("test")["handling_min"].median()),
                       "hubs_ranked": int(len(hub)), "min_legs": 30, "rate_label": "sourced synthetic base rate",
                       "hubs": hub.head(top)[["source_center", "source_name", "legs", "mean_actual_handling_min", "mean_predicted_handling_min",
                                              "excess_hours", "excess_rs_base"]].to_dict("records")})
    c = str(center).strip().casefold()
    hit = hub[(hub["source_center"].str.casefold() == c) | hub["source_name"].str.casefold().str.contains(re.escape(c))]
    if len(hit):
        return _clean({"status": "ok", "network_median_handling_min": float(_t("middle_mile_legs.parquet").query("test")["handling_min"].median()), "hubs": hit.head(top)[["source_center", "source_name", "legs", "mean_actual_handling_min",
                       "mean_predicted_handling_min", "excess_hours", "excess_rs_base"]].to_dict("records"), "rate_label": "sourced synthetic base rate"})
    legs = _t("middle_mile_legs.parquet")
    g = legs[(legs["source_center"].str.casefold() == c) | legs["source_name"].str.casefold().str.contains(re.escape(c))]
    if g.empty:
        return {"status": "insufficient data", "message": f"centre '{center}' not found"}
    return _clean({"status": "not ranked", "message": "fewer than 30 test legs, so the centre is not in the ranking; all-period figures shown",
                   "source_center": g["source_center"].iloc[0], "source_name": g["source_name"].iloc[0], "legs": int(len(g)),
                   "mean_actual_handling_min": float(g["handling_min"].mean())})


def _top(df, n):
    return df.head(n).to_dict("records")


def _q_vendors_by_loss(zone=None, quarter=None, top=10, min_lrs=MIN_LRS):
    v = _lr_valid()
    if zone:
        v = v[v["zone"] == zone]
    if quarter:
        v = v[v["quarter"] == quarter]
    g = v.groupby("vendor_name").agg(lrs=("margin", "size"), loss_share_pct=("is_loss", lambda s: s.mean() * 100), loss_rs=("loss_rs", "sum"),
                                     margin_rs=("margin", "sum"))
    return _top(g[g["lrs"] >= min_lrs].sort_values("loss_rs", ascending=False).reset_index(), top)


def _q_top_overpaying_lanes(top=10):
    return _top(_t("vendor_overpay.csv").sort_values("excess_rs", ascending=False), top)


def _q_vendor_overpay_ranking(top=10):
    s = _t("vendor_overpay_summary.csv").copy()
    s["overpay_pct"] = (s["actual_rs"] - s["fair_rs"]) / s["fair_rs"] * 100
    return _top(s[s["shipments"] >= MIN_LRS].sort_values("excess_rs", ascending=False), top)


def _q_monthly_margin_trend(zone=None):
    v = _lr_valid()
    if zone:
        v = v[v["zone"] == zone]
    g = v.groupby(v["lr_date"].dt.to_period("M").astype(str)).agg(lrs=("margin", "size"), billed_rs=("gta_invoice_value", "sum"), margin_rs=("margin", "sum"))
    g["margin_pct"] = g["margin_rs"] / g["billed_rs"] * 100
    return g.reset_index().rename(columns={"lr_date": "month"}).to_dict("records")


def _q_loss_share_by_vehicle_type(min_lrs=100):
    v = _lr_valid()
    g = v.groupby("vehicle_type").agg(lrs=("margin", "size"), loss_share_pct=("is_loss", lambda s: s.mean() * 100), margin_rs=("margin", "sum"))
    return g[g["lrs"] >= min_lrs].sort_values("loss_share_pct", ascending=False).reset_index().to_dict("records")


def _q_underloaded_lanes(top=10, min_shipments=20):
    s = _t("vendor_shipments.parquet")
    s = s[s["valid_capacity"] & ~s["util_outlier"]]
    g = s.groupby("route").agg(shipments=("utilisation", "size"), underloaded_share_pct=("utilisation", lambda u: (u < 60).mean() * 100),
                               median_utilisation_pct=("utilisation", "median"))
    return _top(g[g["shipments"] >= min_shipments].sort_values("underloaded_share_pct", ascending=False).reset_index(), top)


def _q_hubs_by_delay(top=10):
    return _top(_t("hub_handling_ranking.csv").sort_values("mean_actual_handling_min", ascending=False), top)


def _q_corridors_by_overrun(top=10, min_trips=20):
    t = _t("middle_mile_trips.parquet")
    g = t.groupby("corridor").agg(trips=("ratio", "size"), median_actual_over_planned=("ratio", "median"), severe_share_pct=("ratio", lambda r: (r >= 2.5).mean() * 100))
    return _top(g[g["trips"] >= min_trips].sort_values("median_actual_over_planned", ascending=False).reset_index(), top)


def _q_margin_by_zone(zone=None, quarter=None):
    v = _lr_valid()
    if zone:
        v = v[v["zone"] == zone]
    if quarter:
        v = v[v["quarter"] == quarter]
    g = v.groupby("zone").agg(lrs=("margin", "size"), billed_rs=("gta_invoice_value", "sum"), margin_rs=("margin", "sum"))
    g["margin_pct"] = g["margin_rs"] / g["billed_rs"] * 100
    return g.reset_index().sort_values("margin_pct").to_dict("records")


def _q_route_type_overrun(min_trips=20):
    t = _t("middle_mile_trips.parquet")
    g = t.groupby("route_type").agg(trips=("ratio", "size"), median_actual_over_planned=("ratio", "median"), severe_share_pct=("ratio", lambda r: (r >= 2.5).mean() * 100))
    return g[g["trips"] >= min_trips].reset_index().sort_values("median_actual_over_planned", ascending=False).to_dict("records")


def _q_underload_rate_by_plant(min_shipments=20):
    s = _t("vendor_shipments.parquet")
    s = s[s["valid_capacity"] & ~s["util_outlier"]]
    g = s.groupby("origin_plant").agg(shipments=("utilisation", "size"), underloaded_share_pct=("utilisation", lambda u: (u < 60).mean() * 100),
                                       median_utilisation_pct=("utilisation", "median"))
    return _top(g[g["shipments"] >= min_shipments].sort_values("underloaded_share_pct", ascending=False).reset_index(), 25)


def _q_vendor_price_gap_lanes(top=10, min_lrs=MIN_LRS):
    lane = _t("lane_vendor_rates.csv")
    lane = lane[lane["lr_count"] >= min_lrs]
    g = lane.groupby(["route", "vehicle_type"]).agg(vendors=("vendor_name", "nunique"), min_rs_per_mt=("rs_per_mt", "min"), max_rs_per_mt=("rs_per_mt", "max"))
    g = g[g["vendors"] >= 2]
    g["gap_rs_per_mt"] = g["max_rs_per_mt"] - g["min_rs_per_mt"]
    return _top(g.sort_values("gap_rs_per_mt", ascending=False).reset_index(), top)


class Rows(list):
    """A query's rows plus optional `meta` computed by code: {"total_rows": M (when the rows are a top-N cut), "facts": {slot: value}, "fact_desc": {slot: text}, "notes": [str]}."""

    def __init__(self, rows=(), **meta):
        super().__init__(rows)
        self.meta = meta


def _q_lane_vendor_ranking(origin=None, destination=None, vehicle_type=None, months=6, qty_mt=None):
    """Vendors on one lane A -> B, cheapest first. Reliable vendors only (10+ LRs, used in the last 90 days) unless none qualify.
    With qty_mt the ranking is for the smallest vehicle type that carries the load, exactly as lane_options recommends; if the load cannot be applied the
    code says so in meta["notes"] (never LLM prose)."""
    from app import lanes as LN
    try:
        if not origin or not destination:
            origin, destination = LN.busiest_lane()
        r = LN.lane_options(origin, destination, qty_mt=qty_mt, vehicle_type=vehicle_type, months=months)
    except LN.LaneNotFound as e:
        raise ToolError(f"{e}. Did you mean: {', '.join(e.suggestions) or 'no close match'}?")
    notes, facts, desc = [], {}, {}
    if not r["groups"]:
        if qty_mt is not None:
            notes.append(f"The {qty_mt:g} MT load could not be applied: " + (" ".join(r["notes"]) or "there is no direct lane in this period."))
        return Rows([], notes=notes)
    g = r["groups"][0]  # the busiest vehicle type x pricing basis on the lane (or the chosen vehicle type)
    rec = r.get("recommendation")
    if qty_mt is not None:
        if rec:
            g = next((x for x in r["groups"] if x["vehicle_type"] == rec["vehicle_type"] and x["basis"] == rec["basis"]), g)
            facts = {"load_mt": f"{qty_mt:g} MT", "load_vehicle": g["vehicle_type"], "load_capacity_mt": f"{g['capacity_mt']:g} MT" if g.get("capacity_mt") else "n/a"}
            desc = {"load_mt": "the load the user asked about; already reads like '15 MT', so add no unit", "load_vehicle": "the smallest vehicle type on this lane that carries that load (the ranking is for it)",
                    "load_capacity_mt": "that vehicle type's capacity; already includes MT"}
            notes.append(f"Ranked for the smallest vehicle that carries {qty_mt:g} MT: {g['vehicle_type']}"
                         + (f" ({g['capacity_mt']:g} MT)." if g.get("capacity_mt") else "."))
            notes += [n for n in r["notes"] if "smaller vehicle" in n or "more than one truck" in n]
        else:
            notes.append(f"The {qty_mt:g} MT load could not be applied, so this is the ranking for the busiest vehicle type ({g['vehicle_type']}) instead. "
                         + " ".join(n for n in r["notes"] if n))
    vs = [v for v in g["vendors"] if not v["thin"]] or g["vendors"]
    rows = []
    for v in vs:
        row = {"vendor_code": v["vendor_code"], "rs_per_mt": v["rs_per_mt"], "lrs": v["lrs"], "margin_pct": v["margin_pct"], "gap_to_fair_pct": v["gap_to_fair_pct"],
               "vehicle_type": g["vehicle_type"], "tag": v["tag"]}
        if qty_mt is not None and rec and v["rs_per_mt"] is not None:
            row["rate_x_load_rs"] = v["rs_per_mt"] * qty_mt
        rows.append(row)
    return Rows(rows, facts=facts, fact_desc=desc, notes=notes)


def _q_vendor_head_to_head(vendor_a=None, vendor_b=None, vehicle_type=None, top=10):
    """Two vendors compared on the lanes they share (10+ LRs each), last 12 months. The rows are the top N shared lane groups by volume; the TRUE totals
    (all shared lane groups, lanes each vendor wins) come back in meta so no summary has to infer them from the row count."""
    from app import lanes as LN
    if not vendor_a or not vendor_b:
        vendor_a, vendor_b = LN.best_pair()
    try:
        r = LN.vendor_compare([vendor_a, vendor_b], vehicle_type, 12)
    except LN.VendorNotFound as e:
        raise ToolError(f"{e}; vendor codes look like V001")
    a, b = r["codes"]
    rows = [{"lane": f"{x['origin']} to {x['destination']} ({x['vehicle_type']})", "vendor_a_rs_per_mt": x["rs_per_mt"][a], "vendor_b_rs_per_mt": x["rs_per_mt"][b],
             "gap_pct": x["gap_pct"], "cheaper_vendor": x["cheaper"]} for x in r["lanes"][:top]]
    won = {v["vendor_code"]: v["lanes_won"] for v in r["vendors"]}
    facts = {"shared_lanes": str(r["n_shared_lanes"]), "lanes_won_a": str(won[a]), "lanes_won_b": str(won[b]), "vendor_a": a, "vendor_b": b}
    desc = {"shared_lanes": "the TRUE total number of shared lane groups (lane x vehicle type) where both vendors have 10+ LRs; the table shows only the top few of them",
            "lanes_won_a": "how many of ALL the shared lane groups the first vendor is cheaper on", "lanes_won_b": "how many of ALL the shared lane groups the second vendor is cheaper on",
            "vendor_a": "the first vendor's code", "vendor_b": "the second vendor's code"}
    return Rows(rows, total_rows=r["n_shared_lanes"], facts=facts, fact_desc=desc, notes=list(r["notes"]))


def _q_top_loss_making_lanes(top=10, min_lrs=MIN_LRS):
    v = _lr_valid()
    g = v.groupby(["route", "vehicle_type"]).agg(lrs=("margin", "size"), loss_rs=("loss_rs", "sum"), loss_share_pct=("is_loss", lambda s: s.mean() * 100))
    return _top(g[g["lrs"] >= min_lrs].sort_values("loss_rs", ascending=False).reset_index(), top)


def _q_vendor_quadrant_counts(min_lrs=MIN_LRS):
    v = _lr_valid()
    g = v.groupby("vendor_name").agg(lrs=("margin", "size"), spend_rs=("gta_invoice_value", "sum"), margin_rs=("margin", "sum"))
    g = g[g["lrs"] >= min_lrs]
    g["margin_pct"] = g["margin_rs"] / g["spend_rs"].replace(0, np.nan) * 100
    spend_med, margin_med = g["spend_rs"].median(), g["margin_pct"].median()

    def quad(r):
        hs, hm = r["spend_rs"] >= spend_med, r["margin_pct"] >= margin_med
        if hs and hm:
            return "High spend, healthy margin"
        if hs and not hm:
            return "High spend, thin margin"
        if not hs and hm:
            return "Low spend, healthy margin"
        return "Low spend, thin margin"

    g["quadrant"] = g.apply(quad, axis=1)
    return g.groupby("quadrant").agg(vendors=("spend_rs", "size"), total_spend_rs=("spend_rs", "sum")).reset_index().to_dict("records")


def _q_data_quality_summary():
    """Counts of unusable rows in the raw file, precomputed by scripts/anonymise_vendor_data.py (the raw file is not in the repository)."""
    with open(os.path.join(ROOT, "data", "processed", "data_quality.json"), encoding="utf8") as f:
        items = json.load(f)
    n = items.pop("lr_rows")
    return [{"issue": k, "count": v, "share_pct": v / n * 100} for k, v in items.items()]


def _zones():
    return sorted(_lr_valid()["zone"].dropna().unique())


def _quarters():
    return sorted(_lr_valid()["quarter"].unique())


# id -> (function, {param: (type, default, lo, hi, allowed_fn)}, description)
QUERIES = {
    "vendors_by_loss": (_q_vendors_by_loss, {"zone": ("str", None, None, None, _zones), "quarter": ("str", None, None, None, _quarters),
                                              "top": ("int", 10, 1, 25, None), "min_lrs": ("int", MIN_LRS, MIN_LRS, 1000, None)},
                        "Vendors ranked by Rs lost on loss-making LRs, optionally within a zone and/or quarter (e.g. 2025Q4)."),
    "top_overpaying_lanes": (_q_top_overpaying_lanes, {"top": ("int", 10, 1, 25, None)}, "Vendor x route x vehicle lanes with the highest excess over the fair price in the test quarter."),
    "vendor_overpay_ranking": (_q_vendor_overpay_ranking, {"top": ("int", 10, 1, 25, None)}, "Vendors ranked by excess over the fair price in the test quarter."),
    "monthly_margin_trend": (_q_monthly_margin_trend, {"zone": ("str", None, None, None, _zones)}, "Margin % by month, optionally for one zone."),
    "loss_share_by_vehicle_type": (_q_loss_share_by_vehicle_type, {"min_lrs": ("int", 100, 10, 5000, None)}, "Share of loss-making LRs by vehicle type."),
    "underloaded_lanes": (_q_underloaded_lanes, {"top": ("int", 10, 1, 25, None), "min_shipments": ("int", 20, 10, 1000, None)}, "Routes with the highest share of trucks below 60% load."),
    "hubs_by_delay": (_q_hubs_by_delay, {"top": ("int", 10, 1, 25, None)}, "Hubs ranked by mean handling time (hubs with 30+ test legs)."),
    "corridors_by_overrun": (_q_corridors_by_overrun, {"top": ("int", 10, 1, 25, None), "min_trips": ("int", 20, 5, 1000, None)}, "State-to-state corridors ranked by median actual / planned trip time."),
    "data_quality_summary": (_q_data_quality_summary, {}, "Counts of data-quality issues in the vendor freight file (zero freight, zero capacity, missing fields)."),
    "margin_by_zone": (_q_margin_by_zone, {"zone": ("str", None, None, None, _zones), "quarter": ("str", None, None, None, _quarters)},
                       "Margin % and margin Rs by zone, optionally narrowed to one zone and/or quarter."),
    "route_type_overrun": (_q_route_type_overrun, {"min_trips": ("int", 20, 5, 1000, None)}, "FTL vs Carting compared by median actual/planned trip-time ratio and severe-delay share."),
    "underload_rate_by_plant": (_q_underload_rate_by_plant, {"min_shipments": ("int", 20, 10, 1000, None)}, "Origin plants ranked by share of shipments below 60% truck utilisation."),
    "vendor_price_gap_lanes": (_q_vendor_price_gap_lanes, {"top": ("int", 10, 1, 25, None), "min_lrs": ("int", MIN_LRS, MIN_LRS, 1000, None)},
                               "Lanes with the widest spread between the cheapest and priciest vendor (Rs per MT, 2+ vendors with 10+ LRs)."),
    "top_loss_making_lanes": (_q_top_loss_making_lanes, {"top": ("int", 10, 1, 25, None), "min_lrs": ("int", MIN_LRS, MIN_LRS, 1000, None)},
                              "Route x vehicle-type lanes ranked by total Rs lost on loss-making LRs."),
    "lane_vendor_ranking": (_q_lane_vendor_ranking, {"origin": ("str", None, None, None, None), "destination": ("str", None, None, None, None),
                                                      "vehicle_type": ("str", None, None, None, None), "months": ("int", 6, 1, 12, None), "qty_mt": ("float", None, 0.1, 100, None)},
                            "Vendors on one lane (origin to destination, e.g. Hazira to Khadoli) ranked by Rs per MT, cheapest first; the busiest vehicle type unless one is named, "
                            "or the smallest vehicle that carries qty_mt (the load in MT) if one is given."),
    "vendor_head_to_head": (_q_vendor_head_to_head, {"vendor_a": ("str", None, None, None, None), "vendor_b": ("str", None, None, None, None),
                                                      "vehicle_type": ("str", None, None, None, None), "top": ("int", 10, 1, 25, None)},
                            "Two vendors (by code, e.g. V001 and V005) compared on the lanes they share: Rs per MT each and which is cheaper."),
    "vendor_quadrant_counts": (_q_vendor_quadrant_counts, {"min_lrs": ("int", MIN_LRS, MIN_LRS, 1000, None)}, "Count and total spend of vendors in four spend/margin quadrants."),
}


def list_queries() -> list[dict]:
    return [{"query_id": k, "description": v[2], "params": {p: spec[0] for p, spec in v[1].items()}} for k, v in QUERIES.items()]


def query_data(query_id: str, params: dict | None = None) -> dict:
    """Whitelisted, parameterised, read-only queries. Anything else is refused."""
    params = params or {}
    if query_id not in QUERIES:
        return {"status": "unsupported", "message": "I can't answer that from the approved queries.", "available_queries": list_queries()}
    fn, spec, _ = QUERIES[query_id]
    extra = set(params) - set(spec)
    if extra:
        raise ToolError(f"unknown parameter(s) {sorted(extra)} for {query_id}; allowed: {sorted(spec)}")
    kw = {}
    for name, (typ, default, lo, hi, allowed) in spec.items():
        v = params.get(name, default)
        if v is None:
            kw[name] = None
            continue
        if typ == "int":
            kw[name] = int(_num(v, name, lo=lo, hi=hi))
        elif typ == "float":
            kw[name] = float(_num(v, name, lo=lo, hi=hi))
        else:
            v = str(v)
            if name == "zone":
                v = v.upper()
            if allowed and v not in allowed():
                raise ToolError(f"'{name}' must be one of {allowed()}")
            kw[name] = v
    rows = fn(**kw)
    meta = dict(getattr(rows, "meta", None) or {})
    out = {"status": "ok", "query_id": query_id, "params": kw, "rows": rows, "n_rows": len(rows)}
    if meta:
        out["meta"] = meta
    return _clean(out)


_TOKEN = re.compile(r"\{\{(\w+)\}\}")


def list_templates() -> list[dict]:
    out = []
    for fn in sorted(os.listdir(PROMPTS)) if os.path.isdir(PROMPTS) else []:
        if re.match(r"\d\d_.*\.md$", fn):
            t = _parse_template(fn[:-3])
            out.append({"template_id": t["id"], "title": t["title"], "fields": t["fields"]})
    return out


def _parse_template(template_id: str) -> dict:
    tid = str(template_id).strip()
    path = None
    for fn in sorted(os.listdir(PROMPTS)):
        stem = fn[:-3]
        if fn.endswith(".md") and re.match(r"\d\d_", fn) and tid in (stem, stem[3:], stem[:2]):
            path = os.path.join(PROMPTS, fn)
            break
    if path is None:
        raise ToolError(f"unknown template '{template_id}'")
    text = open(path, encoding="utf8").read()
    secs = {m.group(1).strip().lower(): m.group(2) for m in re.finditer(r"^## (.+?)\n(.*?)(?=^## |\Z)", text, re.S | re.M)}
    body = re.search(r"```(?:text)?\n(.*?)```", secs.get("prompt text", ""), re.S)
    if not body:
        raise ToolError(f"template '{template_id}' has no prompt text block")
    fields = [m.group(1) for m in re.finditer(r"^- `?(\w+)`?\s*:", secs.get("input fields", ""), re.M)]
    return {"id": os.path.basename(path)[:-3][3:], "title": text.splitlines()[0].lstrip("# ").strip(), "body": body.group(1).strip(), "fields": fields}


def _fmt(v):
    if isinstance(v, bool) or v is None:
        return str(v)
    if isinstance(v, (int, np.integer)):
        return f"{int(v):,}"
    if isinstance(v, (float, np.floating)):
        return f"{v:,.1f}"
    return str(v)


def draft_message(template_id: str, fields: dict, polisher=None) -> dict:
    """Fill a prompt-library template. If a polisher (LLM) is given, it only sees the text with {{placeholders}} intact;
    the numbers are inserted by code afterwards."""
    t = _parse_template(template_id)
    missing = [f for f in t["fields"] if f not in fields or fields[f] in (None, "")]
    if missing:
        raise ToolError(f"missing field(s) for {t['id']}: {missing}")
    body, polished, note = t["body"], False, None
    if polisher is not None:
        try:
            cand = polisher(body)
            if set(_TOKEN.findall(cand)) == set(_TOKEN.findall(body)):
                body, polished = cand, True
            else:
                note = "polish rejected: placeholders were changed"
        except Exception as e:  # noqa: BLE001
            note = f"polish failed: {type(e).__name__}"
    text = _TOKEN.sub(lambda m: _fmt(fields[m.group(1)]), body)
    return {"status": "ok", "template_id": t["id"], "title": t["title"], "text": text, "polished": polished, "note": note,
            "numbers_inserted_by_code": True}


def _fn(name, desc, props, required):
    return {"type": "function", "function": {"name": name, "description": desc,
                                              "parameters": {"type": "object", "properties": props, "required": required, "additionalProperties": False}}}


TOOL_SCHEMAS = [
    _fn("check_booking", "Score one planned vendor booking: loss risk (V2), fair price vs the quote (V1) and under-load risk (V3), with the top 3 drivers.",
        {"booking": {"type": "object", "description": "route, vehicle_type, vendor, qty (MT), month (1-12) are required; distance, weekday, vehicle_capacity, quoted_rs optional",
                     "properties": {"route": {"type": "string"}, "vehicle_type": {"type": "string"}, "vendor": {"type": "string"}, "qty": {"type": "number"},
                                    "month": {"type": "integer"}, "distance": {"type": "number"}, "weekday": {"type": "integer"},
                                    "vehicle_capacity": {"type": "number"}, "quoted_rs": {"type": "number"}},
                     "required": ["route", "vehicle_type", "vendor", "qty", "month"]}}, ["booking"]),
    _fn("find_alternative_vendors", "Cheapest vendors (Rs per MT) on the same route and vehicle type with at least 10 LRs.",
        {"route": {"type": "string"}, "vehicle_type": {"type": "string"}, "k": {"type": "integer", "default": 3}, "exclude_vendor": {"type": "string"}},
        ["route", "vehicle_type"]),
    _fn("vendor_profile", "Spend, margin %, loss-making share, overpay % and worst lanes for one vendor.", {"vendor": {"type": "string"}}, ["vendor"]),
    _fn("check_trip", "Predicted duration vs plan (M1) and severe-delay risk (M2) for one trip, by trip_id or by entering the trip fields.",
        {"trip": {"type": "object", "description": "trip_id, or osrm_time_total, osrm_distance_total, route_type (FTL/Carting) plus optional n_legs, first_source_center, last_destination_center, corridor, route_schedule_uuid, creation_hour, creation_weekday",
                  "properties": {"trip_id": {"type": "string"}, "osrm_time_total": {"type": "number"}, "osrm_distance_total": {"type": "number"}, "route_type": {"type": "string"},
                                 "n_legs": {"type": "integer"}, "first_source_center": {"type": "string"}, "last_destination_center": {"type": "string"},
                                 "corridor": {"type": "string"}, "route_schedule_uuid": {"type": "string"}, "creation_hour": {"type": "integer"}, "creation_weekday": {"type": "integer"}}}}, ["trip"]),
    _fn("hub_report", "Hub handling-time ranking with excess hours and Rs, or one centre by code or name.",
        {"center": {"type": "string"}, "top": {"type": "integer", "default": 10}}, []),
    _fn("query_data", "Run one whitelisted read-only data query. Anything not in the list is refused.",
        {"query_id": {"type": "string", "enum": list(QUERIES)},
         "params": {"type": "object", "description": "Optional filters; ONLY these names, per query: " + "; ".join(f"{q}: {', '.join(v[1])}" for q, v in QUERIES.items() if v[1])
                    + ". Put what the user named into them (for example origin and destination place names such as HAZIRA and KHADOLI, vehicle_type such as SB01, "
                    "vendor_a and vendor_b as vendor codes such as V013, qty_mt as a plain number of tonnes such as 15 when the user names a load), otherwise leave them out."}}, ["query_id"]),
    _fn("draft_message", "Fill a prompt-library template with the given fields (numbers are inserted by code).",
        {"template_id": {"type": "string"}, "fields": {"type": "object"}}, ["template_id", "fields"]),
]

TOOLS = {"check_booking": check_booking, "find_alternative_vendors": find_alternative_vendors, "vendor_profile": vendor_profile,
         "check_trip": check_trip, "hub_report": hub_report, "query_data": query_data, "draft_message": draft_message}


def call_tool(name: str, args: dict | None = None) -> dict:
    """Dispatch a tool call; bad input comes back as {'error': ...} instead of raising."""
    if name not in TOOLS:
        return {"error": f"unknown tool '{name}'", "available_tools": list(TOOLS)}
    try:
        return TOOLS[name](**(args or {}))
    except ToolError as e:
        return {"error": str(e)}
    except TypeError as e:
        return {"error": f"bad arguments: {e}"}
