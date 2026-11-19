"""Smoke test for live mode: python -m app.live_check
Needs LLM_API_KEY in .env, plus LLM_BASE_URL and LLM_MODEL for whichever provider you're pointing at - freellmapi
locally (the defaults), or Groq directly for the cloud (LLM_BASE_URL=https://api.groq.com/openai/v1,
LLM_MODEL=openai/gpt-oss-120b): this is how to verify the pinned model ID actually works on Groq before deploying."""
import os
import sys

from app import agent, guardrails as G
from app.llm import LLM
from backend import vendor_codes


def main() -> int:
    if not os.getenv("LLM_API_KEY"):
        print("LLM_API_KEY is not set: copy .env.example to .env and fill it in. The app would run in DEMO_MODE.")
        return 1
    llm = LLM(demo=False)
    print("provider:", llm.provider, "| endpoint:", llm.base_url, "| model:", llm.model)
    r = agent.run_booking({"route": "AFAHM1", "vehicle_type": "OB22", "vendor": vendor_codes.name_for("V002"), "qty": 15, "month": 1, "quoted_rs": 60000}, llm)
    print("mode:", r["mode"], "| model_used (X-Routed-Via):", r["model_used"], "| banner:", r["banner"], "| guardrails:", r["guardrail_events"])
    print(r["text"])
    q = agent.run_ask("Which vendors lose the most money? Show the top 3.", llm)
    print("ask ->", q["mode"], q["model_used"], [c["name"] for c in q["tools_called"]], q["guardrail_events"])
    print(q["text"][:400])
    print("audit records:", len(G.read_audit()))
    return 0 if r["mode"] == "LIVE" and not r["banner"] else 2


if __name__ == "__main__":
    sys.exit(main())
