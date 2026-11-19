"""Task runners for V1-V3: fit three models + baselines on one split, pick the winner, explain it."""
from __future__ import annotations

import json
import os
import time

import numpy as np
import pandas as pd

from backend import vendor_codes
from src import evaluate as ev
from src import plotting as pl
from src.modeling import RANDOM_STATE, build_classifier, build_regressor, inner_pipeline

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
METRICS = os.path.join(ROOT, "reports", "metrics")
FIGS = os.path.join(ROOT, "reports", "figures")
MODELS = os.path.join(ROOT, "models")
PROC = os.path.join(ROOT, "data", "processed")
for _d in (METRICS, FIGS, MODELS, PROC):
    os.makedirs(_d, exist_ok=True)

SIMPLICITY = ["Ridge", "Logistic", "RandomForest", "LightGBM"]
RF_SAMPLE = 50_000


def _sub(X, y, name, n=RF_SAMPLE):
    if name == "RandomForest" and len(X) > n:
        idx = X.sample(n, random_state=RANDOM_STATE).index
        return X.loc[idx], y.loc[idx]
    return X, y


def pick_winner(scores: dict, higher_is_better: bool, tol=0.02):
    best = max(scores.values()) if higher_is_better else min(scores.values())
    ok = [m for m, v in scores.items() if (v >= best * (1 - tol) if higher_is_better else v <= best * (1 + tol))]
    ok.sort(key=SIMPLICITY.index)
    return ok[0], best, ok


def lookup_rate(train, test, keys, target, min_rows=5):
    """Mean of `target` per key combination in `train`; combos with < min_rows rows get the train base rate."""
    tr, te = train[keys + [target]].copy(), test[keys].copy()
    for k in keys:
        tr[k], te[k] = tr[k].fillna("MISSING").astype(str), te[k].fillna("MISSING").astype(str)
    base = float(tr[target].mean())
    g = tr.groupby(keys)[target].agg(["mean", "size"])
    g.loc[g["size"] < min_rows, "mean"] = base
    rate = te.join(g["mean"].rename("rate"), on=keys)["rate"].fillna(base)
    return rate.values, base, g


def run_classification(df, cats, nums, target, train_m, test_m, val_start, models=("Logistic", "RandomForest", "LightGBM")):
    """Fit on train; threshold chosen on an out-of-time validation slice (train before val_start -> predict val_start..train end)."""
    X, y = df[cats + nums], df[target].astype(int)
    early = train_m & (df["lr_date"] < val_start)
    val = train_m & (df["lr_date"] >= val_start)
    out = {"fitted": {}, "proba": {}, "table": {}, "thr": {}, "fit_s": {}}
    for name in models:
        # threshold from a copy trained on the early part of the training period only
        Xe, ye = _sub(X[early], y[early], name)
        pv = build_classifier(name, cats, nums).fit(Xe, ye).predict_proba(X[val])[:, 1]
        thr, vp, vr, vf = ev.choose_threshold(y[val].values, pv)
        t0 = time.time()
        Xf, yf = _sub(X[train_m], y[train_m], name)
        m = build_classifier(name, cats, nums).fit(Xf, yf)
        out["fit_s"][name] = round(time.time() - t0, 1)
        p = m.predict_proba(X[test_m])[:, 1]
        met = ev.cls_metrics(y[test_m].values, p, thr)
        out["fitted"][name], out["proba"][name], out["thr"][name] = m, p, thr
        out["table"][name] = {**met, "val_precision_at_thr": round(vp, 4), "val_recall_at_thr": round(vr, 4),
                              "thr_rule": "recall-max with val precision>=0.60" if vp >= 0.60 else "best val F1 (precision 0.60 unreachable)"}
    return out


def run_regression(df, cats, nums, target, train_m, test_m, models=("Ridge", "RandomForest", "LightGBM")):
    X, y = df[cats + nums], df[target]
    out = {"fitted": {}, "pred": {}, "table": {}, "fit_s": {}}
    for name in models:
        Xf, yf = _sub(X[train_m], y[train_m], name)
        t0 = time.time()
        m = build_regressor(name, cats, nums).fit(Xf, yf)
        out["fit_s"][name] = round(time.time() - t0, 1)
        p = m.predict(X[test_m])
        out["fitted"][name], out["pred"][name] = m, p
        out["table"][name] = ev.reg_metrics(y[test_m].values, p)
    return out


def quantile_interval(df, cats, nums, target, train_m, test_m, lo=0.1, hi=0.9):
    X, y = df[cats + nums], df[target]
    res = {}
    for a in (lo, hi):
        res[a] = build_regressor("LightGBM", cats, nums, objective="quantile", alpha=a).fit(X[train_m], y[train_m])
    return res[lo], res[hi]


def conformal_interval(df, cats, nums, target, early_m, val_m, test_m, point_pred=None, lo=0.1, hi=0.9, alpha=0.2, groups=None, min_cal=200):
    """Split-conformal (CQR) calibration of the LightGBM-quantile interval.
    Quantile models are fitted on `early_m` only; the validation slice `val_m` (later in time) gives the conformity scores
    s = max(q_lo - y, y - q_hi) measured on the log1p scale (the targets are right-skewed, so the correction is multiplicative);
    the interval for test rows is [q_lo, q_hi] widened (or narrowed) by the ceil((n+1)(1-alpha))/n quantile of s.
    `point_pred` (test rows) is folded in so the point forecast always sits inside its interval (this can only widen it).
    `groups` (a label per row of df, e.g. a load band) switches on Mondrian calibration: one correction Q per group, computed from that group's
    validation rows only; a group with fewer than `min_cal` validation rows falls back to the global Q. The global-Q interval is returned as pi_low_global / pi_high_global."""
    X, y = df[cats + nums], df[target]
    ql = build_regressor("LightGBM", cats, nums, objective="quantile", alpha=lo).fit(X[early_m], y[early_m])
    qh = build_regressor("LightGBM", cats, nums, objective="quantile", alpha=hi).fit(X[early_m], y[early_m])

    def band(mask):
        a_, b_ = np.clip(ql.predict(X[mask]), 0, None), np.clip(qh.predict(X[mask]), 0, None)
        return np.minimum(a_, b_), np.maximum(a_, b_)

    lv, hv = band(val_m)
    yv = y[val_m].to_numpy(float)
    score = np.maximum(np.log1p(lv) - np.log1p(yv), np.log1p(yv) - np.log1p(hv))
    n = len(score)
    k = min(np.ceil((n + 1) * (1 - alpha)) / n, 1.0)
    Q = float(np.quantile(score, k, method="higher"))
    lt, ht = band(test_m)
    yt = y[test_m].to_numpy(float)
    def widen(q):
        a_, b_ = np.expm1(np.log1p(lt) - q), np.expm1(np.log1p(ht) + q)
        a_, b_ = np.clip(np.minimum(a_, b_), 0, None), np.maximum(a_, b_)
        if point_pred is not None:
            a_, b_ = np.minimum(a_, point_pred), np.maximum(b_, point_pred)
        return a_, b_

    cg_l, cg_h = widen(Q)
    Q_by_group = None
    if groups is not None:
        g = groups.astype(str)
        gv, gt = g[val_m].to_numpy(), g[test_m].to_numpy()
        Q_by_group = {}
        for lab in sorted(set(gv)):
            sc = score[gv == lab]
            if len(sc) >= min_cal:
                kk = min(np.ceil((len(sc) + 1) * (1 - alpha)) / len(sc), 1.0)
                Q_by_group[lab] = float(np.quantile(sc, kk, method="higher"))
            else:
                Q_by_group[lab] = Q
        cl, ch = widen(np.array([Q_by_group.get(x, Q) for x in gt]))
    else:
        cl, ch = cg_l, cg_h
    if point_pred is not None:
        lt, ht = np.minimum(lt, point_pred), np.maximum(ht, point_pred)
    cov = lambda a_, b_, t_: float(((t_ >= a_) & (t_ <= b_)).mean())
    return {"q_low": ql, "q_high": qh, "Q": Q, "n_cal": int(n), "target_coverage": 1 - alpha,
            "val_coverage_raw": cov(lv, hv, yv), "test_coverage_raw": cov(lt, ht, yt), "test_coverage_calibrated": cov(cl, ch, yt),
            "test_mean_width_raw": float((ht - lt).mean()), "test_mean_width_calibrated": float((ch - cl).mean()),
            "pi_low": cl, "pi_high": ch, "raw_low": lt, "raw_high": ht,
            "Q_by_group": Q_by_group, "pi_low_global": cg_l, "pi_high_global": cg_h}


def shap_values(fitted, X_raw, sample=2000, kind="clf"):
    import shap
    inner = inner_pipeline(fitted)
    Xs = X_raw.sample(min(sample, len(X_raw)), random_state=RANDOM_STATE)
    Z = inner.named_steps["prep"].transform(Xs)
    ex = shap.TreeExplainer(inner.named_steps["model"])
    sv = ex.shap_values(Z, check_additivity=False)
    if isinstance(sv, list):
        sv = sv[1]
    if getattr(sv, "ndim", 2) == 3:
        sv = sv[:, :, 1]
    base = np.ravel(ex.expected_value)[-1]
    return Xs, Z, np.asarray(sv), float(base)


def _numeric_view(Z):
    Zn = Z.copy()
    for c in Zn.columns:
        if str(Zn[c].dtype) == "category":
            Zn[c] = Zn[c].cat.codes
    return Zn.astype(float)


def plain_english(feature, Xraw, Z, sv, cats, outcome_up, outcome_down=None):
    """One data-derived sentence per driver: direction for numeric features, top level for categorical ones."""
    j = list(Z.columns).index(feature)
    s = sv[:, j]
    if feature in cats:
        g = pd.DataFrame({"lvl": Xraw[feature].astype(str).values, "s": s}).groupby("lvl")["s"].agg(["mean", "size"])
        g = g[g["size"] >= 15].sort_values("mean")
        if len(g) < 2:
            return f"{feature} shifts the prediction depending on its value."
        return (f"{feature}: the biggest push toward {outcome_up} comes from '{g.index[-1]}', "
                f"the biggest push away from it from '{g.index[0]}'.")
    v = Xraw[feature].astype(float)
    rho = pd.Series(v.values).corr(pd.Series(s), method="spearman")
    if rho is None or np.isnan(rho) or abs(rho) < 0.1:
        return f"{feature} matters, but not in one direction: its effect depends on the lane and vehicle."
    word = "raises" if rho > 0 else "lowers"
    return f"Higher {feature} {word} the predicted {outcome_up}."


def shap_report(fitted, X_raw, cats, task_id, bar_title, beeswarm_title, outcome, sample=2000):
    import matplotlib.pyplot as plt
    import shap
    Xs, Z, sv, base = shap_values(fitted, X_raw, sample)
    imp = pd.Series(np.abs(sv).mean(0), index=Z.columns).sort_values(ascending=False)
    top = imp.head(15)
    fig, ax = plt.subplots(figsize=(7.5, 5.5))
    ax.barh(top.index[::-1], top.values[::-1], color=pl.TEAL)
    pl.style(ax, title=bar_title, xlabel="mean |SHAP value|")
    pl.save(fig, os.path.join(FIGS, f"{task_id}_shap_bar.png"))
    plt.figure(figsize=(8, 6))
    shap.summary_plot(sv, _numeric_view(Z), max_display=15, show=False, plot_size=None)
    plt.title(beeswarm_title, loc="left", color=pl.INK, fontsize=12, fontweight="bold")
    plt.gcf().patch.set_facecolor("white")
    plt.tight_layout()
    plt.savefig(os.path.join(FIGS, f"{task_id}_shap_beeswarm.png"), dpi=110, facecolor="white")
    plt.close("all")
    drivers = [{"feature": f, "mean_abs_shap": round(float(imp[f]), 5),
                "plain_english": plain_english(f, Xs, Z, sv, cats, outcome)} for f in imp.index[:5]]
    return drivers, (Xs, Z, sv, base), imp


def waterfall_png(Z, sv, base, i, path, title):
    import matplotlib.pyplot as plt
    import shap
    ex = shap.Explanation(values=sv[i], base_values=base, data=_numeric_view(Z).iloc[i].values, feature_names=list(Z.columns))
    plt.figure(figsize=(8, 5.5))
    shap.plots.waterfall(ex, max_display=10, show=False)
    plt.title(title, loc="left", color=pl.INK, fontsize=11, fontweight="bold")
    plt.gcf().patch.set_facecolor("white")
    plt.tight_layout()
    plt.savefig(path, dpi=110, facecolor="white")
    plt.close("all")


def _mode(s):
    s = s.dropna()
    return s.value_counts().index[0] if len(s) else np.nan


def counterfactual_saving(test_flagged, train_all, v1_fitted, v1_cats, v1_nums):
    """For each flagged TEST shipment: smallest TRAIN capacity on the same route with capacity >= total_qty; re-price with V1."""
    tr = train_all[train_all["vehicle_capacity"] > 0]
    caps = {r: np.sort(g["vehicle_capacity"].unique()) for r, g in tr.groupby("route")}
    vt_route = tr.groupby(["route", "vehicle_capacity"])["vehicle_type"].agg(_mode)
    vt_cap = tr.groupby("vehicle_capacity")["vehicle_type"].agg(_mode)

    cf_cap = np.full(len(test_flagged), np.nan)
    for i, (r, q) in enumerate(zip(test_flagged["route"].values, test_flagged["total_qty"].values)):
        a = caps.get(r)
        if a is not None:
            k = np.searchsorted(a, q, side="left")
            if k < len(a):
                cf_cap[i] = a[k]
    cf = test_flagged.copy()
    cf["cf_cap"] = cf_cap
    cf = cf[cf["cf_cap"].notna()].copy()
    cf["orig_cap"] = cf["vehicle_capacity"]
    cf["vehicle_capacity"] = cf["cf_cap"]
    cf["vehicle_type"] = [vt_route.get((r, c), vt_cap.get(c, np.nan)) for r, c in zip(cf["route"], cf["cf_cap"])]

    feats = v1_cats + v1_nums
    orig = test_flagged.loc[cf.index]
    cf["pred_orig"] = v1_fitted.predict(orig[feats])
    cf["pred_cf"] = v1_fitted.predict(cf[feats])
    cf["saving"] = (cf["freight"] - cf["pred_cf"]).clip(lower=0)
    cf["saving_pct"] = cf["saving"] / cf["freight"] * 100
    cf["smaller_vehicle"] = cf["cf_cap"] < cf["orig_cap"]
    return cf


def save_json(task_id, payload):
    """Write reports/metrics/<task_id>.json with any vendor name replaced by its code."""
    def conv(o):
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return None if np.isnan(o) else float(o)
        if isinstance(o, (np.bool_,)):
            return bool(o)
        if isinstance(o, (pd.Timestamp,)):
            return str(o.date())
        raise TypeError(type(o))
    with open(os.path.join(METRICS, f"{task_id}.json"), "w", encoding="utf8") as f:
        json.dump(vendor_codes.scrub(json.loads(json.dumps(payload, default=conv))), f, indent=2, ensure_ascii=False)
