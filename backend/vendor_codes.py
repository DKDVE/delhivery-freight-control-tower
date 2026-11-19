"""Vendor codes V001, V002... ordered by total freight spend. The API speaks codes only.

Shipped tables, models and the app already use codes everywhere, so with data/processed/vendor_codes.csv holding only
(vendor_code, rank, lrs, spend_rs) the code-to-name lookup is the identity on codes. When a rebuild from the confidential
workbook has just run and the table still carries vendor_name, names are translated to codes and scrubbed from payloads
until scripts/anonymise_vendor_data.py replaces them.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "data", "processed", "vendor_codes.csv")
LR = os.path.join(ROOT, "data", "processed", "vendor_lr_clean.parquet")
CODE_RE = re.compile(r"^V\d{3,}$")


def build(save: bool = True) -> pd.DataFrame:
    """Rank every vendor in the clean LR table by total freight (r2_freight) of valid-cost LRs; ties broken by name for a stable order."""
    lr = pd.read_parquet(LR, columns=["vendor_name", "r2_freight", "gta_invoice_value"])
    already_coded = lr["vendor_name"].dropna().map(lambda v: bool(CODE_RE.match(str(v)))).all()
    ok = lr[(lr["r2_freight"] > 0) & (lr["gta_invoice_value"] > 0)]
    g = lr.groupby("vendor_name").size().rename("lrs").to_frame().join(ok.groupby("vendor_name")["r2_freight"].sum().rename("spend_rs")).fillna({"spend_rs": 0.0})
    g = g.reset_index().sort_values(["spend_rs", "vendor_name"], ascending=[False, True]).reset_index(drop=True)
    g.insert(0, "rank", g.index + 1)
    g.insert(0, "vendor_code", g["vendor_name"] if already_coded else [f"V{i:03d}" for i in g["rank"]])
    if already_coded:
        g = g.drop(columns=["vendor_name"])
    if save:
        g.to_csv(PATH, index=False)
    return g


@lru_cache(maxsize=1)
def table() -> pd.DataFrame:
    return pd.read_csv(PATH) if os.path.exists(PATH) else build()


@lru_cache(maxsize=1)
def _maps() -> tuple[dict, dict, re.Pattern | None]:
    t = table()
    if "vendor_name" not in t.columns:
        ident = {c: c for c in t["vendor_code"]}
        return ident, ident, None
    to_name = dict(zip(t["vendor_code"], t["vendor_name"]))
    to_code = dict(zip(t["vendor_name"], t["vendor_code"]))
    names = sorted(to_code, key=len, reverse=True)  # longest first so "A B LTD" wins over "A B"
    pat = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(re.escape(n) for n in names) + r")(?![A-Za-z0-9])", re.I)
    return to_name, to_code, pat


def code_for(name: str | None) -> str | None:
    if name is None:
        return None
    _, to_code, _ = _maps()
    if name in to_code:
        return to_code[name]
    m = {k.casefold(): v for k, v in to_code.items()}
    return m.get(str(name).casefold())


def code_map() -> dict:
    """name -> code (identity on codes when the shipped tables are already anonymised)."""
    return _maps()[1]


def name_for(code: str) -> str | None:
    """Vendor name for a code, or None if the code is unknown. Case-insensitive on the code."""
    return _maps()[0].get(str(code).strip().upper())


def scrub_text(s: str) -> str:
    _, to_code, pat = _maps()
    if pat is None:
        return s
    low = {k.casefold(): v for k, v in to_code.items()}
    return pat.sub(lambda m: low[m.group(1).casefold()], s)


def scrub(o):
    """Recursively replace any vendor name in a payload (values and dict keys) by its code."""
    if isinstance(o, str):
        return scrub_text(o)
    if isinstance(o, dict):
        return {scrub_text(k) if isinstance(k, str) else k: scrub(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [scrub(v) for v in o]
    return o


def reset_cache() -> None:
    table.cache_clear()
    _maps.cache_clear()


def names() -> list[str]:
    """Real vendor names, only when the private mapping exists on this machine (never in the repository)."""
    p = os.path.join(ROOT, "data", "private", "vendor_names.csv")
    return list(pd.read_csv(p)["vendor_name"]) if os.path.exists(p) else []
