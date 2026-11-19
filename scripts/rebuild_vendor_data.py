"""Rebuild every vendor-derived table from the confidential freight workbook.

Usage: python scripts/rebuild_vendor_data.py [path/to/Delhivery_Vendor_data.xlsx]

1. Converts the workbook to data/raw/vendor_lr.parquet (snake_case columns).
2. Executes notebooks/01_vendor_models.ipynb, which writes the cleaned LR and shipment tables,
   lane_vendor_rates.csv, the vendor_overpay*.csv and audit tables, the V1-V3 models and metrics.
3. Builds data/processed/vendor_codes.csv (V001... ordered by spend), then runs scripts/anonymise_vendor_data.py, which
   replaces every vendor name in the processed tables and the V2/V3 models by its code and proves the predictions are unchanged.
"""
import os
import re
import subprocess
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SRC = os.path.join(ROOT, "data", "raw", "Delhivery_Vendor_data.xlsx")
OUT = os.path.join(ROOT, "data", "raw", "vendor_lr.parquet")
NOTEBOOK = os.path.join(ROOT, "notebooks", "01_vendor_models.ipynb")


def snake(col: str) -> str:
    col = col.replace("\xa0", " ").strip().replace("%", "pct")
    return re.sub(r"_+", "_", re.sub(r"[^0-9a-zA-Z]+", "_", col)).strip("_").lower()


def convert(src: str) -> None:
    df = pd.read_excel(src, engine="calamine")
    df.columns = [snake(c) for c in df.columns]
    df = df.rename(columns={"r2_frieght": "r2_freight"})
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    df.to_parquet(OUT)
    print("saved", OUT, df.shape)


def main() -> None:
    src = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SRC
    if not os.path.exists(src):
        sys.exit(f"Raw workbook not found: {src}. See data/README.md.")
    convert(src)
    env = {**os.environ, "PYTHONPATH": ROOT}
    subprocess.run([sys.executable, "-m", "jupyter", "nbconvert", "--to", "notebook", "--execute", "--inplace",
                    "--ExecutePreprocessor.timeout=1800", NOTEBOOK], check=True, env=env)
    sys.path.insert(0, ROOT)
    from backend import vendor_codes
    print("vendor codes:", len(vendor_codes.build()))
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "anonymise_vendor_data.py")], check=True, env=env)


if __name__ == "__main__":
    main()
