"""Guardrails: number check, data minimisation, never-automated actions, and the append-only audit log."""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import socket
import uuid
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LARGE_OUTPUT_CHARS = 2000
MAX_ROWS_TO_LLM = 15

# a number that is not part of an alphanumeric code (SINAS2, OB27, 2025Q4 are ignored)
_NUM = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)(?!\w|\.\d|\s?Q[1-4])|(?<![\w.])(\d[\d,]*(?:\.\d+)?)(?=x\b)")
_LIST_MARKER = re.compile(r"^\s*(?:\(?\d+[.)]|[-*•])\s+|^\s*\|\s*\d{1,2}\s*(?=\|)", re.M)  # list numbering and table rank cells

# phrases that claim an action was taken (the app only drafts; a person decides)
ACTION_CLAIMS = re.compile(
    r"\b(i|we)(?:'ve| have|'ll| will)?\s+(?:already\s+)?(sent|emailed|messaged|terminated|blacklisted|cancelled|canceled|changed the (?:price|rate)|updated the (?:price|rate)|reassigned|booked)\b"
    r"|\b(has|have) been (sent|emailed|terminated|blacklisted|cancelled|canceled)\b|\bvendor (?:is|was) (?:terminated|blacklisted)\b", re.I)


def audit_dir() -> str:
    """AUDIT_LOG_DIR (default app/logs). The cloud mounts an Azure Files share here; locally it's a plain folder."""
    return os.environ.get("AUDIT_LOG_DIR", os.path.join(ROOT, "app", "logs"))


def _replica_id() -> str:
    """Container Apps sets HOSTNAME to the replica's container ID; falls back to the hostname or the PID."""
    return os.environ.get("HOSTNAME") or os.environ.get("COMPUTERNAME") or socket.gethostname() or str(os.getpid())


def audit_path() -> str:
    """This replica's own append-only file. One file per replica (named by hostname) means concurrent writes from
    2+ replicas on the shared mount never interleave or corrupt a line; read_audit() merges every replica's file."""
    return os.path.join(audit_dir(), f"audit-{_replica_id()}.jsonl")


def extract_numbers(text: str) -> list[tuple[float, int]]:
    """(value, decimals) for every standalone number in the text; list markers and code-like tokens are skipped."""
    out = []
    for m in _NUM.finditer(_LIST_MARKER.sub("", text)):
        raw = (m.group(1) or m.group(2)).replace(",", "").rstrip(".")
        if raw:
            out.append((float(raw), len(raw.split(".")[1]) if "." in raw else 0))
    return out


def _walk(o, acc):
    if isinstance(o, bool) or o is None:
        return
    if isinstance(o, (int, float)):
        if o == o:
            acc.add(float(o))
    elif isinstance(o, str):
        for v, _ in extract_numbers(o):
            acc.add(v)
    elif isinstance(o, dict):
        for v in o.values():
            _walk(v, acc)
    elif isinstance(o, (list, tuple)):
        for v in o:
            _walk(v, acc)


def allowed_numbers(*sources) -> set[float]:
    """Every number in the tool outputs (and the user's own inputs), plus the unit conversions the text may use:
    percent (x100), crore (/1e7), lakh (/1e5), hours (/60), thousands."""
    base: set[float] = set()
    for s in sources:
        _walk(s, base)
    out = set()
    for v in base:
        a = abs(v)
        out.update({v, a, a * 100, a / 1e7, a / 1e5, a / 60, a / 1000})
    return out


def _matches(x: float, d: int, allowed: set[float]) -> bool:
    tol = 0.5 * 10 ** (-d) + 1e-9
    return any(abs(c - x) <= tol for c in allowed)


def check_numbers(text: str, *sources) -> list[float]:
    """Numbers in `text` that do not appear (allowing rounding) in the tool outputs / user inputs. Empty list = pass."""
    allowed = allowed_numbers(*sources)
    return [x for x, d in extract_numbers(text) if not _matches(x, d, allowed)]


def check_reply(text: str, *sources) -> list[dict]:
    """Guardrail events for an LLM reply; an empty list means the reply may be shown."""
    events = []
    bad = check_numbers(text, *sources)
    if bad:
        events.append({"event": "hallucination_blocked", "numbers_not_in_tool_outputs": sorted(set(bad)), "blocked_reply": text[:400]})
    m = ACTION_CLAIMS.search(text)
    if m:
        events.append({"event": "action_claim_blocked", "phrase": m.group(0)})
    return events


def minimise(output, max_rows: int = MAX_ROWS_TO_LLM):
    """Aggregated tool output only: long row lists are cut before anything goes to the LLM (free tiers may log prompts)."""
    if isinstance(output, dict):
        return {k: minimise(v, max_rows) for k, v in output.items()}
    if isinstance(output, list):
        return [minimise(v, max_rows) for v in output[:max_rows]]
    return output


def _hash_if_large(output):
    s = json.dumps(output, default=str, ensure_ascii=False)
    if len(s) <= LARGE_OUTPUT_CHARS:
        return output
    return {"sha256": hashlib.sha256(s.encode()).hexdigest(), "chars": len(s), "preview": s[:200]}


def _append(rec: dict) -> None:
    path = audit_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf8") as f:
        f.write(json.dumps(rec, default=str, ensure_ascii=False) + "\n")


def log_run(page: str, inputs, tools_called: list[dict], model_used: str, final_text: str, guardrail_events: list, mode: str,
            banner: str | None = None, extra: dict | None = None) -> str:
    run_id = uuid.uuid4().hex[:12]
    _append({"type": "run", "run_id": run_id, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "page": page, "mode": mode,
             "inputs": inputs, "tools_called": [{"name": t["name"], "args": t["args"], "output": _hash_if_large(t["output"])} for t in tools_called],
             "model_used": model_used, "final_text": final_text, "guardrail_events": guardrail_events, "banner": banner,
             "decision": None, **(extra or {})})
    return run_id


def log_decision(run_id: str, decision: str, final_text: str | None = None) -> None:
    if decision not in ("approved", "rejected", "edited"):
        raise ValueError("decision must be approved, rejected or edited")
    _append({"type": "decision", "run_id": run_id, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "decision": decision,
             "final_text": final_text, "note": "recorded only; the app never sends a message or changes a price"})


def log_feedback(run_id: str, kind: str, reasons: list[str] | None = None, note: str | None = None) -> None:
    """Ask-the-data answer feedback: good / fix_it / request_review. Append-only, like every other audit record."""
    if kind not in ("good", "fix_it", "request_review"):
        raise ValueError("kind must be good, fix_it or request_review")
    _append({"type": "feedback", "run_id": run_id, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "kind": kind, "reasons": reasons or [], "note": note})


def read_audit() -> list[dict]:
    """Every replica's audit file, merged. Callers that need a total order (the audit log screen) sort by ts themselves."""
    out = []
    for fn in sorted(glob.glob(os.path.join(audit_dir(), "audit-*.jsonl"))):
        with open(fn, encoding="utf8") as f:
            out.extend(json.loads(line) for line in f if line.strip())
    return out
