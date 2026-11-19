"""Read-side services for the API. Business logic stays in app/; this module only reshapes tables and tool outputs for the screens."""
from __future__ import annotations

import csv
import io
import json
import os
import re
from functools import lru_cache

import numpy as np
import pandas as pd

from app import guardrails as G
from app import tools as T
from backend import vendor_codes as vc

ROOT = T.ROOT
FIG_DIR = os.path.join(ROOT, "reports", "figures")
MET_DIR = os.path.join(ROOT, "reports", "metrics")
MIN_LRS = T.MIN_LRS


class NotFound(LookupError):
    pass


class BadRequest(ValueError):
    pass


def clean(o):
    return T._clean(o)


def vendor_name(code: str) -> str:
    n = vc.name_for(code)
    if n is None:
        raise NotFound(f"unknown vendor code '{code}'")
    return n


@lru_cache(maxsize=1)
def _vendor_table() -> pd.DataFrame:
    v = T._lr_valid()
    g = v.groupby("vendor_name").agg(lrs=("margin", "size"), spend_rs=("r2_freight", "sum"), billed_rs=("gta_invoice_value", "sum"), margin_rs=("margin", "sum"),
                                     loss_share_pct=("is_loss", lambda s: s.mean() * 100), loss_rs=("loss_rs", "sum"))
    g["margin_pct"] = g["margin_rs"] / g["billed_rs"] * 100
    s = T._t("vendor_overpay_summary.csv").set_index("vendor")
    g["overpay_pct"] = (s["actual_rs"] - s["fair_rs"]) / s["fair_rs"] * 100
    g["excess_rs_test_quarter"] = s["excess_rs"]
    g = g.reset_index()
    g["vendor_code"] = g["vendor_name"].map(vc.code_for)
    return g.drop(columns=["vendor_name", "billed_rs", "margin_rs"]).sort_values("vendor_code").reset_index(drop=True)


VENDOR_SORTS = {"vendor_code", "lrs", "spend_rs", "margin_pct", "loss_share_pct", "loss_rs", "overpay_pct", "excess_rs_test_quarter"}


def list_vendors(q: str | None, min_lrs: int, sort: str, order: str, page: int, page_size: int, flagged: bool | None = None) -> dict:
    if sort not in VENDOR_SORTS:
        raise BadRequest(f"sort must be one of {sorted(VENDOR_SORTS)}")
    d = _vendor_table()
    d = d[d["lrs"] >= min_lrs]
    if q:
        d = d[d["vendor_code"].str.contains(q.strip().upper(), regex=False)]
    if flagged:  # loss-making on more than a quarter of LRs, or paying more than 10% above the fair price
        d = d[(d["loss_share_pct"] > 25) | (d["overpay_pct"].fillna(0) > 10)]
    d = d.sort_values(sort, ascending=(order == "asc"), na_position="last")
    total = len(d)
    rows = d.iloc[(page - 1) * page_size: page * page_size]
    return {"total": total, "page": page, "page_size": page_size, "items": clean(rows.to_dict("records"))}


def vendor_detail(code: str) -> dict:
    name = vendor_name(code)
    prof = T.vendor_profile(name)
    if prof.get("status") != "ok":
        raise NotFound(f"no valid-cost LRs for {code}")
    v = T._lr_valid()
    v = v[v["vendor_name"] == name]
    m = v.groupby(v["lr_date"].dt.to_period("M").astype(str)).agg(lrs=("margin", "size"), billed_rs=("gta_invoice_value", "sum"), margin_rs=("margin", "sum"),
                                                                loss_rs=("loss_rs", "sum"))
    m["margin_pct"] = m["margin_rs"] / m["billed_rs"] * 100
    lanes = (v.groupby(["route", "vehicle_type"], dropna=False).agg(lrs=("margin", "size"), loss_rs=("loss_rs", "sum"), loss_share_pct=("is_loss", lambda s: s.mean() * 100))
             .reset_index())
    lanes = lanes[lanes["lrs"] >= MIN_LRS].sort_values("loss_rs", ascending=False).head(5)
    prof.pop("vendor", None)
    prof["vendor_code"] = code.upper()
    prof["worst_lanes"] = lanes.to_dict("records")
    prof["monthly"] = m.reset_index().rename(columns={"lr_date": "month"}).to_dict("records")
    return clean(prof)


@lru_cache(maxsize=1)
def meta_options() -> dict:
    lane = T._t("lane_vendor_rates.csv")
    lanes = lane.groupby("route")["vehicle_type"].agg(lambda s: sorted(s.unique())).to_dict()
    defaults = T._defaults()
    caps = defaults["vcap"].to_dict()
    hubs = T._t("hub_handling_ranking.csv")
    return clean({"routes": [{"route": r, "vehicle_types": vts} for r, vts in sorted(lanes.items())],
                  "vehicle_types": [{"vehicle_type": k, "capacity_mt": float(v)} for k, v in sorted(caps.items())],
                  "vendors": [{"vendor_code": c, "rank": int(r)} for c, r in zip(vc.table()["vendor_code"], vc.table()["rank"])],
                  "months": [{"month": m, "label": pd.Timestamp(2000, m, 1).strftime("%b")} for m in range(1, 13)],
                  "route_types": ["FTL", "Carting"], "zones": T._zones(), "quarters": T._quarters(),
                  "hubs": hubs[["source_center", "source_name"]].to_dict("records"),
                  "defaults": {"qty": 3, "month": 3, "weekday": defaults["weekday"]}})


def data_as_of() -> dict:
    lr = T._lr_valid()
    trips = T._t("middle_mile_trips.parquet")
    return clean({"vendor_data": {"from": lr["lr_date"].min(), "to": lr["lr_date"].max(), "lrs": int(len(lr))},
                  "middle_mile_data": {"from": trips["trip_creation_time"].min().date(), "to": trips["trip_creation_time"].max().date(), "trips": int(len(trips))}})


def _pct_change(cur, prev):
    return None if prev in (None, 0) or cur is None else (cur - prev) / abs(prev) * 100


def _pp_change(cur, prev):
    """Change in percentage points, for tiles whose value is itself a percentage."""
    return None if prev is None or cur is None else cur - prev


@lru_cache(maxsize=1)
def overview() -> dict:
    v = T._lr_valid()
    v = v.assign(q=v["lr_date"].dt.to_period("Q").astype(str), m=v["lr_date"].dt.to_period("M").astype(str))

    def agg(d):
        return {"spend_rs": d["r2_freight"].sum(), "billed_rs": d["gta_invoice_value"].sum(), "margin_pct": d["margin"].sum() / d["gta_invoice_value"].sum() * 100,
                "loss_share_pct": d["is_loss"].mean() * 100, "loss_rs": d["loss_rs"].sum(), "lrs": len(d)}

    quarters = sorted(v["q"].unique())
    cur_q, prev_q = quarters[-1], (quarters[-2] if len(quarters) > 1 else None)
    cur, prev = agg(v[v["q"] == cur_q]), (agg(v[v["q"] == prev_q]) if prev_q else {})
    monthly = pd.DataFrame([{"month": m, **agg(d)} for m, d in v.groupby("m")])
    ov = T._t("vendor_overpay_summary.csv")
    excess = float(ov["excess_rs"].sum())
    trips = T._t("pred_M2.csv")

    def spark(col):
        return [float(x) for x in monthly[col]]

    kpis = [
        {"id": "spend", "label": "Freight spend", "unit": "rs", "value": cur["spend_rs"], "change_pct": _pct_change(cur["spend_rs"], prev.get("spend_rs")), "change_unit": "pct", "good_direction": "neutral",
         "sparkline": spark("spend_rs")},
        {"id": "margin", "label": "Margin on billed freight", "unit": "pct", "value": cur["margin_pct"], "change_pct": _pp_change(cur["margin_pct"], prev.get("margin_pct")), "change_unit": "pp",
         "good_direction": "up", "sparkline": spark("margin_pct")},
        {"id": "loss_share", "label": "Loss-making LRs", "unit": "pct", "value": cur["loss_share_pct"], "change_pct": _pp_change(cur["loss_share_pct"], prev.get("loss_share_pct")), "change_unit": "pp",
         "good_direction": "down", "sparkline": spark("loss_share_pct")},
        {"id": "loss_rs", "label": "Money lost on loss-making LRs", "unit": "rs", "value": cur["loss_rs"], "change_pct": _pct_change(cur["loss_rs"], prev.get("loss_rs")), "change_unit": "pct",
         "good_direction": "down", "sparkline": spark("loss_rs")},
        {"id": "excess", "label": "Paid above fair price (test quarter)", "unit": "rs", "value": excess, "change_pct": None, "change_unit": None, "good_direction": "down", "sparkline": [],
         "note": "V1 fair-price model, Jan to Mar 2026"}]
    g = v.groupby(["route", "vehicle_type"], dropna=False).agg(lrs=("margin", "size"), loss_rs=("loss_rs", "sum"), loss_share_pct=("is_loss", lambda s: s.mean() * 100),
                                                               margin_rs=("margin", "sum"), billed_rs=("gta_invoice_value", "sum")).reset_index()
    g["margin_pct"] = g["margin_rs"] / g["billed_rs"] * 100
    lanes = g[g["lrs"] >= MIN_LRS].sort_values("loss_rs", ascending=False).head(5)[["route", "vehicle_type", "lrs", "loss_rs", "loss_share_pct", "margin_pct"]]
    # alerts needing a decision: the most likely severe-delay trips and the biggest vendor overpayments (people decide; nothing is sent)
    alerts = []
    for r in trips[trips["predicted_label"] == 1].sort_values("probability", ascending=False).head(3).itertuples():
        alerts.append({"kind": "trip", "id": r.record_id, "title": "Trip likely to run 2.5x its plan", "probability": float(r.probability), "href": f"/trips/{r.record_id}"})
    for r in ov.sort_values("excess_rs", ascending=False).head(2).itertuples():
        code = vc.code_for(r.vendor)
        alerts.append({"kind": "vendor", "id": code, "title": "Vendor paid above the fair price", "excess_rs": float(r.excess_rs), "href": f"/vendors/{code}"})
    return clean({"as_of": data_as_of(), "quarter": cur_q, "previous_quarter": prev_q, "kpis": kpis,
                  "monthly_margin": monthly[["month", "lrs", "billed_rs", "margin_pct", "loss_rs"]].to_dict("records"),
                  "top_lanes_by_loss": lanes.to_dict("records"), "alerts": alerts})


@lru_cache(maxsize=1)
def _trip_table() -> pd.DataFrame:
    p = T._t("pred_M2.csv").rename(columns={"record_id": "trip_id", "probability": "severe_probability", "actual": "actual_severe", "predicted_label": "flag"})
    t = T._t("middle_mile_trips.parquet")[["trip_uuid", "actual_min", "osrm_time_total", "route_type", "corridor", "first_source_center", "last_destination_center", "n_legs",
                                           "trip_creation_time"]]
    d = p.merge(t, left_on="trip_id", right_on="trip_uuid", how="left").drop(columns="trip_uuid")
    d["trip_creation_time"] = d["trip_creation_time"].dt.strftime("%Y-%m-%dT%H:%M:%S")
    d["risk"] = np.where(d["flag"] == 1, "high", "low")
    return d.rename(columns={"osrm_time_total": "planned_min"})


TRIP_SORTS = {"severe_probability", "planned_min", "actual_min", "trip_creation_time"}


def list_trips(risk: str, q: str | None, corridor: str | None, sort: str, order: str, page: int, page_size: int) -> dict:
    if risk not in ("all", "high", "low"):
        raise BadRequest("risk must be all, high or low")
    if sort not in TRIP_SORTS:
        raise BadRequest(f"sort must be one of {sorted(TRIP_SORTS)}")
    full = _trip_table()
    d = full
    if risk != "all":
        d = d[d["risk"] == risk]
    if q:
        d = d[d["trip_id"].str.contains(q.strip(), regex=False)]
    if corridor:
        d = d[d["corridor"] == corridor]
    d = d.sort_values(sort, ascending=(order == "asc"))
    rows = d.iloc[(page - 1) * page_size: page * page_size]
    cols = ["trip_id", "risk", "severe_probability", "threshold", "planned_min", "actual_min", "route_type", "corridor", "first_source_center", "trip_creation_time"]
    return {"total": len(d), "page": page, "page_size": page_size, "items": clean(rows[cols].to_dict("records")), "high_risk_total": int((full["risk"] == "high").sum())}


def trip_detail(trip_id: str) -> dict:
    out = T.check_trip({"trip_id": trip_id})
    if out.get("status") != "ok":
        raise NotFound(out.get("message") or out.get("error") or "trip not found")
    legs = T._t("middle_mile_legs.parquet")
    legs = legs[legs["trip_uuid"] == trip_id].sort_values("leg_position")
    out["legs"] = [{"leg": int(r.leg_position), "from": r.source_name, "to": r.destination_name, "planned_min": float(r.osrm_time), "actual_min": float(r.actual_time),
                    "handling_min": None if pd.isna(r.handling_min) else float(r.handling_min)} for r in legs.itertuples()]
    hub = T.hub_report(out.get("first_source_center")) if out.get("first_source_center") else {}
    out["origin_hub"] = (hub.get("hubs") or [None])[0] if hub.get("status") == "ok" else None
    out["network_median_handling_min"] = hub.get("network_median_handling_min")
    return clean(out)


def _sections(text: str) -> dict:
    return {m.group(1).strip().lower(): m.group(2).strip() for m in re.finditer(r"^## (.+?)\n(.*?)(?=^## |\Z)", text, re.S | re.M)}


def prompt_card(fn: str) -> dict:
    text = open(os.path.join(T.PROMPTS, fn), encoding="utf8").read()
    s = _sections(text)
    fields = [{"name": m.group(1), "description": m.group(2).strip()} for m in re.finditer(r"^- `?(\w+)`?\s*:\s*(.*)$", s.get("input fields", ""), re.M)]
    ex = re.findall(r"```(?:json|text)?\n(.*?)```", s.get("worked example", ""), re.S)
    fails = [m.group(1).strip() for m in re.finditer(r"^- (.*)$", s.get("known failure modes", ""), re.M)]
    body = re.search(r"```(?:text)?\n(.*?)```", s.get("prompt text", ""), re.S)
    return {"template_id": fn[3:-3], "number": fn[:2], "title": text.splitlines()[0].lstrip("# ").strip(), "purpose": s.get("purpose", ""), "fields": fields,
            "template": body.group(1).strip() if body else "", "example_input": ex[0].strip() if ex else "", "example_output": ex[1].strip() if len(ex) > 1 else "",
            "failure_modes": fails}


@lru_cache(maxsize=1)
def prompts() -> list[dict]:
    return vc.scrub([prompt_card(fn) for fn in sorted(os.listdir(T.PROMPTS)) if re.match(r"\d\d_.*\.md$", fn)])


TASKS = ["V1", "V2", "V3", "M1", "M2", "M3"]


@lru_cache(maxsize=1)
def model_cards() -> list[dict]:
    out = []
    for t in TASKS:
        m = json.load(open(os.path.join(MET_DIR, f"{t}.json"), encoding="utf8"))
        win = next(x for x in m["models"] if x["name"] == m["winner"])
        main = "PR_AUC" if m["type"] == "classification" else "MAE"
        figs = {k: f"/api/figures/{t}_{k}.png" for k in ("shap_bar", "shap_beeswarm") if os.path.exists(os.path.join(FIG_DIR, f"{t}_{k}.png"))}
        out.append({**m, "headline": {"metric": main, "winner_value": win["metrics"].get(main), "baseline": m.get("base_rate_or_baseline"),
                                      "higher_is_better": m["type"] == "classification"}, "figures": figs})
    return vc.scrub(out)


def audit_records() -> list[dict]:
    """One record per run with its latest decision merged in (the log itself is append-only)."""
    runs, last = {}, {}
    for r in G.read_audit():
        if r.get("type") == "run":
            runs[r["run_id"]] = r
        elif r.get("type") == "decision":
            last[r["run_id"]] = r
    out = []
    for rid, r in runs.items():
        d = last.get(rid)
        out.append({"run_id": rid, "ts": r["ts"], "page": r["page"], "mode": r.get("mode"), "model_used": r.get("model_used"), "recommended_action": r.get("recommended_action"),
                    "decision": d["decision"] if d else None, "decided_at": d["ts"] if d else None, "final_text": (d or {}).get("final_text") or r.get("final_text"),
                    "guardrail_events": [e.get("event") for e in r.get("guardrail_events", [])], "banner": r.get("banner"), "inputs": r.get("inputs"),
                    "tools_called": r.get("tools_called", []), "run_text": r.get("final_text")})
    return sorted(out, key=lambda x: x["ts"], reverse=True)


def audit_query(page_name: str | None, decision: str | None, mode: str | None, q: str | None, date_from: str | None, date_to: str | None) -> list[dict]:
    rows = audit_records()
    if page_name:
        rows = [r for r in rows if r["page"].casefold() == page_name.casefold()]
    if decision:
        rows = [r for r in rows if (r["decision"] or "pending") == decision]
    if mode:
        rows = [r for r in rows if (r["mode"] or "").upper().startswith(mode.upper())]
    if date_from:
        rows = [r for r in rows if r["ts"][:10] >= date_from]
    if date_to:
        rows = [r for r in rows if r["ts"][:10] <= date_to]
    if q:
        ql = q.casefold()
        rows = [r for r in rows if ql in json.dumps(vc.scrub({k: r[k] for k in ("run_id", "page", "final_text", "inputs")}), default=str).casefold()]
    return rows


AUDIT_CSV_COLS = ["run_id", "ts", "page", "mode", "model_used", "recommended_action", "decision", "decided_at", "guardrail_events", "final_text"]


def audit_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(AUDIT_CSV_COLS)
    for r in rows:
        r = vc.scrub(r)
        w.writerow([" ".join(r[c]) if c == "guardrail_events" else ("" if r[c] is None else r[c]) for c in AUDIT_CSV_COLS])
    return buf.getvalue()


def rows_to_csv(rows: list[dict]) -> str:
    """Any list of flat dict rows (already scrubbed/cleaned by the caller) as CSV, column order from the first row."""
    buf = io.StringIO()
    if not rows:
        return ""
    cols = list(rows[0])
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


TABLES = ["vendor_lr_clean.parquet", "vendor_shipments.parquet", "lane_vendor_rates.csv", "vendor_overpay.csv", "vendor_overpay_summary.csv",
          "hub_handling_ranking.csv", "middle_mile_trips.parquet", "middle_mile_legs.parquet", "pred_M1.csv", "pred_M2.csv", "pred_M3.csv",
          "pred_V1.csv", "pred_V2.csv", "pred_V3.csv"]
MODEL_FILES = ["V1_RandomForest", "V2_LightGBM", "V3_LightGBM", "M1_LightGBM", "M2_RandomForest", "M3_LightGBM"]
EXPLAINED_MODELS = ["V2_LightGBM", "M2_RandomForest"]  # the only ones check_booking / check_trip run SHAP on


def warmup() -> None:
    """Read every processed table, load every saved model pipeline (plus the two SHAP explainers the API actually
    uses) and compute the payloads behind Overview, Vendors and Hubs, so the first real request is served from
    memory instead of paying for pandas aggregation, joblib unpickling or TreeExplainer setup."""
    for name in TABLES:
        T._t(name)
    T._defaults()
    T._lr_valid()
    for name in MODEL_FILES:
        T._model(name)
    for name in EXPLAINED_MODELS:
        T._explainer(name)
    overview()
    _vendor_table()
    T.hub_report(None, 25)
    from app import lanes as LN
    for m in (3, 6, 12):
        LN._agg(m)
    LN.lane_points()
    LN.best_pair()
    meta_options()
    model_cards()
    prompts()
