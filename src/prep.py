"""Steps 0-2: load, clean, build the shipment table, save intermediates."""
from __future__ import annotations

import json
import os
import pandas as pd

from src.features import normalise_columns, build_shipment_table

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
RAW = os.path.join(DATA, "raw", "vendor_lr.parquet")
PROCESSED = os.path.join(DATA, "processed")
os.makedirs(PROCESSED, exist_ok=True)

TRAIN_START = pd.Timestamp("2025-04-01")
TRAIN_END = pd.Timestamp("2025-12-31")
TEST_START = pd.Timestamp("2026-01-01")
TEST_END = pd.Timestamp("2026-03-31")


def load() -> pd.DataFrame:
    df = pd.read_parquet(RAW)
    df = normalise_columns(df)
    df["lr_date"] = pd.to_datetime(df["lr_date"], errors="coerce")
    return df


def dedupe(df: pd.DataFrame, save: bool = True) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Drop exact duplicate rows (every column equal, NaN == NaN). The first copy is kept; the dropped copies are saved
    to data/processed/audit_duplicate_rows.csv with a JSON summary (count and freight) beside it."""
    mask = df.duplicated(keep="first")
    dups = df[mask]
    summary = {"rows_before": int(len(df)), "duplicate_rows_dropped": int(mask.sum()), "rows_after": int((~mask).sum()),
               "columns_compared": int(df.shape[1]),
               "r2_freight_rs_in_duplicates": float(dups["r2_freight"].sum()), "r2_freight_cr_in_duplicates": float(dups["r2_freight"].sum() / 1e7),
               "gta_invoice_value_rs_in_duplicates": float(dups["gta_invoice_value"].sum()),
               "shipments_affected": int(dups["shipment_no"].nunique()), "delivery_ids_affected": int(dups["delivery_id"].nunique())}
    if save:
        dups.to_csv(os.path.join(PROCESSED, "audit_duplicate_rows.csv"), index=False, encoding="utf-8-sig")
        with open(os.path.join(PROCESSED, "audit_duplicate_rows_summary.json"), "w", encoding="utf8") as f:
            json.dump(summary, f, indent=2)
    return df[~mask].reset_index(drop=True), dups, summary


def clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["valid_cost"] = (df["r2_freight"] > 0) & (df["gta_invoice_value"] > 0)
    df["valid_capacity"] = df["vehicle_capacity"] > 0
    # drop rows with missing vendor / route / lr_date
    df = df.dropna(subset=["vendor_name", "route", "lr_date"]).copy()
    df["origin_plant"] = df["origin_plant"].fillna("UNKNOWN")
    return df


def headline_figures(df: pd.DataFrame, ship: pd.DataFrame) -> dict:
    """Utilisation and margin headline numbers with the agreed filters (valid cost, utilisation <= 150%)."""
    vc = ship[ship["vehicle_capacity"] > 0]
    u = vc[vc["utilisation"] <= 150]["utilisation"]
    v = df[(df["r2_freight"] > 0) & (df["gta_invoice_value"] > 0)]
    return {
        "n_ship_all": len(ship), "n_ship_valid_capacity": len(vc), "n_util_outliers": int((vc["utilisation"] > 150).sum()),
        "util_median": float(u.median()), "util_below60": float((u < 60).mean() * 100),
        "n_lr_valid_cost": len(v),
        "margin_cr": float(v["margin"].sum() / 1e7), "gta_cr": float(v["gta_invoice_value"].sum() / 1e7),
        "freight_cr": float(v["r2_freight"].sum() / 1e7),
        "margin_pct_gta": float(v["margin"].sum() / v["gta_invoice_value"].sum() * 100),
        "margin_pct_freight": float(v["margin"].sum() / v["r2_freight"].sum() * 100),
        "neg_share": float((v["margin"] < 0).mean() * 100), "neg_cr": float(v.loc[v["margin"] < 0, "margin"].abs().sum() / 1e7),
    }


def corrections(df: pd.DataFrame, ship: pd.DataFrame) -> str:
    """Markdown cell text: Phase 3 corrections recomputed from the cleaned data (df already cleaned)."""
    h = headline_figures(df, ship)
    fpm = (ship["freight"] / ship["distance"]).where(ship["all_cost_valid"] & (ship["distance"] > 0)).median()
    return "\n".join([
        "## Corrections to Phase 3 figures", "",
        "| # | Phase 3 claim | Recomputed |", "|---|---|---|",
        f"| 1 | 57.7-60% of trucks 'poorly utilised' (each LR measured alone) | truck-level utilisation (sum of `lr_qty` / `vehicle_capacity`, "
        f"capacity > 0, utilisation <= 150%) median **{h['util_median']:.1f}%** on {h['n_ship_valid_capacity'] - h['n_util_outliers']:,} trucks; "
        f"only **{h['util_below60']:.1f}%** below 60%. {h['n_util_outliers']} trucks above 150% treated as data errors |",
        f"| 2 | Fixed Rs78/km cut-off per LR | truck-level freight per km median **Rs{fpm:.0f}/km** (valid-cost trucks) |",
        "| 3 | Modelled imputed freight | LRs with `r2_freight` = 0 (11,985) or `gta_invoice_value` = 0 (1,187) are excluded from all models |",
        f"| 4 | Margin about 4.3% | on {h['n_lr_valid_cost']:,} valid-cost LRs, margin = **{h['margin_pct_gta']:.2f}%** of billed value "
        f"(Rs{h['margin_cr']:.1f} Cr on Rs{h['gta_cr']:.1f} Cr billed; the Phase 3 figure divides by billed value, "
        f"dividing by freight paid instead would give {h['margin_pct_freight']:.2f}%); **{h['neg_share']:.1f}%** of LRs negative (Rs{h['neg_cr']:.1f} Cr lost) |",
        "| 5 | - | rows with missing `vendor_name`, `route` or `lr_date` dropped; missing `origin_plant` labelled UNKNOWN |",
        "",
    ])


def build(df: pd.DataFrame):
    df = clean(df)
    ship = build_shipment_table(df)
    ship["all_cost_valid"] = ship["all_cost_valid"].astype(bool)
    ship["valid_capacity"] = ship["vehicle_capacity"] > 0
    ship["valid_cost"] = ship["all_cost_valid"] & (ship["freight"] > 0) & (ship["gta"] > 0)
    ship["util_outlier"] = ship["utilisation"] > 150
    ship["train"] = (ship["lr_date"] >= TRAIN_START) & (ship["lr_date"] <= TRAIN_END)
    ship["test"] = (ship["lr_date"] >= TEST_START) & (ship["lr_date"] <= TEST_END)
    ship["tune"] = ship["train"] & (ship["lr_date"] >= pd.Timestamp("2025-10-01"))
    ship.to_parquet(os.path.join(PROCESSED, "vendor_shipments.parquet"))
    df.to_parquet(os.path.join(PROCESSED, "vendor_lr_clean.parquet"))
    return df, ship


if __name__ == "__main__":
    df, ship = build(load())
    print(corrections(df, ship))
