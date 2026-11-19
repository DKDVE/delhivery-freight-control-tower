"""Freight Control Tower agent.

Rules and models decide; the LLM only explains, drafts and (in Ask the data) picks which approved query to run.
Every number in an explanation must come from a tool output of the same turn (guardrails.check_reply); if it does not,
the rule-based template text is shown instead and a `hallucination_blocked` event is logged.
A person approves every action: the agent never sends, books or changes anything.
"""
from __future__ import annotations

import json
import os
import re

from app import guardrails as G
from app import placeholders as P
from app import tools as T
from app.llm import BANNER, LLM, LLMUnavailable

MAX_TOOL_CALLS = 5
FLAG_OVERPAY_PCT = 10.0
CHEAPER_BY = 0.05  # an alternative must be at least 5% below the current lane rate to justify reassigning
ASK_SCHEMAS = [t for t in T.TOOL_SCHEMAS if t["function"]["name"] == "query_data"]
SEP ="\n\n-----\n\n"
REFUSAL = "I can't answer that from the approved queries."
FLAG_BELOW_PCT = 30.0  # a quote this far below the fair price is suspicious, not a bargain
ACTIONS = ("downsize vehicle", "reassign vendor", "renegotiate rate", "reprice or top up", "verify quote", "proceed")


def rs(v) -> str:
    return f"₹{v:,.0f}"


class Turn:
    """One agent turn: records every tool call and enforces the 5-call limit."""

    def __init__(self):
        self.calls: list[dict] = []

    def call(self, name: str, args: dict | None = None, fn=None, **kw) -> dict:
        if len(self.calls) >= MAX_TOOL_CALLS:
            raise RuntimeError(f"tool-call limit of {MAX_TOOL_CALLS} per turn reached")
        args = args or {}
        out = fn(**args, **kw) if fn else T.call_tool(name, args)
        if out is None:
            out = {"status": "insufficient data"}
        self.calls.append({"name": name, "args": args, "output": out})
        return out

    @property
    def outputs(self) -> list:
        return [c["output"] for c in self.calls]


def _result(page, turn, text, action, events, model_used, mode, banner, inputs, extra=None, draft=None):
    run_id = G.log_run(page, inputs, turn.calls, model_used, text, events, mode, banner, extra={"recommended_action": action, **(extra or {})})
    return {"run_id": run_id, "recommended_action": action, "text": text, "draft": draft, "banner": banner, "guardrail_events": events,
            "model_used": model_used, "mode": mode, "tools_called": turn.calls}


def _mode_name(llm: LLM) -> str:
    return "DEMO" if llm.demo else "LIVE"


def _model_used(llm: LLM, routed: str | None) -> str:
    """freellmapi's X-Routed-Via header names the model it actually used; a direct provider (Groq) sends no such
    header, so fall back to '<provider>/<pinned model>' so the audit record still says who answered."""
    return routed or f"{llm.provider}/{llm.model}"


def decide_booking(check: dict, alts: dict | None, cur_rate: dict | None, smaller: dict | None = None) -> dict:
    """Booking policy as an explicit priority order. The first rule that fires decides; the reason names that signal.
    1. Under-load flag (V3) and a smaller vehicle fits the load          -> downsize vehicle
    2. Quote more than 10% above the fair price (V1)                     -> reassign vendor if a cheaper vendor with 10+ LRs exists, else renegotiate rate
    3. Loss-risk flag (V2) with the price in range                       -> reprice or top up
    4. Quote more than 30% below the fair price                          -> verify quote
    5. Otherwise                                                         -> proceed
    `smaller` is the smallest vehicle capacity seen on this route that still fits the load (tools.smallest_capacity_for)."""
    v2, v1, v3 = check["v2_loss_risk"], check["v1_fair_price"], check["v3_underload_risk"]
    inputs = check["inputs"]
    over = v1.get("overpay_pct")
    over_flag = over is not None and over > FLAG_OVERPAY_PCT
    below_flag = over is not None and over < -FLAG_BELOW_PCT
    under_flag = bool(v3["flag"])
    planned_cap = inputs.get("vehicle_capacity")
    smaller_fits = bool(smaller and smaller.get("capacity") and (planned_cap is None or smaller["capacity"] < planned_cap))
    best = (alts or {}).get("alternatives", [])
    best = best[0] if best else None
    cur = cur_rate["rs_per_mt"] if cur_rate else (v1.get("quoted_rs") / inputs["qty"] if v1.get("quoted_rs") else None)
    cheaper = bool(best and cur and best["rs_per_mt"] < cur)
    if under_flag and smaller_fits:
        action, signal = "downsize vehicle", "underload_smaller_vehicle"
    elif over_flag:
        action, signal = ("reassign vendor" if cheaper else "renegotiate rate"), "quote_above_fair"
    elif v2["flag"]:
        action, signal = "reprice or top up", "loss_risk"
    elif below_flag:
        action, signal = "verify quote", "quote_below_fair"
    else:
        action, signal = "proceed", "none"
    return {"action": action, "signal": signal, "flagged": bool(v2["flag"]) or over_flag, "over_flag": over_flag, "below_flag": below_flag, "under_flag": under_flag,
            "smaller_vehicle": smaller if smaller_fits else None, "cheaper_alternative": cheaper, "best_alternative": best, "current_rs_per_mt": cur}


def rule_reason(check: dict, d: dict) -> str:
    """The sentence after 'because': it names the signal that triggered the action. Numbers come straight from the tool outputs."""
    v2, v1, v3 = check["v2_loss_risk"], check["v1_fair_price"], check["v3_underload_risk"]
    sig, best = d["signal"], d["best_alternative"]
    if sig == "underload_smaller_vehicle":
        cap = d["smaller_vehicle"]["capacity"]
        return (f"the under-load flag is raised ({v3['probability'] * 100:.1f}% chance the truck leaves below 60% full) and a {cap:g} MT vehicle fits the "
                f"{check['inputs']['qty']:g} MT load, so downsize or consolidate with another load")
    if sig == "quote_above_fair":
        gap = f"the quote is {v1['overpay_pct']:.1f}% above the fair price"
        if d["cheaper_alternative"]:
            return f"{gap}, and {best['vendor_name']} moves this lane at {rs(best['rs_per_mt'])} per MT against {rs(d['current_rs_per_mt'])} per MT now"
        return f"{gap}, and no cheaper vendor with enough history exists on this lane"
    if sig == "loss_risk":
        also = []
        if d["below_flag"]:
            also.append(f"the quote is also {abs(v1['overpay_pct']):.1f}% below the fair price, so confirm what it covers")
        if d["under_flag"]:
            also.append("the under-load flag is also raised but no smaller vehicle fits this load")
        return (f"the loss-risk flag is raised ({v2['probability'] * 100:.1f}% against a threshold of {v2['threshold'] * 100:.1f}%) and the quote is not more than 10% above the fair price, "
                "so reprice the booking or top up the load" + ("; " + "; ".join(also) if also else ""))
    if sig == "quote_below_fair":
        return f"the quote is {abs(v1['overpay_pct']):.1f}% below the fair price, which is too low to be realistic, so confirm what it covers"
    note = ", although the under-load flag is raised no smaller vehicle fits this load" if d["under_flag"] else ""
    return f"no rule fires: loss risk is below its threshold and the quote is within the expected range{note}"


def rule_summary(action: str, check: dict, d: dict) -> str:
    """Two sentences, numbers straight from the tool outputs. Used in DEMO_MODE and as the fallback."""
    v2, v1 = check["v2_loss_risk"], check["v1_fair_price"]
    s1 = f"Estimated chance this booking loses money is {v2['probability'] * 100:.1f}% (flag threshold {v2['threshold'] * 100:.1f}%)"
    if v1.get("quoted_rs"):
        s1 += (f"; the quote of {rs(v1['quoted_rs'])} is {abs(v1['overpay_pct']):.1f}% {'above' if v1['overpay_pct'] >= 0 else 'below'} "
               f"the fair price of {rs(v1['fair_rs'])}")
    else:
        s1 += f"; the fair price is about {rs(v1['fair_rs'])}"
    s1 += "."
    return f"{s1} Recommendation: {action}, because {rule_reason(check, d)}."


def _drivers_text(check: dict) -> list[str]:
    out = []
    for d in check["v2_loss_risk"]["top_drivers"][:3]:
        v = d["value"]
        v = f"{v:,.1f}" if isinstance(v, float) else str(v)
        out.append(f"{d['feature']} = {v} ({d['effect']})")
    return out + ["n/a"] * (3 - len(out))


def _pick_draft(turn: Turn, action: str, check: dict, d: dict, llm: LLM) -> dict | None:
    """Choose the template by the signal that fired; returns the draft_message output or None when no message is needed."""
    inp, v1, v2, v3 = check["inputs"], check["v1_fair_price"], check["v2_loss_risk"], check["v3_underload_risk"]
    polish = llm.polisher()
    if action in ("proceed", "verify quote"):
        return None
    if action == "downsize vehicle" and d.get("smaller_vehicle"):
        cap = d["smaller_vehicle"]
        return turn.call("draft_message", {"template_id": "03", "fields": {
            "route": inp["route"], "planned_vehicle": inp["vehicle_type"], "planned_capacity": f"{inp['vehicle_capacity']:g}",
            "qty": f"{inp['qty']:g}", "utilisation_pct": f"{v3['expected_utilisation_pct']:.1f}",
            "suggested_capacity": f"{cap['capacity']:g}", "underload_probability": f"{v3['probability'] * 100:.1f}"}}, fn=T.draft_message, polisher=polish)
    if d["over_flag"] and v1.get("quoted_rs"):
        return turn.call("draft_message", {"template_id": "02", "fields": {
            "vendor": inp["vendor"], "route": inp["route"], "vehicle_type": inp["vehicle_type"], "quoted_rs": rs(v1["quoted_rs"]),
            "fair_rs": rs(v1["fair_rs"]), "overpay_pct": f"{v1['overpay_pct']:.1f}", "excess_rs": rs(v1["excess_rs"])}}, fn=T.draft_message, polisher=polish)
    dr = _drivers_text(check)
    return turn.call("draft_message", {"template_id": "09", "fields": {
        "route": inp["route"], "vendor": inp["vendor"], "probability_pct": f"{v2['probability'] * 100:.1f}", "threshold_pct": f"{v2['threshold'] * 100:.1f}",
        "driver1": dr[0], "driver2": dr[1], "driver3": dr[2], "recommended_action": action}}, fn=T.draft_message, polisher=polish)


def _system_prompt() -> str:
    return open(os.path.join(G.ROOT, "prompts", "system_agent.md"), encoding="utf8").read()


def _explain_slots(llm: LLM, turn: Turn, instruction: str, values: dict, descs: dict, user_ctx) -> tuple[str | None, str | None, list, dict]:
    """The LLM writes the explanation with {slot} placeholders; code fills them from the tool results.
    Returns (final text or None when blocked, routed_via, guardrail events, audit extras). The number check then runs on the filled text as a backstop."""
    user = (f"{instruction}\n\nSlots you may use. Code fills each one with the real figure or name; you never see the values:\n{P.catalogue(descs)}\n\n"
            "Write exactly two short sentences. Do not type any digit yourself: every figure, name and unit must be one of the slots above, copied exactly with its curly braces. "
            "Do not invent slots, and do not state trends, rankings or comparisons that the slots do not carry.")
    r = llm.chat([{"role": "system", "content": _system_prompt()}, {"role": "user", "content": user}])
    raw = (r.content or "").strip()
    extra = {"raw_llm_text": raw, "slots_used": P.slots_in(raw), "literal_numbers_typed": P.typed_numbers(raw)}
    if not raw:
        return None, r.routed_via, [{"event": "empty_reply"}], extra
    filled, unknown = P.fill(raw, values)
    events = [{"event": "unknown_placeholder", "names": unknown, "blocked_reply": raw[:400]}] if unknown else []
    if extra["literal_numbers_typed"]:  # digits AND number words ('five', 'one lane', '3rd'): every figure must be a code-filled slot, even one that happens to match a tool output
        events.append({"event": "hallucination_blocked", "numbers_not_in_tool_outputs": extra["literal_numbers_typed"], "blocked_reply": raw[:400]})
    events += G.check_reply(filled, turn.outputs, user_ctx)
    return (None if events else filled), r.routed_via, events, extra


def run_booking(booking: dict, llm: LLM | None = None) -> dict:
    llm = llm or LLM()
    turn = Turn()
    events, banner, routed, extra_llm = [], None, None, {}
    check = turn.call("check_booking", {"booking": booking})
    if "error" in check:
        return _result("Booking check", turn, f"Cannot score this booking: {check['error']}", None, events, "rules", _mode_name(llm), None, booking)
    inp = check["inputs"]
    alts = turn.call("find_alternative_vendors", {"route": inp["route"], "vehicle_type": inp["vehicle_type"], "k": 3, "exclude_vendor": inp["vendor"]})
    cur = turn.call("lane_rate", {"route": inp["route"], "vehicle_type": inp["vehicle_type"], "vendor": inp["vendor"]}, fn=T.lane_rate)
    cur = cur if cur.get("rs_per_mt") else None
    smaller = None
    if check["v3_underload_risk"]["flag"]:  # only needed when the under-load rule could fire
        smaller = turn.call("smallest_capacity_for", {"route": inp["route"], "qty": inp["qty"]}, fn=T.smallest_capacity_for)
        smaller = smaller if smaller.get("capacity") else None
    d = decide_booking(check, alts, cur, smaller)
    action = d["action"]
    text = rule_summary(action, check, d)
    if not llm.demo:
        try:
            vals, descs = P.booking_slots(check, alts, cur, d)
            live, routed, ev, extra_llm = _explain_slots(
                llm, turn, f"Explain this booking recommendation. The rules engine chose: {action}. Facts: {P.booking_facts(check, d)}.", vals, descs, booking)
            events += ev
            if live:
                text = live
        except LLMUnavailable as err:
            events.append({"event": "llm_unavailable", "detail": str(err)[:300]})
            banner = BANNER
    draft = _pick_draft(turn, action, check, d, LLM(demo=True) if banner else llm)
    if draft and str(draft.get("note") or "").startswith("polish failed"):
        banner = banner or BANNER
    mode = _mode_name(llm) if not banner else "DEMO (fallback)"
    model_used = "rules (DEMO_MODE)" if llm.demo else _model_used(llm, routed) if not banner else "rules (LLM unavailable)"
    res = _result("Booking check", turn, text, action, events, model_used, mode, banner, booking,
                  extra={"warnings": check.get("warnings", []), "assumed_defaults": check.get("assumed_defaults", {}), "signal": d["signal"], **extra_llm},
                  draft=draft["text"] if draft and draft.get("status") == "ok" else None)
    res["policy"] = d
    return res


def trip_summary(t: dict) -> str:
    sd = t["severe_delay"]
    s1 = (f"Planned {t['planned_min']:,.0f} min; predicted {t['predicted_min']:,.0f} min (range {t['predicted_low_min']:,.0f} to {t['predicted_high_min']:,.0f}), "
          f"{t['predicted_vs_plan_ratio']:.2f}x the plan.")
    if sd["flag"]:
        s2 = f"Severe-delay risk is {sd['probability'] * 100:.1f}% (flag threshold {sd['threshold'] * 100:.1f}%): alert the dispatcher and tell the receiving hub; a person decides."
    else:
        s2 = f"Severe-delay risk is {sd['probability'] * 100:.1f}%, below the flag threshold of {sd['threshold'] * 100:.1f}%: no alert needed."
    return f"{s1} {s2}"


def run_trip(trip: dict, llm: LLM | None = None) -> dict:
    llm = llm or LLM()
    turn = Turn()
    events, banner, routed, extra_llm = [], None, None, {}
    t = turn.call("check_trip", {"trip": trip})
    if "error" in t or t.get("status") != "ok":
        return _result("Trip alert", turn, f"Cannot score this trip: {t.get('error') or t.get('message')}", None, events, "rules", _mode_name(llm), None, trip)
    action = "alert" if t["severe_delay"]["flag"] else "no alert"
    hub = turn.call("hub_report", {"center": t["first_source_center"]}) if t.get("first_source_center") else {}
    text = trip_summary(t)
    if not llm.demo:
        try:
            vals, descs = P.trip_slots(t)
            live, routed, ev, extra_llm = _explain_slots(
                llm, turn, f"Explain this trip check. The rules engine says: {action} (alert means the severe-delay flag was raised).", vals, descs, trip)
            events += ev
            if live:
                text = live
        except LLMUnavailable as err:
            events.append({"event": "llm_unavailable", "detail": str(err)[:300]})
            banner = BANNER
    drafts = []
    pol = None if banner else llm.polisher()
    if action == "alert":
        h = (hub.get("hubs") or [None])[0] if hub.get("status") == "ok" else None
        if h:
            drafts.append(turn.call("draft_message", {"template_id": "06", "fields": {
                "hub_name": h["source_name"], "legs": h["legs"], "mean_handling_min": h["mean_actual_handling_min"],
                "network_median_min": hub["network_median_handling_min"], "excess_hours": h["excess_hours"],
                "excess_cost": rs(h["excess_rs_base"])}}, fn=T.draft_message, polisher=pol))
        drafts.append(turn.call("draft_message", {"template_id": "07", "fields": {
            "trip_ref": t["trip_id"] or "manual trip", "planned_hours": f"{t['planned_min'] / 60:.1f}", "expected_hours": f"{t['predicted_min'] / 60:.1f}",
            "range_low_hours": f"{t['predicted_low_min'] / 60:.1f}", "range_high_hours": f"{t['predicted_high_min'] / 60:.1f}"}}, fn=T.draft_message, polisher=pol))
    if any(str(d.get("note") or "").startswith("polish failed") for d in drafts):
        banner = banner or BANNER
    model_used = "rules (DEMO_MODE)" if llm.demo else ("rules (LLM unavailable)" if banner else _model_used(llm, routed))
    return _result("Trip alert", turn, text, action, events, model_used, "DEMO (fallback)" if banner else _mode_name(llm), banner, trip, extra=extra_llm,
                   draft=SEP.join(d["text"] for d in drafts if d.get("status") == "ok") or None)


_ROUTER = [  # (query_id, keywords that must all/any match) - first match wins; deterministic in DEMO_MODE
    ("data_quality_summary", [("data quality", "missing", "zero freight", "zero capacity", "bad data")]),
    ("margin_by_zone", [("margin",), ("zone",)]),
    ("monthly_margin_trend", [("margin",), ("trend", "month")]),
    ("vendor_price_gap_lanes", [("lane", "route"), ("gap", "spread", "widest", "cheapest and priciest", "price difference")]),
    ("top_loss_making_lanes", [("lane", "route"), ("loss", "losing", "lose")]),
    ("top_overpaying_lanes", [("lane",), ("overpa", "excess", "expensive")]),
    ("vendor_quadrant_counts", [("quadrant", "segment")]),
    ("vendor_overpay_ranking", [("vendor", "who"), ("overpa", "excess", "fair price")]),
    ("underload_rate_by_plant", [("plant",), ("underload", "under-load", "under load")]),
    ("underloaded_lanes", [("underload", "under-load", "under load", "empty", "half full", "below 60", "low load", "utilis")]),
    ("loss_share_by_vehicle_type", [("vehicle", "truck"), ("loss", "losing", "lose")]),
    ("vendors_by_loss", [("vendor",), ("loss", "losing", "lose", "worst")]),
    ("hubs_by_delay", [("hub", "center", "centre"), ("delay", "slow", "handling")]),
    ("route_type_overrun", [("ftl", "carting", "route type", "route-type"), ("overrun", "delay", "late", "slow", "compar")]),
    ("corridors_by_overrun", [("corridor", "route", "state"), ("overrun", "delay", "late", "slow")]),
]
_ACTION_REQUEST = re.compile(r"\b(send|email|mail|message|terminate|blacklist|cancel|book|reassign|change|update|delete|pay)\b", re.I)


def is_action_request(q: str) -> bool:
    """Orders to send, cancel, blacklist, change... The app only drafts; these are refused before any tool or LLM call."""
    low = q.lower()
    return bool(_ACTION_REQUEST.search(low)) and not re.search(r"\b(which|who|what|how|show|list|rank)\b", low)


_LANE_WORDS = ("cheapest", "cheaper", "cheap", "lowest", "best rate", "best price", "vendor rate", "vendors on", "rate from", "rates from", "compare vendors on", "rank vendors")
_H2H_WORDS = ("compare", " vs ", "versus", "head to head", "head-to-head", "against", "between")


def _norm_place(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _lane_entities(low: str) -> tuple[str, str] | None:
    """('HAZIRA', 'KHADOLI') from 'cheapest vendor from Hazira to Khadoli', or None unless both are real origin / destination places."""
    if not any(w in low for w in _LANE_WORDS):
        return None
    from app import lanes as LN
    deps, dests = LN._places()
    nd = {_norm_place(n): n for n in deps}
    nt = {_norm_place(n): n for n in dests}
    text = f" {_norm_place(low)} "
    best = None
    for a, an in nd.items():
        i = text.find(f" {a} to ")
        if i < 0:
            continue
        rest = text[i + len(a) + 5:]
        for b, bn in nt.items():
            if a != b and (rest.startswith(b + " ") or rest == b) and (best is None or len(a) + len(b) > best[0]):
                best = (len(a) + len(b), an, bn)
    return (best[1], best[2]) if best else None


def _vendor_pair(low: str) -> tuple[str, str] | None:
    codes = list(dict.fromkeys(c.upper() for c in re.findall(r"\bv\d{3,}\b", low)))
    return (codes[0], codes[1]) if len(codes) >= 2 and any(w in f" {low} " for w in _H2H_WORDS) else None


_LOAD_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:mt|tonnes?|tons?)\b", re.I)


def question_load(q: str) -> float | None:
    """A load in MT named in the question ('15 MT', '22.5 tonnes'), or None."""
    m = _LOAD_RE.search(q or "")
    return float(m.group(1)) if m else None


def route_question(q: str) -> tuple[str | None, dict]:
    low = q.lower()
    if is_action_request(q):
        return None, {}
    lane = _lane_entities(low)
    if lane:
        params = {"origin": lane[0], "destination": lane[1]}
        vt = re.search(r"\b((?:sb|ob|cb|bu)\d{2})\b", low)
        if vt:
            params["vehicle_type"] = vt.group(1).upper()
        load = question_load(low)
        if load:
            params["qty_mt"] = load
        return "lane_vendor_ranking", params
    pair = _vendor_pair(low)
    if pair:
        return "vendor_head_to_head", {"vendor_a": pair[0], "vendor_b": pair[1]}
    for qid, groups in _ROUTER:
        if all(any(k in low for k in g) for g in groups):
            params = {}
            m = re.search(r"\btop\s+(\d+)\b", low)
            if m and "top" in T.QUERIES[qid][1]:
                params["top"] = int(m.group(1))
            if "zone" in T.QUERIES[qid][1]:
                z = [z for z in T._zones() if re.search(rf"\b{re.escape(z.lower())}\b", low)]
                if z:
                    params["zone"] = z[0]
            if "quarter" in T.QUERIES[qid][1]:
                m = re.search(r"\b(20\d\d)\s*q([1-4])\b", low)
                if m:
                    params["quarter"] = f"{m.group(1)}Q{m.group(2)}"
            return qid, params
    return None, {}


def _num_text(v) -> str:
    if isinstance(v, float):
        return f"{v:,.1f}" if abs(v) < 1e6 else f"{v:,.0f}"
    return str(v)


def render_rows(out: dict, limit: int = 10) -> str:
    rows = out.get("rows", [])[:limit]
    lines = [f"{i}. " + "; ".join(f"{k} {_num_text(v)}" for k, v in r.items() if v is not None) for i, r in enumerate(rows, 1)]
    return "\n".join(lines) if lines else "No rows returned (insufficient data)."


def refusal_text() -> str:
    qs = "; ".join(f"{q['query_id']} ({q['description'].rstrip('.')})" for q in T.list_queries())
    return f"{REFUSAL} Available queries: {qs}."


def run_ask(question: str, llm: LLM | None = None) -> dict:
    llm = llm or LLM()
    turn = Turn()
    events, routed = [], None
    inputs = {"question": question}

    def demo_answer():
        qid, params = route_question(question)
        if qid is None:
            events.append({"event": "refused", "reason": "not an approved query or an action request"})
            return refusal_text()
        out = turn.call("query_data", {"query_id": qid, "params": params})
        if "error" in out:
            return f"I couldn't run that query: {out['error']}"
        return f"{T.QUERIES[qid][2]}\n{render_rows(out)}"

    if is_action_request(question):
        events.append({"event": "action_refused", "reason": "the app only drafts; a person sends, books or changes anything"})
        return _result("Ask the data", turn, refusal_text(), None, events, "rules (pre-filter)", _mode_name(llm), None, inputs)
    if llm.demo:
        text = demo_answer()
        return _result("Ask the data", turn, text, None, events, "rules (DEMO_MODE)", "DEMO", None, inputs)
    try:
        msgs = [{"role": "system", "content": _system_prompt()}, {"role": "user", "content": question}]
        text, slots = None, None
        for _ in range(MAX_TOOL_CALLS + 1):
            r = llm.chat(msgs, tools=ASK_SCHEMAS if len(turn.calls) < MAX_TOOL_CALLS and slots is None else None)
            routed = r.routed_via or routed
            if not r.tool_calls:
                text = (r.content or "").strip()
                break
            msgs.append({"role": "assistant", "content": r.content, "tool_calls": [
                {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}} for c in r.tool_calls]})
            for c in r.tool_calls:
                if c["name"] != "query_data":  # Ask the data answers only through the approved queries
                    out = {"error": "only query_data is available here"}
                elif slots is not None or len(turn.calls) >= MAX_TOOL_CALLS:
                    out = {"error": "only one query per question"}
                else:
                    try:
                        args = json.loads(c["arguments"] or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    out = turn.call("query_data", args)
                    if out.get("status") == "ok" and out.get("rows"):
                        slots = P.ask_slots(out)
                        # the LLM gets slot names only, never the rows: code fills the values in
                        out = {"status": "ok", "query_id": out["query_id"], "instruction": "Write the answer using only these {slots} (replace <i> by the row number, 1 to n_rows). "
                               "Type no digits, names or figures yourself, and state no trends or comparisons.", "slots": slots[1]}
                msgs.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(out, default=str)})
        if text is None:
            raise LLMUnavailable("no final answer")
        extra = {"raw_llm_text": text, "slots_used": P.slots_in(text), "literal_numbers_typed": P.typed_numbers(text)}
        ev = []
        if extra["literal_numbers_typed"]:  # digits AND number words typed by the model itself: only code-filled slots may carry a figure
            ev.append({"event": "hallucination_blocked", "numbers_not_in_tool_outputs": extra["literal_numbers_typed"], "blocked_reply": extra["raw_llm_text"][:400]})
        if slots is not None:
            text, unknown = P.fill(text, slots[0])
            if not extra["slots_used"]:  # the query returned rows but the reply uses none of them (e.g. a needless refusal)
                ev.append({"event": "answer_without_slots", "blocked_reply": extra["raw_llm_text"][:400]})
            if unknown:
                ev.append({"event": "unknown_placeholder", "names": unknown, "blocked_reply": extra["raw_llm_text"][:400]})
        elif P.slots_in(text):
            ev.append({"event": "unknown_placeholder", "names": P.slots_in(text), "blocked_reply": text[:400]})
        ev += G.check_reply(text, turn.outputs, question)
        if not turn.calls and not text.startswith(REFUSAL[:25]):
            ev = ev + [{"event": "no_tool_answer_blocked", "blocked_reply": text[:400]}]  # nothing to ground the reply on
        if ev:
            events += ev
            text = demo_answer()  # template version of the same answer
            return _result("Ask the data", turn, text, None, events, _model_used(llm, routed), "LIVE (guardrail fallback)", None, inputs, extra=extra)
        return _result("Ask the data", turn, text, None, events, _model_used(llm, routed), "LIVE", None, inputs, extra=extra)
    except LLMUnavailable as err:
        events.append({"event": "llm_unavailable", "detail": str(err)[:300]})
        text = demo_answer()
        return _result("Ask the data", turn, text, None, events, "rules (LLM unavailable)", "DEMO (fallback)", BANNER, inputs)
