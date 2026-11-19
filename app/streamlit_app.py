"""Freight Control Tower (GenAI bonus). Run: streamlit run app/streamlit_app.py"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from app import agent, guardrails as G, tools as T  # noqa: E402
from app.llm import LLM  # noqa: E402
from backend import vendor_codes  # noqa: E402

DEFAULT_VENDOR = vendor_codes.name_for("V002")

st.set_page_config(page_title="Freight Control Tower", layout="wide")
llm = LLM()

st.sidebar.title("Freight Control Tower")
page = st.sidebar.radio("Page", ["Booking check", "Trip alert", "Ask the data", "Prompt library", "Audit log"])
st.sidebar.markdown(f"**Mode:** {'DEMO (rule-based, offline)' if llm.demo else 'LIVE (' + llm.model + ')'}")
st.sidebar.caption("Models and rules decide. The LLM only explains and drafts. Every number comes from a tool. A person approves every action; this app never sends or changes anything.")


@st.cache_data
def _options():
    d = T._defaults()
    lane = T._t("lane_vendor_rates.csv")
    return sorted(d["route"].index), d["vehicle_types"], sorted(T._lr_valid()["vendor_name"].unique()), lane


def show_result(res: dict, key: str):
    if res["banner"]:
        st.warning(res["banner"])
    for e in res["guardrail_events"]:
        st.info(f"Guardrail: {e['event']}" + (f" ({e.get('numbers_not_in_tool_outputs')})" if e.get("numbers_not_in_tool_outputs") else "") + ". The rule-based text is shown instead.")
    if res["recommended_action"]:
        st.subheader(f"Recommended action: {res['recommended_action']}")
    st.write(res["text"])
    st.caption(f"Mode: {res['mode']} | model: {res['model_used']} | tools: {', '.join(c['name'] for c in res['tools_called'])}")
    with st.expander("Tool outputs (the source of every number above)"):
        for c in res["tools_called"]:
            st.markdown(f"**{c['name']}**")
            st.json(G.minimise(c["output"]), expanded=False)
    if res.get("draft"):
        st.markdown("#### Draft message (not sent)")
        edited = st.text_area("Edit if needed", res["draft"], height=260, key=f"draft_{key}")
    else:
        edited = None
    c1, c2, c3 = st.columns(3)
    done = st.session_state.get(f"decided_{res['run_id']}")
    if done:
        st.success(f"Decision recorded: {done}. Nothing was sent or changed.")
        return
    if c1.button("Approve", key=f"a_{key}"):
        G.log_decision(res["run_id"], "approved", edited or res["text"])
        st.session_state[f"decided_{res['run_id']}"] = "approved"
        st.rerun()
    if res.get("draft") and c2.button("Save as edited", key=f"e_{key}"):
        G.log_decision(res["run_id"], "edited", edited)
        st.session_state[f"decided_{res['run_id']}"] = "edited"
        st.rerun()
    if c3.button("Reject", key=f"r_{key}"):
        G.log_decision(res["run_id"], "rejected")
        st.session_state[f"decided_{res['run_id']}"] = "rejected"
        st.rerun()


if page == "Booking check":
    st.title("Booking check")
    routes, vtypes, vendors, lane = _options()
    c1, c2, c3 = st.columns(3)
    route = c1.selectbox("Route", routes, index=routes.index("AFBAN1") if "AFBAN1" in routes else 0)
    vt = c2.selectbox("Vehicle type", vtypes, index=vtypes.index("OB22") if "OB22" in vtypes else 0)
    vendor = c3.selectbox("Vendor", vendors, index=vendors.index(DEFAULT_VENDOR) if DEFAULT_VENDOR in vendors else 0)
    c4, c5, c6 = st.columns(3)
    qty = c4.number_input("Load (MT)", 0.1, 100.0, 3.0, 0.5)
    month = c5.selectbox("Month", list(range(1, 13)), index=0)
    quoted = c6.number_input("Quoted freight (₹, optional)", 0.0, 10_000_000.0, 0.0, 500.0)
    if st.button("Check booking", type="primary"):
        b = {"route": route, "vehicle_type": vt, "vendor": vendor, "qty": qty, "month": month}
        if quoted > 0:
            b["quoted_rs"] = quoted
        st.session_state["booking_res"] = agent.run_booking(b, llm)
    if "booking_res" in st.session_state:
        show_result(st.session_state["booking_res"], "booking")

elif page == "Trip alert":
    st.title("Trip alert")
    p2 = pd.read_csv(os.path.join(T.PROC, "pred_M2.csv"))
    example = p2[p2["predicted_label"] == 1]["record_id"].iloc[0]  # an example the model flags
    tid = st.text_input("Trip ID (trip_uuid)", example)
    if st.button("Check trip", type="primary"):
        st.session_state["trip_res"] = agent.run_trip({"trip_id": tid}, llm)
    if "trip_res" in st.session_state:
        show_result(st.session_state["trip_res"], "trip")

elif page == "Ask the data":
    st.title("Ask the data")
    st.caption("Answers come only from approved read-only queries: " + ", ".join(q["query_id"] for q in T.list_queries()))
    q = st.text_input("Question", "Which vendors lose the most money?")
    if st.button("Ask", type="primary"):
        st.session_state["ask_res"] = agent.run_ask(q, llm)
    if "ask_res" in st.session_state:
        res = st.session_state["ask_res"]
        show_result(res, "ask")
        for c in res["tools_called"]:
            if c["name"] == "query_data" and c["output"].get("rows"):
                st.dataframe(pd.DataFrame(c["output"]["rows"]))

elif page == "Prompt library":
    st.title("Prompt library")
    for t in T.list_templates():
        with st.expander(f"{t['template_id']}: {t['title']}"):
            st.write("Fields: " + ", ".join(t["fields"]))
            fn = [f for f in os.listdir(T.PROMPTS) if f.endswith(f"{t['template_id']}.md")][0]
            st.markdown(open(os.path.join(T.PROMPTS, fn), encoding="utf8").read())

else:
    st.title("Audit log")
    recs = G.read_audit()
    decisions = {r["run_id"]: r for r in recs if r["type"] == "decision"}
    runs = [r for r in recs if r["type"] == "run"]
    if not runs:
        st.info("No runs yet.")
    else:
        df = pd.DataFrame([{"time": r["ts"], "page": r["page"], "mode": r["mode"], "model": r["model_used"], "tools": ", ".join(t["name"] for t in r["tools_called"]),
                            "guardrails": ", ".join(e["event"] for e in r["guardrail_events"]), "decision": decisions.get(r["run_id"], {}).get("decision", "pending"),
                            "text": r["final_text"][:120]} for r in reversed(runs)])
        st.dataframe(df, use_container_width=True)
        st.download_button("Download audit.jsonl", open(G.audit_path(), encoding="utf8").read(), "audit.jsonl")
