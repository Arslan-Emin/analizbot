"""Model eğitimi, kaydı ve yüklenmesi.

Aşırı-öğrenme/leakage önlemleri:
  - Özellikler nedensel (features.py).
  - KRONOLOJİK bölme: eğitim = erken %80, test = geç %20 (KARIŞTIRMA YOK).
  - **Walk-forward CV** (TimeSeriesSplit): zaman sırasını koruyan dürüst doğrulama.
  - Sabit random_state → tekrarlanabilir.

Modeller: rf (RandomForest), hgb (HistGradientBoosting), lgbm (LightGBM), xgb (XGBoost).
Etiketler tek bir yolda sayısala kodlanır (`_LabelEncodedClassifier`) → tüm modeller
ve olasılık kalibrasyonu (CalibratedClassifierCV) aynı arayüzle çalışır; tahminde
string sınıflar (BUY/SELL/HOLD) geri verilir.
"""

from __future__ import annotations

import logging
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from src.ml.features import FEATURE_COLUMNS, build_features, build_labels

log = logging.getLogger(__name__)


class _LabelEncodedClassifier:
    """Sayısal-etiketli bir sklearn modelini sarıp string sınıflar sunan ince adaptör.

    fit yok: önceden eğitilmiş bir estimator + LabelEncoder tutar. `classes_`,
    predict_proba sütun sırasıyla hizalıdır (LabelEncoder sıralı → estimator.classes_
    sıralı). MlStrategy bunu standart sınıflandırıcı gibi kullanır.
    """

    def __init__(self, estimator, label_encoder) -> None:
        self.estimator = estimator
        self._le = label_encoder

    @property
    def classes_(self):
        return self._le.classes_

    def predict_proba(self, X):
        return self.estimator.predict_proba(X)

    def predict(self, X):
        return self._le.inverse_transform(self.estimator.predict(X))


def model_path(model_dir: str | Path, symbol: str, timeframe: str) -> Path:
    safe = symbol.replace("/", "_")
    return Path(model_dir) / f"ml_{safe}_{timeframe}.joblib"


def build_estimator(model_type: str, params: dict, overrides: dict | None = None):
    """`model_type`'a göre sayısal-etiketli bir sklearn-uyumlu sınıflandırıcı kurar."""
    n_estimators = int(params.get("n_estimators", 200))
    max_depth = params.get("max_depth")
    over = overrides or {}

    if model_type == "rf":
        from sklearn.ensemble import RandomForestClassifier

        est = RandomForestClassifier(
            n_estimators=n_estimators, max_depth=max_depth, random_state=42,
            class_weight="balanced", n_jobs=-1,
        )
    elif model_type == "hgb":
        from sklearn.ensemble import HistGradientBoostingClassifier

        est = HistGradientBoostingClassifier(
            max_iter=n_estimators, max_depth=max_depth, random_state=42,
            class_weight="balanced",
        )
    elif model_type == "lgbm":
        try:
            from lightgbm import LGBMClassifier
        except ImportError as exc:
            raise ValueError("lightgbm kurulu değil: pip install lightgbm") from exc

        est = LGBMClassifier(
            n_estimators=n_estimators, max_depth=(max_depth or -1), random_state=42,
            class_weight="balanced", n_jobs=-1, verbose=-1,
        )
    elif model_type == "xgb":
        try:
            from xgboost import XGBClassifier
        except ImportError as exc:
            raise ValueError("xgboost kurulu değil: pip install xgboost") from exc

        est = XGBClassifier(
            n_estimators=n_estimators, max_depth=(max_depth or 6), random_state=42,
            tree_method="hist", eval_metric="mlogloss", n_jobs=-1,
        )
    else:
        raise ValueError(f"Bilinmeyen model_type: {model_type!r} (rf/hgb/lgbm/xgb)")

    if over:
        est.set_params(**over)
    return est


def _param_distributions(model_type: str) -> dict:
    """RandomizedSearch için mütevazı hiperparametre dağılımları."""
    if model_type in ("rf",):
        return {"n_estimators": [100, 200, 400], "max_depth": [None, 6, 10, 16],
                "min_samples_leaf": [1, 2, 5]}
    if model_type == "hgb":
        return {"max_iter": [100, 200, 400], "max_depth": [None, 4, 8],
                "learning_rate": [0.03, 0.06, 0.1]}
    if model_type == "lgbm":
        return {"n_estimators": [100, 200, 400], "num_leaves": [15, 31, 63],
                "learning_rate": [0.03, 0.06, 0.1]}
    if model_type == "xgb":
        return {"n_estimators": [100, 200, 400], "max_depth": [3, 6, 9],
                "learning_rate": [0.03, 0.06, 0.1]}
    return {}


def train_model(df: pd.DataFrame, params: dict) -> dict:
    """Veriden bir sınıflandırıcı eğitir, walk-forward metriklerle 'bundle' döndürür."""
    from sklearn.metrics import accuracy_score, classification_report
    from sklearn.model_selection import TimeSeriesSplit, cross_validate
    from sklearn.preprocessing import LabelEncoder

    horizon = int(params.get("horizon", 8))
    threshold = float(params.get("threshold_pct", 1.5))
    model_type = str(params.get("model_type", "rf"))
    cv_splits = int(params.get("cv_splits", 5))
    do_tune = bool(params.get("tune", False))
    do_calibrate = bool(params.get("calibrate", False))

    feats = build_features(df, params)
    labels = build_labels(df, horizon, threshold)

    data = feats.copy()
    data["__label__"] = labels
    data = data.dropna()  # warmup NaN + son horizon NaN düşer
    if len(data) < 50:
        raise ValueError("Eğitim için yeterli veri yok (en az ~50 temiz satır gerekli).")

    X = data[FEATURE_COLUMNS]
    y_str = data["__label__"]
    le = LabelEncoder()
    y = le.fit_transform(y_str)  # string → 0..n-1 (tek yol)

    # Kronolojik bölme (sızıntı yok): erken kısım eğitim, geç kısım test.
    split = int(len(X) * 0.8)
    X_train, X_test = X.iloc[:split], X.iloc[split:]
    y_train, y_test = y[:split], y[split:]

    # Walk-forward CV (tüm temiz veride, zaman sırasını koruyarak).
    cv_acc = cv_f1 = float("nan")
    n_splits = max(2, min(cv_splits, len(X) // 30))
    try:
        tscv = TimeSeriesSplit(n_splits=n_splits)
        cv = cross_validate(
            build_estimator(model_type, params), X, y, cv=tscv,
            scoring=["accuracy", "f1_macro"], n_jobs=-1,
        )
        cv_acc = float(np.mean(cv["test_accuracy"]))
        cv_f1 = float(np.mean(cv["test_f1_macro"]))
    except Exception as exc:  # CV bazı model/veri bileşimlerinde düşebilir; metriksiz devam
        log.warning("Walk-forward CV atlandı: %s", exc)

    # Opsiyonel hiperparametre arama (zaman-serisi CV ile).
    best_params: dict = {}
    if do_tune:
        from sklearn.model_selection import RandomizedSearchCV

        try:
            search = RandomizedSearchCV(
                build_estimator(model_type, params),
                _param_distributions(model_type),
                n_iter=8, cv=TimeSeriesSplit(n_splits=n_splits),
                scoring="f1_macro", random_state=42, n_jobs=-1,
            )
            search.fit(X_train, y_train)
            best_params = {k: v for k, v in search.best_params_.items()}
        except Exception as exc:
            log.warning("Hiperparametre araması atlandı: %s", exc)

    # Temel modeli eğit (özellik önemi + kalibrasyonsuz tahmin modeli).
    base = build_estimator(model_type, params, overrides=best_params)
    base.fit(X_train, y_train)

    importance = getattr(base, "feature_importances_", None)
    feature_importance = (
        {f: round(float(v), 5) for f, v in zip(FEATURE_COLUMNS, importance, strict=False)}
        if importance is not None else None
    )

    # Opsiyonel olasılık kalibrasyonu (sigmoid, zaman-serisi CV).
    calibrated = False
    estimator = base
    if do_calibrate:
        from sklearn.calibration import CalibratedClassifierCV

        try:
            cal = CalibratedClassifierCV(
                build_estimator(model_type, params, overrides=best_params),
                method="sigmoid", cv=TimeSeriesSplit(n_splits=n_splits),
            )
            cal.fit(X_train, y_train)
            estimator = cal
            calibrated = True
        except Exception as exc:
            log.warning("Kalibrasyon atlandı (temel modele dönülüyor): %s", exc)

    model = _LabelEncodedClassifier(estimator, le)

    if len(X_test):
        preds = model.predict(X_test)
        y_test_str = le.inverse_transform(y_test)
        test_acc = float(accuracy_score(y_test_str, preds))
        report = classification_report(y_test_str, preds, zero_division=0)
    else:
        test_acc = float("nan")
        report = ""

    return {
        "model": model,
        "features": FEATURE_COLUMNS,
        "model_type": model_type,
        "horizon": horizon,
        "threshold_pct": threshold,
        "test_accuracy": test_acc,
        "cv_accuracy": cv_acc,
        "cv_f1": cv_f1,
        "calibrated": calibrated,
        "best_params": best_params,
        "feature_importance": feature_importance,
        "report": report,
        "train_size": len(X_train),
        "test_size": len(X_test),
        "label_counts": pd.Series(y_str).value_counts().to_dict(),
    }


def save_bundle(bundle: dict, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, path)


def load_bundle(path: str | Path) -> dict | None:
    path = Path(path)
    if not path.exists():
        return None
    return joblib.load(path)
