"""
Trains the initial 'production' model (v1) on training_data.csv, using:
  - the exact preprocessing pipeline from the user's housing.ipynb notebook
    (see data/house_price_preprocessor.py)
  - the exact tuned GradientBoostingRegressor configuration that notebook
    found to have the lowest cross-validated log RMSE (~0.122), beating
    Linear Regression (0.126), Ridge (0.134-0.142), and untuned Random
    Forest (0.145)
  - the same log1p(SalePrice) target transform, so error metrics are
    directly comparable to the notebook's

Saves the fitted preprocessor alongside the model — both are needed to
turn a raw feature dict into a prediction, so they're saved and loaded
as a pair everywhere in this project (serving, retraining).
"""
import json
import time
import joblib
import numpy as np
import pandas as pd
from pathlib import Path
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error

from data.house_price_preprocessor import HousePricePreprocessor, TARGET_COL

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MODELS_DIR = Path(__file__).resolve().parent

# The user's tuned final configuration from housing.ipynb
FINAL_GB_PARAMS = dict(
    n_estimators=1500,
    learning_rate=0.05,
    max_depth=3,
    min_samples_leaf=2,
    min_samples_split=5,
    loss="huber",
    random_state=42,
)


def train(version_name="v1", data_path=None):
    df = pd.read_csv(data_path or DATA_DIR / "training_data.csv")

    y = df[TARGET_COL]
    X_train_raw, X_val_raw, y_train, y_val = train_test_split(
        df, y, test_size=0.2, random_state=42
    )

    preprocessor = HousePricePreprocessor()
    preprocessor.fit(X_train_raw)

    X_train = preprocessor.transform(X_train_raw)
    X_val = preprocessor.transform(X_val_raw)

    y_train_log = np.log1p(y_train)
    y_val_log = np.log1p(y_val)

    model = GradientBoostingRegressor(**FINAL_GB_PARAMS)
    model.fit(X_train, y_train_log)

    val_pred_log = model.predict(X_val)
    val_pred = np.expm1(val_pred_log)

    metrics = {
        "log_rmse": float(np.sqrt(mean_squared_error(y_val_log, val_pred_log))),
        "rmse": float(np.sqrt(mean_squared_error(y_val, val_pred))),
        "mae": float(np.mean(np.abs(y_val - val_pred))),  # dollars, for the drift-watchdog's own MAE tracking
        "trained_at": time.time(),
        "n_train_rows": len(X_train),
        "n_features": X_train.shape[1],
        "model_params": FINAL_GB_PARAMS,
    }

    model_path = MODELS_DIR / f"model_{version_name}.joblib"
    meta_path = MODELS_DIR / f"model_{version_name}.meta.json"
    preproc_path = MODELS_DIR / f"preprocessor_{version_name}.joblib"

    joblib.dump(model, model_path)
    preprocessor.save(preproc_path)
    meta_path.write_text(json.dumps(metrics, indent=2))

    print(f"Saved {model_path.name} + {preproc_path.name}")
    print(f"Log RMSE={metrics['log_rmse']:.4f} | RMSE=${metrics['rmse']:,.0f} | MAE=${metrics['mae']:,.0f}")
    return model_path, metrics


if __name__ == "__main__":
    train("v1")
