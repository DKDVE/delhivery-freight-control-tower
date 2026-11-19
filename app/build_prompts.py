"""Generate prompts/*.md (the 10-template prompt library). Worked examples are filled from real tool outputs,
so no number in an example is typed by hand.   Run:  python -m app.build_prompts
"""
from __future__ import annotations

import json
import os

import pandas as pd

from app import tools as T

OUT = T.PROMPTS
rs = lambda x: f"₹{x:,.0f}"
cr = lambda x: f"₹{x / 1e7:,.2f} Cr"
lakh = lambda x: f"₹{x / 1e5:,.1f} lakh"


def _context() -> dict:
    ov = pd.read_csv(os.path.join(T.PROC, "vendor_overpay.csv")).sort_values("excess_rs", ascending=False)
    lane_tbl = pd.read_csv(os.path.join(T.PROC, "lane_vendor_rates.csv"))
    ctx = {}
    for _, r in ov.iterrows():
        alts = T.find_alternative_vendors(r.route, r.vehicle_type, 3, exclude_vendor=r.vendor)
        own = lane_tbl[(lane_tbl.vendor_name == r.vendor) & (lane_tbl.route == r.route) & (lane_tbl.vehicle_type == r.vehicle_type)]
        if alts["status"] == "ok" and len(own) and alts["alternatives"][0]["rs_per_mt"] < own.iloc[0].rs_per_mt:
            ctx.update(lane=r, alts=alts, own=own.iloc[0])
            break
    ctx["profile"] = T.vendor_profile(ctx["lane"].vendor)
    ctx["hub"] = T.hub_report(top=3)
    tt = pd.read_parquet(os.path.join(T.PROC, "middle_mile_trips.parquet"))
    tid = tt[tt.test].sort_values("ratio", ascending=False).iloc[0].trip_uuid
    ctx["trip"] = T.check_trip({"trip_id": tid})
    b = {"route": ctx["lane"].route, "vehicle_type": ctx["lane"].vehicle_type, "vendor": ctx["lane"].vendor, "qty": 4.0, "month": 2,
         "quoted_rs": float(ctx["lane"].actual_rs / ctx["lane"].shipments * 1.15)}
    ctx["booking"] = T.check_booking(b)
    ctx["loss"] = T.query_data("vendors_by_loss", {"top": 1})["rows"][0]
    ctx["overpay"] = T.query_data("vendor_overpay_ranking", {"top": 1})["rows"][0]
    ctx["corridor"] = T.query_data("corridors_by_overrun", {"top": 1})["rows"][0]
    ctx["dq"] = {r["issue"]: r for r in T.query_data("data_quality_summary")["rows"]}
    ship = T.pd.read_parquet(os.path.join(T.PROC, "vendor_shipments.parquet"))
    rr = ship[(ship.route == ctx["lane"].route) & (ship.vehicle_capacity > 0)]
    ctx["cap_small"] = float(rr[rr.vehicle_capacity >= 4.0].vehicle_capacity.min())
    return ctx


def _specs(c: dict) -> list[dict]:
    L, A, O, P, H = c["lane"], c["alts"]["alternatives"][0], c["own"], c["profile"], c["hub"]["hubs"][0]
    bk, tr = c["booking"], c["trip"]
    wl = P["worst_lanes"][0] if P["worst_lanes"] else {"route": "n/a", "vehicle_type": "n/a", "loss_rs": 0, "lrs": 0}
    d = bk["v2_loss_risk"]["top_drivers"]
    return [
        dict(slug="vendor_renegotiation", title="Vendor rate renegotiation request",
             purpose="Ask a vendor to move a lane's rate toward the observed benchmark. Used after check_booking or vendor_profile shows the vendor above the fair price.",
             fields=[("vendor", "vendor name"), ("route", "route code"), ("vehicle_type", "vehicle type code"), ("current_rate", "vendor's volume-weighted Rs per MT on the lane"),
                     ("benchmark_rate", "cheapest alternative vendor's Rs per MT"), ("lr_count", "vendor's LRs on the lane"), ("period", "data period")],
             body="Subject: Rate review for lane {{route}} ({{vehicle_type}})\n\nDear {{vendor}} team,\n\nOver {{period}} you carried {{lr_count}} consignments for us on lane {{route}} with vehicle type {{vehicle_type}} at an average of {{current_rate}} per MT. Other approved vendors on the same lane and vehicle type are currently moving freight at about {{benchmark_rate}} per MT.\n\nWe would like to review the rate for this lane with you. Could you share a revised proposal this week?\n\nRegards,\nFreight Procurement, Delhivery",
             example=dict(vendor=L.vendor, route=L.route, vehicle_type=L.vehicle_type, current_rate=rs(O.rs_per_mt), benchmark_rate=rs(A["rs_per_mt"]), lr_count=int(O.lr_count), period="April 2025 to March 2026"),
             failures=["Tone too aggressive if the LLM 'polishes' too hard; keep it factual.", "Benchmark can come from a single vendor with few LRs: the tool requires 10+ LRs, check lr_count before sending.",
                       "Anonymised vendor names do not map to real contracts."]),
        dict(slug="overcharge_query", title="Overcharge query on a specific lane",
             purpose="Query a quoted or invoiced rate that is well above the model's fair price for the lane.",
             fields=[("vendor", "vendor name"), ("route", "route code"), ("vehicle_type", "vehicle type code"), ("quoted_rs", "quoted amount"), ("fair_rs", "model fair price"),
                     ("overpay_pct", "percent above fair price"), ("excess_rs", "amount above fair price")],
             body="Subject: Query on quoted freight for {{route}} ({{vehicle_type}})\n\nDear {{vendor}} team,\n\nThe quote of {{quoted_rs}} for this movement is {{overpay_pct}}% above the benchmark of {{fair_rs}} we see for comparable loads on the same lane and vehicle type, a difference of {{excess_rs}}.\n\nPlease confirm the basis of the quote (distance, waiting, return load or other charges) so we can reconcile it before booking.\n\nRegards,\nFreight Procurement, Delhivery",
             example=dict(vendor=L.vendor, route=L.route, vehicle_type=L.vehicle_type, quoted_rs=rs(bk["v1_fair_price"]["quoted_rs"]), fair_rs=rs(bk["v1_fair_price"]["fair_rs"]),
                          overpay_pct=f"{bk['v1_fair_price']['overpay_pct']:.1f}", excess_rs=rs(bk["v1_fair_price"]["excess_rs"])),
             failures=["The fair price is a market median for the lane profile, not the vendor's cost: do not present it as an invoice error.", "Wide prediction range on rare lanes: check the fair_low / fair_high range.",
                       "Never send automatically; the planner approves."]),
        dict(slug="vehicle_downsizing", title="Load-consolidation / vehicle downsizing notice to the planner",
             purpose="Tell the planner that a planned load will fill little of the vehicle and suggest a smaller vehicle or combining loads.",
             fields=[("route", "route code"), ("planned_vehicle", "planned vehicle type"), ("planned_capacity", "planned capacity in MT"), ("qty", "load in MT"),
                     ("utilisation_pct", "planned utilisation"), ("suggested_capacity", "smallest capacity in MT seen on this route that fits the load"), ("underload_probability", "V3 probability")],
             body="Subject: Low planned load on {{route}}\n\nHi,\n\nThe planned load of {{qty}} MT on {{route}} fills {{utilisation_pct}}% of the {{planned_vehicle}} vehicle ({{planned_capacity}} MT). The under-load model gives a {{underload_probability}}% chance this truck leaves below 60% full.\n\nPlease consider a vehicle of about {{suggested_capacity}} MT, which has been used on this route, or combining this load with another shipment on the same lane.",
             example=dict(route=L.route, planned_vehicle=L.vehicle_type, planned_capacity=bk["inputs"]["vehicle_capacity"], qty=4.0,
                          utilisation_pct=f"{4.0 / bk['inputs']['vehicle_capacity'] * 100:.0f}", suggested_capacity=c["cap_small"],
                          underload_probability=f"{bk['v3_underload_risk']['probability'] * 100:.0f}"),
             failures=["The suggested capacity comes from history on the route; the vehicle may not be available.", "Under-load flag is a probability, not a certainty.", "Do not claim a saving unless a tool returned it."]),
        dict(slug="vendor_quarterly_summary", title="Vendor quarterly performance summary",
             purpose="One-paragraph performance summary for a vendor review meeting.",
             fields=[("vendor", "vendor name"), ("period", "data period"), ("lrs", "number of LRs"), ("spend", "freight paid"), ("margin_pct", "margin % on billed value"),
                     ("loss_share_pct", "share of loss-making LRs"), ("overpay_pct", "percent above fair price in the test quarter"), ("worst_lane", "lane with the largest losses")],
             body="Vendor summary: {{vendor}} ({{period}})\n\n{{vendor}} handled {{lrs}} consignments with freight paid of {{spend}}. Margin on billed value was {{margin_pct}}%, and {{loss_share_pct}}% of consignments lost money. In the latest quarter its freight ran {{overpay_pct}}% above the benchmark price for comparable loads. The lane with the largest losses is {{worst_lane}}.",
             example=dict(vendor=P["vendor"], period=P["period"], lrs=P["lrs"], spend=cr(P["spend_rs"]), margin_pct=f"{P['margin_pct']:.1f}", loss_share_pct=f"{P['loss_share_pct']:.1f}",
                          overpay_pct=f"{P['overpay_pct']:.1f}", worst_lane=f"{wl['route']} / {wl['vehicle_type']}"),
             failures=["Vendors with fewer than 10 LRs are not ranked; do not draft a summary for them.", "Margin is on valid-cost LRs only (imputed freight excluded).", "overpay_pct is None for vendors without test-quarter shipments."]),
        dict(slug="repeated_loss_escalation", title="Escalation for a vendor with repeated loss-making LRs",
             purpose="Escalate to the procurement head when a vendor's consignments keep losing money.",
             fields=[("vendor", "vendor name"), ("lrs", "number of LRs"), ("loss_share_pct", "share of loss-making LRs"), ("loss_rs", "total Rs lost on loss-making LRs"),
                     ("worst_lane", "lane with the largest losses"), ("worst_lane_loss", "Rs lost on that lane"), ("period", "data period")],
             body="Subject: Escalation: repeated loss-making consignments with {{vendor}}\n\nOver {{period}}, {{loss_share_pct}}% of the {{lrs}} consignments with {{vendor}} lost money, a total of {{loss_rs}}. The largest single lane is {{worst_lane}}, with {{worst_lane_loss}} lost.\n\nRecommended next step for review: renegotiate the rate card for the worst lanes or move that volume to another approved vendor. No vendor action is taken until you approve.",
             example=dict(vendor=P["vendor"], lrs=P["lrs"], loss_share_pct=f"{P['loss_share_pct']:.1f}", loss_rs=cr(P["loss_rs"]), worst_lane=f"{wl['route']} / {wl['vehicle_type']}",
                          worst_lane_loss=lakh(wl["loss_rs"]), period=P["period"]),
             failures=["Must not recommend terminating or blacklisting a vendor (never automated).", "Losses depend on the customer billing as well as vendor cost.", "A loss-making LR is not necessarily the vendor's fault."]),
        dict(slug="hub_delay_alert", title="Hub delay alert to the hub manager",
             purpose="Alert a hub manager that legs starting at their hub lose more time than the network median.",
             fields=[("hub_name", "hub name"), ("legs", "test legs"), ("mean_handling_min", "mean handling minutes"), ("network_median_min", "network median handling minutes"),
                     ("excess_hours", "hours above the network median"), ("excess_cost", "cost of the excess hours (sourced synthetic rate)")],
             body="Subject: Handling time at {{hub_name}}\n\nHi,\n\nLegs starting at {{hub_name}} averaged {{mean_handling_min}} minutes of handling beyond driving time across {{legs}} legs, against a network median of {{network_median_min}} minutes. That is about {{excess_hours}} hours above the median, roughly {{excess_cost}} at the planning rate.\n\nCould you review loading and dispatch practices and share what is driving it?",
             example=dict(hub_name=H["source_name"], legs=H["legs"], mean_handling_min=f"{H['mean_actual_handling_min']:.0f}", network_median_min=f"{c['hub']['network_median_handling_min']:.0f}",
                          excess_hours=f"{H['excess_hours']:.0f}", excess_cost=lakh(H["excess_rs_base"])),
             failures=["Rupee figure uses sourced synthetic rates, not actual costs.", "Based on one week of 2018 data; say so if sent outside the project.", "Handling time = scan time minus driving time, so recording errors show up as delay."]),
        dict(slug="customer_eta_note", title="Revised ETA note to the customer",
             purpose="Plain-language note giving a customer a realistic travel time instead of the planned one.",
             fields=[("trip_ref", "trip reference"), ("planned_hours", "planned travel hours"), ("expected_hours", "model's expected travel hours"), ("range_low_hours", "low end of range"),
                     ("range_high_hours", "high end of range")],
             body="Subject: Update on your shipment {{trip_ref}}\n\nHello,\n\nThis shipment was planned for about {{planned_hours}} hours on the road. Based on how similar journeys have run, we now expect around {{expected_hours}} hours, most likely between {{range_low_hours}} and {{range_high_hours}} hours. We will update you if anything changes.\n\nThank you for your patience.",
             example=dict(trip_ref=tr["trip_id"], planned_hours=f"{tr['planned_min'] / 60:.1f}", expected_hours=f"{tr['predicted_min'] / 60:.1f}",
                          range_low_hours=f"{tr['predicted_low_min'] / 60:.1f}", range_high_hours=f"{tr['predicted_high_min'] / 60:.1f}"),
             failures=["The range is indicative: the model's nominal 80% interval covers about 67% of trips.", "Customer-facing: do not mention model flags or internal drivers.", "Based on 2018 data."]),
        dict(slug="weekly_digest", title="Weekly control-tower digest (top risks)",
             purpose="Short digest of the biggest cost and delay risks, built from query_data results.",
             fields=[("period", "reporting period"), ("top_overpay_vendor", "vendor with the largest excess"), ("top_overpay_excess", "that vendor's excess"), ("top_loss_vendor", "vendor with the largest losses"),
                     ("top_loss_amount", "its loss"), ("worst_hub", "hub with the most excess hours"), ("worst_hub_hours", "its excess hours"), ("worst_corridor", "corridor with the highest median overrun"),
                     ("worst_corridor_ratio", "its median actual / planned")],
             body="Control-tower digest: {{period}}\n\n1. Price: {{top_overpay_vendor}} shows the largest excess over benchmark price ({{top_overpay_excess}}).\n2. Losses: {{top_loss_vendor}} has the largest loss on loss-making consignments ({{top_loss_amount}}).\n3. Hubs: {{worst_hub}} has the most excess handling time ({{worst_hub_hours}} hours above the median).\n4. Corridors: {{worst_corridor}} runs at a median {{worst_corridor_ratio}}x its planned time.\n\nAll figures come from the approved queries; actions need your approval.",
             example=dict(period="latest data period", top_overpay_vendor=c["overpay"]["vendor"], top_overpay_excess=cr(c["overpay"]["excess_rs"]), top_loss_vendor=c["loss"]["vendor_name"],
                          top_loss_amount=cr(c["loss"]["loss_rs"]), worst_hub=H["source_name"], worst_hub_hours=f"{H['excess_hours']:.0f}", worst_corridor=c["corridor"]["corridor"],
                          worst_corridor_ratio=f"{c['corridor']['median_actual_over_planned']:.1f}"),
             failures=["Mixes two datasets (2025-26 vendor file and 2018 trips): label them if the audience might confuse them.", "A single top item per list hides the long tail.", "Not a forecast."]),
        dict(slug="explain_flag", title="Explain a model flag to a non-technical manager",
             purpose="Turn a V2 loss-risk flag and its top drivers into plain English with one recommended action.",
             fields=[("route", "route code"), ("vendor", "vendor name"), ("probability_pct", "loss probability"), ("threshold_pct", "flag threshold"), ("driver1", "top driver"),
                     ("driver2", "second driver"), ("driver3", "third driver"), ("recommended_action", "one action")],
             body="Why this booking was flagged\n\nBooking on {{route}} with {{vendor}} has a {{probability_pct}}% estimated chance of losing money (flag threshold {{threshold_pct}}%).\n\nMain reasons: {{driver1}}; {{driver2}}; {{driver3}}.\n\nSuggested action: {{recommended_action}}. A person decides; nothing changes automatically.",
             example=dict(route=L.route, vendor=L.vendor, probability_pct=f"{bk['v2_loss_risk']['probability'] * 100:.0f}", threshold_pct=f"{bk['v2_loss_risk']['threshold'] * 100:.0f}",
                          driver1=f"{d[0]['feature']} = {d[0]['value']} ({d[0]['effect']})", driver2=f"{d[1]['feature']} = {d[1]['value']} ({d[1]['effect']})",
                          driver3=f"{d[2]['feature']} = {d[2]['value']} ({d[2]['effect']})", recommended_action="renegotiate the rate for this lane or reassign the vendor"),
             failures=["SHAP drivers describe the model, not causes.", "A probability is not a guarantee.", "Driver values like 'route' are codes; add plain names where known."]),
        dict(slug="data_quality_report", title="Data-quality issue report",
             purpose="Report a data issue (for example vehicle_capacity = 0) with its size and effect on the models.",
             fields=[("issue", "short name of the issue"), ("field", "affected column"), ("count", "affected rows"), ("share_pct", "share of all rows"), ("impact", "how it affects the analysis"),
                     ("suggested_fix", "suggested fix at source")],
             body="Data-quality report: {{issue}}\n\nColumn {{field}} has {{count}} affected rows ({{share_pct}}% of the vendor freight file). Impact: {{impact}}.\n\nSuggested fix: {{suggested_fix}}.",
             example=dict(issue="Vehicle capacity zero or missing", field="vehicle_capacity", count=c["dq"]["vehicle_capacity_zero_or_missing"]["count"],
                          share_pct=f"{c['dq']['vehicle_capacity_zero_or_missing']['share_pct']:.1f}",
                          impact="truck utilisation cannot be computed, so those shipments are excluded from the under-load model",
                          suggested_fix="make vehicle capacity mandatory when a shipment is created, or derive it from the vehicle type"),
             failures=["Counts are from the raw file before cleaning.", "Do not guess the cause of the issue; the fix must come from the data owner.", "Keep the report factual."]),
    ]


def build():
    os.makedirs(OUT, exist_ok=True)
    c = _context()
    nl = "\n"
    for i, s in enumerate(_specs(c), 1):
        fields = nl.join(f"- `{n}`: {desc}" for n, desc in s["fields"])
        path = os.path.join(OUT, f"{i:02d}_{s['slug']}.md")
        example = json.dumps(s["example"], indent=2, default=str, ensure_ascii=False)
        parts = [
            f"# {s['title']}", "",
            "## Purpose", s["purpose"], "",
            "## Input fields", fields, "",
            "## Prompt text", "```text", s["body"], "```", "",
            "The LLM may polish the tone of this text but must keep every `{{placeholder}}` exactly as written; numbers are inserted by code afterwards.", "",
            "## Worked example", "Input:", "```json", example, "```", "",
            "Output (before any LLM polish):", "```text", "@@OUT@@", "```", "",
            "## Known failure modes", *[f"- {f}" for f in s["failures"]], "",
        ]
        pre = nl.join(parts)
        open(path, "w", encoding="utf8").write(pre)
        out = T.draft_message(f"{i:02d}", s["example"])["text"]
        open(path, "w", encoding="utf8").write(pre.replace("@@OUT@@", out))
    print("wrote", i, "templates to", OUT)


if __name__ == "__main__":
    build()
