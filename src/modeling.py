"""Model pipelines shared by V1-V3: Ridge / Logistic (target encoding), RandomForest (ordinal codes), LightGBM (native categoricals)."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.compose import ColumnTransformer, TransformedTargetRegressor
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler, TargetEncoder
from lightgbm import LGBMClassifier, LGBMRegressor

RANDOM_STATE = 42


class CatFrame(BaseEstimator, TransformerMixin):
    """Groups levels with < min_rows training rows into 'OTHER' (unseen levels also -> 'OTHER').

    mode: 'str' (object columns), 'category' (pandas categories, for LightGBM), 'codes' (int codes, for RandomForest).
    Numeric NaNs are filled with the training median when fill_num=True.
    """

    def __init__(self, cat_cols, num_cols, mode="str", min_rows=20, fill_num=True):
        self.cat_cols = cat_cols
        self.num_cols = num_cols
        self.mode = mode
        self.min_rows = min_rows
        self.fill_num = fill_num

    @staticmethod
    def _s(col):
        return col.astype(object).where(col.notna(), "MISSING").astype(str)

    def fit(self, X, y=None):
        self.levels_ = {}
        for c in self.cat_cols:
            vc = self._s(X[c]).value_counts()
            self.levels_[c] = sorted(vc[vc >= self.min_rows].index) + ["OTHER"]
        self.medians_ = {c: float(X[c].median()) for c in self.num_cols}
        return self

    def transform(self, X):
        out = {}
        for c in self.cat_cols:
            s = self._s(X[c])
            s = s.where(s.isin(self.levels_[c]), "OTHER")
            if self.mode == "str":
                out[c] = s.values
            else:
                cat = pd.Categorical(s, categories=self.levels_[c])
                out[c] = cat if self.mode == "category" else cat.codes
        for c in self.num_cols:
            v = X[c].astype(float)
            out[c] = v.fillna(self.medians_[c]).values if self.fill_num else v.values
        return pd.DataFrame(out, index=X.index)

    def get_feature_names_out(self, input_features=None):
        return np.array(list(self.cat_cols) + list(self.num_cols))


def _linear_prep(cat_cols, num_cols):
    ct = ColumnTransformer([
        ("te", TargetEncoder(cv=5, random_state=RANDOM_STATE), list(cat_cols)),
        ("sc", StandardScaler(), list(num_cols)),
    ])
    return Pipeline([("frame", CatFrame(cat_cols, num_cols, "str")), ("ct", ct)])


def _log_target(reg):
    return TransformedTargetRegressor(regressor=reg, func=np.log1p, inverse_func=np.expm1)


def build_regressor(name, cat_cols, num_cols, **kw):
    if name == "Ridge":
        pipe = Pipeline([("prep", _linear_prep(cat_cols, num_cols)), ("model", Ridge(alpha=1.0))])
    elif name == "RandomForest":
        pipe = Pipeline([("prep", CatFrame(cat_cols, num_cols, "codes")),
                         ("model", RandomForestRegressor(n_estimators=150, min_samples_leaf=3, max_features=0.6,
                                                         n_jobs=-1, random_state=RANDOM_STATE))])
    elif name == "LightGBM":
        pipe = Pipeline([("prep", CatFrame(cat_cols, num_cols, "category", fill_num=False)),
                         ("model", LGBMRegressor(n_estimators=500, learning_rate=0.05, num_leaves=63, min_child_samples=20,
                                                 subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                                                 cat_smooth=20, random_state=RANDOM_STATE, n_jobs=-1, verbose=-1, **kw))])
    else:
        raise ValueError(name)
    return _log_target(pipe)


def build_classifier(name, cat_cols, num_cols):
    if name == "Logistic":
        return Pipeline([("prep", _linear_prep(cat_cols, num_cols)),
                         ("model", LogisticRegression(max_iter=2000, class_weight="balanced", random_state=RANDOM_STATE))])
    if name == "RandomForest":
        return Pipeline([("prep", CatFrame(cat_cols, num_cols, "codes")),
                         ("model", RandomForestClassifier(n_estimators=200, min_samples_leaf=3, max_features="sqrt",
                                                          class_weight="balanced", n_jobs=-1, random_state=RANDOM_STATE))])
    if name == "LightGBM":
        return Pipeline([("prep", CatFrame(cat_cols, num_cols, "category", fill_num=False)),
                         ("model", LGBMClassifier(n_estimators=400, learning_rate=0.05, num_leaves=31, min_child_samples=20,
                                                  subsample=0.8, subsample_freq=1, colsample_bytree=0.8, cat_smooth=20,
                                                  class_weight="balanced", random_state=RANDOM_STATE, n_jobs=-1, verbose=-1))])
    raise ValueError(name)


def inner_pipeline(fitted):
    """Fitted sklearn Pipeline inside a TransformedTargetRegressor (or the pipeline itself)."""
    return fitted.regressor_ if isinstance(fitted, TransformedTargetRegressor) else fitted
