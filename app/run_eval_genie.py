"""Local live check: run all ~15 catalogue questions through the structured answer pipeline in LIVE mode (via Groq).

Reports the LLM-vs-template summary split, chart types, follow-ups dropped as invalid, median time to the first table
and to the full answer, and a vendor-name leak check on every LLM-facing prompt and the turn's output.
Run: python -m app.run_eval_genie   (needs LLM_API_KEY in .env)
"""
from __future__ import annotations

import json
import os
import re
import statistics
import time

from backend import vendor_codes as VC
from app import ask_pipeline as AP
from app import guardrails as G
from app.llm import LLM, LLMUnavailable

VENDOR_NAMES = VC.names()  # real names this script must never find in any prompt/output

QUESTIONS: list[tuple[str, str]] = [
    ("vendors_by_loss", "Which vendors lose the most money?"),
    ("top_overpaying_lanes", "Which lanes have the highest overpayment against the fair price?"),
    ("vendor_overpay_ranking", "Which vendors overpay the most against the fair price?"),
    ("monthly_margin_trend", "How has margin trended by month?"),
    ("loss_share_by_vehicle_type", "Which vehicle types lose money most often?"),
    ("underloaded_lanes", "Which routes have the most underloaded trucks?"),
    ("hubs_by_delay", "Which hubs are slowest at handling?"),
    ("corridors_by_overrun", "Which corridors overrun their planned time the most?"),
    ("data_quality_summary", "Show the data quality issues, such as missing fields or zero freight."),
    ("margin_by_zone", "How does margin percent compare across zones?"),
    ("route_type_overrun", "Does FTL or Carting overrun its planned time more?"),
    ("underload_rate_by_plant", "Which origin plants have the highest under-load rate?"),
    ("vendor_price_gap_lanes", "Which lanes have the biggest gap between the cheapest and priciest vendor?"),
    ("top_loss_making_lanes", "Which lanes lose the most total money?"),
    ("vendor_quadrant_counts", "How many vendors fall into each spend/margin quadrant?"),
]

PACE_S, BACKOFF_S, TRIES = 8, 70, 3  # Groq free tier: pace between calls (2 LLM calls/question: query pick + summary, plus followups)


def _no_vendor_names(obj) -> list[str]:
    """Scans a (possibly nested) object's string content for any real vendor name. Returns the names found, if any."""
    blob = json.dumps(obj, default=str)
    return [n for n in VENDOR_NAMES if n and re.search(re.escape(n), blob, re.I)]


def _instrumented_run(question: str, llm: LLM) -> tuple[dict, float, float]:
    """Returns (res, time_to_first_table_s, time_to_full_answer_s) by timing query_data (first table, i.e. the
    `result` event) separately from the LLM summary/follow-up calls that come after it (the `done` event)."""
    import app.tools as T
    t0 = time.monotonic()
    first_table_s = {}
    orig_query_data = T.query_data

    def _spy(*a, **kw):
        out = orig_query_data(*a, **kw)
        if "t" not in first_table_s:
            first_table_s["t"] = time.monotonic() - t0
        return out

    T.query_data = _spy
    try:
        res = AP.run_ask_structured(question, [], llm=llm)
    finally:
        T.query_data = orig_query_data
    full_s = time.monotonic() - t0
    res = VC.scrub(res)  # mirror backend/main.py's /api/ask: every SSE payload is scrubbed before it leaves the API
    return res, first_table_s.get("t", full_s), full_s


def paced(fn, *a, **kw):
    for k in range(TRIES):
        try:
            return fn(*a, **kw)
        except LLMUnavailable:
            if k == TRIES - 1:
                raise
            time.sleep(BACKOFF_S)
    raise AssertionError("unreachable")


def main():
    if not os.getenv("LLM_API_KEY"):
        print("LLM_API_KEY is not set in .env - this script only has something to say in LIVE mode. Stopping.")
        return
    os.environ.setdefault("AUDIT_LOG_DIR", os.path.join(G.ROOT, "logs", "_eval_genie_tmp"))
    llm = LLM(demo=False)
    rows = []
    for qid, question in QUESTIONS:
        try:
            res, first_table_s, full_s = paced(_instrumented_run, question, llm)
        except LLMUnavailable as e:
            rows.append({"qid": qid, "question": question, "error": f"LLMUnavailable: {e}"})
            time.sleep(PACE_S)
            continue
        leak_in_summary = _no_vendor_names(res.get("summary"))
        leak_in_followups = _no_vendor_names(res.get("followups"))
        leak_in_result = _no_vendor_names(res.get("result"))
        rows.append({
            "qid": qid, "question": question, "kind": res["kind"],
            "picked_qid": res.get("query_id"), "qid_matches_expected": res.get("query_id") == qid,
            "mode": res.get("mode"), "model_used": res.get("model_used"),
            "summary_source": res.get("summary_source"), "guardrail_events": [e.get("event") for e in res.get("guardrail_events", [])],
            "chart_type": (res.get("chart") or {}).get("type"), "n_rows": (res.get("result") or {}).get("row_count"),
            "followups_dropped": res.get("followups_dropped", 0), "followups_offered": len(res.get("followups", [])),
            "time_to_first_table_s": round(first_table_s, 3), "time_to_full_answer_s": round(full_s, 3),
            "vendor_name_leak": leak_in_summary + leak_in_followups + leak_in_result,
        })
        time.sleep(PACE_S)

    ok_rows = [r for r in rows if "error" not in r]
    errored = [r for r in rows if "error" in r]
    n = len(ok_rows)
    llm_summaries = sum(r["summary_source"] == "llm" for r in ok_rows)
    template_summaries = sum(r["summary_source"] == "template" for r in ok_rows)
    chart_types = {}
    for r in ok_rows:
        chart_types[r["chart_type"]] = chart_types.get(r["chart_type"], 0) + 1
    total_dropped = sum(r["followups_dropped"] for r in ok_rows)
    total_offered_and_dropped = sum(r["followups_dropped"] + r["followups_offered"] for r in ok_rows)
    leaks = [r for r in ok_rows if r["vendor_name_leak"]]
    mismatches = [r for r in ok_rows if not r["qid_matches_expected"]]

    summary = {
        "n_questions": len(QUESTIONS), "n_completed": n, "n_errored": len(errored), "errors": errored,
        "summary_source": {"llm": llm_summaries, "template": template_summaries,
                            "template_reasons": [e for r in ok_rows if r["summary_source"] == "template" for e in r["guardrail_events"]]},
        "chart_types_chosen": chart_types,
        "followups_dropped_as_invalid": {"total_dropped": total_dropped, "total_proposed": total_offered_and_dropped,
                                          "share_dropped": round(total_dropped / total_offered_and_dropped, 3) if total_offered_and_dropped else None},
        "query_selection": {"llm_picked_expected_query": f"{len(ok_rows) - len(mismatches)}/{n}",
                             "mismatches": [{"question": r["question"], "expected": r["qid"], "picked": r["picked_qid"]} for r in mismatches]},
        "timing_s": {"median_time_to_first_table": round(statistics.median([r["time_to_first_table_s"] for r in ok_rows]), 3) if ok_rows else None,
                     "median_time_to_full_answer": round(statistics.median([r["time_to_full_answer_s"] for r in ok_rows]), 3) if ok_rows else None,
                     "per_question": {r["qid"]: {"first_table_s": r["time_to_first_table_s"], "full_answer_s": r["time_to_full_answer_s"]} for r in ok_rows}},
        "privacy_vendor_name_leak_check": {"n_leaked": len(leaks), "details": [{"question": r["question"], "names": r["vendor_name_leak"]} for r in leaks]},
        "model_pinned": os.getenv("LLM_MODEL"), "provider": llm.provider,
    }
    out_path = os.path.join(G.ROOT, "docs", "ask_live_eval.json")
    with open(out_path, "w", encoding="utf8") as f:
        json.dump({"summary": summary, "rows": rows}, f, indent=2, default=str)
    print(json.dumps(summary, indent=2, default=str))
    print(f"\nFull per-question detail written to {out_path}")


if __name__ == "__main__":
    main()
