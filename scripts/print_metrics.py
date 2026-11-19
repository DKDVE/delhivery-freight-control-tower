"""Print the metrics tables of all six tasks from reports/metrics/*.json."""
import json
import os
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pd.set_option("display.width", 200, "display.max_columns", 20)
for t in ["V1", "V2", "V3", "M1", "M2", "M3"]:
    d = json.load(open(os.path.join(ROOT, "reports", "metrics", f"{t}.json"), encoding="utf8"))
    print(f"\n=== {t} ({d['type']}): {d['business_question']}")
    print(f"train {d['train_period']} (n={d['n_train']:,}) | test {d['test_period']} (n={d['n_test']:,}) | winner: {d['winner']}")
    rows = {m["name"]: {k: v for k, v in m["metrics"].items()} for m in d["models"]}
    b = d["base_rate_or_baseline"]
    if d["type"] == "classification":
        rows["Base rate (constant)"] = {"PR_AUC": b["value"]}
        for x in d.get("extra_baselines", []):
            rows[x["name"].split(" (")[0]] = {"PR_AUC": x["PR_AUC"]}
    else:
        rows["Naive baseline"] = b.get("all_metrics", {"MAE": b["value"]})
    print(pd.DataFrame(rows).T.round(4).to_string())
    if d["type"] == "classification":
        w = next(m for m in d["models"] if m["name"] == d["winner"])["metrics"]
        print(f"threshold {d['threshold']:.4f} ({d.get('threshold_rule')}); winner val P/R at threshold: {w.get('val_precision_at_thr')}/{w.get('val_recall_at_thr')}; test P/R: {w['precision']}/{w['recall']}")
    else:
        bi = d["business_impact_inputs"]
        print(f"80% interval test coverage: raw {bi['interval_coverage_before_calibration']:.1%} -> conformal {bi['interval_80pct_coverage']:.1%} "
              f"(Q={bi['conformal']['Q']:.3f}, n_cal={bi['conformal']['n_cal']:,}, width {bi['conformal']['test_mean_width_raw']:,.0f} -> {bi['conformal']['test_mean_width_calibrated']:,.0f})")
        for r in bi.get("interval_by_total_qty_band", []):
            print(f"  total_qty {r['band_MT']:>5} MT: n_test={r['n_test']:,}  coverage {r['coverage_global_Q']:.1%} (global Q) -> {r['coverage_per_band_Q']:.1%} (per-band Q)  "
                  f"median width ₹{r['median_width_rs_global_Q']:,.0f} -> ₹{r['median_width_rs_per_band_Q']:,.0f}")
    print("top drivers:", "; ".join(f"{x['feature']}" for x in d["top_drivers"][:5]))
