"""Lane finder: the cheapest way from A to B, and a vendor cost comparison.

A lane is `Departure Point` -> `Destination Point`. Each pair has one road route (one route code, one distance), so
"cheapest" means the cheapest vendor + vehicle on that lane, never a choice of road. Everything here is a *cost*
comparison on valid-cost LRs; the data has no on-time or damage records, so cheapest is not the same as best.

Vendor names never leave this module: rows carry vendor codes (V001...) from backend.vendor_codes.

Gap to fair price. No per-LR V1 fair price exists. pred_V1.csv holds the V1 fair price per *shipment*, out of sample,
for the test quarter (Jan-Mar 2026) only. Scoring train-period shipments with the saved model would be in-sample
(the RandomForest has seen them), so the gap is computed only on test-quarter shipments, and every row says how many
shipments it rests on (`n_gap_shipments`). Vendors with none show no gap.

"Last N months" is counted back from the last date in the data (30 Mar 2026), not from today.
"""
from __future__ import annotations

import difflib
from functools import lru_cache

import numpy as np
import pandas as pd

from app import tools as T
from backend import vendor_codes as vc

MIN_GAP_SHIPMENTS = 10  # a fair-price gap is shown only when it rests on at least this many covered (out-of-sample) shipments
CHECK_DATA_GAP_PCT = 30.0  # |gap| above this gets a 'check data' flag; it never changes the rupee-based tags
MIN_LRS = 10          # evidence needed before a vendor is ranked on a lane
RECENT_DAYS = 90      # a vendor not used in this many days before the data ends is "thin"
COSTLY_PCT = 10.0     # more than this % above the lane median is "Costly"
TWO_LEG_MIN_SAVING_PCT = 10.0
TWO_LEG_LABEL = "indicative: excludes handling at X and the extra transit time"
FOOTER = "Cost comparison only; the data has no on-time or damage records, so 'cheapest' is not the same as 'best'."
GAP_NOTE = ("Gap to fair price uses the V1 fair price, which exists out of sample only for the Jan-Mar 2026 test quarter, "
            "so it rests on those shipments only, and is shown only when at least 10 of them are covered (hover a value to see how many). A gap above 30% either way is flagged 'check data'.")


class LaneNotFound(LookupError):
    """Unknown origin / destination place. Carries close matches so the API can answer 404 with suggestions."""

    def __init__(self, kind: str, value: str, suggestions: list[str]):
        super().__init__(f"Unknown {kind} '{value}'")
        self.kind, self.value, self.suggestions = kind, value, suggestions


class VendorNotFound(LookupError):
    def __init__(self, code: str):
        super().__init__(f"Unknown vendor code '{code}'")
        self.code = code


@lru_cache(maxsize=1)
def _code_map() -> dict:
    return vc.code_map()


@lru_cache(maxsize=1)
def _lr() -> pd.DataFrame:
    lr = T._t("vendor_lr_clean.parquet")
    v = lr[(lr["r2_freight"] > 0) & (lr["gta_invoice_value"] > 0) & (lr["lr_qty"] > 0)]
    out = pd.DataFrame({
        "dep": v["departure_point"].astype(str), "dest": v["destination_point"].astype(str), "route": v["route"].astype(str),
        "distance": v["distance"], "vendor_code": v["vendor_name"].map(_code_map()),
        "vehicle_type": v["vehicle_type"].fillna("MISSING").astype(str), "basis": v["truck_load"].fillna("NA").astype(str),
        "lr_date": v["lr_date"], "qty": v["lr_qty"], "freight": v["r2_freight"], "gta": v["gta_invoice_value"], "margin": v["margin"],
        "shipment_no": v["shipment_no"]})
    return out[out["vendor_code"].notna()]


@lru_cache(maxsize=1)
def data_end() -> pd.Timestamp:
    return T._t("vendor_lr_clean.parquet")["lr_date"].max().normalize()


@lru_cache(maxsize=1)
def _ships() -> pd.DataFrame:
    """One row per (lane, vendor, vehicle, basis, shipment): what the shipment cost on this lane, its utilisation and its out-of-sample V1 gap."""
    lr = _lr()
    s = lr.groupby(["dep", "dest", "vendor_code", "vehicle_type", "basis", "shipment_no"], sort=False).agg(
        route=("route", "first"), distance=("distance", "median"), lrs=("qty", "size"), qty=("qty", "sum"), freight=("freight", "sum"),
        gta=("gta", "sum"), margin=("margin", "sum"), last=("lr_date", "max")).reset_index()
    sh = T._t("vendor_shipments.parquet")
    ok = sh[sh["valid_capacity"] & ~sh["util_outlier"]].drop_duplicates("shipment_no").set_index("shipment_no")["utilisation"]
    s["util"] = s["shipment_no"].map(ok)
    p = T._t("pred_V1.csv")
    p = p[p["predicted"] > 0].drop_duplicates("record_id").set_index("record_id")
    s["gap"] = ((p["actual"] - p["predicted"]) / p["predicted"] * 100).reindex(s["shipment_no"]).to_numpy()
    return s


@lru_cache(maxsize=1)
def _caps() -> dict:
    lr = T._t("vendor_lr_clean.parquet")
    c = lr[lr["vehicle_capacity"] > 0].groupby("vehicle_type")["vehicle_capacity"].median()
    return {str(k): float(v) for k, v in c.items()}


@lru_cache(maxsize=8)
def _agg(months: int) -> pd.DataFrame:
    """Per (lane, vendor, vehicle type, pricing basis) over the last `months` months."""
    s = _ships()
    if months < 12:
        s = s[s["last"] > data_end() - pd.DateOffset(months=months)]
    g = s.groupby(["dep", "dest", "vehicle_type", "basis", "vendor_code"], sort=False).agg(
        route=("route", "first"), distance=("distance", "median"), lrs=("lrs", "sum"), shipments=("shipment_no", "size"), total_mt=("qty", "sum"),
        freight=("freight", "sum"), gta=("gta", "sum"), margin=("margin", "sum"), median_rs_per_shipment=("freight", "median"),
        utilisation_pct=("util", "median"), last_used=("last", "max"), gap_to_fair_pct=("gap", "median"), n_gap_shipments=("gap", "count")).reset_index()
    g.loc[g["n_gap_shipments"] < MIN_GAP_SHIPMENTS, "gap_to_fair_pct"] = np.nan   # too few covered shipments: no gap (n_gap_shipments stays for the tooltip)
    g["rs_per_mt"] = g["freight"] / g["total_mt"]
    g["margin_pct"] = g["margin"] / g["gta"] * 100
    g["days_since_last_use"] = (data_end() - g["last_used"]).dt.days
    return g


@lru_cache(maxsize=1)
def _places() -> tuple[list[str], list[str]]:
    lr = _lr()
    return sorted(lr["dep"].unique()), sorted(lr["dest"].unique())


def _suggest(value: str, names: list[str]) -> list[str]:
    """Close matches for an unknown place: names that start with what was typed, or with a similarity of at least 0.8. Nothing weaker; may be empty."""
    v = value.strip().upper()
    hits = [n for n in names if v and n.upper().startswith(v)]
    upper = {n.upper(): n for n in names}
    hits += [upper[u] for u in difflib.get_close_matches(v, list(upper), n=5, cutoff=0.8)]
    return list(dict.fromkeys(hits))[:5]


def resolve_place(kind: str, value: str | None) -> str:
    """kind is 'origin' or 'destination'; case-insensitive. Raises LaneNotFound with suggestions."""
    deps, dests = _places()
    names = deps if kind == "origin" else dests
    v = (value or "").strip()
    for n in names:
        if n.casefold() == v.casefold() and v:
            return n
    raise LaneNotFound(kind, v, _suggest(v, names))


def _check_months(months) -> int:
    try:
        m = int(months)
    except (TypeError, ValueError):
        raise T.ToolError("'months' must be a whole number from 1 to 12")
    if not 1 <= m <= 12:
        raise T.ToolError("'months' must be between 1 and 12")
    return m


def _check_qty(qty) -> float | None:
    if qty is None or qty == "":
        return None
    return T._num(qty, "qty", lo=0.01, hi=100)


def lane_points() -> dict:
    """Origins and, for each, the destinations reachable by a direct lane (for dependent dropdowns)."""
    g = _lr().groupby(["dep", "dest"]).size().rename("lrs").reset_index()
    origins = []
    for dep, d in g.groupby("dep"):
        d = d.sort_values(["lrs", "dest"], ascending=[False, True])
        origins.append({"name": dep, "lrs": int(d["lrs"].sum()), "destinations": [{"name": r.dest, "lrs": int(r.lrs)} for r in d.itertuples()]})
    origins.sort(key=lambda o: (-o["lrs"], o["name"]))
    return {"n_origins": len(origins), "n_destinations": int(g["dest"].nunique()), "n_lanes": int(len(g)), "origins": origins,
            "vehicle_types": sorted(_caps()), "footer": FOOTER}


def tag_group(rows: pd.DataFrame) -> pd.DataFrame:
    """Rank and tag the vendors of ONE vehicle type x pricing basis group (rows need rs_per_mt, lrs, days_since_last_use).

    thin = fewer than MIN_LRS LRs or not used in the last RECENT_DAYS days: no tag, no rank.
    Among the rest: Cheapest = lowest Rs/MT; Costly = more than COSTLY_PCT above the median of the reliable vendors; otherwise Fair.
    With no reliable vendor at all nothing is tagged ('thin only')."""
    r = rows.sort_values("rs_per_mt").copy()
    r["thin"] = (r["lrs"] < MIN_LRS) | (r["days_since_last_use"] > RECENT_DAYS)
    ok = ~r["thin"]
    med = float(r.loc[ok, "rs_per_mt"].median()) if ok.any() else float(r["rs_per_mt"].median())
    r["lane_median_rs_per_mt"] = med
    r["tag"] = None
    r["rank"] = np.nan
    if ok.any():
        idx = r.index[ok]
        r.loc[idx, "rank"] = np.arange(1, len(idx) + 1)
        r.loc[idx, "tag"] = np.where(r.loc[idx, "rs_per_mt"] > med * (1 + COSTLY_PCT / 100), "Costly", "Fair")
        r.loc[idx[0], "tag"] = "Cheapest"
        cheapest = float(r.loc[idx[0], "rs_per_mt"])
        r["saving_if_switched_rs"] = np.where(r["rs_per_mt"] > cheapest, (r["rs_per_mt"] - cheapest) * r["total_mt"], 0.0)
    else:
        r["saving_if_switched_rs"] = np.nan
    r = r.sort_values(["thin", "rs_per_mt"])  # reliable vendors first (cheapest to costliest), thin ones after
    r["thin_reason"] = np.where(r["lrs"] < MIN_LRS, f"fewer than {MIN_LRS} LRs", np.where(r["days_since_last_use"] > RECENT_DAYS, f"not used in the last {RECENT_DAYS} days", ""))
    r.loc[~r["thin"], "thin_reason"] = ""
    return r


def _vendor_rows(r: pd.DataFrame, full: pd.DataFrame) -> list[dict]:
    out = []
    for x in r.itertuples():
        f = full[full["vendor_code"] == x.vendor_code]
        out.append(T._clean({
            "rank": None if np.isnan(x.rank) else int(x.rank), "vendor_code": x.vendor_code, "tag": x.tag, "thin": bool(x.thin), "thin_reason": x.thin_reason or None,
            "rs_per_mt": x.rs_per_mt, "median_rs_per_shipment": x.median_rs_per_shipment, "gap_to_fair_pct": x.gap_to_fair_pct, "n_gap_shipments": int(x.n_gap_shipments),
            "check_data": bool(x.gap_to_fair_pct == x.gap_to_fair_pct and abs(x.gap_to_fair_pct) > CHECK_DATA_GAP_PCT),
            "lrs": int(x.lrs), "shipments": int(x.shipments), "total_mt": x.total_mt, "utilisation_pct": x.utilisation_pct, "margin_pct": x.margin_pct,
            "last_used": x.last_used, "days_since_last_use": int(x.days_since_last_use), "saving_if_switched_rs": x.saving_if_switched_rs,
            "full_year_rs_per_mt": None if f.empty else f["rs_per_mt"].iloc[0], "full_year_lrs": 0 if f.empty else int(f["lrs"].iloc[0])}))
    return out


def lane_options(origin: str, destination: str, qty_mt: float | None = None, vehicle_type: str | None = None, months: int = 6) -> dict:
    """Vendors on one lane, ranked per vehicle type x pricing basis, with the recommendation and the savings if switched."""
    o, d = resolve_place("origin", origin), resolve_place("destination", destination)
    months, qty = _check_months(months), _check_qty(qty_mt)
    vt_filter = str(vehicle_type).strip() if vehicle_type else None
    base = {"origin": o, "destination": d, "months": months, "qty_mt": qty, "vehicle_type_filter": vt_filter, "data_end": str(data_end().date()),
            "period": {"from": str((data_end() - pd.DateOffset(months=months)).date() if months < 12 else data_end() - pd.DateOffset(months=12)), "to": str(data_end().date())},
            "footer": FOOTER, "gap_note": GAP_NOTE, "notes": []}
    lane_all = _agg(months)
    lane = lane_all[(lane_all["dep"] == o) & (lane_all["dest"] == d)]
    full_all = _agg(12)
    full_lane = full_all[(full_all["dep"] == o) & (full_all["dest"] == d)]
    if vt_filter:
        lane, full_lane = lane[lane["vehicle_type"] == vt_filter], full_lane[full_lane["vehicle_type"] == vt_filter]
    if lane.empty:
        full_any = full_all[(full_all["dep"] == o) & (full_all["dest"] == d)]
        reason = ("No LRs on this lane in the chosen period." if not full_any.empty else "No direct lane: no valid-cost LRs run from this origin to this destination.")
        base["notes"].append(reason)
        return {**base, "has_direct": not full_any.empty, "route": None, "distance_km": None, "summary": None, "groups": [], "recommendation": None, "savings_total_rs": 0.0}

    groups = []
    for (vt, basis), rows in lane.groupby(["vehicle_type", "basis"], sort=False):
        tagged = tag_group(rows)
        fl = full_lane[(full_lane["vehicle_type"] == vt) & (full_lane["basis"] == basis)]
        reliable = tagged[~tagged["thin"]]
        lo, hi = (float(reliable["rs_per_mt"].min()), float(reliable["rs_per_mt"].max())) if not reliable.empty else (None, None)
        groups.append({
            "vehicle_type": vt, "basis": basis, "capacity_mt": _caps().get(vt), "lrs": int(rows["lrs"].sum()), "shipments": int(rows["shipments"].sum()),
            "n_vendors": int(len(rows)), "n_reliable": int(len(reliable)), "thin_only": bool(reliable.empty),
            "lane_median_rs_per_mt": float(tagged["lane_median_rs_per_mt"].iloc[0]), "min_rs_per_mt": lo, "max_rs_per_mt": hi,
            "spread_pct": None if lo is None or lo == 0 else (hi - lo) / lo * 100,
            "vendors": _vendor_rows(tagged, fl)})
    groups.sort(key=lambda g: -g["lrs"])
    for g in groups:
        g["label"] = g["vehicle_type"] + ("" if g["basis"] == "PMT" else f" ({g['basis']})")
    if len(groups) and all(g["thin_only"] for g in groups):
        base["notes"].append("Thin data; showing all vendors without tags.")

    rec = _recommend(o, d, groups, lane, qty, vt_filter, base["notes"])
    focus = next((g for g in groups if rec and g["vehicle_type"] == rec["vehicle_type"] and g["basis"] == rec["basis"]), groups[0])
    route = lane.sort_values("lrs", ascending=False).iloc[0]
    tot_g, tot_m = float(lane["gta"].sum()), float(lane["margin"].sum())
    summary = T._clean({
        "lrs": int(lane["lrs"].sum()), "shipments": int(lane["shipments"].sum()), "vendors": int(lane["vendor_code"].nunique()),
        "full_year_lrs": int(full_lane["lrs"].sum()), "margin_pct": tot_m / tot_g * 100 if tot_g else None,
        "focus": {"vehicle_type": focus["vehicle_type"], "basis": focus["basis"], "min_rs_per_mt": focus["min_rs_per_mt"], "max_rs_per_mt": focus["max_rs_per_mt"],
                  "spread_pct": focus["spread_pct"], "n_reliable": focus["n_reliable"]},
        "route_codes": sorted(lane["route"].unique())})
    saving = sum(v["saving_if_switched_rs"] or 0.0 for g in groups for v in g["vendors"])
    return T._clean({**base, "has_direct": True, "route": route["route"], "distance_km": float(route["distance"]) if route["distance"] == route["distance"] else None,
                     "summary": summary, "groups": groups, "recommendation": rec, "savings_total_rs": saving})


def _recommend(o: str, d: str, groups: list[dict], lane: pd.DataFrame, qty: float | None, vt_filter: str | None, notes: list[str]) -> dict | None:
    """Cheapest reliable vendor for the smallest vehicle with capacity >= qty (or the busiest group if no load is given)."""
    cand = [g for g in groups if not g["thin_only"]]
    if not cand:
        return None
    if qty is None:
        cand.sort(key=lambda g: -g["lrs"])
    else:
        if not vt_filter:
            fit = [g for g in cand if g["capacity_mt"] and g["capacity_mt"] >= qty]
            if not fit:
                biggest = max((g["capacity_mt"] or 0) for g in cand)
                notes.append(f"No vehicle type with enough data on this lane carries {qty:g} MT in one truck (largest {biggest:g} MT); recommendation not given.")
                return None
            cand = sorted(fit, key=lambda g: (g["capacity_mt"], -g["lrs"]))
            smallest_any = min((g for g in groups if g["capacity_mt"] and g["capacity_mt"] >= qty), key=lambda g: g["capacity_mt"])
            if smallest_any["thin_only"]:
                notes.append("A smaller vehicle that fits the load has only thin data, so the recommendation uses the next size up.")
        else:
            cand.sort(key=lambda g: -g["lrs"])
            cap = cand[0]["capacity_mt"]
            if cap and qty > cap:
                notes.append(f"{qty:g} MT is above the {cap:g} MT capacity of {vt_filter}; more than one truck is needed.")
    g = cand[0]
    v = next(x for x in g["vendors"] if x["tag"] == "Cheapest")
    q = qty if qty is not None else (v["total_mt"] / v["shipments"] if v["shipments"] else None)
    route = lane.sort_values("lrs", ascending=False).iloc[0]["route"]
    est = _estimate(route, g["vehicle_type"], v, q)
    month = int(data_end().month)
    rate_based = v["rs_per_mt"] * q if q else None
    return T._clean({
        "vendor_code": v["vendor_code"], "vehicle_type": g["vehicle_type"], "basis": g["basis"], "capacity_mt": g["capacity_mt"], "route": route,
        "rs_per_mt": v["rs_per_mt"], "lane_median_rs_per_mt": g["lane_median_rs_per_mt"], "lrs": v["lrs"], "last_used": v["last_used"],
        "days_since_last_use": v["days_since_last_use"], "qty_mt": q, "qty_assumed": qty is None,
        "only_reliable_vendor": g["n_reliable"] == 1, "gap_to_fair_pct": v["gap_to_fair_pct"], "n_gap_shipments": v["n_gap_shipments"],
        "estimate": est, "rate_based_rs": rate_based,
        "booking_prefill": {"route": route, "vehicle_type": g["vehicle_type"], "vendor": v["vendor_code"], "qty": round(q, 2) if q else None, "month": month,
                            "quoted": int(round(rate_based)) if rate_based else None}})


def _estimate(route: str, vehicle_type: str, vendor: dict, qty: float | None) -> dict | None:
    """V1 fair price for this lane, vehicle and load, plus the vendor's typical gap (only if it has out-of-sample shipments). None if V1 cannot score it."""
    if not qty:
        return None
    try:
        X, _assumed, _w = T._booking_features({"route": route, "vehicle_type": vehicle_type, "vendor": vendor["vendor_code"], "qty": qty, "month": int(data_end().month)})
        fair = T._score_v1(X)
    except Exception:  # noqa: BLE001 - an unscorable lane just has no V1 estimate; the rate-based figure still stands
        return None
    gap = vendor["gap_to_fair_pct"]   # already None unless it rests on >= MIN_GAP_SHIPMENTS covered shipments
    if gap is None:
        return {"fair_rs": fair["fair_rs"], "fair_low_rs": fair["fair_low_rs"], "fair_high_rs": fair["fair_high_rs"], "vendor_gap_pct": None, "estimate_rs": None,
                "basis": f"Rate x load: this vendor has fewer than {MIN_GAP_SHIPMENTS} fair-price-covered shipments, so no gap is applied (V1 fair price shown for reference only)"}
    return {"fair_rs": fair["fair_rs"], "fair_low_rs": fair["fair_low_rs"], "fair_high_rs": fair["fair_high_rs"], "vendor_gap_pct": gap, "estimate_rs": fair["fair_rs"] * (1 + gap / 100),
            "basis": "V1 fair price plus this vendor's typical gap"}


def _best_legs(months: int, vehicle_type: str | None) -> pd.DataFrame:
    a = _agg(months)
    a = a[(a["lrs"] >= MIN_LRS) & (a["basis"] == "PMT")]
    if vehicle_type:
        a = a[a["vehicle_type"] == vehicle_type]
    i = a.groupby(["dep", "dest", "vehicle_type"])["rs_per_mt"].idxmin()
    return a.loc[i, ["dep", "dest", "vehicle_type", "vendor_code", "rs_per_mt", "lrs"]]


def two_leg_options(origin: str, destination: str, vehicle_type: str | None = None, top: int = 3, months: int = 6) -> dict:
    """A -> X -> B where each leg has >= 10 LRs; cost = cheapest Rs/MT of each leg (same vehicle type, per-tonne pricing). Shown only when
    >= 10% cheaper than the cheapest direct lane. Indicative: handling at X and extra transit time are not in the data. Never recommended automatically."""
    o, d = resolve_place("origin", origin), resolve_place("destination", destination)
    months = _check_months(months)
    best = _best_legs(months, str(vehicle_type).strip() if vehicle_type else None)
    l1 = best[best["dep"] == o].rename(columns={"dest": "via", "vendor_code": "v1", "rs_per_mt": "r1", "lrs": "n1"}).drop(columns="dep")
    l2 = best[best["dest"] == d].rename(columns={"dep": "via", "vendor_code": "v2", "rs_per_mt": "r2", "lrs": "n2"}).drop(columns="dest")
    m = l1.merge(l2, on=["via", "vehicle_type"])
    m = m[(m["via"] != o) & (m["via"] != d)]
    direct = best[(best["dep"] == o) & (best["dest"] == d)].set_index("vehicle_type")
    m["total"] = m["r1"] + m["r2"]
    res = {"origin": o, "destination": d, "label": TWO_LEG_LABEL, "min_saving_pct": TWO_LEG_MIN_SAVING_PCT, "has_direct": not direct.empty, "months": months, "options": []}
    if m.empty:
        return res
    m["direct"] = m["vehicle_type"].map(direct["rs_per_mt"]) if not direct.empty else np.nan
    m["saving_pct"] = (m["direct"] - m["total"]) / m["direct"] * 100
    m = m[m["saving_pct"] >= TWO_LEG_MIN_SAVING_PCT] if not direct.empty else m.assign(saving_pct=np.nan)
    m = m.sort_values(["saving_pct", "total"], ascending=[False, True]) if not direct.empty else m.sort_values("total")
    m = m.drop_duplicates("via").head(int(top))
    for x in m.itertuples():
        res["options"].append(T._clean({
            "via": x.via, "vehicle_type": x.vehicle_type, "leg1": {"from": o, "to": x.via, "vendor_code": x.v1, "rs_per_mt": x.r1, "lrs": int(x.n1)},
            "leg2": {"from": x.via, "to": d, "vendor_code": x.v2, "rs_per_mt": x.r2, "lrs": int(x.n2)}, "total_rs_per_mt": x.total,
            "direct_rs_per_mt": None if direct.empty else x.direct, "saving_pct": None if direct.empty else x.saving_pct,
            "saving_rs_per_mt": None if direct.empty else x.direct - x.total, "label": TWO_LEG_LABEL}))
    return res


def lane_search(origin: str, destination: str, qty_mt: float | None = None, vehicle_type: str | None = None, months: int = 6) -> dict:
    """lane_options + two_leg_options in one payload (what /api/lanes/search returns)."""
    res = lane_options(origin, destination, qty_mt, vehicle_type, months)
    res["two_leg"] = two_leg_options(origin, destination, vehicle_type, 3, months)
    if not res["has_direct"] and not res["two_leg"]["options"]:
        res["notes"].append("No two-leg option either.")
    return res


def parse_codes(codes) -> list[str]:
    """'V001, v005' or a list -> upper-case codes, 2-4 distinct; raises VendorNotFound / ToolError."""
    raw = codes.split(",") if isinstance(codes, str) else list(codes or [])
    out = list(dict.fromkeys(str(c).strip().upper() for c in raw if str(c).strip()))
    if not 2 <= len(out) <= 4:
        raise T.ToolError("choose 2 to 4 distinct vendor codes")
    for c in out:
        if vc.name_for(c) is None:
            raise VendorNotFound(c)
    return out


def vendor_compare(vendor_codes, vehicle_type: str | None = None, months: int = 12) -> dict:
    """Head to head on the lanes (lane x vehicle type x pricing basis) where every chosen vendor has >= 10 LRs."""
    codes = parse_codes(vendor_codes)
    months = _check_months(months)
    a = _agg(months)
    if vehicle_type:
        a = a[a["vehicle_type"] == str(vehicle_type).strip()]
    key = ["dep", "dest", "vehicle_type", "basis"]
    elig = a[a["lrs"] >= MIN_LRS]
    med = elig.groupby(key)["rs_per_mt"].median().rename("lane_median")
    mine = elig[elig["vendor_code"].isin(codes)]
    wide = mine.pivot_table(index=key, columns="vendor_code", values=["rs_per_mt", "total_mt", "lrs"], aggfunc="first")
    wide = wide.dropna()  # keeps only the lane groups where every chosen vendor has 10+ LRs
    rows, per = [], {c: {"mt": 0.0, "idx_num": 0.0, "idx_den": 0.0, "won": 0, "extra_rs": 0.0} for c in codes}
    for k, w in wide.iterrows():
        rates = {c: float(w[("rs_per_mt", c)]) for c in codes}
        mts = {c: float(w[("total_mt", c)]) for c in codes}
        lrs = {c: int(w[("lrs", c)]) for c in codes}
        cheaper = min(rates, key=rates.get)
        lm = float(med.loc[k])
        for c in codes:
            p = per[c]
            p["mt"] += mts[c]; p["idx_num"] += rates[c] * mts[c]; p["idx_den"] += lm * mts[c]; p["extra_rs"] += (rates[c] - rates[cheaper]) * mts[c]
        per[cheaper]["won"] += 1
        rows.append({"origin": k[0], "destination": k[1], "vehicle_type": k[2], "basis": k[3], "rs_per_mt": rates, "lrs": lrs, "total_mt": mts, "cheaper": cheaper,
                     "lane_median_rs_per_mt": lm, "gap_pct": (max(rates.values()) - min(rates.values())) / min(rates.values()) * 100})
    rows.sort(key=lambda r: -sum(r["total_mt"].values()))
    vendors = [{"vendor_code": c, "lanes_won": per[c]["won"], "total_mt": per[c]["mt"], "index_vs_lane_median": (per[c]["idx_num"] / per[c]["idx_den"] * 100) if per[c]["idx_den"] else None,
                "extra_cost_vs_cheapest_rs": per[c]["extra_rs"]} for c in codes]
    return T._clean({"codes": codes, "vehicle_type": vehicle_type or None, "months": months, "n_shared_lanes": len(rows), "vendors": vendors, "lanes": rows,
                     "index_note": "Index = the vendor's volume-weighted Rs/MT divided by the lane median Rs/MT (100 = at the median; lower is cheaper).",
                     "extra_cost_note": "Extra cost = what that vendor's volume on the shared lanes cost above the cheapest chosen vendor's rate.",
                     "footer": FOOTER, "notes": [] if rows else ["These vendors share no lane with 10+ LRs each in this period."]})


def best_pair() -> tuple[str, str]:
    """The two vendors with the most shared lane x vehicle groups (>= 10 LRs each, last 12 months): the default for the head-to-head query."""
    a = _agg(12)
    a = a[a["lrs"] >= MIN_LRS]
    top = a.groupby("vendor_code").size().sort_values(ascending=False).head(25).index
    a = a[a["vendor_code"].isin(top)][["dep", "dest", "vehicle_type", "basis", "vendor_code"]]
    m = a.merge(a, on=["dep", "dest", "vehicle_type", "basis"])
    m = m[m["vendor_code_x"] < m["vendor_code_y"]]
    c = m.groupby(["vendor_code_x", "vendor_code_y"]).size().sort_values(ascending=False)
    return str(c.index[0][0]), str(c.index[0][1])


def busiest_lane() -> tuple[str, str]:
    g = _lr().groupby(["dep", "dest"]).size().sort_values(ascending=False)
    return g.index[0]
