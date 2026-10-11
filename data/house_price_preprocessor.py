"""
Reproduces the preprocessing pipeline from the user's housing.ipynb notebook
as a proper fit/transform object, instead of the notebook's approach of
repeating the same fillna/encoding logic three times (train/val/test) with
manual column reindexing.

Why this needed to exist: the serving API and the retrain pipeline both
need to turn a raw feature dict into the exact same encoded shape the model
expects — same one-hot columns, in the same order, with the same fitted
medians/modes. A HousePricePreprocessor is fit ONCE on training data and
saved (models/preprocessor.joblib) alongside the model. Loading it back
guarantees serving and retraining apply identical transforms.
"""
import joblib
import numpy as np
import pandas as pd

# Columns where a missing value means "the house doesn't have this feature"
# (per data_description.txt), not "unknown" — filled with the literal
# string "None", exactly as in the notebook.
NONE_COLS = [
    "PoolQC", "MiscFeature", "Alley", "Fence", "MasVnrType", "FireplaceQu",
    "GarageType", "GarageFinish", "GarageQual", "GarageCond",
    "BsmtQual", "BsmtCond", "BsmtExposure", "BsmtFinType1", "BsmtFinType2",
]

# Ordinal quality columns, mapped None/Po/Fa/TA/Gd/Ex -> 0-5, exactly as
# in the notebook's quality_map.
ORDINAL_COLS = [
    "ExterQual", "ExterCond", "BsmtQual", "BsmtCond", "HeatingQC",
    "KitchenQual", "FireplaceQu", "GarageQual", "GarageCond", "PoolQC",
]
QUALITY_MAP = {"None": 0, "Po": 1, "Fa": 2, "TA": 3, "Gd": 4, "Ex": 5}

# Extra categorical/numeric columns that can be missing in test-time data
# even when they weren't missing in training (per the notebook's separate
# handling for `test`).
EXTRA_CATEGORICAL_FILL = [
    "MSZoning", "Utilities", "Functional", "Exterior2nd", "Exterior1st",
    "KitchenQual", "SaleType",
]
EXTRA_NUMERIC_FILL = [
    "BsmtFullBath", "BsmtHalfBath", "BsmtUnfSF", "BsmtFinSF2", "BsmtFinSF1",
    "TotalBsmtSF", "GarageCars", "GarageArea",
]

TARGET_COL = "SalePrice"
ID_COL = "Id"


class HousePricePreprocessor:
    def __init__(self):
        self.masvnr_median_ = None
        self.electrical_mode_ = None
        self.neighborhood_lotfrontage_medians_ = None
        self.extra_categorical_modes_ = {}
        self.extra_numeric_medians_ = {}
        self.final_columns_ = None  # the exact one-hot column set/order after fitting

    def fit(self, df: pd.DataFrame):
        df = df.copy()
        if TARGET_COL in df.columns:
            df = df.drop(columns=[TARGET_COL])
        if ID_COL in df.columns:
            df = df.drop(columns=[ID_COL])

        self.masvnr_median_ = df["MasVnrArea"].median()
        self.electrical_mode_ = df["Electrical"].mode()[0]
        self.neighborhood_lotfrontage_medians_ = (
            df.groupby("Neighborhood")["LotFrontage"].median()
        )
        for col in EXTRA_CATEGORICAL_FILL:
            self.extra_categorical_modes_[col] = df[col].mode()[0]
        for col in EXTRA_NUMERIC_FILL:
            self.extra_numeric_medians_[col] = df[col].median()

        transformed = self._transform_core(df)
        self.final_columns_ = transformed.columns.tolist()
        return self

    def _transform_core(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()

        for col in NONE_COLS:
            df[col] = df[col].fillna("None")

        df["MasVnrArea"] = df["MasVnrArea"].fillna(self.masvnr_median_)
        df["Electrical"] = df["Electrical"].fillna(self.electrical_mode_)
        df["GarageYrBlt"] = df["GarageYrBlt"].fillna(0)

        df["LotFrontage"] = df.apply(
            lambda row: self.neighborhood_lotfrontage_medians_.get(
                row["Neighborhood"], self.neighborhood_lotfrontage_medians_.median()
            )
            if pd.isna(row["LotFrontage"])
            else row["LotFrontage"],
            axis=1,
        )

        for col in EXTRA_CATEGORICAL_FILL:
            if col in df.columns:
                df[col] = df[col].fillna(self.extra_categorical_modes_[col])
        for col in EXTRA_NUMERIC_FILL:
            if col in df.columns:
                df[col] = df[col].fillna(self.extra_numeric_medians_[col])

        for col in ORDINAL_COLS:
            df[col] = df[col].map(QUALITY_MAP)

        categorical_cols = df.select_dtypes(include="object").columns
        df = pd.get_dummies(df, columns=categorical_cols)

        return df

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        if self.final_columns_ is None:
            raise RuntimeError("Preprocessor must be fit before transform.")
        df = df.copy()
        if TARGET_COL in df.columns:
            df = df.drop(columns=[TARGET_COL])
        if ID_COL in df.columns:
            df = df.drop(columns=[ID_COL])

        transformed = self._transform_core(df)
        transformed = transformed.reindex(columns=self.final_columns_, fill_value=0)
        transformed = transformed.astype(int)
        return transformed

    def transform_single(self, feature_dict: dict) -> pd.DataFrame:
        """Convenience for the serving API: one raw feature dict -> one encoded row."""
        return self.transform(pd.DataFrame([feature_dict]))

    def save(self, path):
        joblib.dump(self, path)

    @staticmethod
    def load(path):
        return joblib.load(path)
