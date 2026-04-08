"""Inference-time feature engine that replicates DatasetBuilder feature construction.

Produces the same feature set as DatasetBuilder.build_train_test() with BUILD_KWARGS
from exp_001_baseline_reproduce.py, but operates on a small history window (289 rows)
for a single route instead of the full training DataFrame.

The model was trained with native LightGBM categorical features (category dtype),
NOT one-hot encoding.
"""

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Holiday dates used during training
HOLIDAY_DATES = pd.to_datetime([
    "2025-05-01", "2025-05-02", "2025-05-08", "2025-05-09",
])

STATUS_COLS = [f"status_{i}" for i in range(1, 9)]

# Native LightGBM categorical features — MUST match the training-time
# ``booster.params['categorical_column']`` for the production model. Keep in
# sync with the trainer; ``ModelManager`` cross-checks feature names at load.
CAT_FEATURES = [
    "dow", "pod", "slot", "is_hooliday", "horizon_step",
]

# Identifier/merge-key columns. They are numeric (int64) at the model level,
# but MUST NOT be touched by ``_fill_na``: a zero fallback would silently
# rewrite them into the ``route_id=0`` / ``office_from_id=0`` bucket in
# ``_merge_static_aggs`` and poison predictions without any error surface.
# This is a distinct concern from ``CAT_FEATURES`` (the Booster's native
# categoricals) and is kept separate to avoid semantic overloading of one list.
MERGE_KEY_COLS = ["route_id", "office_from_id"]

# Matches _add_default_ts_features in DatasetBuilder
TARGET = "target_2h"
TARGET_LAGS = list(range(1, 11))
TARGET_DIFF_PERIODS = list(range(1, 11)) + [15, 20, 48, 96]
TARGET_ROLLING_WINDOWS = [3, 6, 12, 24, 48, 96, 144, 288]
TARGET_ROLLING_STATS = ("mean", "std", "max", "min")

INVENTORY_FEATURES = ["total_inventory", "status_early", "status_mid", "status_late"]
INVENTORY_LAGS = list(range(1, 11))
INVENTORY_DIFF_PERIODS = list(range(1, 11)) + [15, 20, 48, 96]
INVENTORY_ROLLING_WINDOWS = [3, 6, 12, 24, 48, 96, 144, 288]
INVENTORY_ROLLING_STATS = ("mean", "std")

DETAILED_STATUS_LAGS = [1, 2, 3, 6, 12, 18, 36, 96]

# Static aggregation config (matches BUILD_KWARGS)
STATIC_AGG_FEATURES = [f"status_{i}" for i in range(1, 9)]
STATIC_GROUP_KEYS_LIST = [
    ["route_id"], ["office_from_id"], ["route_id", "dow"], ["route_id", "pod"],
]

TOTAL_INVENTORY_AGG_FEATURES = [
    "total_inventory", "early_inventory", "mid_inventory", "late_inventory",
    "early_share", "mid_share", "late_share", "status_entropy",
]
TOTAL_INVENTORY_GROUP_KEYS_LIST = [
    ["route_id"], ["office_from_id"],
    ["route_id", "dow"], ["route_id", "pod"], ["route_id", "slot"],
]

TARGET_HIST_GROUP_KEYS_LIST = [
    ["route_id"], ["route_id", "pod"], ["route_id", "dow"],
]


def _get_part_of_day(hour: int) -> str:
    if 0 <= hour < 6:
        return "night"
    if 6 <= hour < 12:
        return "morning"
    if 12 <= hour < 18:
        return "day"
    return "evening"


def _add_time_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add temporal features matching DatasetBuilder._add_time_features."""
    df = df.copy()
    df["dow"] = df["timestamp"].dt.day_name()
    df["pod"] = df["timestamp"].dt.hour.map(_get_part_of_day)
    df["slot"] = df["timestamp"].dt.hour * 2 + df["timestamp"].dt.minute // 30
    df["is_hooliday"] = df["timestamp"].dt.normalize().isin(HOLIDAY_DATES).astype(int)
    return df


def _add_total_status_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add inventory aggregation features matching DatasetBuilder._add_total_status_features."""
    df = df.copy()

    available = [c for c in STATUS_COLS if c in df.columns]
    if not available:
        return df

    df["total_inventory"] = df[available].sum(axis=1)

    for status in available:
        df[f"{status}_share"] = df[status] / (df["total_inventory"] + 1e-8)

    early_cols = [c for c in ["status_1", "status_2", "status_3"] if c in df.columns]
    mid_cols = [c for c in ["status_4", "status_5", "status_6"] if c in df.columns]
    late_cols = [c for c in ["status_7", "status_8"] if c in df.columns]

    df["early_inventory"] = df[early_cols].sum(axis=1) if early_cols else 0.0
    df["mid_inventory"] = df[mid_cols].sum(axis=1) if mid_cols else 0.0
    df["late_inventory"] = df[late_cols].sum(axis=1) if late_cols else 0.0

    df["status_early"] = df["early_inventory"]
    df["status_mid"] = df["mid_inventory"]
    df["status_late"] = df["late_inventory"]

    df["early_share"] = df["early_inventory"] / (df["total_inventory"] + 1e-8)
    df["mid_share"] = df["mid_inventory"] / (df["total_inventory"] + 1e-8)
    df["late_share"] = df["late_inventory"] / (df["total_inventory"] + 1e-8)

    share_cols = [f"{status}_share" for status in available]
    shares = df[share_cols].to_numpy()
    df["status_entropy"] = -np.sum(shares * np.log(shares + 1e-8), axis=1) / np.log(len(available))

    return df


def _add_lag_features(series: pd.Series, lags: list[int], prefix: str) -> dict[str, float]:
    """Compute lag features from a single-route sorted series. Returns values for last row."""
    result: dict[str, float] = {}
    values = series.to_numpy()
    n = len(values)
    for lag in lags:
        col_name = f"{prefix}_lag_{lag}"
        if lag < n:
            result[col_name] = values[n - 1 - lag]
        else:
            result[col_name] = np.nan
    return result


def _add_diff_features(series: pd.Series, periods: list[int], prefix: str) -> dict[str, float]:
    """Compute diff features for the last row of a single-route sorted series."""
    result: dict[str, float] = {}
    values = series.to_numpy()
    n = len(values)
    current = values[n - 1]
    for period in periods:
        col_name = f"{prefix}_diff_{period}"
        if period < n:
            result[col_name] = current - values[n - 1 - period]
        else:
            result[col_name] = np.nan
    return result


def _add_rolling_features(
    series: pd.Series,
    windows: list[int],
    statistics: tuple[str, ...],
    prefix: str,
) -> dict[str, float]:
    """Compute rolling features for the last row.

    Matches TimeSeriesFeatureBuilder.add_rolling_features:
    - The rolling is computed on shift(1) of the series (excludes current value).
    - min_periods=1 for mean/max/min, min_periods=2 for std.
    """
    result: dict[str, float] = {}
    values = series.to_numpy()
    # shift(1): exclude current value, use values[:-1]
    shifted = values[:-1] if len(values) > 1 else np.array([], dtype=float)

    for window in windows:
        # Take the last `window` elements of the shifted series
        window_data = shifted[-window:] if len(shifted) >= 1 else np.array([])

        if "mean" in statistics:
            col = f"{prefix}_roll_{window}_mean"
            if len(window_data) >= 1:
                result[col] = float(np.mean(window_data))
            else:
                result[col] = np.nan

        if "std" in statistics:
            col = f"{prefix}_roll_{window}_std"
            if len(window_data) >= 2:
                result[col] = float(np.std(window_data, ddof=1))
            else:
                result[col] = 0.0

        if "max" in statistics:
            col = f"{prefix}_roll_{window}_max"
            if len(window_data) >= 1:
                result[col] = float(np.max(window_data))
            else:
                result[col] = np.nan

        if "min" in statistics:
            col = f"{prefix}_roll_{window}_min"
            if len(window_data) >= 1:
                result[col] = float(np.min(window_data))
            else:
                result[col] = np.nan

    return result


def _compute_target_hist_features(
    target_series: pd.Series,
    group_values: dict[str, Any],
    group_keys_list: list[list[str]],
) -> dict[str, float]:
    """Compute expanding target statistics (mean, std, zero_rate, count).

    These match _add_target_mean_hist, _add_target_std_hist,
    _add_target_zero_rate_hist, and _add_target_count_hist.

    For a single route, the group_keys involving route_id use the full history.
    group_values provides the current row's categorical values for filtering.
    """
    result: dict[str, float] = {}
    values = target_series.to_numpy()
    # Exclude current value (cumulative stats exclude current row in training code)
    hist_values = values[:-1] if len(values) > 1 else np.array([])
    n = len(hist_values)
    global_mean = float(np.mean(values)) if len(values) > 0 else 0.0

    for group_keys in group_keys_list:
        group_name = "_".join(group_keys)

        # Target mean hist
        mean_name = f"{group_name}_target_mean_hist"
        if n > 0:
            result[mean_name] = float(np.mean(hist_values))
        else:
            result[mean_name] = global_mean

        # Target std hist
        std_name = f"{group_name}_target_std_hist"
        if n >= 2:
            mean_val = np.mean(hist_values)
            mean_sq = np.mean(hist_values ** 2)
            var_val = max(mean_sq - mean_val ** 2, 0.0)
            result[std_name] = float(np.sqrt(var_val))
        else:
            result[std_name] = 0.0

        # Target zero rate hist
        zero_rate_name = f"{group_name}_target_zero_rate_hist"
        if n > 0:
            result[zero_rate_name] = float(np.mean(hist_values == 0))
        else:
            result[zero_rate_name] = 0.0

        # Target count hist
        count_name = f"{group_name}_target_count_hist"
        result[count_name] = float(n)

    return result


class InferenceFeatureEngine:
    """Produces model-ready features for a single route at inference time.

    Replicates the exact feature pipeline from DatasetBuilder.build_train_test()
    with the BUILD_KWARGS used in exp_001_baseline_reproduce.py.

    Usage:
        engine = InferenceFeatureEngine()
        engine.load_static_aggregations("path/to/static_aggs.json")
        features_df = engine.build_features(history_df, route_id, warehouse_id, forecast_steps=10)
    """

    def __init__(self) -> None:
        self._static_aggs: dict[str, pd.DataFrame] = {}
        self._fill_values: dict[str, float] = {}

    def load_static_aggregations(self, path: str) -> None:
        """Load pre-computed static aggregation tables from a JSON file.

        These are computed once from the training set by _agg_stats_by_group_keys
        and stored for inference reuse.
        """
        agg_path = Path(path)
        if not agg_path.exists():
            logger.warning("Static aggregations file not found at %s, skipping", agg_path)
            return

        with open(agg_path, "r") as f:
            raw = json.load(f)

        for key, records in raw.items():
            self._static_aggs[key] = pd.DataFrame(records)

        logger.info("Loaded %d static aggregation tables", len(self._static_aggs))

    def load_fill_values(self, path: str) -> None:
        """Load median fill values computed from training data."""
        fill_path = Path(path)
        if not fill_path.exists():
            logger.warning("Fill values file not found at %s, skipping", fill_path)
            return

        with open(fill_path, "r") as f:
            self._fill_values = json.load(f)

        logger.info("Loaded %d fill values", len(self._fill_values))

    def build_features(
        self,
        history_df: pd.DataFrame,
        route_id: int,
        warehouse_id: int,
        forecast_steps: int = 10,
    ) -> pd.DataFrame:
        """Build feature DataFrame for all forecast horizon steps.

        Args:
            history_df: Historical observations for a single route, sorted by timestamp.
                Must contain columns: timestamp, target_2h, status_1..status_8,
                route_id, office_from_id.
            route_id: The route ID.
            warehouse_id: The office_from_id (warehouse).
            forecast_steps: Number of horizon steps (default 10).

        Returns:
            A DataFrame with `forecast_steps` rows, one per horizon step,
            containing all features expected by the model with correct dtypes.
        """
        df = history_df.sort_values("timestamp").reset_index(drop=True)
        df = _add_time_features(df)
        df = _add_total_status_features(df)

        # Compute time-series features for the anchor (last) row
        anchor_row = df.iloc[-1].to_dict()
        anchor_ts = anchor_row["timestamp"]
        ts_features = self._compute_ts_features(df)

        # Compute target hist features
        target_series = df[TARGET]
        group_values = {
            "route_id": route_id,
            "dow": anchor_row.get("dow"),
            "pod": anchor_row.get("pod"),
        }
        hist_features = _compute_target_hist_features(
            target_series, group_values, TARGET_HIST_GROUP_KEYS_LIST,
        )

        # Build long-format rows for each horizon step
        rows: list[dict[str, Any]] = []
        for step in range(1, forecast_steps + 1):
            future_ts = anchor_ts + pd.Timedelta(minutes=30 * step)
            future_hour = future_ts.hour
            future_minute = future_ts.minute

            row: dict[str, Any] = {}

            # Identifiers
            row["route_id"] = route_id
            row["office_from_id"] = warehouse_id

            # Time features for the FUTURE timestamp (matches _expand_anchors_to_long)
            row["dow"] = future_ts.day_name()
            row["pod"] = _get_part_of_day(future_hour)
            row["slot"] = future_hour * 2 + future_minute // 30
            row["is_hooliday"] = int(future_ts.normalize() in HOLIDAY_DATES)

            # Horizon features
            row["horizon_step"] = step
            row["horizon_minutes"] = step * 30

            # Status features from anchor row
            for sc in STATUS_COLS:
                row[sc] = anchor_row.get(sc, 0.0)

            # Total status features from anchor row
            for feat in [
                "total_inventory", "status_early", "status_mid", "status_late",
                "early_inventory", "mid_inventory", "late_inventory",
                "early_share", "mid_share", "late_share", "status_entropy",
            ] + [f"status_{i}_share" for i in range(1, 9)]:
                row[feat] = anchor_row.get(feat, 0.0)

            # Time-series features (same for all horizon steps -- computed at anchor)
            row.update(ts_features)

            # Target hist features
            row.update(hist_features)

            rows.append(row)

        features_df = pd.DataFrame(rows)

        # Merge static aggregations
        features_df = self._merge_static_aggs(features_df)

        # Fill NaN numeric values
        features_df = self._fill_na(features_df)

        # Set categorical dtypes (native LightGBM categoricals)
        features_df = self._set_cat_dtypes(features_df)

        return features_df

    def _compute_ts_features(self, df: pd.DataFrame) -> dict[str, float]:
        """Compute all time-series features for the last row of the history.

        Replicates _add_default_ts_features from DatasetBuilder.
        """
        features: dict[str, float] = {}

        # Target features
        if TARGET in df.columns:
            target_series = df[TARGET]
            features.update(_add_lag_features(target_series, TARGET_LAGS, TARGET))
            features.update(_add_diff_features(target_series, TARGET_DIFF_PERIODS, TARGET))
            features.update(
                _add_rolling_features(target_series, TARGET_ROLLING_WINDOWS, TARGET_ROLLING_STATS, TARGET)
            )

        # Inventory features
        for inv_feat in INVENTORY_FEATURES:
            if inv_feat in df.columns:
                series = df[inv_feat]
                features.update(_add_lag_features(series, INVENTORY_LAGS, inv_feat))
                features.update(_add_diff_features(series, INVENTORY_DIFF_PERIODS, inv_feat))
                features.update(
                    _add_rolling_features(series, INVENTORY_ROLLING_WINDOWS, INVENTORY_ROLLING_STATS, inv_feat)
                )

        # Detailed status features (lags only)
        for status_col in STATUS_COLS:
            if status_col in df.columns:
                series = df[status_col]
                features.update(_add_lag_features(series, DETAILED_STATUS_LAGS, status_col))

        return features

    def _merge_static_aggs(self, df: pd.DataFrame) -> pd.DataFrame:
        """Merge pre-computed static aggregation tables.

        Handles both STATIC_AGG_FEATURES and TOTAL_INVENTORY_AGG_FEATURES
        aggregation groups.
        """
        df = df.copy()

        for key, agg_df in self._static_aggs.items():
            # The key encodes the group columns, e.g. "route_id" or "route_id_and_dow"
            merge_keys = key.split("_and_")
            available_keys = [k for k in merge_keys if k in df.columns and k in agg_df.columns]
            if not available_keys:
                continue

            # Ensure compatible dtypes for merge
            for mk in available_keys:
                if agg_df[mk].dtype != df[mk].dtype:
                    try:
                        agg_df = agg_df.copy()
                        agg_df[mk] = agg_df[mk].astype(df[mk].dtype)
                    except (ValueError, TypeError):
                        pass

            existing_cols = [c for c in agg_df.columns if c in df.columns and c not in available_keys]
            if existing_cols:
                df = df.drop(columns=existing_cols)

            df = df.merge(agg_df, how="left", on=available_keys)

        return df

    def _fill_na(self, df: pd.DataFrame) -> pd.DataFrame:
        """Fill NaN values in numeric columns using stored median fill values.

        Merge-key columns (``route_id``, ``office_from_id``) are numeric at
        the model level but are IDs, not measurements — filling them with 0
        would silently rewrite them into the zero-bucket in downstream
        static-aggregation merges. They are excluded here.
        """
        df = df.copy()
        excluded = set(CAT_FEATURES) | set(MERGE_KEY_COLS)
        numeric_cols = [c for c in df.columns if c not in excluded]

        if self._fill_values:
            for col in numeric_cols:
                if col in self._fill_values and df[col].isna().any():
                    df[col] = df[col].fillna(self._fill_values[col])

        # Fill remaining NaNs with 0 as a safe fallback
        remaining_na_cols = [c for c in numeric_cols if df[c].isna().any()]
        if remaining_na_cols:
            df[remaining_na_cols] = df[remaining_na_cols].fillna(0.0)

        return df

    def _set_cat_dtypes(self, df: pd.DataFrame) -> pd.DataFrame:
        """Convert categorical columns to pandas category dtype for LightGBM."""
        df = df.copy()
        for col in CAT_FEATURES:
            if col in df.columns:
                df[col] = df[col].astype("category")
        return df
