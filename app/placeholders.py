"""Placeholders: the LLM writes text with {name} slots, code fills them from tool results.

The LLM is told which slot names exist and what they mean, but never sees the values, so it has nothing to copy wrongly.
Any slot name it invents is rejected (unknown_placeholder), and the number guardrail still checks the filled text as a backstop.
"""
from __future__ import annotations

import re

from app import guardrails as G

SLOT = re.compile(r"(?<!\{)\{([A-Za-z_]\w*)\}(?!\})")


def rs(v) -> str:
    return f"₹{v:,.0f}"


def slots_in(text: str) -> list[str]:
    return SLOT.findall(text or "")


def fill(text: str, values: dict[str, str]) -> tuple[str, list[str]]:
    """(text with known slots filled, names of unknown slots). Unknown slots are left as they are."""
    unknown = [n for n in slots_in(text) if n not in values]
    return SLOT.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), text), unknown


def literal_numbers(text: str) -> list[float]:
    """Numbers the LLM typed itself (slots contain no standalone digits, so they are not counted)."""
    return [v for v, _ in G.extract_numbers(SLOT.sub("", text or ""))]


_COUNT_NOUN = r"(?:vendors?|lanes?|routes?|trips?|hubs?|zones?|plants?|rows?|trucks?|vehicles?|shipments?|lrs?|corridors?|options?|quadrants?|types?|results?|places?|origins?|destinations?)"
NUMBER_WORD = re.compile(
    r"\b(?:zero|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety"
    r"|hundreds?|thousands?|millions?|billions?|dozens?|twice|thrice)\b"
    rf"|\b(?:one|single)\s+{_COUNT_NOUN}\b"      # 'one vendor', 'a single lane' state a count; plain 'one of the cheapest' does not
    r"|\b\d+(?:st|nd|rd|th)\b",                    # '5th'
    re.I)


def literal_number_words(text: str) -> list[str]:
    """Number words, counts next to nouns ('one vendor') and numeric ordinals the LLM typed itself. Slots never contain these (they are digits filled by code)."""
    return [m.group(0).lower() for m in NUMBER_WORD.finditer(SLOT.sub("", text or ""))]


def typed_numbers(text: str) -> list:
    """Everything numeric the LLM typed by itself: digits plus number words. Empty means every figure in the text came from a code-filled slot."""
    return literal_numbers(text) + literal_number_words(text)


def catalogue(descs: dict[str, str]) -> str:
    return "\n".join(f"- {{{k}}}: {v}" for k, v in descs.items())


def booking_slots(check: dict, alts: dict | None, cur: dict | None, d: dict) -> tuple[dict, dict]:
    """(values, descriptions) for one booking, filled only with figures the tools returned."""
    inp, v1, v2, v3 = check["inputs"], check["v1_fair_price"], check["v2_loss_risk"], check["v3_underload_risk"]
    v = {"route": inp["route"], "vehicle_type": inp["vehicle_type"], "vendor": inp["vendor"], "action": d["action"],
         "qty_mt": f"{inp['qty']:g}", "v2_probability": f"{v2['probability'] * 100:.1f}%", "v2_threshold": f"{v2['threshold'] * 100:.1f}%",
         "fair_rs": rs(v1["fair_rs"]), "fair_low_rs": rs(v1["fair_low_rs"]), "fair_high_rs": rs(v1["fair_high_rs"]),
         "v3_probability": f"{v3['probability'] * 100:.1f}%"}
    desc = {"route": "route code", "vehicle_type": "vehicle type code", "vendor": "vendor name", "action": "the action the rules chose",
            "qty_mt": "planned load in MT", "v2_probability": "estimated chance the booking loses money", "v2_threshold": "loss-risk flag threshold",
            "fair_rs": "model fair price for this movement", "fair_low_rs": "low end of the fair-price range", "fair_high_rs": "high end of the fair-price range",
            "v3_probability": "estimated chance the truck leaves below 60% full"}
    if v3.get("expected_utilisation_pct") is not None:
        v["expected_utilisation_pct"] = f"{v3['expected_utilisation_pct']:.1f}%"
        desc["expected_utilisation_pct"] = "planned load as a share of the vehicle capacity"
    if v1.get("quoted_rs"):
        v.update({"quoted_rs": rs(v1["quoted_rs"]), "overpay_pct": f"{abs(v1['overpay_pct']):.1f}%",
                  "overpay_direction": "above" if v1["overpay_pct"] >= 0 else "below", "excess_rs": rs(v1["excess_rs"])})
        desc.update({"quoted_rs": "the quote", "overpay_pct": "size of the gap between quote and fair price (a positive number)",
                     "overpay_direction": "the word above or below", "excess_rs": "quote minus fair price, zero if the quote is lower"})
    if d.get("best_alternative"):
        b = d["best_alternative"]
        v.update({"alt_vendor": b["vendor_name"], "alt_rs_per_mt": rs(b["rs_per_mt"])})
        desc.update({"alt_vendor": "cheapest other vendor on this lane", "alt_rs_per_mt": "that vendor's rupees per MT"})
    if d.get("smaller_vehicle"):
        v["suggested_capacity_mt"] = f"{d['smaller_vehicle']['capacity']:g}"
        desc["suggested_capacity_mt"] = "capacity in MT of the smaller vehicle that fits the load"
    if d.get("current_rs_per_mt"):
        v["current_rs_per_mt"] = rs(d["current_rs_per_mt"])
        desc["current_rs_per_mt"] = "this vendor's rupees per MT on the lane"
    return v, desc


SIGNAL_TEXT = {"underload_smaller_vehicle": "the under-load flag is raised and a smaller vehicle fits the load",
               "quote_above_fair": "the quote is more than 10% above the fair price",
               "loss_risk": "the loss-risk flag is raised while the price is within range",
               "quote_below_fair": "the quote is more than 30% below the fair price",
               "none": "no rule fires"}


def booking_facts(check: dict, d: dict) -> str:
    """Yes/no facts for the LLM. The first line is the signal that triggered the action; the explanation must name it."""
    return (f"triggering signal: {SIGNAL_TEXT[d['signal']]}; loss-risk flag raised: {'yes' if check['v2_loss_risk']['flag'] else 'no'}; "
            f"price more than 10% over fair: {'yes' if d['over_flag'] else 'no'}; price more than 30% under fair: {'yes' if d['below_flag'] else 'no'}; "
            f"under-load flag raised: {'yes' if d['under_flag'] else 'no'}; smaller vehicle fits: {'yes' if d['smaller_vehicle'] else 'no'}; "
            f"cheaper vendor on the lane: {'yes' if d['cheaper_alternative'] else 'no'}")


def trip_slots(t: dict) -> tuple[dict, dict]:
    sd = t["severe_delay"]
    v = {"planned_min": f"{t['planned_min']:,.0f}", "predicted_min": f"{t['predicted_min']:,.0f}", "predicted_low_min": f"{t['predicted_low_min']:,.0f}",
         "predicted_high_min": f"{t['predicted_high_min']:,.0f}", "ratio": f"{t['predicted_vs_plan_ratio']:.2f}x",
         "severe_probability": f"{sd['probability'] * 100:.1f}%", "severe_threshold": f"{sd['threshold'] * 100:.1f}%"}
    desc = {"planned_min": "planned trip time in minutes", "predicted_min": "model's expected trip time in minutes", "predicted_low_min": "low end of the likely range, minutes",
            "predicted_high_min": "high end of the likely range, minutes", "ratio": "expected time divided by planned time",
            "severe_probability": "estimated chance the trip runs 2.5x its plan or more", "severe_threshold": "flag threshold for that chance"}
    return v, desc


def _fmt_col(col: str, x):
    if x is None:
        return None
    if isinstance(x, bool):
        return str(x)
    if isinstance(x, (int, float)):
        if col.endswith("_rs"):
            return rs(x)
        if "pct" in col or "share" in col:
            return f"{x:.1f}%"
        return f"{x:,}" if isinstance(x, int) else f"{x:,.1f}"
    return str(x)


def ask_slots(out: dict, max_rows: int = 25) -> tuple[dict, dict]:
    """Slots r{i}_{column} for the first rows of the one query result (money columns also get r{i}_{column}_cr)."""
    rows = (out.get("rows") or [])[:max_rows]
    shown = out.get("n_rows", len(out.get("rows") or []))
    t = (out.get("meta") or {}).get("total_rows")
    total = t if isinstance(t, int) and t >= shown else shown   # the TRUE count behind the table, never the capped one
    v, cols = {"n_rows": str(total), "rows_shown": str(shown)}, {}
    for i, r in enumerate(rows, 1):
        for c, x in r.items():
            f = _fmt_col(c, x)
            if f is None:
                continue
            v[f"r{i}_{c}"] = f
            if c.endswith("_rs") and isinstance(x, (int, float)):
                v[f"r{i}_{c}_cr"] = f"₹{x / 1e7:.2f} Cr"
            cols[c] = "rupees (also {r<i>_%s_cr}, which already reads like ₹1.23 Cr, so add no unit after it)" % c if c.endswith("_rs") else ("percent" if "pct" in c or "share" in c else "number" if isinstance(x, (int, float)) else "text")
    desc = {"n_rows": ("the total number of rows behind this result (for a top-N table, the full count, not the few shown); call them 'rows' or 'results'" if total != shown else
                       "number of rows in the result table (all of them, not only the few described here); call them 'rows' or 'results', never a total of lanes, vendors or anything else")}
    if total != shown:
        desc["rows_shown"] = "how many rows the table actually shows (a top-N cut of the n_rows total)"
    desc.update({f"r<i>_{c}": t for c, t in cols.items()})
    meta = out.get("meta") or {}   # code-computed totals/facts (e.g. shared_lanes, lanes_won_a): true figures the row count cannot give
    for name, val in (meta.get("facts") or {}).items():
        v[name] = str(val)
        desc[name] = (meta.get("fact_desc") or {}).get(name, "a figure computed by code")
    return v, desc
