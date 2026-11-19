"""Replace vendor names by codes in every processed table and saved model, and prove the models behave identically.

Run after scripts/rebuild_vendor_data.py (which writes name-bearing files). Afterwards the repository holds codes only;
the private code-to-name mapping is saved to data/private/vendor_names.csv (git-ignored).

Models: each vendor category inside a saved pipeline (LightGBM pandas_categorical, CatFrame levels, any encoder
categories_) is relabelled in place with its code, keeping the same position. Predictions on the full test set are
compared before and after; the maximum absolute difference must be exactly 0.
"""
import io
import json
import os
import sys

import joblib
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import prep  # noqa: E402

PROC = os.path.join(ROOT, "data", "processed")
MODELS = os.path.join(ROOT, "models")
PRIVATE = os.path.join(ROOT, "data", "private")
CODES_PATH = os.path.join(PROC, "vendor_codes.csv")
NAMES_PATH = os.path.join(PRIVATE, "vendor_names.csv")
TABLES = ["vendor_lr_clean.parquet", "vendor_shipments.parquet", "lane_vendor_rates.csv", "vendor_overpay.csv",
          "vendor_overpay_summary.csv", "audit_duplicate_rows.csv", "audit_list_isolation_forest.csv"]
VENDOR_COLS = ("vendor", "vendor_name")
MODEL_FILES = ["V1_RandomForest.joblib", "V2_LightGBM.joblib", "V3_LightGBM.joblib"]


def load_mapping() -> dict:
    """name -> code. Built from the name-bearing vendor_codes.csv, or read back from the private file when already anonymised."""
    if os.path.exists(CODES_PATH) and "vendor_name" in pd.read_csv(CODES_PATH, nrows=1).columns:
        t = pd.read_csv(CODES_PATH)
        os.makedirs(PRIVATE, exist_ok=True)
        t[["vendor_code", "vendor_name"]].to_csv(NAMES_PATH, index=False)
        return dict(zip(t["vendor_name"], t["vendor_code"]))
    t = pd.read_csv(NAMES_PATH)
    return dict(zip(t["vendor_name"], t["vendor_code"]))


def relabel(obj, m: dict, seen: set):
    """Return obj with every string found in m replaced by m[string]; containers are mutated in place where possible."""
    if isinstance(obj, str):
        return m.get(obj, obj)
    if id(obj) in seen:
        return obj
    if isinstance(obj, (int, float, bool, bytes, type(None), np.generic)):
        return obj
    seen.add(id(obj))
    if isinstance(obj, dict):
        new = {relabel(k, m, seen): relabel(v, m, seen) for k, v in obj.items()}
        obj.clear()
        obj.update(new)
        return obj
    if isinstance(obj, list):
        obj[:] = [relabel(v, m, seen) for v in obj]
        return obj
    if isinstance(obj, tuple):
        return tuple(relabel(v, m, seen) for v in obj)
    if isinstance(obj, np.ndarray):
        if obj.dtype == object:
            for i, v in enumerate(obj.flat):
                obj.flat[i] = relabel(v, m, seen)
        return obj
    if isinstance(obj, pd.Index):
        return obj.map(lambda v: m.get(v, v)) if obj.dtype == object else obj
    if isinstance(obj, pd.CategoricalDtype):
        return pd.CategoricalDtype([m.get(c, c) for c in obj.categories], ordered=obj.ordered)
    if hasattr(obj, "__dict__"):
        for k, v in list(vars(obj).items()):
            setattr(obj, k, relabel(v, m, seen))
        return obj
    return obj


def anonymise_frame(df: pd.DataFrame, m: dict) -> pd.DataFrame:
    df = df.copy()
    for c in VENDOR_COLS:
        if c in df.columns:
            df[c] = df[c].map(lambda v: m.get(v, v))
    return df


def proof_frames(lr: pd.DataFrame, ship: pd.DataFrame) -> dict:
    """Full test-period feature frames for each model, as built in notebook 01."""
    s = ship[ship["lr_date"] >= prep.TEST_START]
    v1 = s[s["all_cost_valid"] & (s["distance"] > 0) & (s["total_qty"] > 0) & (s["freight"] > 0)]
    v2 = lr[(lr["r2_freight"] > 0) & (lr["gta_invoice_value"] > 0)].copy()
    v2["month"], v2["weekday"] = v2["lr_date"].dt.month, v2["lr_date"].dt.weekday
    v2 = v2.merge(
        ship[["shipment_no", "n_lrs", "utilisation"]].rename(columns={"utilisation": "shipment_utilisation"}), on="shipment_no", how="left")
    v2 = v2[v2["lr_date"] >= prep.TEST_START]
    v3 = s[s["vehicle_capacity"] > 0]
    return {"V1_RandomForest": v1, "V2_LightGBM": v2, "V3_LightGBM": v3}


def predict(model: dict, frame: pd.DataFrame) -> np.ndarray:
    X = frame[model["cats"] + model["nums"]]
    p = model["pipeline"]
    return p.predict_proba(X)[:, 1] if hasattr(p, "predict_proba") else p.predict(X)


def names_in_pickle(model, m: dict) -> list:
    buf = io.BytesIO()
    joblib.dump(model, buf, compress=0)
    raw = buf.getvalue()
    return [n for n in m if isinstance(n, str) and n.encode("utf8") in raw]


def write_data_quality() -> None:
    raw_path = os.path.join(ROOT, "data", "raw", "vendor_lr.parquet")
    if not os.path.exists(raw_path):
        return
    raw = pd.read_parquet(raw_path, columns=["vendor_name", "route", "lr_date", "r2_freight", "gta_invoice_value", "vehicle_capacity"])
    items = {"lr_rows": len(raw), "r2_freight_zero": int((raw["r2_freight"] == 0).sum()),
             "gta_invoice_value_zero": int((raw["gta_invoice_value"] == 0).sum()),
             "vehicle_capacity_zero_or_missing": int((~(raw["vehicle_capacity"] > 0)).sum()),
             "missing_vendor_name": int(raw["vendor_name"].isna().sum()),
             "missing_route": int(raw["route"].isna().sum()), "missing_lr_date": int(raw["lr_date"].isna().sum())}
    with open(os.path.join(PROC, "data_quality.json"), "w", encoding="utf8") as f:
        json.dump(items, f, indent=2)


def main() -> None:
    write_data_quality()
    m = load_mapping()
    lr = pd.read_parquet(os.path.join(PROC, "vendor_lr_clean.parquet"))
    ship = pd.read_parquet(os.path.join(PROC, "vendor_shipments.parquet"))
    named = "vendor" in ship and ship["vendor"].isin(m).any()
    frames_before = proof_frames(lr, ship) if named else None
    codes_lr, codes_ship = anonymise_frame(lr, m), anonymise_frame(ship, m)
    frames_after = proof_frames(codes_lr, codes_ship)

    report = {}
    for f in MODEL_FILES:
        path = os.path.join(MODELS, f)
        model = joblib.load(path)
        if frames_before is not None and not names_in_pickle(model, m):
            report[f] = ("no vendor names in file, left unchanged", None)
        elif frames_before is not None:
            before = predict(model, frames_before[f.rsplit(".", 1)[0]])
            model = relabel(model, m, set())
            after = predict(model, frames_after[f.rsplit(".", 1)[0]])
            diff = float(np.max(np.abs(before - after)))
            report[f] = (len(before), diff)
            if diff != 0.0:
                sys.exit(f"{f}: predictions changed after relabelling (max abs diff {diff}); nothing written")
            leaked = names_in_pickle(model, m)
            if leaked:
                sys.exit(f"{f}: {len(leaked)} vendor names still present after relabelling; nothing written")
            joblib.dump(model, path, compress=3)
        else:
            report[f] = ("already anonymised", None)

    for t in TABLES:
        p = os.path.join(PROC, t)
        if not os.path.exists(p):
            continue
        df = pd.read_parquet(p) if t.endswith(".parquet") else pd.read_csv(p)
        df = anonymise_frame(df, m)
        df.to_parquet(p, index=False) if t.endswith(".parquet") else df.to_csv(p, index=False)

    codes = pd.read_csv(NAMES_PATH)
    full = pd.read_csv(CODES_PATH) if "vendor_name" in pd.read_csv(CODES_PATH, nrows=1).columns else None
    if full is not None:
        full.drop(columns=["vendor_name"]).to_csv(CODES_PATH, index=False)
    for f, (n, d) in report.items():
        print(f"{f}: {n}" if d is None else f"{f}: rows compared {n}, max abs difference {d}")
    print("vendors mapped:", len(codes), "| private mapping:", NAMES_PATH)


if __name__ == "__main__":
    main()
