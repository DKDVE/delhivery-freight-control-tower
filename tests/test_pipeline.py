import numpy as np
import pytest
import pandas as pd

from src import prep
from src import vendor_pipeline as vp


def _frame(n=6000, seed=0):
    rng = np.random.default_rng(seed)
    x = rng.uniform(0, 10, n)
    c = rng.choice(["a", "b", "c"], n)
    y = np.exp(0.3 * x + rng.normal(0, 0.3 + 0.05 * x, n)) * 10  # right-skewed, spread grows with x
    d = pd.DataFrame({"x": x, "c": c, "y": y, "t": np.arange(n)})
    d["lr_date"] = pd.Timestamp("2025-04-01") + pd.to_timedelta(d["t"] // 30, unit="D")
    return d


def test_dedupe_drops_only_exact_copies_and_reports_freight():
    df = pd.DataFrame({"shipment_no": [1, 1, 2, 3, 3], "delivery_id": [10, 10, 20, 30, 31], "r2_freight": [100.0, 100.0, 50.0, 70.0, 70.0],
                       "gta_invoice_value": [120.0, 120.0, 60.0, 80.0, 80.0], "note": [None, None, "x", None, None]})
    kept, dups, s = prep.dedupe(df, save=False)
    assert len(kept) == 4 and len(dups) == 1  # rows 0/1 are identical (NaN == NaN); rows 3/4 differ in delivery_id
    assert s["duplicate_rows_dropped"] == 1 and s["r2_freight_rs_in_duplicates"] == 100.0 and s["shipments_affected"] == 1


def test_conformal_interval_reaches_nominal_coverage_out_of_sample():
    d = _frame()
    tr, va, te = d.t < 3000, (d.t >= 3000) & (d.t < 4500), d.t >= 4500
    ci = vp.conformal_interval(d, ["c"], ["x"], "y", tr, va, te)
    assert abs(ci["test_coverage_calibrated"] - 0.8) < 0.05
    assert (ci["pi_low"] <= ci["pi_high"]).all() and ci["n_cal"] == int(va.sum())
    point = d.loc[te, "y"].to_numpy() * 0 + d.loc[te, "y"].median()
    ci2 = vp.conformal_interval(d, ["c"], ["x"], "y", tr, va, te, point_pred=point)
    assert ((ci2["pi_low"] <= point) & (point <= ci2["pi_high"])).all()


def test_threshold_uses_validation_slice_only():
    """Changing the test labels must not move the threshold (it comes from the validation slice, before any test data is touched)."""
    d = _frame(4000, 1)
    d["target"] = (d["y"] > d["y"].median()).astype(int)
    d["lr_date"] = pd.Timestamp("2025-04-01") + pd.to_timedelta(d["t"] // 20, unit="D")
    tr, te = d["lr_date"] <= pd.Timestamp("2025-09-30"), d["lr_date"] > pd.Timestamp("2025-09-30")
    val_start = pd.Timestamp("2025-08-01")
    a = vp.run_classification(d, ["c"], ["x"], "target", tr, te, val_start, models=("LightGBM",))
    d2 = d.copy()
    d2.loc[te, "target"] = 1 - d2.loc[te, "target"]
    b = vp.run_classification(d2, ["c"], ["x"], "target", tr, te, val_start, models=("LightGBM",))
    assert a["thr"]["LightGBM"] == b["thr"]["LightGBM"]
    assert a["table"]["LightGBM"]["val_precision_at_thr"] >= 0.60 or "F1" in a["table"]["LightGBM"]["thr_rule"]


class _Q:
    def __init__(self, v):
        self.v = v

    def predict(self, X):
        return np.array([self.v])


def test_v1_interval_uses_the_correction_of_the_load_band():
    from app import tools as T
    m = {"q_low": _Q(100.0), "q_high": _Q(200.0), "conformal_Q": 0.1, "conformal_Q_by_band": {"<5": 0.5, "5-15": 0.2, "15-25": 0.05, ">25": 0.01},
         "qty_band_edges": [0, 5, 15, 25, float("inf")], "qty_band_labels": ["<5", "5-15", "15-25", ">25"]}
    for qty, q in [(3, 0.5), (5, 0.2), (14.9, 0.2), (15, 0.05), (24, 0.05), (25, 0.01), (40, 0.01)]:
        lo, hi = T._interval(m, pd.DataFrame({"total_qty": [qty]}), 150.0)
        assert lo == pytest.approx(np.expm1(np.log1p(100.0) - q)) and hi == pytest.approx(np.expm1(np.log1p(200.0) + q)), qty
    lo, hi = T._interval({k: v for k, v in m.items() if k != "conformal_Q_by_band"}, pd.DataFrame({"total_qty": [3]}), 150.0)
    assert lo == pytest.approx(np.expm1(np.log1p(100.0) - 0.1))  # models without bands keep the global Q


def test_mondrian_conformal_calibrates_each_group_separately():
    d = _frame(9000, 3)
    d["g"] = np.where(d["x"] < 3, "low", "high")
    tr, va, te = d.t < 4000, (d.t >= 4000) & (d.t < 6500), d.t >= 6500
    ci = vp.conformal_interval(d, ["c"], ["x"], "y", tr, va, te, groups=d["g"], min_cal=100)
    assert set(ci["Q_by_group"]) == {"low", "high"} and ci["Q_by_group"]["low"] != ci["Q_by_group"]["high"]
    y, g = d.loc[te, "y"].to_numpy(), d.loc[te, "g"].to_numpy()
    for lab in ("low", "high"):
        m = g == lab
        cov = ((y[m] >= ci["pi_low"][m]) & (y[m] <= ci["pi_high"][m])).mean()
        assert abs(cov - 0.8) < 0.08, (lab, cov)
    tiny = vp.conformal_interval(d, ["c"], ["x"], "y", tr, va, te, groups=d["g"], min_cal=10 ** 6)  # too few rows per group: global Q is used
    assert all(q == tiny["Q"] for q in tiny["Q_by_group"].values())


def test_v1_metrics_report_interval_coverage_by_band():
    import json
    import os
    m = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports", "metrics", "V1.json"), encoding="utf8"))
    t = m["business_impact_inputs"]["interval_by_total_qty_band"]
    assert [r["band_MT"] for r in t] == ["<5", "5-15", "15-25", ">25"]
    assert all(r["n_test"] > 0 and 0 < r["coverage_per_band_Q"] <= 1 for r in t)
    assert abs(t[0]["coverage_per_band_Q"] - 0.8) < abs(t[0]["coverage_global_Q"] - 0.8)  # the worst band improves
