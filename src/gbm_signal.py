"""Gradient-boosting entry signal: calibrated P(target before stop) for longs and shorts."""

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression


class SignalModel:
    """Two boosted-tree classifiers (long, short), each calibrated on the newest part of its data."""

    def __init__(self, max_iter: int = 300, learning_rate: float = 0.05, min_samples_leaf: int = 200,
                 calib_fraction: float = 0.2, seed: int = 0):
        self.params: Dict[str, Any] = dict(max_iter=max_iter, learning_rate=learning_rate, max_leaf_nodes=31,
                           min_samples_leaf=min_samples_leaf, l2_regularization=1.0,
                           early_stopping=False, random_state=seed)
        self.calib_fraction = calib_fraction
        self.sides: dict = {}

    def _fit_side(self, X: np.ndarray, y: np.ndarray):
        # Rows are chronological: fit on the older part, calibrate on the newest part.
        split = int(len(X) * (1 - self.calib_fraction))
        clf = HistGradientBoostingClassifier(**self.params).fit(X[:split], y[:split])
        raw = clf.predict_proba(X[split:])[:, 1]
        iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(raw, y[split:])
        return clf, iso

    def fit(self, X: np.ndarray, y_long: np.ndarray, y_short: np.ndarray) -> "SignalModel":
        self.sides = {"long": self._fit_side(X, y_long), "short": self._fit_side(X, y_short)}
        return self

    def predict(self, X: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        def side(name):
            clf, iso = self.sides[name]
            return iso.predict(clf.predict_proba(X)[:, 1])
        return side("long"), side("short")

    def save(self, path: Path, metadata: Optional[dict] = None) -> None:
        joblib.dump({"params": self.params, "calib_fraction": self.calib_fraction,
                     "sides": self.sides, "metadata": metadata or {}}, path)

    @classmethod
    def load(cls, path: Path) -> "SignalModel":
        # joblib unpickles, which can run code: load only model files this project wrote
        # (experiments/train_gbm.py into models/), never a path taken from a request.
        data = joblib.load(path)
        model = cls(calib_fraction=data["calib_fraction"])
        model.params, model.sides = data["params"], data["sides"]
        model.metadata = data.get("metadata", {})
        return model
