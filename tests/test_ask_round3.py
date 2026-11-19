"""Round 3: true totals in head-to-head, load on the lane ranking, the missing-query regression, follow-up drop reasons."""
import json
from types import SimpleNamespace as NS

import pytest

from app import agent as A
from app import ask_pipeline as AP
from app import lanes as LN
from app import placeholders as P
from app import tools as T
from app.llm import LLM
from app.queries import catalogue as CAT


@pytest.fixture(autouse=True)
def audit(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_LOG_DIR", str(tmp_path))


class FakeClient:
    """Scripted replies (str, or a list of (tool name, args) tool calls, or an Exception), same shape as tests/test_agent.py."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.chat = NS(completions=NS(with_raw_response=NS(create=self._create)))

    def _create(self, **kw):
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        calls = None
        if isinstance(r, list):
            calls = [NS(id=f"c{i}", function=NS(name=n, arguments=json.dumps(a))) for i, (n, a) in enumerate(r)]
            r = None
        msg = NS(content=r, tool_calls=calls)
        return NS(parse=lambda: NS(choices=[NS(message=msg)]), headers={"X-Routed-Via": "fake/model"})


def _llm(*replies):
    return LLM(client=FakeClient(replies), demo=False)


H2H = {"vendor_a": "V013", "vendor_b": "V023"}


def test_head_to_head_returns_true_totals_as_separate_fields():
    r = LN.vendor_compare(["V013", "V023"], None, 12)
    out = T.query_data("vendor_head_to_head", H2H)
    won = {v["vendor_code"]: v["lanes_won"] for v in r["vendors"]}
    assert out["n_rows"] == 10 and r["n_shared_lanes"] > 10          # the table is a top-10 cut of a longer list
    m = out["meta"]
    assert m["total_rows"] == r["n_shared_lanes"] and m["facts"]["shared_lanes"] == str(r["n_shared_lanes"])
    assert (m["facts"]["lanes_won_a"], m["facts"]["lanes_won_b"]) == (str(won["V013"]), str(won["V023"]))
    assert int(m["facts"]["lanes_won_a"]) + int(m["facts"]["lanes_won_b"]) == r["n_shared_lanes"]
    slots, descs = P.ask_slots(out, max_rows=5)
    assert slots["shared_lanes"] == m["facts"]["shared_lanes"] and {"shared_lanes", "lanes_won_a", "lanes_won_b"} <= set(descs)


def test_head_to_head_summary_uses_the_code_filled_totals_and_blocks_typed_ones():
    out = T.query_data("vendor_head_to_head", H2H)
    f = out["meta"]["facts"]
    good = "{vendor_a} and {vendor_b} share {shared_lanes} lane groups; {vendor_a} is cheaper on {lanes_won_a} and {vendor_b} on {lanes_won_b}."
    text, source, events = AP.fill_or_fallback(good, "vendor_head_to_head", out)
    assert source == "llm" and events == [] and f"share {f['shared_lanes']} lane groups" in text and f["lanes_won_a"] in text
    typed = f"They share {f['shared_lanes']} lane groups."      # the right figure, but typed by the model
    text, source, events = AP.fill_or_fallback(typed, "vendor_head_to_head", out)
    assert source == "template" and any(e["event"] == "hallucination_blocked" for e in events)
    assert f"share {f['shared_lanes']} lane groups" in text and f["lanes_won_a"] in text   # the template states the true totals too


def test_head_to_head_result_is_labelled_top_n_of_m_and_the_prompt_names_the_slots():
    res = AP.run_ask_structured("compare V013 and V023")
    assert res["kind"] == "ok" and res["query_id"] == "vendor_head_to_head"
    assert res["result"]["row_count"] == 10 and res["result"]["total_rows"] > 10
    assert str(res["result"]["total_rows"]) in res["summary"]
    plain = AP.run_ask_structured("Which vendors lose the most money?")        # an ordinary query is not a cut: total == rows
    assert plain["result"]["total_rows"] == plain["result"]["row_count"]
    seen = []

    class Spy(LLM):
        def chat(self, msgs, tools=None, **kw):
            seen.append(msgs[-1]["content"])
            return super().chat(msgs, tools=tools, **kw)

    llm = Spy(client=FakeClient([[("query_data", {"query_id": "vendor_head_to_head", "params": H2H})], "{vendor_a} vs {vendor_b}: {shared_lanes} shared lane groups.", "[]"]), demo=False)
    AP.run_ask_structured("compare V013 and V023", [], llm=llm)
    assert "{shared_lanes}" in seen[1] and "never the row count" in seen[1]


def test_lane_ranking_with_a_load_uses_the_smallest_fitting_vehicle():
    plain = T.query_data("lane_vendor_ranking", {"origin": "PATALGANGA", "destination": "D-NIYOL"})
    ld = T.query_data("lane_vendor_ranking", {"origin": "PATALGANGA", "destination": "D-NIYOL", "qty_mt": 15})
    rec = LN.lane_options("PATALGANGA", "D-NIYOL", qty_mt=15)["recommendation"]
    assert rec and {r["vehicle_type"] for r in ld["rows"]} == {rec["vehicle_type"]} and ld["rows"][0]["vendor_code"] == rec["vendor_code"]
    assert rec["vehicle_type"] != plain["rows"][0]["vehicle_type"] and ld["params"]["qty_mt"] == 15.0
    assert ld["rows"][0]["rate_x_load_rs"] == pytest.approx(ld["rows"][0]["rs_per_mt"] * 15)
    assert ld["meta"]["facts"]["load_mt"] == "15 MT" and rec["vehicle_type"] in ld["meta"]["notes"][0]


def test_load_is_in_the_catalogue_sql_filters_and_tool_schema():
    spec = CAT.get("lane_vendor_ranking")["params"]["qty_mt"]
    assert spec["type"] == "float" and (spec["min"], spec["max"]) == (0.1, 100)
    assert "15 MT" in CAT.sql_equivalent("lane_vendor_ranking", {"origin": "A", "destination": "B", "months": 6, "qty_mt": 15.0}) or \
        "15" in CAT.sql_equivalent("lane_vendor_ranking", {"origin": "A", "destination": "B", "months": 6, "qty_mt": 15.0})
    assert "qty_mt" in json.dumps(A.ASK_SCHEMAS)
    res = AP.run_ask_structured("Who is cheapest from Patalganga to D-Niyol for a 15 MT load?")
    assert res["result"]["filters_applied"]["qty_mt"] == 15.0 and "qty_mt" in res["analysis"]["params"]
    assert any(c["name"] == "rate_x_load_rs" for c in res["result"]["columns"])


@pytest.mark.parametrize("bad", [0, -3, 1000, "abc", float("nan")])
def test_lane_ranking_rejects_an_unreasonable_load(bad):
    with pytest.raises(T.ToolError):
        T.query_data("lane_vendor_ranking", {"origin": "PATALGANGA", "destination": "D-NIYOL", "qty_mt": bad})


def test_a_load_that_cannot_be_applied_is_stated_by_code_in_the_answer():
    res = AP.run_ask_structured("Who is cheapest from Patalganga to D-Niyol for a 99 MT load?")
    assert res["kind"] == "ok" and res["params"]["qty_mt"] == 99.0
    assert "could not be applied" in res["summary"] and "99 MT" in res["summary"] and res["result"]["notes"]


def test_a_load_on_a_lane_with_no_direct_service_says_so_on_the_empty_answer():
    out = T.query_data("lane_vendor_ranking", {"origin": "AHMEDABAD", "destination": "24 PRG-N", "qty_mt": 10})
    assert out["n_rows"] == 0 and "could not be applied" in " ".join(out["meta"]["notes"]) and "10 MT" in " ".join(out["meta"]["notes"])


PAT = "Who is cheapest from Patalganga to D-Niyol for a 15 MT load?"


def test_the_picker_gets_a_load_parameter_so_it_no_longer_declines_the_question():
    """Root cause (reproduced LIVE, 2 of 5 tries): the schema had no load parameter, so the model answered in plain text that it had no query for a specific
    load size (no tool call), choose_query_llm returned None and the pipeline gave the not_approved refusal. The schema now offers qty_mt."""
    assert "qty_mt as a plain number of tonnes" in json.dumps(A.ASK_SCHEMAS)
    call = [("query_data", {"query_id": "lane_vendor_ranking", "params": {"origin": "Patalganga", "destination": "D-Niyol", "qty_mt": 15}})]
    qid, params, _ = AP.choose_query_llm(_llm(call), PAT, [])
    assert qid == "lane_vendor_ranking" and params["qty_mt"] == 15
    out = T.query_data(qid, params)
    assert out["status"] == "ok" and out["rows"][0]["vehicle_type"] == LN.lane_options("PATALGANGA", "D-NIYOL", qty_mt=15)["recommendation"]["vehicle_type"]
    call = [("query_data", {"query_id": "lane_vendor_ranking", "params": {"origin": "Patalganga", "destination": "D-Niyol", "qty_mt": "15"}})]
    qid, params, _ = AP.choose_query_llm(_llm(call), PAT, [])
    assert T.query_data(qid, params)["params"]["qty_mt"] == 15.0          # a number sent as text still validates


def test_a_picker_call_with_no_params_is_filled_from_the_question_not_the_default_lane():
    """Second LIVE finding: the model sometimes calls lane_vendor_ranking with no params at all, which silently answered for the busiest lane (wrong lane)."""
    qid, params, _ = AP.choose_query_llm(_llm([("query_data", {"query_id": "lane_vendor_ranking"})]), PAT, [])
    assert qid == "lane_vendor_ranking" and params == {"origin": "PATALGANGA", "destination": "D-NIYOL", "qty_mt": 15.0}
    qid, params, _ = AP.choose_query_llm(_llm([("query_data", {"query_id": "vendor_head_to_head"})]), "compare V013 and V023", [])
    assert params == {"vendor_a": "V013", "vendor_b": "V023"}
    qid, params, _ = AP.choose_query_llm(_llm([("query_data", {"query_id": "lane_vendor_ranking"})]), "show me the busiest lane", [])
    assert params == {}                                                   # a question that names no lane keeps whatever the model sent


def test_plain_text_decline_is_still_a_refusal_not_a_crash():
    qid, params, _ = AP.choose_query_llm(_llm("Sorry, I cannot do that."), "something unrelated", [])
    assert qid is None and params == {}


def test_demo_router_reads_the_load_from_the_question():
    assert A.route_question(PAT) == ("lane_vendor_ranking", {"origin": "PATALGANGA", "destination": "D-NIYOL", "qty_mt": 15.0})
    assert A.route_question("cheapest vendor Hazira to Khadoli 22.5 tonnes SB04")[1]["qty_mt"] == 22.5


def _proposals():
    return json.dumps([
        {"query_id": "no_such_query", "params": {}},                                                # unknown_query_id
        {"query_id": "vendor_overpay_ranking", "params": {"top": 9999}},                            # invalid_param (out of range)
        {"query_id": "vendors_by_loss", "params": {"zone": "ATLANTIS"}},                            # same query as the current one (checked before the params)
        "not an object",                                                                            # proposal_not_an_object
        {"query_id": "lane_vendor_ranking", "params": {"origin": "PATALGANGA", "destination": "D-NIYOL", "vehicle_type": "ZZ99"}},   # empty_result
        {"query_id": "margin_by_zone", "params": {}},                                               # kept
        {"query_id": "margin_by_zone", "params": {}},                                               # duplicate_query_id
    ])


def test_followup_drop_reasons_are_recorded_without_changing_what_is_dropped():
    reasons = []
    picked, dropped = AP.llm_followups(_llm(_proposals()), "vendors_by_loss", "q", reasons=reasons)
    picked2, dropped2 = AP.llm_followups(_llm(_proposals()), "vendors_by_loss", "q")          # same call without the log
    assert (dropped, [p["query_id"] for p in picked]) == (dropped2, [p["query_id"] for p in picked2])
    got = [r["reason"] for r in reasons]
    assert got[:5] == ["unknown_query_id", "invalid_param", "same_query_as_current", "proposal_not_an_object", "empty_result"]
    assert len(reasons) == dropped and all(set(r) == {"query_id", "reason", "detail"} for r in reasons)


def test_unparseable_followup_reply_is_logged_too():
    reasons = []
    AP.llm_followups(_llm("here are some ideas: ..."), "vendors_by_loss", "q", reasons=reasons)
    assert [r["reason"] for r in reasons] == ["reply_not_json"]


def test_drop_reasons_reach_the_audit_record_and_the_result():
    from app import guardrails as G
    llm = _llm([("query_data", {"query_id": "vendors_by_loss", "params": {"top": 3}})], "{r1_vendor_name} lost the most.", _proposals())
    res = AP.run_ask_structured("Which vendors lose the most money?", [], llm=llm)
    assert res["followups_dropped"] == len(res["followups_drop_reasons"]) > 0
    rec = [r for r in G.read_audit() if r.get("run_id") == res["run_id"]][0]
    assert rec["followups_drop_reasons"] == res["followups_drop_reasons"]


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("AUDIT_LOG_DIR", str(tmp_path))
    from fastapi.testclient import TestClient
    from backend import main as M
    with TestClient(M.app) as c:
        yield c, M


def _ask(api, monkeypatch, question, *replies):
    c, M = api
    monkeypatch.setattr(M, "get_llm", lambda: _llm(*replies))
    ev = {}
    for block in c.post("/api/ask", json={"question": question}).text.strip().split("\n\n"):
        name, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        ev[name] = data
    return ev


H2H_CALL = [("query_data", {"query_id": "vendor_head_to_head", "params": H2H})]
PAT_FAIL_CALL = [("query_data", {"query_id": "lane_vendor_ranking", "params": {"origin": "Patalganga", "destination": "D-Niyol"}})]   # the exact cloud shape: no qty_mt


def test_sse_n_rows_placeholder_is_the_true_total_never_the_capped_count(api, monkeypatch):
    ev = _ask(api, monkeypatch, "compare V013 and V023", H2H_CALL, "Across {n_rows} shared lanes, {vendor_a} is cheaper on {lanes_won_a}.", "[]")
    assert ev["result"]["row_count"] == 10 and ev["result"]["total_rows"] == 56
    assert ev["summary"]["source"] == "llm" and "Across 56 shared lanes" in ev["summary"]["text"] and "Across 5 " not in ev["summary"]["text"] and "Across 10 " not in ev["summary"]["text"]


def test_sse_a_correct_number_next_to_the_wrong_noun_falls_back(api, monkeypatch):
    for bad in ("The table shows {n_rows} rows.", "Across {rows_shown} shared lanes, {vendor_a} wins most.", "{lanes_won_a} shared lanes were compared."):
        ev = _ask(api, monkeypatch, "compare V013 and V023", H2H_CALL, bad, "[]")
        assert ev["summary"]["source"] == "template" and "wrong_count_claim" in ev["summary"]["guardrail_events"], bad
        assert "56 lane groups" in ev["summary"]["text"] and "39" in ev["summary"]["text"]


def test_sse_number_word_in_the_summary_falls_back(api, monkeypatch):
    ev = _ask(api, monkeypatch, "Who is cheapest from Patalganga to D-Niyol for a 15 MT load?", PAT_FAIL_CALL,
              "The cheapest vendor for a fifteen MT shipment from Patalganga to D-Niyol is {r1_vendor_code} at {r1_rs_per_mt} per MT.", "[]")
    assert ev["summary"]["source"] == "template" and "hallucination_blocked" in ev["summary"]["guardrail_events"] and "fifteen" not in ev["summary"]["text"]


def test_sse_load_is_backfilled_when_the_model_sent_only_origin_and_destination(api, monkeypatch):
    ev = _ask(api, monkeypatch, "Who is cheapest from Patalganga to D-Niyol for a 15 MT load?", PAT_FAIL_CALL,
              "For {load_mt}, the cheapest is {r1_vendor_code} on {load_vehicle} at {r1_rate_x_load_rs}.", "[]")
    assert ev["analysis"]["params"]["qty_mt"] == 15.0 and ev["result"]["filters_applied"]["qty_mt"] == 15.0
    assert {r["vehicle_type"] for r in ev["result"]["rows"]} == {"SB07"} and ev["summary"]["source"] == "llm"
    assert "15 MT" in ev["summary"]["text"] and "SB07" in ev["summary"]["text"] and "Ranked for the smallest vehicle" in ev["summary"]["text"]


def test_backfill_never_overrides_what_the_model_sent():
    call = [("query_data", {"query_id": "lane_vendor_ranking", "params": {"origin": "Hazira", "destination": "Khadoli", "vehicle_type": "SB01", "qty_mt": 5}})]
    qid, params, _ = AP.choose_query_llm(_llm(call), "cheapest vendor from Hazira to Khadoli for a 20 MT load SB04", [])
    assert params["vehicle_type"] == "SB01" and params["qty_mt"] == 5 and params["origin"] == "Hazira"      # the model's values win
    call = [("query_data", {"query_id": "lane_vendor_ranking", "params": {"origin": "Hazira", "destination": "Khadoli", "vehicle_type": ""}})]
    qid, params, _ = AP.choose_query_llm(_llm(call), "cheapest vendor from Hazira to Khadoli for a 20 MT load SB04", [])
    assert params["vehicle_type"] == "SB04" and params["qty_mt"] == 20.0                                     # blanks count as left out
    call = [("query_data", {"query_id": "vendor_head_to_head", "params": {"vendor_a": "V013"}})]
    assert AP.choose_query_llm(_llm(call), "compare V013 and V023", [])[1] == {"vendor_a": "V013", "vendor_b": "V023"}
    call = [("query_data", {"query_id": "vendors_by_loss", "params": {"top": 3}})]                            # a different query than the router would pick: untouched
    assert AP.choose_query_llm(_llm(call), "compare V013 and V023", [])[1] == {"top": 3}


def test_a_load_the_chosen_query_cannot_use_is_stated_in_the_answer(api, monkeypatch):
    c, M = api
    c_ev = _ask(api, monkeypatch, "Which vendors lose the most money on a 15 MT load?", [("query_data", {"query_id": "vendors_by_loss", "params": {}})], "{r1_vendor_name} lost the most.", "[]")
    assert "15 MT was mentioned, but this query does not use a load" in c_ev["summary"]["text"]
    demo = AP.run_ask_structured("Which vendors lose the most money on a 15 MT load?")        # DEMO router path says it too
    assert "does not use a load" in demo["summary"]
    plain = AP.run_ask_structured("Which vendors lose the most money?")
    assert "load" not in plain["summary"].lower()
