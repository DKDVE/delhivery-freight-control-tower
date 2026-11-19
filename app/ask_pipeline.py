"""Structured "Ask the data" answer pipeline: analysis -> result -> chart -> summary -> followups -> done.

Code decides the data, the chart and the numbers. The LLM may only choose which catalogue query to run and write words
around cell placeholders; it never writes a figure, picks a chart type outside app.charts, or proposes a follow-up the
catalogue cannot answer (invalid follow-ups are dropped). DEMO mode uses the keyword router in app.agent and a
code-written template summary and never calls the LLM. In LIVE mode the LLM sees at most the top 5 rows' slot
descriptions, never raw rows.
"""
from __future__ import annotations

import json
import logging
import re

from app import agent as A
from app import charts as CH
from app import guardrails as G
from app import placeholders as P
from app import tools as T
from app.llm import LLM, LLMUnavailable
from app.queries import catalogue as CAT

log = logging.getLogger(__name__)

STRAY_INSTRUCTION = re.compile(r"\(\s*continue\b[^)]*\)|\bcontinue up to \d+ rows\b", re.I)
ASK_SLOT_ROW_LIMIT = 5  # privacy: the LLM sees at most the top 5 rows, aggregates and vendor codes only

# a few plausible next questions per query, for the DEMO (no-LLM) follow-up picker
_RELATED = {
    "vendors_by_loss": ["vendor_overpay_ranking", "vendor_quadrant_counts", "margin_by_zone"],
    "top_overpaying_lanes": ["vendor_overpay_ranking", "vendor_price_gap_lanes", "vendors_by_loss"],
    "vendor_overpay_ranking": ["vendors_by_loss", "top_overpaying_lanes", "vendor_quadrant_counts"],
    "monthly_margin_trend": ["margin_by_zone", "vendors_by_loss", "loss_share_by_vehicle_type"],
    "loss_share_by_vehicle_type": ["vendors_by_loss", "top_loss_making_lanes", "monthly_margin_trend"],
    "underloaded_lanes": ["underload_rate_by_plant", "vendor_quadrant_counts", "vendors_by_loss"],
    "hubs_by_delay": ["corridors_by_overrun", "route_type_overrun", "data_quality_summary"],
    "corridors_by_overrun": ["route_type_overrun", "hubs_by_delay", "data_quality_summary"],
    "data_quality_summary": ["vendors_by_loss", "corridors_by_overrun", "hubs_by_delay"],
    "margin_by_zone": ["monthly_margin_trend", "vendors_by_loss", "vendor_quadrant_counts"],
    "route_type_overrun": ["corridors_by_overrun", "hubs_by_delay", "data_quality_summary"],
    "underload_rate_by_plant": ["underloaded_lanes", "vendor_quadrant_counts", "vendors_by_loss"],
    "vendor_price_gap_lanes": ["lane_vendor_ranking", "top_overpaying_lanes", "vendor_overpay_ranking"],
    "lane_vendor_ranking": ["vendor_price_gap_lanes", "vendor_head_to_head", "top_overpaying_lanes"],
    "vendor_head_to_head": ["vendor_overpay_ranking", "lane_vendor_ranking", "vendors_by_loss"],
    "top_loss_making_lanes": ["vendors_by_loss", "loss_share_by_vehicle_type", "margin_by_zone"],
    "vendor_quadrant_counts": ["vendors_by_loss", "vendor_overpay_ranking", "margin_by_zone"],
}


def closest_questions(_question: str, n: int = 6) -> list[str]:
    """The 6 closest supported questions for a refusal card. DEMO: the first n catalogue titles as questions."""
    return [f"Show: {CAT.CATALOGUE[qid]['title']}" for qid in list(CAT.CATALOGUE)[:n]]


def _reuse_follow_up(question: str, last_qid: str, last_params: dict) -> tuple[str, dict] | None:
    """A plain refinement of the previous query ('only FTL', 'top 20', 'south zone') reuses its query_id."""
    low = question.lower()
    spec = T.QUERIES[last_qid][1]
    params = dict(last_params)
    changed = False
    m = re.search(r"\btop\s+(\d+)\b", low)
    if m and "top" in spec:
        params["top"] = int(m.group(1))
        changed = True
    if "zone" in spec:
        zones = [z for z in T._zones() if re.search(rf"\b{re.escape(z.lower())}\b", low)]
        if zones:
            params["zone"] = zones[0]
            changed = True
    if "route_type" in low and last_qid in ("route_type_overrun",):
        changed = changed  # route_type_overrun always returns both types; nothing to narrow server-side yet
    return (last_qid, params) if changed else None


def choose_query(question: str, history: list[dict]) -> tuple[str | None, dict]:
    """DEMO-mode routing: app.agent's keyword router, with a follow-up fallback that reuses the last turn's
    query_id when the new question looks like a refinement rather than a new topic."""
    qid, params = A.route_question(question)
    if qid is not None:
        return qid, params
    if history:
        last = history[-1]
        reuse = _reuse_follow_up(question, last["query_id"], last.get("params", {}))
        if reuse:
            return reuse
    return None, {}


def _fmt_value(unit: str | None, decimals: int, v) -> str:
    if v is None:
        return "n/a"
    if unit == "Rs":
        return f"Rs{v / 1e7:.2f} Cr" if abs(v) >= 1_000_000 else f"Rs{v:,.0f}"
    if unit == "%":
        return f"{v:.{decimals}f}%"
    if unit == "x":
        return f"{v:.{decimals}f}x"
    if isinstance(v, float):
        return f"{v:,.{decimals}f}"
    return f"{v:,}" if isinstance(v, int) else str(v)


def template_summary(qid: str, out: dict) -> str:
    """Code-written fallback/DEMO summary: one sentence naming the top row, from catalogue column metadata only."""
    cat = CAT.CATALOGUE[qid]
    row = out["rows"][0]
    dims = [c for c in cat["columns"] if c["role"] in ("dimension", "time") and c["name"] in row]
    measures = [c for c in cat["columns"] if c["role"] == "measure" and c["name"] in row]
    lead = f"{cat['title']}." if not dims else f"{cat['title']}: {row[dims[0]['name']]} leads."
    parts = [f"{m['label'].lower()} {_fmt_value(m['unit'], m['decimals'], row[m['name']])}" for m in measures[:3]]
    total, shown = _total_rows(out), out["n_rows"]
    count = f"{shown} rows" if total == shown else f"top {shown} of {total} rows"
    tail = (", ".join(parts) + f" ({count} in total)." if parts and total == shown else ", ".join(parts) + f" ({count})." if parts else f"{count} returned.")
    f = (out.get("meta") or {}).get("facts") or {}
    if "shared_lanes" in f:   # head to head: the true totals, not the length of the table
        lead = (f"{f['vendor_a']} and {f['vendor_b']} share {f['shared_lanes']} lane groups: {f['vendor_a']} is cheaper on {f['lanes_won_a']}, "
                f"{f['vendor_b']} on {f['lanes_won_b']}.")
        return f"{lead} {count[0].upper() + count[1:]} shown by volume."
    return f"{lead} {tail}"


def fill_or_fallback(raw_text: str | None, qid: str, out: dict) -> tuple[str, str, list[dict]]:
    """(summary text, source "llm"|"template", guardrail events). raw_text is an LLM reply using {r<i>_col}
    placeholders, or None to force the template path (DEMO mode, or when the caller
    already knows there is no LLM reply to try). Mirrors app.agent's existing placeholder-fill-then-check
    pattern, but with the whole-reply-replaced-by-template fallback the rest of this codebase already uses
    for any guardrail failure (not a surgical edit), so "removed" means "the template took over"."""
    template = template_summary(qid, out)
    if raw_text is None:
        return template, "template", []
    events: list[dict] = []
    if STRAY_INSTRUCTION.search(raw_text):
        events.append({"event": "stray_instruction_removed", "blocked_reply": raw_text[:400]})
    slots, _descs = P.ask_slots(out, max_rows=ASK_SLOT_ROW_LIMIT)
    filled, unknown = P.fill(raw_text, slots)
    if unknown:
        events.append({"event": "unknown_placeholder", "names": unknown, "blocked_reply": raw_text[:400]})
    literal = P.typed_numbers(raw_text)  # digits AND number words ('five', 'one vendor', '5th'): every figure must be a code-filled slot
    if literal:
        events.append({"event": "hallucination_blocked", "numbers_not_in_tool_outputs": literal, "blocked_reply": raw_text[:400]})
    events += G.check_reply(filled, out)
    events += _count_claims(filled, out)
    if events:
        return template, "template", events
    return filled, "llm", []


_NUM = r"(\d[\d,]*(?:\.\d+)?)"
_SHARED_BEFORE = re.compile(_NUM + r"\s+(?:shared|common)\s+lane", re.I)
_SHARED_AFTER = re.compile(r"(?:shared|common)\s+lanes?(?:\s+groups?)?\s*(?:is|are|total|of|:|=|\()\s*" + _NUM, re.I)
_TABLE_SHOWS = re.compile(r"\b(?:table|result set|results?|list)\s+(?:only\s+)?(?:shows?|displays?|contains?|includes?|lists?|has)\s+" + _NUM, re.I)


def _count_claims(text: str, out: dict) -> list[dict]:
    """Backstop on WHAT a figure is claimed to count, not just where it came from: a code-filled number can still sit next to the wrong noun
    (seen LIVE: '{n_rows} shared lanes' filled with the table's row count). When the result carries true totals, a number next to 'shared lanes' must be
    shared_lanes, and a number the table 'shows' must be the number of rows the table really shows."""
    meta = out.get("meta") or {}
    facts = meta.get("facts") or {}
    shown = str(out.get("n_rows", len(out.get("rows") or [])))
    bad = []

    def num(m):
        return m.group(1).replace(",", "")
    if "shared_lanes" in facts:
        for rx in (_SHARED_BEFORE, _SHARED_AFTER):
            bad += [num(m) for m in rx.finditer(text) if num(m) != facts["shared_lanes"]]
    if isinstance(meta.get("total_rows"), int) and meta["total_rows"] > out.get("n_rows", 0):
        bad += [num(m) for m in _TABLE_SHOWS.finditer(text) if num(m) != shown]
    return [{"event": "wrong_count_claim", "numbers": bad, "blocked_reply": text[:400]}] if bad else []


def choose_query_llm(llm: LLM, question: str, history: list[dict]) -> tuple[str | None, dict, str | None]:
    """LIVE mode query picker: the LLM chooses {query_id, params} via the same query_data tool schema
    app.agent.run_ask uses (query_id is a strict enum of the catalogue, see app.tools.TOOL_SCHEMAS) - code then
    validates qid is actually in the catalogue before trusting it at all. Returns (query_id or None, params,
    routed_via). Raises LLMUnavailable on a connection/API failure so the caller can fall back to the DEMO router."""
    ctx = ""
    if history:
        ctx = "Recent turns in this conversation (most recent last), for refinements like 'only FTL' or 'top 20':\n" + "\n".join(
            f"- question: {h['question']!r} -> query_id={h['query_id']!r}, params={h.get('params', {})}" for h in history) + "\n\n"
    sys = ("You are the query picker for a freight-cost analytics tool. Choose exactly one approved query via the "
           "query_data tool that best answers the user's question, with params if useful (leave params out if the "
           "defaults are fine). If nothing in the approved list can answer the question, reply in plain text (no "
           "tool call) explaining briefly that you cannot - do not call the tool just to say no.")
    r = llm.chat([{"role": "system", "content": sys}, {"role": "user", "content": f"{ctx}Question: {question}"}], tools=A.ASK_SCHEMAS)
    if not r.tool_calls:
        return None, {}, r.routed_via
    call = r.tool_calls[0]
    if call["name"] != "query_data":
        return None, {}, r.routed_via
    try:
        args = json.loads(call["arguments"] or "{}")
    except json.JSONDecodeError:
        return None, {}, r.routed_via
    qid, params = args.get("query_id"), args.get("params") or {}
    if qid not in CAT.CATALOGUE:
        return None, {}, r.routed_via
    if not isinstance(params, dict):
        params = {}
    # The model sometimes sends no params, or only some of them (seen LIVE: origin and destination but not the load), and the query then silently answers for the
    # default or ignores what the user said. Whenever the deterministic router reads the same query from the question, any param it found that the model left
    # out is filled in. Values the model did send are never overridden.
    rq, rp = A.route_question(question)
    if rq == qid and rp:
        params = {**rp, **{k: v for k, v in params.items() if v not in (None, "")}}
    return qid, params, r.routed_via


def llm_summary(llm: LLM, qid: str, out: dict, question: str) -> str | None:
    """LIVE mode summary: the LLM writes 1-3 sentences using only {r<i>_col} cell placeholders; code fills them in fill_or_fallback, which still runs the full guardrail check on the result exactly as
    in DEMO mode - this function only produces the raw candidate text. Privacy: only the top ASK_SLOT_ROW_LIMIT
    rows' worth of slots are ever described to the model, never raw rows. Raises LLMUnavailable on failure."""
    cat_entry = CAT.CATALOGUE[qid]
    _slots, descs = P.ask_slots(out, max_rows=ASK_SLOT_ROW_LIMIT)
    instr = (f"The user asked: {question!r}\nQuery run: {cat_entry['title']}\n\n"
             "Write 1 to 3 short sentences answering the question. Use ONLY the cell placeholders listed below, "
             "copied exactly with their curly braces - never type a digit, number word (like five), percentage or name "
             "yourself, and do not state a trend or comparison the placeholders don't carry. To state how many rows there "
             "are, use {n_rows}; for any other count or total (shared lanes, lanes won, the load) use only the matching named slot below, never the row count and never a count "
             "written any other way. The table is often only the top few of a longer list.\n\nSlots you may use:\n" + P.catalogue(descs))
    r = llm.chat([{"role": "system", "content": "You write short, factual summaries for a freight-cost analytics tool."},
                  {"role": "user", "content": instr}])
    text = (r.content or "").strip()
    return text or None


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)


def llm_followups(llm: LLM, qid: str, question: str, n: int = 3, reasons: list | None = None) -> tuple[list[dict], int]:
    """LIVE mode follow-ups: the LLM proposes next {query_id, params} pairs from the catalogue; code dry-runs every one through query_data and drops anything invalid (bad query_id, bad params, or a
    query that returns no rows) before it is ever offered - every chip stays guaranteed answerable. Returns
    (followups, n_dropped). Falls back to the DEMO curated map (never raises LLMUnavailable itself) so a flaky
    follow-up call never breaks the rest of the answer card. If `reasons` is a list, one {"query_id", "reason", "detail"} entry is appended for every proposal
    that was dropped (and one for a reply that was not a JSON array) - logging only, it never changes which proposals are dropped."""
    def why(pqid, reason, detail=""):
        if reasons is not None:
            reasons.append({"query_id": str(pqid)[:60] if pqid is not None else None, "reason": reason, "detail": str(detail)[:160]})
        log.info("followup dropped: query_id=%r reason=%s %s", pqid, reason, str(detail)[:160])

    menu = "\n".join(f"- {other}: {CAT.CATALOGUE[other]['title']} (params: {list(T.QUERIES[other][1])})" for other in CAT.CATALOGUE if other != qid)
    sys = ('Suggest 3 good follow-up questions as a JSON array like [{"query_id": "...", "params": {}}], each '
           "query_id taken from the approved list below. Reply with ONLY the JSON array, nothing else.")
    user = f"Approved queries:\n{menu}\n\nThe user just asked: {question!r} and got results from {qid!r}. Suggest 3 different, genuinely useful follow-ups."
    try:
        r = llm.chat([{"role": "system", "content": sys}, {"role": "user", "content": user}])
    except LLMUnavailable:
        why(None, "llm_unavailable")
        return build_followups(qid, n), 0
    text = _FENCE.sub("", (r.content or "").strip())
    try:
        proposals = json.loads(text)
        if not isinstance(proposals, list):
            why(None, "reply_not_a_json_array")
            proposals = []
    except (json.JSONDecodeError, ValueError):
        why(None, "reply_not_json")
        proposals = []
    dropped, seen, picked = 0, set(), []
    for p in proposals:
        if not isinstance(p, dict):
            dropped += 1
            why(None, "proposal_not_an_object", type(p).__name__)
            continue
        pqid, pparams = p.get("query_id"), p.get("params") or {}
        if pqid == qid or pqid in seen or pqid not in CAT.CATALOGUE:
            dropped += 1
            why(pqid, "same_query_as_current" if pqid == qid else "duplicate_query_id" if pqid in seen else "unknown_query_id")
            continue
        try:
            res = T.query_data(pqid, pparams)
        except Exception as e:  # noqa: BLE001 - any bad/unexpected param shape is just a dropped suggestion
            dropped += 1
            why(pqid, "invalid_param" if isinstance(e, T.ToolError) else "query_error", f"{type(e).__name__}: {e}")
            continue
        if res.get("status") != "ok" or not res.get("rows"):
            dropped += 1
            why(pqid, "empty_result" if res.get("status") == "ok" else "query_not_ok", res.get("status"))
            continue
        picked.append({"query_id": pqid, "params": pparams, "question": f"Show: {CAT.CATALOGUE[pqid]['title']}"})
        seen.add(pqid)
        if len(picked) == n:
            break
    if len(picked) < n:  # backfill from the curated DEMO map so the card is never short a chip
        for cand in build_followups(qid, n):
            if cand["query_id"] not in seen:
                picked.append(cand)
                seen.add(cand["query_id"])
            if len(picked) == n:
                break
    return picked, dropped


def build_followups(qid: str, n: int = 3) -> list[dict]:
    """3 {query_id, params, question} picks from the catalogue, each dry-run validated so every chip is
    guaranteed answerable. DEMO mode: a curated related-query map instead of an LLM pick."""
    out = []
    for other in _RELATED.get(qid, list(CAT.CATALOGUE))[:n + 2]:
        if other == qid or other not in CAT.CATALOGUE:
            continue
        try:
            res = T.query_data(other, {})
        except Exception:  # noqa: BLE001
            continue
        if res.get("status") != "ok" or not res.get("rows"):
            continue
        out.append({"query_id": other, "params": {}, "question": f"Show: {CAT.CATALOGUE[other]['title']}"})
        if len(out) == n:
            break
    return out


def _chart_columns(cat_entry: dict, display_cols: list[dict]) -> list[dict]:
    """The catalogue's curated 'headline' measures for charting (table columns can be richer than what a
    bar/line/KPI-tile can show); falls back to every display column when a query has no narrowing set."""
    wanted = cat_entry.get("chart_measures")
    if not wanted:
        return display_cols
    keep = {c["name"] for c in display_cols if c["role"] in ("dimension", "time")} | set(wanted)
    return [c for c in display_cols if c["name"] in keep]


def run_ask_structured(question: str, history: list[dict] | None = None, llm: LLM | None = None) -> dict:
    """Runs the whole pipeline and returns every SSE payload at once (the endpoint streams them with small delays
    so the UI can show its skeleton sequence). history: last <=3 {question, query_id, params} turns, newest last
    - never raw rows.

    llm=None or llm.demo: checkpoint 1's DEMO path (deterministic router, template summary, curated follow-ups).
    llm live: checkpoint 2's LIVE path (LLM picks the query, writes the summary, proposes follow-ups) - any
    LLMUnavailable at any step falls back to the DEMO behaviour for the rest of this turn rather than failing
    the whole answer, same spirit as app.agent's existing LLM-unavailable fallback."""
    turn = A.Turn()
    history = (history or [])[-3:]

    if A.is_action_request(question):
        return {"kind": "refused", "reason": "action_request", "closest": closest_questions(question)}

    live = llm is not None and not llm.demo
    routed_via, llm_unavailable = None, False
    if live:
        try:
            qid, params, routed_via = choose_query_llm(llm, question, history)
        except LLMUnavailable:
            llm_unavailable, live = True, False
            qid, params = choose_query(question, history)
    else:
        qid, params = choose_query(question, history)
    if qid is None:
        return {"kind": "refused", "reason": "not_approved", "closest": closest_questions(question)}

    cat_entry = CAT.get(qid)
    analysis = {"query_id": qid, "params": params, "interpretation": f"Interpreting this as: {cat_entry['title'].lower()}.", "data_scope": cat_entry["data_scope"]}

    out = turn.call("query_data", {"query_id": qid, "params": params})
    if out.get("error"):
        return {"kind": "error", "message": out["error"], "analysis": analysis}
    if out.get("n_rows", 0) == 0:
        return {"kind": "empty", "analysis": analysis, "filters_applied": params, "query_id": qid, "notes": (out.get("meta") or {}).get("notes") or []}

    display_cols = [c for c in cat_entry["columns"] if c["name"] in out["rows"][0]]
    chart_cols = _chart_columns(cat_entry, display_cols)
    chart = CH.choose_chart(chart_cols, out["n_rows"], title_hint=cat_entry["title"])
    chart["valid_types"] = CH.valid_types_for_shape(chart_cols, out["n_rows"])

    raw_summary, followups_dropped = None, 0
    if live:
        try:
            raw_summary = llm_summary(llm, qid, out, question)
        except LLMUnavailable:
            llm_unavailable, live = True, False
    summary, summary_source, events = fill_or_fallback(raw_summary, qid, out)
    notes = _load_notes(question, qid, params, list((out.get("meta") or {}).get("notes") or []))
    if notes:  # code-written caveats (e.g. a load that could not be applied) are appended after the guardrail check, never left to the LLM
        summary = f"{summary} {' '.join(notes)}"

    drop_reasons: list[dict] = []
    if live:
        followups, followups_dropped = llm_followups(llm, qid, question, reasons=drop_reasons)
    else:
        followups = build_followups(qid)

    mode = "LIVE" if live else ("DEMO (fallback)" if llm_unavailable else "DEMO")
    model_used = (routed_via or f"{llm.provider}/{llm.model}") if live else ("rules (LLM unavailable)" if llm_unavailable else "rules (DEMO_MODE)")
    run_id = G.log_run("Ask the data", {"question": question}, turn.calls, model_used, summary, events, mode, None,
                        extra={"query_id": qid, "params": params, "followups_dropped": followups_dropped, "followups_drop_reasons": drop_reasons})

    result = {"columns": display_cols, "rows": out["rows"][:50], "row_count": out["n_rows"], "total_rows": _total_rows(out),
              "notes": notes,
              "sql_equivalent": CAT.sql_equivalent(qid, {**{p: spec[1] for p, spec in T.QUERIES[qid][1].items()}, **params}),
              "filters_applied": params}

    return {"kind": "ok", "run_id": run_id, "query_id": qid, "params": params, "analysis": analysis, "result": result,
            "chart": chart, "summary": summary, "summary_source": summary_source, "guardrail_events": events,
            "guardrail_status": "replaced" if events else "passed", "followups": followups, "followups_dropped": followups_dropped,
            "followups_drop_reasons": drop_reasons, "mode": mode, "model_used": model_used}


def _load_notes(question: str, qid: str, params: dict, notes: list[str]) -> list[str]:
    """A load named in the question ('15 MT') that the chosen query did not apply is said so in code-written text, never left silent."""
    load = A.question_load(question)
    if load is None:
        return notes
    if "qty_mt" not in T.QUERIES[qid][1]:
        return notes + [f"A load of {load:g} MT was mentioned, but this query does not use a load, so it is not applied."]
    if params.get("qty_mt") is None:
        return notes + [f"A load of {load:g} MT was mentioned, but it could not be applied to this lane, so the figures are not for that load."]
    return notes


def _total_rows(out: dict) -> int:
    """All rows behind the table: the true total when the query returned only a top-N cut of a longer list (meta.total_rows), else the rows returned."""
    t = (out.get("meta") or {}).get("total_rows")
    return int(t) if isinstance(t, int) and t >= out["n_rows"] else out["n_rows"]


def rerun(run_id: str, params: dict) -> dict:
    """POST /api/ask/rerun: the same query with edited filters, fast and deterministic (no LLM call at all)."""
    for r in G.read_audit():
        if r.get("type") == "run" and r.get("run_id") == run_id:
            qid = r.get("query_id")
            break
    else:
        raise KeyError(f"unknown run_id '{run_id}'")
    if not qid:
        raise KeyError(f"run '{run_id}' has no query_id to rerun")
    out = T.query_data(qid, params)
    if out.get("error"):
        return {"kind": "error", "message": out["error"]}
    if out.get("n_rows", 0) == 0:
        return {"kind": "empty", "query_id": qid, "filters_applied": params, "notes": (out.get("meta") or {}).get("notes") or []}
    cat_entry = CAT.get(qid)
    display_cols = [c for c in cat_entry["columns"] if c["name"] in out["rows"][0]]
    chart_cols = _chart_columns(cat_entry, display_cols)
    chart = CH.choose_chart(chart_cols, out["n_rows"], title_hint=cat_entry["title"])
    chart["valid_types"] = CH.valid_types_for_shape(chart_cols, out["n_rows"])
    summary, _source, _events = fill_or_fallback(None, qid, out)
    notes = list((out.get("meta") or {}).get("notes") or [])
    if notes:
        summary = f"{summary} {' '.join(notes)}"
    result = {"columns": display_cols, "rows": out["rows"][:50], "row_count": out["n_rows"], "total_rows": _total_rows(out), "notes": notes,
              "sql_equivalent": CAT.sql_equivalent(qid, {**{p: spec[1] for p, spec in T.QUERIES[qid][1].items()}, **params}),
              "filters_applied": params}
    return {"kind": "ok", "query_id": qid, "params": params, "result": result, "chart": chart, "summary": summary}
