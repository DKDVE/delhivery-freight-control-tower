"""Guardrail regression + placeholder/unit formatting + follow-up validation + rerun parity."""
from app import ask_pipeline as AP
from app.queries import catalogue as CAT


def _some_ok_result():
    return AP.run_ask_structured("Which vendors lose the most money?")


def test_typed_number_in_llm_reply_falls_back_to_template():
    out = AP.run_ask_structured("Which vendors lose the most money?")
    q = out["query_id"]
    from app import tools as T
    raw = T.query_data(q, out["params"])
    scripted_reply = "This vendor lost exactly 3.0 crore, way worse than the rest."
    text, source, events = AP.fill_or_fallback(scripted_reply, q, raw)
    assert source == "template"
    assert any(e["event"] == "hallucination_blocked" for e in events)
    assert text == AP.template_summary(q, raw)


def test_stray_instruction_text_is_removed():
    from app import tools as T
    out = AP.run_ask_structured("Which vendors lose the most money?")
    q = out["query_id"]
    raw = T.query_data(q, out["params"])
    scripted_reply = "Here are the top vendors (continue up to 10 rows)"
    text, source, events = AP.fill_or_fallback(scripted_reply, q, raw)
    assert source == "template"
    assert any(e["event"] == "stray_instruction_removed" for e in events)
    assert "continue" not in text.lower()


def test_clean_placeholder_reply_with_known_slots_passes_through():
    from app import tools as T
    out = AP.run_ask_structured("Which vendors lose the most money?")
    q = out["query_id"]
    raw = T.query_data(q, out["params"])
    reply = "{r1_vendor_name} lost the most, at {r1_loss_rs}."
    text, source, events = AP.fill_or_fallback(reply, q, raw)
    assert source == "llm"
    assert events == []
    assert "{r1_vendor_name}" not in text


def test_unknown_placeholder_falls_back_to_template():
    from app import tools as T
    out = AP.run_ask_structured("Which vendors lose the most money?")
    q = out["query_id"]
    raw = T.query_data(q, out["params"])
    reply = "{r1_made_up_slot} is the worst offender."
    text, source, events = AP.fill_or_fallback(reply, q, raw)
    assert source == "template"
    assert any(e["event"] == "unknown_placeholder" for e in events)


def test_catalogue_is_json_serialisable_with_no_leaked_function_objects():
    """/api/ask/catalogue returns app.queries.catalogue.list_catalogue() as-is: every param's `allowed` callable
    must already be resolved to a plain list of values, never the function object itself."""
    import json
    text = json.dumps(CAT.list_catalogue())
    assert "function" not in text and "0x" not in text


def test_every_catalogue_followup_is_answerable():
    for qid in CAT.CATALOGUE:
        for f in AP.build_followups(qid):
            from app import tools as T
            res = T.query_data(f["query_id"], f["params"])
            assert res["status"] == "ok" and res["rows"], f"{qid} suggested an unanswerable follow-up: {f}"


def test_action_request_is_refused_before_any_query():
    out = AP.run_ask_structured("Please cancel this vendor's contract")
    assert out["kind"] == "refused"


def test_nonsense_question_is_refused_with_closest_questions():
    out = AP.run_ask_structured("purple elephant quantum soup")
    assert out["kind"] == "refused"
    assert len(out["closest"]) == 6


def test_rerun_gives_identical_result_to_the_original_params():
    out = _some_ok_result()
    rerun = AP.rerun(out["run_id"], out["params"])
    assert rerun["kind"] == "ok"
    assert rerun["result"]["rows"] == out["result"]["rows"]
    assert rerun["chart"]["type"] == out["chart"]["type"]


def test_rerun_with_changed_params_changes_the_result():
    out = _some_ok_result()
    rerun = AP.rerun(out["run_id"], {**out["params"], "top": 3})
    assert rerun["result"]["row_count"] == 3


def test_vendor_names_never_appear_in_any_catalogue_result_before_scrubbing():
    """tools.query_data() itself still returns raw vendor names (scrubbing happens once, at the API boundary
    in backend.main.out()) - this just confirms vc.code_for can actually recognise every name these queries
    produce, so the API-layer scrub has something to translate."""
    from app import tools as T
    from backend import vendor_codes as vc
    for qid in CAT.CATALOGUE:
        res = T.query_data(qid, {})
        for row in res["rows"]:
            for col, v in row.items():
                if col in ("vendor_name", "vendor") and isinstance(v, str):
                    assert vc.code_for(v) is not None, f"{qid}.{col} has a name vc.code_for doesn't recognise: {v}"


def _lane_raw():
    from app import tools as T
    return "lane_vendor_ranking", T.query_data("lane_vendor_ranking", {"origin": "HAZIRA", "destination": "KHADOLI"})


def test_n_rows_slot_is_the_real_row_count_not_the_five_rows_the_llm_sees():
    from app import placeholders as P
    q, raw = _lane_raw()
    assert raw["n_rows"] > 5
    slots, descs = P.ask_slots(raw, max_rows=5)
    assert slots["n_rows"] == str(raw["n_rows"]) and "r6_vendor_code" not in slots and "n_rows" in descs
    text, source, events = AP.fill_or_fallback("There are {n_rows} vendors ranked; {r1_vendor_code} is cheapest at {r1_rs_per_mt} per MT.", q, raw)
    assert source == "llm" and str(raw["n_rows"]) in text and " 5 " not in f" {text} "


def test_typed_digit_count_next_to_a_noun_falls_back():
    q, raw = _lane_raw()
    for reply in ("There are 5 vendor options; {r1_vendor_code} is cheapest.", "{r1_vendor_code} beats 11 vendors on this lane.", "The top 5 vendors are listed."):
        text, source, events = AP.fill_or_fallback(reply, q, raw)
        assert source == "template" and any(e["event"] == "hallucination_blocked" for e in events), reply


def test_typed_number_words_fall_back():
    q, raw = _lane_raw()
    for reply in ("There are five vendor options; {r1_vendor_code} is cheapest.", "Only twenty-five lanes matter.", "A dozen vendors serve it.", "{r1_vendor_code} is cheapest by Twice the margin.",
                  "There is one vendor at {r1_rs_per_mt}.", "It runs a single lane.", "V029 is the 5th cheapest."):
        text, source, events = AP.fill_or_fallback(reply, q, raw)
        assert source == "template" and any(e["event"] == "hallucination_blocked" for e in events), reply


def test_words_that_are_not_counts_are_allowed():
    q, raw = _lane_raw()
    for reply in ("{r1_vendor_code} is one of the cheapest, at {r1_rs_per_mt} per MT.", "The first row is {r1_vendor_code}; no one else is close.", "Vendors are listed cheapest first, from {r1_vendor_code}."):
        text, source, events = AP.fill_or_fallback(reply, q, raw)
        assert source == "llm" and events == [], (reply, events)


def test_clean_placeholder_only_summary_with_row_count_still_passes():
    q, raw = _lane_raw()
    text, source, events = AP.fill_or_fallback("{n_rows} rows: {r1_vendor_code} is cheapest at {r1_rs_per_mt} per MT with {r1_lrs} LRs and a margin of {r1_margin_pct}.", q, raw)
    assert source == "llm" and events == [] and "{" not in text
