import numpy as np
import pytest
from sklearn.metrics import roc_auc_score

from src.gbm_signal import SignalModel


def synthetic(n=6000, seed=0):
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, 5)).astype(np.float32)
    X[rng.random(n) < 0.05, 2] = np.nan  # missing values must be tolerated
    y_long = (X[:, 0] + rng.normal(0, 1.0, n) > 1.2).astype(float)
    y_short = (-X[:, 1] + rng.normal(0, 1.0, n) > 1.2).astype(float)
    return X, y_long, y_short


def test_model_learns_each_side_from_its_own_signal():
    X, y_long, y_short = synthetic()
    model = SignalModel(max_iter=100).fit(X[:5000], y_long[:5000], y_short[:5000])
    p_long, p_short = model.predict(X[5000:])

    assert roc_auc_score(y_long[5000:], p_long) > 0.75
    assert roc_auc_score(y_short[5000:], p_short) > 0.75
    assert ((0 <= p_long) & (p_long <= 1)).all() and ((0 <= p_short) & (p_short <= 1)).all()


def test_calibrated_probabilities_track_observed_frequency():
    X, y_long, y_short = synthetic(20000, seed=1)
    model = SignalModel(max_iter=100).fit(X[:15000], y_long[:15000], y_short[:15000])
    p_long, _ = model.predict(X[15000:])
    # Among bars the model rates >= 45%, the long outcome should happen at a rate near that level.
    confident = p_long >= 0.45
    assert confident.sum() > 50
    assert y_long[15000:][confident].mean() == pytest.approx(p_long[confident].mean(), abs=0.1)


def test_save_and_load_round_trip(tmp_path):
    X, y_long, y_short = synthetic(3000)
    model = SignalModel(max_iter=50).fit(X, y_long, y_short)
    path = tmp_path / "gbm.joblib"
    model.save(path)
    loaded = SignalModel.load(path)
    np.testing.assert_array_equal(np.stack(model.predict(X[:100])), np.stack(loaded.predict(X[:100])))
