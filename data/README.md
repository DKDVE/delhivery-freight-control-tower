# Data

## Middle mile (public, not redistributed)

The trip data is the public Delhivery dataset (trips created 12 Sep to 3 Oct 2018, 144,867 checkpoint rows) from <https://github.com/iamjaspreetsingh/DelhiveryCaseStudy>. That repository has no licence file, so the file is **not** included here. Download it with:

```bash
python scripts/download_trip_data.py
```

The script saves `data/raw/delhivery_data.csv` and checks it against the checksum of the file used for the reported results.

## Vendor freight (confidential, not in this repository)

The vendor freight file has 296,946 consignment (LR) rows for April 2025 to March 2026. It belongs to the client, is confidential, and is shared with the mentor separately. Vendor names in it are already anonymised by the client; the app and the reports show them as codes (V001, V002, ...) ordered by freight spend.

To use it:

1. Save the workbook as `data/raw/Delhivery_Vendor_data.xlsx` (a different path can be passed as an argument).
2. Run one command from the repository root:

```bash
python scripts/rebuild_vendor_data.py
```

This converts the workbook to `data/raw/vendor_lr.parquet`, executes `notebooks/01_vendor_models.ipynb` and writes the tables below.

| Rebuilt file | Used by |
|---|---|
| `data/raw/vendor_lr.parquet` | notebook 01 |
| `data/processed/vendor_lr_clean.parquet`, `vendor_shipments.parquet` | app, backend, ROI script |
| `data/processed/lane_vendor_rates.csv` | lane finder |
| `data/processed/vendor_overpay.csv`, `vendor_overpay_summary.csv` | vendor pages, Ask the data |
| `data/processed/audit_duplicate_rows.csv`, `audit_list_isolation_forest.csv` | audit lists |
| `data/processed/vendor_codes.csv` | code-to-name mapping, server side only |
| `models/V2_LightGBM.joblib`, `models/V3_LightGBM.joblib` | booking check (their encoders contain vendor names) |

## What is committed and why

- Committed: `data/processed/pred_*.csv` (record IDs are LR numbers or trip IDs; no vendor column), `hub_handling_ranking.csv`, the middle-mile tables, ROI CSVs under `reports/roi/`, metrics and figures.
- Not committed: the raw vendor file and every table that carries a vendor name. Without them the app still starts, but vendor, lane and booking pages have no data until the rebuild above has been run.
