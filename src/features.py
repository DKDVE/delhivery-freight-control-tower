"""Shared feature engineering for the vendor capstone."""
from __future__ import annotations

import pandas as pd

# Columns that must never be features (leakage / IDs / constants).
LEAK_COLS = [
    "r2_freight", "gta_invoice_value", "margin", "margin_pct",
    "transport_charges_paid", "total_transport_charges", "other_charges",
    "liability_amount", "liability_amount_as_on_date", "b2b_billing",
    "shipment_cost_no", "shipment_cost", "condition_flow", "reporting_number",
    "rp5_shipment", "billing_document_date", "billed_qty", "billing_qty_in_mt",
    "ftl_value", "propotionate_ftl_value",
]
ID_COLS = ["delivery_id", "shipment_no", "container_id"]

# Constant / junk columns dropped silently.
CONST_COLS = [
    "sector", "shipping_type", "segment", "rate", "line_value",
    "vendor", "route_code", "vehicle_description", "city",
    "grn_number", "grn_date", "grn_unit_of_measure", "grn_quantity",
    "vendor_claim_rate", "vendor_claim_freight", "full_truck_load_indicator",
    "online_billing", "business_area", "sales_office", "ctd_indicator",
    "rate_condition_amount_percantage", "gross_weight", "net_weight",
    "weight_unit", "rlw", "ulw", "payload", "billed_qty",
    "category", "departure_point", "zone_region",
]


def normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Fix the R2 Frieght typo and drop the duplicate margin column if present."""
    df = df.copy()
    if "r2_frieght" in df.columns:
        df = df.rename(columns={"r2_frieght": "r2_freight"})
    # margin appears twice in raw; keep first
    if df.columns.duplicated().any():
        df = df.loc[:, ~df.columns.duplicated(keep="first")]
    return df


def add_time_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    dt = pd.to_datetime(df["lr_date"], errors="coerce")
    df["month"] = dt.dt.month
    df["weekday"] = dt.dt.weekday
    return df


def _mode_by(lr, col):
    return (lr.dropna(subset=[col]).groupby(["shipment_no", col]).size().rename("n").reset_index()
            .sort_values(["shipment_no", "n"], ascending=[True, False])
            .drop_duplicates("shipment_no").set_index("shipment_no")[col])


def build_shipment_table(lr: pd.DataFrame) -> pd.DataFrame:
    """Aggregate LR rows to shipment level (one truck = one shipment_no)."""
    lr = lr.dropna(subset=["shipment_no"]).copy()
    lr["_valid_cost"] = (lr["r2_freight"] > 0) & (lr["gta_invoice_value"] > 0)
    lr["_qty"] = lr["lr_qty"].fillna(0)
    g = lr.groupby("shipment_no", sort=False)
    largest = lr.loc[g["_qty"].idxmax()].set_index("shipment_no")
    out = pd.DataFrame({
        "vendor": g["vendor_name"].first(),
        "lr_date": g["lr_date"].min(),
        "n_lrs": g.size(),
        "n_routes": g["route"].nunique(),
        "route": largest["route"],
        "destination_point": largest["destination_point"],
        "state_destination": largest["state_destination"],
        "origin_plant": g["origin_plant"].first(),
        "zone": g["zone"].first(),
        "distance": g["distance"].max(),
        "vehicle_capacity": g["vehicle_capacity"].max(),
        "total_qty": g["_qty"].sum(),
        "freight": g["r2_freight"].sum(),
        "gta": g["gta_invoice_value"].sum(),
        "margin": g["margin"].sum(),
        "all_cost_valid": g["_valid_cost"].min().astype(bool),
    })
    for c in ["vehicle_type", "truck_load", "shipment_type", "inco_terms"]:
        out[c] = _mode_by(lr, c)
    out = out.reset_index()
    out["origin_plant"] = out["origin_plant"].fillna("UNKNOWN")
    cap = out["vehicle_capacity"].where(out["vehicle_capacity"] > 0)
    out["utilisation"] = out["total_qty"] / cap * 100
    out["util_outlier"] = out["utilisation"] > 150
    return add_time_features(out)


def low_card_group(df: pd.DataFrame, col: str, min_rows: int = 20) -> pd.DataFrame:
    """Group levels with fewer than min_rows into 'OTHER'."""
    counts = df[col].value_counts()
    rare = set(counts[counts < min_rows].index)
    df = df.copy()
    df[col] = df[col].where(~df[col].isin(rare), "OTHER")
    return df