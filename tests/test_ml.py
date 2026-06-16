"""ML katmanı testleri — sentetik veriyle, ağsız, deterministik (sabit seed yok; saf sinüs)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.core.models import Action
from src.ml.features import FEATURE_COLUMNS, build_features, build_labels
from src.ml.train import build_estimator, model_path, save_bundle, train_model
from src.strategies.ml_strategy import MlStrategy


def _df(n: int = 400) -> pd.DataFrame:
    # Çok dalgalı seri → etiketlerde hem BUY hem SELL hem HOLD çıkar.
    t = np.arange(n)
    closes = 100 + 15 * np.sin(t / 20.0) + 8 * np.sin(t / 7.0) + t * 0.03
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    close = pd.Series(closes, index=idx)
    open_ = close.shift(1).fillna(close.iloc[0])
    high = pd.concat([open_, close], axis=1).max(axis=1) + 0.5
    low = pd.concat([open_, close], axis=1).min(axis=1) - 0.5
    volume = pd.Series(1000.0 + (t % 7) * 50, index=idx)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume})


def test_features_columns_and_no_nan_after_warmup():
    feats = build_features(_df(200), {})
    assert list(feats.columns) == FEATURE_COLUMNS
    clean = feats.dropna()
    assert len(clean) > 0
    assert not clean.isna().any().any()


def test_labels_classes_and_tail_nan():
    labels = build_labels(_df(400), horizon=8, threshold_pct=1.5)
    classes = set(labels.dropna().unique())
    assert classes.issubset({"BUY", "SELL", "HOLD"})
    assert len(classes) >= 2  # en az iki sınıf
    assert labels.iloc[-8:].isna().all()  # son horizon barda hedef yok


def test_train_and_predict(tmp_path):
    params = {
        "horizon": 8,
        "threshold_pct": 1.5,
        "n_estimators": 50,
        "model_dir": str(tmp_path),
        "atr_period": 14,
        "timeframe": "1h",
    }
    df = _df(400)
    bundle = train_model(df, params)
    assert bundle["features"] == FEATURE_COLUMNS
    assert hasattr(bundle["model"], "predict_proba")

    save_bundle(bundle, model_path(tmp_path, "TEST/USDT", "1h"))
    sig = MlStrategy(params).generate(df, "TEST/USDT")
    assert sig.action in (Action.BUY, Action.SELL, Action.HOLD)
    assert 0.0 <= sig.confidence <= 1.0


def test_ml_without_model_returns_hold(tmp_path):
    params = {"model_dir": str(tmp_path), "timeframe": "1h", "atr_period": 14}
    sig = MlStrategy(params).generate(_df(200), "NOMODEL/USDT")
    assert sig.action == Action.HOLD
    assert sig.confidence == 0.0


# --------------------------- yeni: model fabrikası / CV / kalibrasyon ---------------------------


def _train_params(tmp_path, **extra) -> dict:
    return {
        "horizon": 8, "threshold_pct": 1.5, "n_estimators": 40, "cv_splits": 3,
        "model_dir": str(tmp_path), "atr_period": 14, "timeframe": "1h", **extra,
    }


def test_build_estimator_known_types():
    for mt in ("rf", "hgb"):
        est = build_estimator(mt, {"n_estimators": 10})
        assert hasattr(est, "fit")
    with pytest.raises(ValueError):
        build_estimator("bilinmeyen", {})


@pytest.mark.parametrize("model_type", ["hgb", "lgbm", "xgb"])
def test_train_with_model_types(tmp_path, model_type):
    if model_type == "lgbm":
        pytest.importorskip("lightgbm")
    if model_type == "xgb":
        pytest.importorskip("xgboost")

    bundle = train_model(_df(400), _train_params(tmp_path, model_type=model_type))
    assert bundle["model_type"] == model_type
    assert bundle["features"] == FEATURE_COLUMNS
    # Walk-forward CV metrikleri hesaplanmış olmalı (NaN değil).
    assert bundle["cv_accuracy"] == bundle["cv_accuracy"]  # not NaN

    save_bundle(bundle, model_path(tmp_path, "MT/USDT", "1h"))
    sig = MlStrategy(_train_params(tmp_path, model_type=model_type)).generate(_df(400), "MT/USDT")
    assert sig.action in (Action.BUY, Action.SELL, Action.HOLD)
    assert 0.0 <= sig.confidence <= 1.0


def test_train_with_calibration_and_importance(tmp_path):
    bundle = train_model(_df(500), _train_params(tmp_path, calibrate=True))
    # Özellik önemi RF için dolu olmalı, tüm özellikleri kapsamalı.
    assert bundle["feature_importance"] is not None
    assert set(bundle["feature_importance"].keys()) == set(FEATURE_COLUMNS)
    # Kalibrasyon ya başarılı ya da güvenli şekilde atlanmış (bool).
    assert isinstance(bundle["calibrated"], bool)


def test_old_bundle_feature_subset_backcompat(tmp_path):
    # Eski model yalnız ilk 13 özellikle eğitilmiş gibi davran; tahmin hâlâ çalışmalı.
    from sklearn.ensemble import RandomForestClassifier

    old_cols = FEATURE_COLUMNS[:13]
    df = _df(400)
    feats = build_features(df, {})
    labels = build_labels(df, 8, 1.5)
    data = feats.assign(__y__=labels).dropna()
    model = RandomForestClassifier(n_estimators=20, random_state=42).fit(
        data[old_cols], data["__y__"]
    )
    bundle = {"model": model, "features": old_cols, "horizon": 8, "threshold_pct": 1.5}
    save_bundle(bundle, model_path(tmp_path, "OLD/USDT", "1h"))

    sig = MlStrategy({"model_dir": str(tmp_path), "timeframe": "1h", "atr_period": 14}).generate(
        df, "OLD/USDT"
    )
    assert sig.action in (Action.BUY, Action.SELL, Action.HOLD)
