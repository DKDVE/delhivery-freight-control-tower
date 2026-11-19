"""Query catalogue: the "trusted assets" an answer may be built from.

Each entry adds presentation metadata (title, columns with role/unit/decimals, readable SQL, data scope, chart hint) to
the executable query in app.tools.QUERIES, which stays the only code that touches the dataframes. Ids and columns are
checked against tools.QUERIES at import time.
"""
from __future__ import annotations

from app import tools as T

VENDOR_SCOPE = "Vendor data, Apr 2025-Mar 2026, vendor codes only"
MIN_LRS_SQL = 10
MIDDLE_MILE_SCOPE = "Delhivery public trip data, test week 27 Sep-3 Oct 2018"


def col(name: str, label: str, role: str, unit: str | None = None, decimals: int = 0) -> dict:
    """role: dimension | measure | time. unit: Rs | %  | x | count | hours | min | None."""
    return {"name": name, "label": label, "role": role, "unit": unit, "decimals": decimals}


# id -> {title, data_scope, columns, sql_equivalent(params)->str, default_sort, chart_hint}
CATALOGUE: dict[str, dict] = {
    "vendors_by_loss": {
        "title": "Vendors by Rs lost on loss-making LRs",
        "data_scope": VENDOR_SCOPE,
        "columns": [col("vendor_name", "Vendor", "dimension"), col("lrs", "LRs", "measure", "count"),
                    col("loss_share_pct", "Loss share", "measure", "%", 1), col("loss_rs", "Loss", "measure", "Rs"),
                    col("margin_rs", "Margin", "measure", "Rs")],
        "sql_equivalent": lambda p: (
            "SELECT vendor_name, COUNT(*) lrs, AVG(is_loss)*100 loss_share_pct, SUM(loss_rs) loss_rs, SUM(margin) margin_rs FROM vendor_lr WHERE margin_valid"
            + (f" AND zone = '{p['zone']}'" if p.get("zone") else "") + (f" AND quarter = '{p['quarter']}'" if p.get("quarter") else "")
            + f" GROUP BY vendor_name HAVING lrs >= {p['min_lrs']} ORDER BY loss_rs DESC LIMIT {p['top']}"),
        "default_sort": {"column": "loss_rs", "order": "desc"}, "chart_hint": "bar", "chart_measures": ["loss_rs"],
    },
    "top_overpaying_lanes": {
        "title": "Vendor x lane with the highest overpayment",
        "data_scope": VENDOR_SCOPE,
        "columns": [col("vendor", "Vendor", "dimension"), col("route", "Route", "dimension"), col("vehicle_type", "Vehicle", "dimension"),
                    col("shipments", "Shipments", "measure", "count"), col("excess_rs", "Excess vs fair price", "measure", "Rs"),
                    col("median_overpay_pct", "Median overpay", "measure", "%", 1)],
        "sql_equivalent": lambda p: f"SELECT vendor, route, vehicle_type, shipments, excess_rs, median_overpay_pct FROM vendor_overpay ORDER BY excess_rs DESC LIMIT {p['top']}",
        "default_sort": {"column": "excess_rs", "order": "desc"}, "chart_hint": "table",
    },
    "vendor_overpay_ranking": {
        "title": "Vendors by excess over the fair price",
        "data_scope": VENDOR_SCOPE,
        "columns": [col("vendor", "Vendor", "dimension"), col("shipments", "Shipments", "measure", "count"),
                    col("excess_rs", "Excess vs fair price", "measure", "Rs"), col("overpay_pct", "Overpay", "measure", "%", 1)],
        "sql_equivalent": lambda p: f"SELECT vendor, shipments, excess_rs, overpay_pct FROM vendor_overpay_summary WHERE shipments >= 10 ORDER BY excess_rs DESC LIMIT {p['top']}",
        "default_sort": {"column": "excess_rs", "order": "desc"}, "chart_hint": "bar", "chart_measures": ["excess_rs"],
    },
    "monthly_margin_trend": {
        "title": "Margin % by month",
        "data_scope": VENDOR_SCOPE,
        "columns": [col("month", "Month", "time"), col("lrs", "LRs", "measure", "count"), col("billed_rs", "Billed", "measure", "Rs"),
                    col("margin_rs", "Margin", "measure", "Rs"), col("margin_pct", "Margin %", "measure", "%", 1)],
        "sql_equivalent": lambda p: "SELECT month, COUNT(*) lrs, SUM(gta_invoice_value) billed_rs, SUM(margin) margin_rs FROM vendor_lr" + (f" WHERE zone = '{p['zone']}'" if p.get("zone") else "") + " GROUP BY month ORDER BY month",
        "default_sort": {"column": "month", "order": "asc"}, "chart_hint": "line", "chart_measures": ["margin_pct"],
    },
    "loss_share_by_vehicle_type": {
        "title": "Share of loss-making LRs by vehicle type",
        "data_scope": VENDOR_SCOPE,
        "columns": [col("vehicle_type", "Vehicle type", "dimension"), col("lrs", "LRs", "measure", "count"),
                    col("loss_share_pct", "Loss share", "measure", "%", 1), col("margin_rs", "Margin", "measure", "Rs")],
        "sql_equivalent": lambda p: f"SELECT vehicle_type, COUNT(*) lrs, AVG(is_loss)*100 loss_share_pct FROM vendor_lr GROUP BY vehicle_type HAVING lrs >= {p['min_lrs']} ORDER BY loss_share_pct DESC",
        "default_sort": {"column": "loss_share_pct", "order": "desc"}, "chart_hint": "stacked100", "chart_measures": ["loss_share_pct"],
    },
    "underloaded_lanes": {
        "title": "Routes with the most under-loaded trucks",
        "data_scope": VENDOR_SCOPE,
        "columns": [col("route", "Route", "dimension"), col("shipments", "Shipments", "measure", "count"),
                    col("underloaded_share_pct", "Under-loaded share", "measure", "%", 1), col("median_utilisation_pct", "Median utilisation", "measure", "%", 1)],
        "sql_equivalent": lambda p: f"SELECT route, COUNT(*) shipments, AVG(utilisation < 60)*100 underloaded_share_pct FROM vendor_shipments WHERE valid_capacity AND NOT util_outlier GROUP BY route HAVING shipments >= {p['min_shipments']} ORDER BY underloaded_share_pct DESC LIMIT {p['top']}",
        "default_sort": {"column": "underloaded_share_pct", "order": "desc"}, "chart_hint": "bar", "chart_measures": ["underloaded_share_pct"],
    },
    "hubs_by_delay": {
        "title": "Hubs by mean handling time",
        "data_scope": MIDDLE_MILE_SCOPE,
        "columns": [col("source_name", "Hub", "dimension"), col("legs", "Legs", "measure", "count"),
                    col("mean_actual_handling_min", "Mean handling", "measure", "min", 1), col("excess_hours", "Excess hours", "measure", "hours", 1),
                    col("excess_rs_base", "Excess cost", "measure", "Rs")],
        "sql_equivalent": lambda p: f"SELECT source_name, legs, mean_actual_handling_min, excess_hours, excess_rs_base FROM hub_handling_ranking ORDER BY mean_actual_handling_min DESC LIMIT {p['top']}",
        "default_sort": {"column": "mean_actual_handling_min", "order": "desc"}, "chart_hint": "bar", "chart_measures": ["mean_actual_handling_min"],
    },
    "corridors_by_overrun": {
        "title": "Corridors by planned-time overrun",
        "data_scope": MIDDLE_MILE_SCOPE,
        "columns": [col("corridor", "Corridor", "dimension"), col("trips", "Trips", "measure", "count"),
                    col("median_actual_over_planned", "Median actual/planned", "measure", "x", 2), col("severe_share_pct", "Severely late", "measure", "%", 1)],
        "sql_equivalent": lambda p: f"SELECT corridor, COUNT(*) trips, MEDIAN(actual_min/osrm_time_total) ratio FROM middle_mile_trips GROUP BY corridor HAVING trips >= {p['min_trips']} ORDER BY ratio DESC LIMIT {p['top']}",
        "default_sort": {"column": "median_actual_over_planned", "order": "desc"}, "chart_hint": "bar", "chart_measures": ["median_actual_over_planned"],
    },
    "data_quality_summary": {
        "title": "Data-quality issues in the vendor freight file",
        "data_scope": VENDOR_SCOPE,
        "columns": [col("issue", "Issue", "dimension"), col("count", "Rows", "measure", "count"), col("share_pct", "Share", "measure", "%", 2)],
        "sql_equivalent": lambda p: "SELECT issue, count, share_pct FROM vendor_lr_quality_summary",
        "default_sort": {"column": "count", "order": "desc"}, "chart_hint": "table",
    },
    "margin_by_zone": {
        "title": "Margin % by zone",
        "data_scope": VENDOR_SCOPE,
        "columns": [col("zone", "Zone", "dimension"), col("lrs", "LRs", "measure", "count"), col("billed_rs", "Billed", "measure", "Rs"),
                    col("margin_rs", "Margin", "measure", "Rs"), col("margin_pct", "Margin %", "measure", "%", 1)],
        "sql_equivalent": lambda p: "SELECT zone, COUNT(*) lrs, SUM(gta_invoice_value) billed_rs, SUM(margin) margin_rs FROM vendor_lr" + (f" WHERE quarter = '{p['quarter']}'" if p.get("quarter") else "") + " GROUP BY zone ORDER BY margin_pct",
        "default_sort": {"column": "margin_pct", "order": "asc"}, "chart_hint": "bar", "chart_measures": ["margin_pct"],
    },
    "route_type_overrun": {
        "title": "FTL vs Carting on planned-time overrun",
        "data_scope": MIDDLE_MILE_SCOPE,
        "columns": [col("route_type", "Route type", "dimension"), col("trips", "Trips", "measure", "count"),
                    col("median_actual_over_planned", "Median actual/planned", "measure", "x", 2), col("severe_share_pct", "Severely late", "measure", "%", 1)],
        "sql_equivalent": lambda p: f"SELECT route_type, COUNT(*) trips, MEDIAN(actual_min/osrm_time_total) ratio FROM middle_mile_trips GROUP BY route_type HAVING trips >= {p['min_trips']} ORDER BY ratio DESC",
        "default_sort": {"column": "median_actual_over_planned", "order": "desc"}, "chart_hint": "bar", "chart_measures": ["median_actual_over_planned"],
    },
    "underload_rate_by_plant": {
        "title": "Origin plants by under-load rate",
        "data_scope": VENDOR_SCOPE,
        "columns": [col("origin_plant", "Plant", "dimension"), col("shipments", "Shipments", "measure", "count"),
                    col("underloaded_share_pct", "Under-loaded share", "measure", "%", 1), col("median_utilisation_pct", "Median utilisation", "measure", "%", 1)],
        "sql_equivalent": lambda p: f"SELECT origin_plant, COUNT(*) shipments, AVG(utilisation < 60)*100 underloaded_share_pct FROM vendor_shipments WHERE valid_capacity AND NOT util_outlier GROUP BY origin_plant HAVING shipments >= {p['min_shipments']} ORDER BY underloaded_share_pct DESC",
        "default_sort": {"column": "underloaded_share_pct", "order": "desc"}, "chart_hint": "bar", "chart_measures": ["underloaded_share_pct"],
    },
    "vendor_price_gap_lanes": {
        "title": "Lanes with the largest vendor price gap",
        "data_scope": VENDOR_SCOPE,
        "columns": [col("route", "Route", "dimension"), col("vehicle_type", "Vehicle", "dimension"), col("vendors", "Vendors", "measure", "count"),
                    col("min_rs_per_mt", "Cheapest Rs/MT", "measure", "Rs"), col("max_rs_per_mt", "Priciest Rs/MT", "measure", "Rs"),
                    col("gap_rs_per_mt", "Gap Rs/MT", "measure", "Rs")],
        "sql_equivalent": lambda p: f"SELECT route, vehicle_type, COUNT(DISTINCT vendor_name) vendors, MIN(rs_per_mt), MAX(rs_per_mt) FROM lane_vendor_rates WHERE lr_count >= {p['min_lrs']} GROUP BY route, vehicle_type HAVING vendors >= 2 ORDER BY (MAX(rs_per_mt) - MIN(rs_per_mt)) DESC LIMIT {p['top']}",
        "default_sort": {"column": "gap_rs_per_mt", "order": "desc"}, "chart_hint": "table",
    },
    "top_loss_making_lanes": {
        "title": "Lanes with the biggest total Rs loss",
        "data_scope": VENDOR_SCOPE,
        "columns": [col("route", "Route", "dimension"), col("vehicle_type", "Vehicle", "dimension"), col("lrs", "LRs", "measure", "count"),
                    col("loss_rs", "Loss", "measure", "Rs"), col("loss_share_pct", "Loss share", "measure", "%", 1)],
        "sql_equivalent": lambda p: f"SELECT route, vehicle_type, COUNT(*) lrs, SUM(loss_rs) loss_rs FROM vendor_lr GROUP BY route, vehicle_type HAVING lrs >= {p['min_lrs']} ORDER BY loss_rs DESC LIMIT {p['top']}",
        "default_sort": {"column": "loss_rs", "order": "desc"}, "chart_hint": "table",
    },
    "lane_vendor_ranking": {
        "title": "Vendors on one lane, cheapest first",
        "data_scope": VENDOR_SCOPE,
        "columns": [col("vendor_code", "Vendor", "dimension"), col("rs_per_mt", "Rs per MT", "measure", "Rs"), col("lrs", "LRs", "measure", "count"),
                    col("margin_pct", "Margin %", "measure", "%", 1), col("gap_to_fair_pct", "Gap to fair price", "measure", "%", 1),
                    col("rate_x_load_rs", "Rate x load", "measure", "Rs")],
        "sql_equivalent": lambda p: (
            "SELECT vendor_code, SUM(r2_freight)/SUM(lr_qty) rs_per_mt, COUNT(*) lrs FROM vendor_lr WHERE margin_valid AND lr_qty > 0 "
            + (f"AND departure_point = '{p['origin']}' AND destination_point = '{p['destination']}' " if p.get("origin") and p.get("destination") else "AND (departure_point, destination_point) = (busiest lane) ")
            + (f"AND vehicle_type = '{p['vehicle_type']}' " if p.get("vehicle_type") else (
                f"AND vehicle_type = (smallest type with capacity >= {p['qty_mt']:g} MT) " if p.get("qty_mt") else ""))
            + f"AND lr_date > max(lr_date) - INTERVAL {p['months']} MONTH GROUP BY vendor_code HAVING lrs >= {MIN_LRS_SQL} AND last_used within 90 days ORDER BY rs_per_mt ASC" + (f" -- rate x load = rs_per_mt * {p['qty_mt']:g}" if p.get("qty_mt") else "")),
        "default_sort": {"column": "rs_per_mt", "order": "asc"}, "chart_hint": "bar", "chart_measures": ["rs_per_mt"],
    },
    "vendor_head_to_head": {
        "title": "Two vendors head to head on shared lanes",
        "data_scope": VENDOR_SCOPE,
        "columns": [col("lane", "Lane", "dimension"), col("vendor_a_rs_per_mt", "First vendor Rs/MT", "measure", "Rs"), col("vendor_b_rs_per_mt", "Second vendor Rs/MT", "measure", "Rs"),
                    col("gap_pct", "Gap", "measure", "%", 1), col("cheaper_vendor", "Cheaper vendor", "dimension")],
        "sql_equivalent": lambda p: ("SELECT lane, vehicle_type, rs_per_mt_a, rs_per_mt_b FROM (lane_vendor_rates a JOIN lane_vendor_rates b USING (lane, vehicle_type, basis)) "
                                     f"WHERE a.vendor = '{p.get('vendor_a') or 'most-shared pair'}' AND b.vendor = '{p.get('vendor_b') or 'most-shared pair'}' AND a.lrs >= {MIN_LRS_SQL} AND b.lrs >= {MIN_LRS_SQL} LIMIT {p['top']}"),
        "default_sort": {"column": "gap_pct", "order": "desc"}, "chart_hint": "table",
    },
    "vendor_quadrant_counts": {
        "title": "Vendors by spend/margin quadrant",
        "data_scope": VENDOR_SCOPE,
        "columns": [col("quadrant", "Quadrant", "dimension"), col("vendors", "Vendors", "measure", "count"), col("total_spend_rs", "Total spend", "measure", "Rs")],
        "sql_equivalent": lambda p: f"SELECT quadrant, COUNT(*) vendors, SUM(spend_rs) total_spend_rs FROM vendor_quadrants WHERE lrs >= {p['min_lrs']} GROUP BY quadrant",
        "default_sort": {"column": "vendors", "order": "desc"}, "chart_hint": "bar", "chart_measures": ["vendors"],
    },
}

assert set(CATALOGUE) == set(T.QUERIES), f"catalogue/tools.QUERIES id mismatch: {set(CATALOGUE) ^ set(T.QUERIES)}"


def _param_json(spec: dict) -> dict:
    """tools.QUERIES' internal (type, default, lo, hi, allowed_fn) tuples as plain JSON - allowed_fn is called
    and its *values* substituted, never the function object itself (that bug would otherwise leak a Python
    object repr straight through the API)."""
    out = {}
    for name, (typ, default, lo, hi, allowed) in spec.items():
        out[name] = {"type": typ, "default": default, "min": lo, "max": hi, "allowed_values": list(allowed()) if allowed else None}
    return out


def get(query_id: str) -> dict:
    if query_id not in CATALOGUE:
        raise KeyError(query_id)
    fn, spec, desc = T.QUERIES[query_id]
    return {"id": query_id, "title": CATALOGUE[query_id]["title"], "description": desc, "params": _param_json(spec),
            "columns": CATALOGUE[query_id]["columns"], "data_scope": CATALOGUE[query_id]["data_scope"],
            "default_sort": CATALOGUE[query_id]["default_sort"], "chart_hint": CATALOGUE[query_id]["chart_hint"],
            "chart_measures": CATALOGUE[query_id].get("chart_measures")}


def sql_equivalent(query_id: str, params: dict) -> str:
    return CATALOGUE[query_id]["sql_equivalent"](params)


def column_names(query_id: str) -> list[str]:
    return [c["name"] for c in CATALOGUE[query_id]["columns"]]


def list_catalogue() -> list[dict]:
    return [get(qid) for qid in CATALOGUE]
