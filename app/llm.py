"""LLM connection (OpenAI SDK against the local freellmapi proxy) and the DEMO_MODE switch.

DEMO_MODE is on when LLM_API_KEY is missing (or DEMO_MODE=1). In DEMO_MODE no LLM is called: the agent uses its
deterministic templates, so the app works fully for anyone who clones the repo.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))

DEFAULT_MODEL = "gpt-oss-120b"  # pinned: one specific freellmapi model ID, not auto routing
TEMPERATURE = 0.2
TIMEOUT_S = 30
BANNER = "LLM unavailable, rule-based summary shown"


class LLMUnavailable(RuntimeError):
    """The LLM call failed twice (or the app is in DEMO_MODE and an LLM call was attempted)."""


@dataclass
class LLMReply:
    content: str | None = None
    tool_calls: list[dict] = field(default_factory=list)
    routed_via: str | None = None


def demo_mode_default() -> bool:
    flag = os.getenv("DEMO_MODE")
    if flag is not None and flag.strip() != "":
        return flag.strip().lower() in ("1", "true", "yes", "on")
    return not os.getenv("LLM_API_KEY")


def _provider_from_url(url: str) -> str:
    """A short label for the audit log. freellmapi sets X-Routed-Via itself; direct providers (Groq in the cloud) don't,
    so the audit record needs this to say who actually answered."""
    if "groq.com" in url:
        return "groq"
    if "localhost" in url or "127.0.0.1" in url:
        return "freellmapi"
    from urllib.parse import urlparse
    return urlparse(url).hostname or "unknown"


class LLM:
    def __init__(self, client=None, model: str | None = None, demo: bool | None = None):
        self.demo = demo_mode_default() if demo is None else demo
        self.model = model or os.getenv("LLM_MODEL", DEFAULT_MODEL)
        self.base_url = os.getenv("LLM_BASE_URL", "http://localhost:3001/v1")
        self.provider = _provider_from_url(self.base_url)
        self._client = client

    @property
    def client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(base_url=self.base_url, api_key=os.getenv("LLM_API_KEY"))
        return self._client

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMReply:
        """One chat completion; retried once. Raises LLMUnavailable if both attempts fail."""
        if self.demo:
            raise LLMUnavailable("DEMO_MODE is on")
        kw = {"model": self.model, "messages": messages, "temperature": TEMPERATURE, "timeout": TIMEOUT_S}
        if tools:
            kw["tools"] = tools
        last = None
        for _ in range(2):
            try:
                raw = self.client.chat.completions.with_raw_response.create(**kw)
                msg = raw.parse().choices[0].message
                calls = [{"id": c.id, "name": c.function.name, "arguments": c.function.arguments} for c in (getattr(msg, "tool_calls", None) or [])]
                return LLMReply(content=msg.content, tool_calls=calls, routed_via=raw.headers.get("X-Routed-Via"))
            except Exception as e:  # noqa: BLE001  (network, rate limit, bad JSON, timeouts)
                last = e
        raise LLMUnavailable(f"{type(last).__name__}: {last}")

    def polisher(self):
        """A function for tools.draft_message: polishes tone only, placeholders must survive. None in DEMO_MODE."""
        if self.demo:
            return None

        def polish(body: str) -> str:
            r = self.chat([
                {"role": "system", "content": "You polish business emails. Improve tone and clarity only. Keep every {{placeholder}} exactly as written, "
                                              "do not add, remove or change any figure, and do not add new facts. Return only the text."},
                {"role": "user", "content": body}])
            if not r.content:
                raise LLMUnavailable("empty polish")
            return r.content.strip()
        return polish
