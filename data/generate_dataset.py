"""
Builds training_data.csv and stream_data.csv from the REAL Kaggle house
prices dataset (house_prices_raw.csv), replacing the earlier synthetic
version. There's no time axis in this dataset (it's not a time series), so
— per the earlier decision to keep drift simulated — this treats the raw
row order as a stand-in timeline and injects a controlled drift event
partway through, the same way the synthetic version did.

Drift injected here simulates a housing-market shift: a renovation/luxury
wave where overall quality and living area both climb — concretely:
  - OverallQual pushed up (capped at 10, the dataset's real max)
  - GrLivArea scaled up
  - YearRemodAdd nudged later (newer-feeling homes)
This is a genuine distribution shift in real features, not synthetic noise
bolted onto a fake dataset.

Usage:
    python data/generate_dataset.py
"""
import numpy as np
import pandas as pd
from pathlib import Path

RNG = np.random.default_rng(42)
RAW_PATH = Path(__file__).resolve().parent / "house_prices_raw.csv"

TRAIN_FRACTION = 0.6  # rest becomes the "stream"
DRIFT_START_FRACTION = 0.4  # within the stream portion


def inject_drift(df: pd.DataFrame, start_idx: int) -> pd.DataFrame:
    df = df.copy().reset_index(drop=True)
    n = len(df) - start_idx

    df.loc[start_idx:, "OverallQual"] = np.clip(
        df.loc[start_idx:, "OverallQual"] + RNG.integers(1, 3, size=n), 1, 10
    )
    df.loc[start_idx:, "GrLivArea"] = (df.loc[start_idx:, "GrLivArea"] * 1.25).astype(int)
    df.loc[start_idx:, "YearRemodAdd"] = np.clip(
        df.loc[start_idx:, "YearRemodAdd"] + RNG.integers(5, 15, size=n), None, 2010
    )
    return df


def main():
    raw = pd.read_csv(RAW_PATH)
    raw = raw.sample(frac=1.0, random_state=42).reset_index(drop=True)  # shuffle once, fixed seed

    n_train = int(len(raw) * TRAIN_FRACTION)
    train_df = raw.iloc[:n_train].reset_index(drop=True)
    stream_df = raw.iloc[n_train:].reset_index(drop=True)

    drift_start = int(len(stream_df) * DRIFT_START_FRACTION)
    stream_df = inject_drift(stream_df, drift_start)
    stream_df["timestep"] = np.arange(len(stream_df))
    stream_df["drifted"] = stream_df["timestep"] >= drift_start

    train_df.to_csv(Path(__file__).resolve().parent / "training_data.csv", index=False)
    stream_df.to_csv(Path(__file__).resolve().parent / "stream_data.csv", index=False)

    print(f"Wrote training_data.csv ({len(train_df)} rows)")
    print(f"Wrote stream_data.csv ({len(stream_df)} rows, drift starts at timestep {drift_start})")


if __name__ == "__main__":
    main()
