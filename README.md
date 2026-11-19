# Delhivery cost-to-serve: models and Freight Control Tower

This is an MBA capstone (KPMG experiential project) for Delhivery. It studies two problems: vendor freight cost, margin leakage and truck loading (April 2025 to March 2026), and middle-mile trips that run far longer than planned (public 2018 trip data). Each problem has three prediction models, and a demo web app called Freight Control Tower puts the models behind a booking check, vendor and trip views, and a question-answering page.

## 1. Results

Time-based split for every model; the test period is never used for fitting or for choosing thresholds. The winner is chosen on the headline metric; when two models are within 2%, the simpler one wins (rule in `reports/metrics/*.json`, field `winner_reason`).

| Task | Question | Winner | Headline metric (test) | Naive baseline | Source |
|---|---|---|---|---|---|
| V1 | What should this truck movement cost? | RandomForest | MAE ₹2,696, R² 0.98 | MAE ₹10,631 (median ₹/km x distance) | [V1.json](reports/metrics/V1.json) |
| V2 | Which consignments will lose money? | LightGBM | PR-AUC 0.743 | 0.304 (base rate) | [V2.json](reports/metrics/V2.json) |
| V3 | Which loads end up below 60% full? | LightGBM | PR-AUC 0.752 | 0.089 (base rate) | [V3.json](reports/metrics/V3.json) |
| M1 | How long will this trip really take? | LightGBM | MAE 57.3 min, R² 0.94 | MAE 80.6 min (planned time x median ratio) | [M1.json](reports/metrics/M1.json) |
| M2 | Which trips will be severely late (>= 2.5x plan)? | RandomForest | PR-AUC 0.752 | 0.330 (base rate) | [M2.json](reports/metrics/M2.json) |
| M3 | How many minutes does a leg lose at a hub? | LightGBM | MAE 47.5 min, R² 0.30 | MAE 53.8 min (median by source centre) | [M3.json](reports/metrics/M3.json) |

Each model is compared with two other algorithms (Ridge or Logistic Regression, and RandomForest or LightGBM); all numbers are in the JSON files. M3 explains little of the variation and is the weakest model.

**Value at stake** ([reports/roi_inputs.json](reports/roi_inputs.json), computed by `scripts/roi_inputs.py`). Test quarter Jan-Mar 2026, each lever counted once in the order S3, S1, S2 so that levers do not double count. Annualised with a factor of 3.85 (valid freight of the year divided by valid freight of the test quarter).

| Lever | Test quarter | Annualised | Note |
|---|---|---|---|
| S3 right-size vehicles flagged by V3 (priced with V1) | ₹1.1 Cr | ₹4.3 Cr | indicative; V1 is a market-median price |
| S1 move volume to cheaper vendors on the same lane (35% of volume moved) | ₹1.0 Cr | ₹3.9 Cr | 20% and 50% scenarios are in the JSON |
| S2 loss exposure caught by the V2 alert | ₹2.3 Cr | ₹8.7 Cr | exposure, not a saving; a capture rate has to be applied |

The vendor file also has 560 exact duplicate LR rows (₹1.06 Cr of freight) that were removed before modelling; whether any was paid twice needs checking against payment records. Middle-mile hours use sourced synthetic rates and the 2018 network scale (section `S4` of the JSON) and are illustrative only.

## 2. Repository map

| Folder | Contents |
|---|---|
| `notebooks/` | `01_vendor_models.ipynb`, `02_middle_mile_models.ipynb`: the six models, comparisons, SHAP, business impact |
| `src/` | shared code: data preparation, features, modelling, evaluation, plotting |
| `models/` | saved pipelines (`*.joblib`) |
| `reports/` | `metrics/` (one JSON per task), `figures/`, `roi/`, `roi_inputs.json` |
| `data/` | `raw/` (not committed), `processed/` (prediction CSVs and tables with vendor codes only); see [data/README.md](data/README.md) |
| `app/` | agent, tools, guardrails and the question-answering pipeline |
| `backend/` | FastAPI service over `app/` |
| `frontend/` | React and TypeScript app (`src/`, `e2e/`, built output in `dist/`) |
| `prompts/` | the ten prompt templates and the agent system prompt |
| `docs/` | [responsible AI note](docs/responsible_ai.md), evaluation results, screenshots |
| `infra/` | Azure Bicep templates and notes ([infra/README.md](infra/README.md)) |
| `scripts/` | ROI inputs, metrics printer, data download and rebuild, demo-day helpers |
| `tests/` | pytest suite |

## 3. How to run

Python 3.10 or newer.

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt     # Windows; on macOS/Linux use .venv/bin/python
```

**(a) Notebooks.** The middle-mile notebook needs the public trip file; the vendor notebook needs the confidential freight workbook, which is shared with the mentor separately (see [data/README.md](data/README.md)).

```bash
python scripts/download_trip_data.py
python -m jupyter nbconvert --to notebook --execute --inplace notebooks/02_middle_mile_models.ipynb
python scripts/rebuild_vendor_data.py        # needs data/raw/Delhivery_Vendor_data.xlsx; runs notebook 01 and anonymises its outputs
python scripts/print_metrics.py              # prints the metrics tables for all six tasks
```

**(b) The app in demo mode** (no key, no network; the built frontend is committed, so Node.js is not needed):

```bash
python -m uvicorn backend.main:app --port 8000
```

Open <http://127.0.0.1:8000>. Demo mode uses rule-based explanations; the models and data are real. A live LLM is optional: copy `.env.example` to `.env` and set the key (see [docs/responsible_ai.md](docs/responsible_ai.md)).

**(c) Tests.**

```bash
python -m pytest tests -q
cd frontend && npm install && npm run build && npm run e2e     # Playwright end-to-end tests, demo mode, uses the installed Edge browser
```

One test starts the app from a copy of the repository that has no `data/raw` and no vendor name mapping, to show that a clean clone works in demo mode.

## 4. Data

- **Vendor freight:** 296,946 consignment (LR) rows, April 2025 to March 2026, client data. Vendor names were already anonymised by the client; in this repository vendors appear only as codes (V001, V002, ...) ordered by freight spend. The raw file is confidential and not included.
- **Trips:** the public Delhivery trip data (12 Sep to 3 Oct 2018, 144,867 checkpoint rows) from <https://github.com/iamjaspreetsingh/DelhiveryCaseStudy>. It has no licence file, so it is downloaded by script, not redistributed.
- **Processed tables, models and predictions** are committed with vendor codes in place of names. The saved V2 and V3 models were relabelled from names to codes in place, and predictions on the full test set were checked to be identical (maximum absolute difference 0). Details and the rebuild path are in [data/README.md](data/README.md).

## 5. Design decisions

- **Time-based split.** Vendor models train on April to December 2025 and test on January to March 2026; middle-mile models train on trips created up to 26 Sep 2018 and test on 27 Sep to 3 Oct. Thresholds and interval calibration use a validation slice at the end of the training period.
- **Leakage rules.** Columns that contain or are derived from the outcome are never features (listed per task in `excluded_for_leakage` in each metrics JSON). Rate-card lookups used as baselines are fitted on training rows only.
- **Threshold rule.** For classifiers, the threshold maximises recall while keeping validation precision at 0.60 or higher; otherwise the best-F1 threshold is used. The precision-recall curves are saved with the figures.
- **Intervals.** Regression predictions carry an 80% interval from LightGBM quantile models calibrated by split-conformal prediction.
- **Guardrails.** The LLM never writes numbers: it fills placeholders that code replaces with tool results, and a number check blocks any figure that is not in the tool output. Vendors leave the API only as codes.
- **Human approval.** Every action (a drafted message, a decision) needs a person to approve it; nothing is sent or changed automatically, and every run is written to an append-only audit log.

## 6. Known limitations

- One client sector and one year of vendor data; the middle-mile data covers about three weeks in 2018.
- Cost rates used for middle-mile hours are sourced synthetic values, not Delhivery actuals; the trip data has no cost fields.
- No service-quality data (delays, damage, claims), so vendor comparisons are on price and margin only.
- M3 (hub handling time) has R² 0.30 and is indicative only.
- Hyper-parameters are fixed defaults with no search; one temporal split.
- V2 uses shipment-level fields (`n_lrs`, `shipment_utilisation`) that may not be known at booking; the sensitivity check is in notebook 01.
- The live mode uses a free-tier LLM endpoint with rate limits; demo mode is the reliable path.
- Savings estimates are indicative: V1 is a market-median price, and the cheapest-vendor lever assumes equal service quality.

## 7. How this was built

Code was developed with AI coding assistants under the team's direction; all analysis decisions, results and recommendations were reviewed by the team.

## 8. Team and mentor

Team: Srinjay Paul, Dhairya, Dhruv, Manit, Saurav, Saksham. Mentor: Deepti Agrawal, KPMG. UPES MBA experiential project with KPMG, client Delhivery.

Licence: all rights reserved, see [LICENSE](LICENSE).
