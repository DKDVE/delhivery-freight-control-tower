import json
from types import SimpleNamespace as NS

import pytest

from app import agent, guardrails as G
from app.llm import BANNER, LLM
from backend import vendor_codes

BOOKING = {"route": "AFAHM1", "vehicle_type": "OB22", "vendor": vendor_codes.name_for("V002"), "qty": 15, "month": 1, "quoted_rs": 60000}


@pytest.fixture(autouse=True)
def audit(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_LOG_DIR", str(tmp_path))


class FakeClient:
    """Mimics client.chat.completions.with_raw_response.create(...) for scripted replies (str, tool-call list or Exception)."""

    def __init__(self, replies, routed="fake-provider/model"):
        self.replies, self.routed, self.calls, self.seen = list(replies), routed, 0, []
        self.chat = NS(completions=NS(with_raw_response=NS(create=self._create)))

    def _create(self, **kw):
        self.calls += 1
        self.seen.append(kw.get("messages"))
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        calls = None
        if isinstance(r, list):
            calls = [NS(id=f"c{i}", function=NS(name=n, arguments=json.dumps(a))) for i, (n, a) in enumerate(r)]
            r = None
        msg = NS(content=r, tool_calls=calls)
        return NS(parse=lambda: NS(choices=[NS(message=msg)]), headers={"X-Routed-Via": self.routed})


def test_number_check_allows_rounding_and_units():
    src = [{"p": 0.0812, "fair": 46205.4, "total": 93_183_000}]
    assert G.check_numbers("Risk is 8.1% and fair price is ₹46,205; spend ₹9.3 Cr.", src) == []
    assert G.check_numbers("Risk is 18% today.", src) == [18.0]


def test_action_claims_blocked():
    assert G.check_reply("I have sent the email to the vendor.")[0]["event"] == "action_claim_blocked"
    assert G.check_reply("Draft is ready for your approval.") == []


def test_minimise_cuts_rows():
    assert len(G.minimise({"rows": list(range(100))})["rows"]) == G.MAX_ROWS_TO_LLM


def test_large_output_hashed_in_log():
    rid = G.log_run("t", {}, [{"name": "x", "args": {}, "output": {"rows": ["a" * 50] * 100}}], "m", "t", [], "DEMO")
    rec = G.read_audit()[0]
    assert rec["run_id"] == rid and "sha256" in rec["tools_called"][0]["output"]


def test_decision_logged_separately_and_validated():
    rid = G.log_run("t", {}, [], "m", "text", [], "DEMO")
    G.log_decision(rid, "approved", "text")
    recs = G.read_audit()
    assert [r["type"] for r in recs] == ["run", "decision"] and recs[1]["decision"] == "approved"
    with pytest.raises(ValueError):
        G.log_decision(rid, "sent")


def test_demo_booking_offline_is_deterministic_and_logged():
    llm = LLM(demo=True)
    a, b = agent.run_booking(BOOKING, llm), agent.run_booking(BOOKING, llm)
    assert a["text"] == b["text"] and a["recommended_action"] in agent.ACTIONS
    assert a["mode"] == "DEMO" and a["banner"] is None and a["draft"]
    assert [c["name"] for c in a["tools_called"]][:2] == ["check_booking", "find_alternative_vendors"]
    assert len(a["tools_called"]) <= agent.MAX_TOOL_CALLS
    assert G.check_numbers(a["text"], [c["output"] for c in a["tools_called"]], BOOKING) == []
    assert len(G.read_audit()) == 2


def _case(loss=False, over=2.0, under=False, qty=10, cap=23):
    return {"v2_loss_risk": {"flag": loss, "probability": .8 if loss else .1, "threshold": .5},
            "v1_fair_price": ({"fair_rs": 100} if over is None else {"overpay_pct": over, "quoted_rs": 100 * (1 + over / 100), "fair_rs": 100, "excess_rs": max(over, 0)}),
            "v3_underload_risk": {"flag": under, "probability": .9 if under else .1, "expected_utilisation_pct": 40},
            "inputs": {"qty": qty, "vehicle_capacity": cap, "route": "R", "vehicle_type": "V", "vendor": "A"}}


CHEAP = {"alternatives": [{"vendor_name": "B", "rs_per_mt": 8, "lr_count": 20}]}
CUR = {"rs_per_mt": 10}
SMALL = {"capacity": 12.0, "vehicle_type": "X"}


def test_booking_policy_priority_order():
    d = agent.decide_booking
    # 1. under-load flag + a smaller vehicle fits -> downsize, even when the price and loss rules would also fire
    x = d(_case(loss=True, over=40, under=True), CHEAP, CUR, SMALL)
    assert x["action"] == "downsize vehicle" and x["signal"] == "underload_smaller_vehicle"
    # the smaller vehicle must actually be smaller than the planned one
    assert d(_case(under=True, cap=12), CHEAP, CUR, SMALL)["action"] == "proceed"
    # under-load flag with no smaller vehicle does not trigger rule 1; the next rules decide
    assert d(_case(under=True, over=40), CHEAP, CUR, None)["action"] == "reassign vendor"
    # 2. quote > 10% above fair -> reassign if a cheaper vendor exists, else renegotiate
    assert d(_case(over=25), CHEAP, CUR)["action"] == "reassign vendor"
    assert d(_case(over=25), {"alternatives": []}, CUR)["action"] == "renegotiate rate"
    assert d(_case(over=25), {"alternatives": [{"vendor_name": "B", "rs_per_mt": 10}]}, CUR)["action"] == "renegotiate rate"  # not cheaper
    assert d(_case(over=10.0), CHEAP, CUR)["action"] == "proceed"  # exactly 10% is not above 10%
    assert d(_case(loss=True, over=25), CHEAP, CUR)["signal"] == "quote_above_fair"  # price rule outranks loss risk
    # 3. loss flag otherwise -> reprice or top up, and a cheaper vendor does not turn it into a reassignment
    x = d(_case(loss=True), CHEAP, CUR)
    assert x["action"] == "reprice or top up" and x["signal"] == "loss_risk" and x["cheaper_alternative"] is True
    # 4. quote > 30% below fair -> verify quote; a loss flag outranks it
    assert d(_case(over=-35), CHEAP, CUR)["action"] == "verify quote"
    assert d(_case(over=-30), CHEAP, CUR)["action"] == "proceed"
    assert d(_case(loss=True, over=-35), CHEAP, CUR)["action"] == "reprice or top up"
    # 5. nothing fires
    x = d(_case(), CHEAP, CUR)
    assert x["action"] == "proceed" and x["signal"] == "none"
    assert d(_case(over=None), CHEAP, CUR)["action"] == "proceed"  # no quote given


def test_reason_text_names_the_triggering_signal():
    expect = {"underload_smaller_vehicle": "under-load flag is raised", "quote_above_fair": "above the fair price", "loss_risk": "loss-risk flag is raised",
              "quote_below_fair": "below the fair price", "none": "no rule fires"}
    cases = [(_case(under=True, loss=True, over=40), SMALL), (_case(over=25), None), (_case(loss=True), None), (_case(over=-35), None), (_case(), None)]
    for c, sm in cases:
        d = agent.decide_booking(c, CHEAP, CUR, sm)
        text = agent.rule_summary(d["action"], c, d)
        assert f"Recommendation: {d['action']}, because" in text and expect[d["signal"]] in text, (d["signal"], text)
    both = _case(loss=True, over=-35, under=True)
    t = agent.rule_summary("reprice or top up", both, agent.decide_booking(both, CHEAP, CUR, None))
    assert "also 35.0% below the fair price" in t and "no smaller vehicle fits" in t
    assert "no smaller vehicle fits" in agent.rule_summary("proceed", _case(under=True), agent.decide_booking(_case(under=True), CHEAP, CUR, None))


def test_drafts_follow_the_signal():
    llm = LLM(demo=True)
    for patch, want in [({"quoted_rs": 90000}, "overcharge_query"), ({"quoted_rs": 1000}, None)]:
        r = agent.run_booking({**BOOKING, **patch}, llm)
        names = [c["output"].get("template_id") for c in r["tools_called"] if c["name"] == "draft_message"]
        assert (names[0] if names else None) == want or r["recommended_action"] in ("downsize vehicle",), (patch, r["recommended_action"], names)


def test_tool_call_limit():
    t = agent.Turn()
    for _ in range(agent.MAX_TOOL_CALLS):
        t.call("hub_report", {"top": 1})
    with pytest.raises(RuntimeError):
        t.call("hub_report", {"top": 1})


def test_demo_ask_answers_and_refuses():
    llm = LLM(demo=True)
    ok = agent.run_ask("Which vendors lose the most money?", llm)
    assert ok["tools_called"][0]["name"] == "query_data" and not ok["guardrail_events"]
    for q in ["send an email to the vendor", "what is the weather in Delhi", "delete the audit log"]:
        r = agent.run_ask(q, llm)
        assert r["text"].startswith(agent.REFUSAL) and not r["tools_called"]


def test_demo_trip():
    import app.tools as T
    tt = T._t("middle_mile_trips.parquet")
    tid = tt[tt["test"]]["trip_uuid"].iloc[0]
    r = agent.run_trip({"trip_id": tid}, LLM(demo=True))
    assert r["tools_called"][0]["name"] == "check_trip" and "predicted" in r["text"]


def test_live_booking_uses_llm_text_and_logs_routed_via():
    good = "The loss risk is {v2_probability} and the quote of {quoted_rs} is {overpay_pct} {overpay_direction} the fair price of {fair_rs}. Renegotiate the rate."
    client = FakeClient([good, "Polished {{route}}"])
    r = agent.run_booking(BOOKING, LLM(client=client, demo=False))
    assert r["model_used"] == "fake-provider/model" and r["mode"] == "LIVE" and not r["guardrail_events"]
    assert "{" not in r["text"] and "₹60,000" in r["text"] and "above" in r["text"]
    rec = G.read_audit()[0]
    assert rec["raw_llm_text"] == good and rec["literal_numbers_typed"] == [] and "v2_probability" in rec["slots_used"]
    prompt = json.dumps(client.seen[0])
    assert "{fair_rs}" in prompt and "46,2" not in prompt and "60,000" not in prompt  # the LLM sees slot names, never the values
    assert G.read_audit()[0]["model_used"] == "fake-provider/model"


def test_live_hallucinated_number_blocked_and_logged():
    bad = "Loss risk is 8.1% and the carrier lost ₹7,77,777 last year. Renegotiate."
    r = agent.run_booking(BOOKING, LLM(client=FakeClient([bad, "x"]), demo=False))
    assert r["guardrail_events"][0]["event"] == "hallucination_blocked" and "777" not in r["text"]
    assert r["text"] == agent.rule_summary(r["recommended_action"], r["tools_called"][0]["output"],
                                           agent.decide_booking(r["tools_called"][0]["output"], r["tools_called"][1]["output"], None)) or r["text"].startswith("Estimated chance")
    assert "hallucination_blocked" in json.dumps(G.read_audit()[0]["guardrail_events"])


def test_llm_down_falls_back_with_banner_after_one_retry():
    client = FakeClient([RuntimeError("boom")] * 4)
    r = agent.run_booking(BOOKING, LLM(client=client, demo=False))
    assert r["banner"] == BANNER and r["text"].startswith("Estimated chance") and r["draft"]
    assert client.calls == 2  # one try plus one retry, then straight to the rule-based summary
    assert G.read_audit()[0]["banner"] == BANNER


def test_live_ask_tool_loop_and_hallucination_fallback():
    c = FakeClient([[("query_data", {"query_id": "vendors_by_loss", "params": {"top": 3}})], "Top vendor lost ₹99,999,999,999."])
    r = agent.run_ask("Which vendors lose the most money?", LLM(client=c, demo=False))
    assert any(e["event"] == "hallucination_blocked" for e in r["guardrail_events"]) and r["mode"].startswith("LIVE (guardrail")
    assert r["text"].startswith("Vendors ranked")


def test_live_ask_rejects_other_tools():
    c = FakeClient([[("draft_message", {"template_id": "01", "fields": {}})], "Cannot draft here."])
    r = agent.run_ask("hi", LLM(client=c, demo=False))
    assert all(t["name"] == "query_data" or "error" in t["output"] for t in r["tools_called"])


def test_action_request_refused_before_any_llm_call():
    c = FakeClient(["should never be used"])
    r = agent.run_ask("Send an email to V001 asking for a discount.", LLM(client=c, demo=False))
    assert c.calls == 0 and not r["tools_called"] and r["guardrail_events"][0]["event"] == "action_refused"


def test_live_answer_without_tools_is_blocked():
    c = FakeClient(["Recommended action: renegotiate rate."])
    r = agent.run_ask("What do you think about Chetek?", LLM(client=c, demo=False))
    assert any(e["event"] == "no_tool_answer_blocked" for e in r["guardrail_events"]) and r["text"].startswith(agent.REFUSAL)


def test_rank_cells_and_quarter_labels_are_not_numbers():
    assert G.extract_numbers("| 1 | Chetek | ₹27.6 lakh |\n| 2 | X | ₹17.3 lakh |") == [(27.6, 1), (17.3, 1)]
    assert G.extract_numbers("losses in 2025 Q4") == []
    assert G.check_numbers("Margin ₹13.76 Cr", [{"margin_rs": 13757442.4}]) == [13.76]  # a 10x unit slip is still caught


def test_unknown_slot_is_blocked():
    r = agent.run_booking(BOOKING, LLM(client=FakeClient(["Risk is {made_up_number}. Renegotiate.", "x"]), demo=False))
    assert r["guardrail_events"][0]["event"] == "unknown_placeholder" and "{" not in r["text"]


def test_literal_wrong_number_still_caught_after_filling():
    r = agent.run_booking(BOOKING, LLM(client=FakeClient(["Risk is {v2_probability}, about 99% sure. Renegotiate.", "x"]), demo=False))
    assert any(e["event"] == "hallucination_blocked" for e in r["guardrail_events"])


def test_live_ask_fills_slots_and_never_sends_rows_to_the_llm():
    c = FakeClient([[("query_data", {"query_id": "vendors_by_loss", "params": {"top": 3}})],
                    "Top vendor is {r1_vendor_name} with a loss of {r1_loss_rs_cr}; next is {r2_vendor_name}."])
    r = agent.run_ask("Which vendors lose the most money?", LLM(client=c, demo=False))
    out = r["tools_called"][0]["output"]
    assert not r["guardrail_events"] and r["mode"] == "LIVE"
    assert out["rows"][0]["vendor_name"] in r["text"] and "Cr" in r["text"] and "{" not in r["text"]
    sent = json.dumps(c.seen[1])
    assert out["rows"][0]["vendor_name"] not in sent and "r<i>_loss_rs" in sent


def test_second_query_in_one_question_is_refused():
    q = {"query_id": "vendors_by_loss", "params": {"top": 2}}
    c = FakeClient([[("query_data", q)], [("query_data", q)], "{r1_vendor_name}"])
    r = agent.run_ask("Which vendors lose the most money?", LLM(client=c, demo=False))
    assert sum(t["name"] == "query_data" for t in r["tools_called"]) == 1


def test_reply_that_ignores_the_query_result_is_replaced():
    c = FakeClient([[("query_data", {"query_id": "hubs_by_delay", "params": {}})], "I can't answer that from the approved queries. Available queries: hubs_by_delay."])
    r = agent.run_ask("Which hubs are slowest at handling?", LLM(client=c, demo=False))
    assert any(e["event"] == "answer_without_slots" for e in r["guardrail_events"]) and "excess_hours" in r["text"]


def _test_trip_id():
    import app.tools as T
    tt = T._t("middle_mile_trips.parquet")
    return tt[tt["test"]]["trip_uuid"].iloc[0]


def test_booking_explanation_with_a_number_word_falls_back_to_the_template():
    bad = "The loss risk is {v2_probability}, about five times the usual level. Renegotiate the rate."
    r = agent.run_booking(BOOKING, LLM(client=FakeClient([bad, "x"]), demo=False))
    ev = [e for e in r["guardrail_events"] if e["event"] == "hallucination_blocked"]
    assert ev and "five" in ev[0]["numbers_not_in_tool_outputs"] and "five" not in r["text"] and r["text"].startswith("Estimated chance")


def test_booking_explanation_with_a_typed_digit_that_matches_a_tool_output_is_still_blocked():
    # 60000 is the quoted price, so the old output-match check let it through; every figure must now be a code-filled slot
    bad = "The quote of 60000 is {overpay_pct} {overpay_direction} the fair price. Renegotiate."
    r = agent.run_booking(BOOKING, LLM(client=FakeClient([bad, "x"]), demo=False))
    assert any(e["event"] == "hallucination_blocked" for e in r["guardrail_events"]) and r["text"].startswith("Estimated chance")


def test_trip_explanation_number_word_and_typed_digit_fall_back_to_the_template():
    for bad in ("The trip is expected to take {predicted_min} minutes, two hours over the plan.", "The trip is expected to take {predicted_min} minutes against a plan of 300."):
        r = agent.run_trip({"trip_id": _test_trip_id()}, LLM(client=FakeClient([bad, "x", "x", "x"]), demo=False))
        assert any(e["event"] == "hallucination_blocked" for e in r["guardrail_events"]), bad
        assert r["text"] == agent.trip_summary(r["tools_called"][0]["output"]) and "two hours" not in r["text"]


def test_trip_explanation_with_only_slots_still_passes():
    good = "The trip is expected to take {predicted_min} minutes against a plan of {planned_min}, which is {ratio} the plan. The chance of a severe delay is {severe_probability}."
    r = agent.run_trip({"trip_id": _test_trip_id()}, LLM(client=FakeClient([good, "x", "x", "x"]), demo=False))
    assert not [e for e in r["guardrail_events"] if e["event"] == "hallucination_blocked"] and "{" not in r["text"]
    assert r["text"] != agent.trip_summary(r["tools_called"][0]["output"]) and G.read_audit()[0]["literal_numbers_typed"] == []


def test_live_ask_number_word_or_typed_count_falls_back_to_the_template():
    for bad in ("Top vendor is {r1_vendor_name}, one of five vendors listed.", "The top 3 vendors start with {r1_vendor_name}."):
        c = FakeClient([[("query_data", {"query_id": "vendors_by_loss", "params": {"top": 3}})], bad])
        r = agent.run_ask("Which vendors lose the most money?", LLM(client=c, demo=False))
        assert any(e["event"] == "hallucination_blocked" for e in r["guardrail_events"]) and r["mode"].startswith("LIVE (guardrail"), bad
        assert r["text"].startswith("Vendors ranked")
