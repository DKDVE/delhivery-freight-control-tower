"""Shared evaluation helpers for the Delhivery cost-to-serve capstone."""
from __future__ import annotations

import numpy as np


def reg_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    """MAE in original units, MAPE on rows with target > 0, R2."""
    from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    mae = float(mean_absolute_error(y_true, y_pred))
    rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))
    mask = y_true > 0
    mape = float(np.mean(np.abs((y_true[mask] - y_pred[mask]) / y_true[mask]))) * 100 if mask.any() else float("nan")
    r2 = float(r2_score(y_true, y_pred))
    return {"MAE": round(mae, 2), "RMSE": round(rmse, 2), "MAPE": round(mape, 2), "R2": round(r2, 4)}


def baseline_reg_metrics(y_true: np.ndarray, pred_fn) -> dict:
    return reg_metrics(y_true, pred_fn(y_true))


def cls_metrics(y_true: np.ndarray, proba: np.ndarray, threshold: float) -> dict:
    """PR-AUC, ROC-AUC, precision, recall, F1 at the chosen threshold."""
    from sklearn.metrics import (
        average_precision_score,
        roc_auc_score,
        precision_score,
        recall_score,
        f1_score,
    )

    y_true = np.asarray(y_true)
    proba = np.asarray(proba)
    pred = (proba >= threshold).astype(int)
    pr = float(average_precision_score(y_true, proba))
    try:
        roc = float(roc_auc_score(y_true, proba))
    except ValueError:
        roc = float("nan")
    prec = float(precision_score(y_true, pred, zero_division=0))
    rec = float(recall_score(y_true, pred, zero_division=0))
    f1 = float(f1_score(y_true, pred, zero_division=0))
    return {
        "PR_AUC": round(pr, 4),
        "ROC_AUC": round(roc, 4),
        "precision": round(prec, 4),
        "recall": round(rec, 4),
        "F1": round(f1, 4),
        "threshold": round(threshold, 4),
    }


def pr_curve(y_true: np.ndarray, proba: np.ndarray):
    from sklearn.metrics import precision_recall_curve
    return precision_recall_curve(y_true, proba)


def choose_threshold(y_true: np.ndarray, proba: np.ndarray, min_precision: float = 0.60):
    """Pick threshold maximising recall while precision >= min_precision; else best F1."""
    from sklearn.metrics import precision_recall_curve
    p, r, t = precision_recall_curve(y_true, proba)
    # p, r are len n+1; t is len n
    prec = p[:-1]
    rec = r[:-1]
    f1 = 2 * prec * rec / np.where(prec + rec == 0, 1, prec + rec)
    valid = prec >= min_precision
    if valid.any():
        idx = np.where(valid, rec, -1).argmax()
        return float(t[idx]), float(prec[idx]), float(rec[idx]), float(f1[idx])
    idx = int(np.argmax(f1))
    return float(t[idx]), float(prec[idx]), float(rec[idx]), float(f1[idx])


def fit_seconds(fn, *args, **kwargs):
    import time
    t0 = time.time()
    out = fn(*args, **kwargs)
    return out, round(time.time() - t0, 2)


def safe_div(a, b):
    return float(a / b) if b else float("nan")