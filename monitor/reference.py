"""
Per-version drift references (BUG-2 / FR-2).

"Drift" only means something relative to what the CURRENT production model was trained
on. Every model therefore saves a snapshot of its own training data,
`models/reference_<version>.parquet`, holding the monitored features and the target.
The drift monitor compares live traffic with the snapshot of whichever version is in
production right now, so after a promotion the new regime stops looking like drift.

Snapshots for v1 are made from data/training_data.csv. A version trained before this
feature existed has no snapshot; the monitor then falls back to the baseline's and
records that it did (see drift_monitor.resolve_reference).
"""
from pathlib import Path
from typing import Optional

import pandas as pd

from data.house_price_preprocessor import TARGET_COL

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT / "models"
TRAINING_DATA_PATH = ROOT / "data" / "training_data.csv"
BASELINE_VERSION = "v1"

# Deliberately a curated set of meaningful RAW columns, not the ~250 one-hot dummies the
# model trains on: a KS test on a 0/1 column is close to meaningless. This list includes
# the columns the dataset generator injects drift into (OverallQual, GrLivArea,
# YearRemodAdd) plus related columns that should stay stable, as a control.
MONITORED_COLUMNS = [
    "OverallQual", "GrLivArea", "YearBuilt", "YearRemodAdd",
    "TotalBsmtSF", "GarageCars", "GarageArea", "1stFlrSF",
    "FullBath", "TotRmsAbvGrd",
]


def reference_path(version: str, models_dir: Optional[Path] = None) -> Path:
    return Path(models_dir or MODELS_DIR) / f"reference_{version}.parquet"


def save_reference(df: pd.DataFrame, version: str, models_dir: Optional[Path] = None) -> Path:
    """Write the monitored features (and the target, when present) of a model's training rows."""
    columns = [c for c in MONITORED_COLUMNS + [TARGET_COL] if c in df.columns]
    path = reference_path(version, models_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    df[columns].reset_index(drop=True).to_parquet(path, index=False)
    return path


def load_reference_for(version: str, models_dir: Optional[Path] = None) -> Optional[pd.DataFrame]:
    path = reference_path(version, models_dir)
    return pd.read_parquet(path) if path.exists() else None


def ensure_baseline_reference(models_dir: Optional[Path] = None) -> pd.DataFrame:
    """v1's snapshot, created once from the original training data if it isn't there yet."""
    existing = load_reference_for(BASELINE_VERSION, models_dir)
    if existing is not None:
        return existing
    df = pd.read_csv(TRAINING_DATA_PATH)
    try:
        save_reference(df, BASELINE_VERSION, models_dir)
    except OSError as e:  # read-only volume etc.: still usable, just not cached
        print(f"[reference] could not save the v1 snapshot: {e}")
    columns = [c for c in MONITORED_COLUMNS + [TARGET_COL] if c in df.columns]
    return df[columns]
