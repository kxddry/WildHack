"""Model retraining engine.

Fetches fresh data, builds features matching InferenceFeatureEngine,
trains a LightGBM model with out-of-time validation, evaluates it,
and persists the artifact to disk + model_metadata table.

Note: pickle is used intentionally for model serialization because the
prediction-service loads models via pickle (ModelManager.load). Only
models produced by this service are loaded — no untrusted content is
deserialized from external sources.
"""

import json
import logging
import pickle  # noqa: S403 — intentional, see module docstring
from datetime import datetime
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

from app.config import settings
from app.core.baseline import NaiveSeasonalBaseline
from app.storage import postgres as db

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Feature constants — must match InferenceFeatureEngine exactly
# ---------------------------------------------------------------------------

STATUS_COLS = [f"status_{i}" for i in range(1, 9)]

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

CAT_FEATURES = [
    "office_from_id", "route_id", "dow", "pod",
    "is_hooliday", "slot", "horizon_step",
]

# Holiday dates used during original training
HOLIDAY_DATES = pd.to_datetime([
    "2025-05-01", "2025-05-02", "2025-05-08", "2025-05-09",
])

# Out-of-time validation split: last N time steps held out
OOT_STEPS = 10


# ---------------------------------------------------------------------------
# Time / status feature helpers (DataFrame-level, grouped by route)
# ---------------------------------------------------------------------------


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
    """Add inventory aggregation features per row."""
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


# ---------------------------------------------------------------------------
# Grouped shift helpers for lag / diff / rolling on full DataFrame
# ---------------------------------------------------------------------------


def _add_grouped_lag_features(
    df: pd.DataFrame,
    col: str,
    lags: list[int],
    group_col: str = "route_id",
) -> pd.DataFrame:
    """Add lag features for `col` grouped by `group_col`."""
    grouped = df.groupby(group_col, sort=False)[col]
    for lag in lags:
        df[f"{col}_lag_{lag}"] = grouped.shift(lag)
    return df


def _add_grouped_diff_features(
    df: pd.DataFrame,
    col: str,
    periods: list[int],
    group_col: str = "route_id",
) -> pd.DataFrame:
    """Add diff features for `col` grouped by `group_col`."""
    grouped = df.groupby(group_col, sort=False)[col]
    for period in periods:
        df[f"{col}_diff_{period}"] = grouped.diff(period)
    return df


def _add_grouped_rolling_features(
    df: pd.DataFrame,
    col: str,
    windows: list[int],
    stats: tuple[str, ...],
    group_col: str = "route_id",
) -> pd.DataFrame:
    """Add rolling features for `col` grouped by `group_col`.

    Matches InferenceFeatureEngine: rolling is computed on shift(1),
    i.e. excludes the current value (min_periods=1 for mean/max/min, 2 for std).
    """
    shifted = df.groupby(group_col, sort=False)[col].shift(1)

    for window in windows:
        if "mean" in stats:
            df[f"{col}_roll_{window}_mean"] = (
                shifted.groupby(df[group_col])
                .transform(lambda s, w=window: s.rolling(w, min_periods=1).mean())
            )
        if "std" in stats:
            df[f"{col}_roll_{window}_std"] = (
                shifted.groupby(df[group_col])
                .transform(lambda s, w=window: s.rolling(w, min_periods=2).std().fillna(0.0))
            )
        if "max" in stats:
            df[f"{col}_roll_{window}_max"] = (
                shifted.groupby(df[group_col])
                .transform(lambda s, w=window: s.rolling(w, min_periods=1).max())
            )
        if "min" in stats:
            df[f"{col}_roll_{window}_min"] = (
                shifted.groupby(df[group_col])
                .transform(lambda s, w=window: s.rolling(w, min_periods=1).min())
            )

    return df


# ---------------------------------------------------------------------------
# ModelTrainer
# ---------------------------------------------------------------------------


class ModelTrainer:
    """Full retraining pipeline: data → features → train → evaluate → save."""

    def __init__(self) -> None:
        self._last_metrics: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # 1. Fetch training data
    # ------------------------------------------------------------------

    def fetch_training_data(self, window_days: int) -> pd.DataFrame:
        """Fetch route_status_history for the last `window_days` days.

        Uses a synchronous SQLAlchemy engine so pandas.read_sql works without
        needing a running asyncio event loop (training is CPU-bound).
        """
        df = db.fetch_training_data(settings.sync_database_url, window_days)
        if df.empty:
            raise ValueError(
                f"No training data available for the last {window_days} days"
            )
        if len(df) < settings.min_training_rows:
            raise ValueError(
                f"Insufficient training data: {len(df)} rows "
                f"(minimum {settings.min_training_rows})"
            )
        return df

    # ------------------------------------------------------------------
    # 2. Build features
    # ------------------------------------------------------------------

    def build_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Build feature DataFrame from raw route_status_history data.

        Produces the same feature set as InferenceFeatureEngine but applied
        to the full DataFrame using grouped shift operations for efficiency.
        The DataFrame must be sorted by (route_id, timestamp) ascending.
        """
        df = df.sort_values(["route_id", "timestamp"]).reset_index(drop=True)

        # Time and status aggregation features
        df = _add_time_features(df)
        df = _add_total_status_features(df)

        # Add a dummy horizon_step column (1 for all training rows — the
        # training dataset was built without multi-step expansion; we keep
        # this column to match the feature set expected by the model).
        df["horizon_step"] = 1
        df["horizon_minutes"] = 30

        # Target time-series features
        df = _add_grouped_lag_features(df, TARGET, TARGET_LAGS)
        df = _add_grouped_diff_features(df, TARGET, TARGET_DIFF_PERIODS)
        df = _add_grouped_rolling_features(df, TARGET, TARGET_ROLLING_WINDOWS, TARGET_ROLLING_STATS)

        # Inventory time-series features
        for inv_feat in INVENTORY_FEATURES:
            if inv_feat in df.columns:
                df = _add_grouped_lag_features(df, inv_feat, INVENTORY_LAGS)
                df = _add_grouped_diff_features(df, inv_feat, INVENTORY_DIFF_PERIODS)
                df = _add_grouped_rolling_features(
                    df, inv_feat, INVENTORY_ROLLING_WINDOWS, INVENTORY_ROLLING_STATS
                )

        # Detailed status lags
        for status_col in STATUS_COLS:
            if status_col in df.columns:
                df = _add_grouped_lag_features(df, status_col, DETAILED_STATUS_LAGS)

        # Drop rows where target is NaN (happens at the head of each group)
        df = df.dropna(subset=[TARGET]).reset_index(drop=True)

        # Set categorical dtypes for LightGBM native categoricals
        df = self._set_cat_dtypes(df)

        logger.info(
            "Built feature DataFrame: %d rows x %d columns", len(df), len(df.columns)
        )
        return df

    def _set_cat_dtypes(self, df: pd.DataFrame) -> pd.DataFrame:
        """Convert categorical columns to pandas category dtype."""
        df = df.copy()
        for col in CAT_FEATURES:
            if col in df.columns:
                df[col] = df[col].astype("category")
        return df

    # ------------------------------------------------------------------
    # 3. Train model
    # ------------------------------------------------------------------

    def train_model(
        self, features_df: pd.DataFrame, target: str = TARGET
    ) -> tuple[lgb.Booster, dict[str, Any]]:
        """Train LightGBM with out-of-time validation (last OOT_STEPS time steps).

        Returns the trained Booster and a metrics dict.
        """
        # Identify columns to drop (non-feature columns)
        drop_cols = {
            "timestamp", "route_id", "office_from_id",
            target, "horizon_minutes",
        }
        feature_cols = [c for c in features_df.columns if c not in drop_cols]

        # Out-of-time split: last OOT_STEPS unique timestamps held out
        unique_ts = sorted(features_df["timestamp"].unique())
        if len(unique_ts) <= OOT_STEPS:
            raise ValueError(
                f"Not enough time steps for OOT split: {len(unique_ts)} "
                f"(need > {OOT_STEPS})"
            )
        cutoff_ts = unique_ts[-(OOT_STEPS + 1)]

        train_mask = features_df["timestamp"] <= cutoff_ts
        val_mask = features_df["timestamp"] > cutoff_ts

        X_train = features_df.loc[train_mask, feature_cols]
        y_train = features_df.loc[train_mask, target]
        X_val = features_df.loc[val_mask, feature_cols]
        y_val = features_df.loc[val_mask, target]

        logger.info(
            "Training split: %d train rows, %d val rows, %d features",
            len(X_train), len(X_val), len(feature_cols),
        )

        lgb_train = lgb.Dataset(X_train, label=y_train, free_raw_data=False)
        lgb_val = lgb.Dataset(X_val, label=y_val, reference=lgb_train, free_raw_data=False)

        params: dict[str, Any] = {
            "objective": "regression_l1",
            "metric": "mae",
            "learning_rate": settings.learning_rate,
            "num_leaves": settings.num_leaves,
            "max_depth": settings.max_depth,
            "min_child_samples": settings.min_child_samples,
            "subsample": settings.subsample,
            "colsample_bytree": settings.colsample_bytree,
            "reg_alpha": settings.reg_alpha,
            "reg_lambda": settings.reg_lambda,
            "verbose": -1,
        }

        callbacks = [
            lgb.early_stopping(
                stopping_rounds=settings.early_stopping_rounds,
                verbose=False,
            ),
            lgb.log_evaluation(period=100),
        ]

        model = lgb.train(
            params,
            lgb_train,
            num_boost_round=settings.n_estimators,
            valid_sets=[lgb_val],
            callbacks=callbacks,
        )

        metrics = self.evaluate_model(model, X_val, y_val)
        metrics["feature_count"] = len(feature_cols)
        metrics["feature_names"] = feature_cols
        metrics["best_iteration"] = model.best_iteration
        metrics["train_rows"] = int(len(X_train))
        metrics["val_rows"] = int(len(X_val))

        # Naive seasonal baseline (PRD §9.3) — fitted on the same train split
        # and evaluated on the same OOT validation set so the delta against
        # the LightGBM model is directly comparable.
        try:
            baseline_metrics = self._train_and_evaluate_baseline(
                features_df, train_mask, val_mask, target
            )
            metrics["baseline"] = baseline_metrics
            metrics["baseline_combined_score"] = baseline_metrics["combined_score"]
            metrics["wape_vs_baseline"] = round(
                baseline_metrics["wape"] - metrics["wape"], 6
            )
            metrics["rbias_vs_baseline"] = round(
                baseline_metrics["rbias"] - metrics["rbias"], 6
            )
            logger.info(
                "Baseline (mean by route_id x hour x dow) — WAPE=%.4f, RBias=%.4f, "
                "combined=%.4f, n_groups=%d, coverage=%.2f",
                baseline_metrics["wape"],
                baseline_metrics["rbias"],
                baseline_metrics["combined_score"],
                baseline_metrics["n_groups"],
                baseline_metrics["coverage"],
            )
        except Exception as exc:
            # Persist a sentinel so downstream consumers can distinguish
            # "baseline failed for this run" from "baseline never ran".
            logger.exception("Baseline evaluation failed — model metrics still returned")
            metrics["baseline"] = {"error": str(exc), "status": "failed"}
            metrics["baseline_combined_score"] = None
            metrics["wape_vs_baseline"] = None
            metrics["rbias_vs_baseline"] = None

        self._last_metrics = metrics
        logger.info(
            "Training complete — WAPE=%.4f, RBias=%.4f, combined=%.4f",
            metrics["wape"], metrics["rbias"], metrics["combined_score"],
        )
        return model, metrics

    def _train_and_evaluate_baseline(
        self,
        features_df: pd.DataFrame,
        train_mask: pd.Series,
        val_mask: pd.Series,
        target: str,
    ) -> dict[str, float]:
        """Fit and score the naive seasonal baseline on the same OOT split."""
        required_cols = ["timestamp", "route_id", target]
        missing = [c for c in required_cols if c not in features_df.columns]
        if missing:
            raise ValueError(f"Baseline requires columns {missing}")

        train_slice = features_df.loc[train_mask, required_cols]
        val_slice = features_df.loc[val_mask, required_cols]
        baseline = NaiveSeasonalBaseline().fit(train_slice, target=target)
        return baseline.evaluate(val_slice, target=target).to_dict()

    # ------------------------------------------------------------------
    # 4. Evaluate model
    # ------------------------------------------------------------------

    def evaluate_model(
        self, model: lgb.Booster, X_val: pd.DataFrame, y_val: pd.Series
    ) -> dict[str, float]:
        """Compute WAPE + |Relative Bias| on the validation set."""
        preds = model.predict(X_val, num_iteration=model.best_iteration)
        y_true = y_val.to_numpy()

        total_actual = np.sum(np.abs(y_true))
        if total_actual == 0:
            wape = 0.0
        else:
            wape = float(np.sum(np.abs(y_true - preds)) / total_actual)

        mean_actual = np.mean(y_true)
        if mean_actual == 0:
            rbias = 0.0
        else:
            rbias = float(abs((np.mean(preds) - mean_actual) / mean_actual))

        combined = wape + rbias

        return {
            "wape": round(wape, 6),
            "rbias": round(rbias, 6),
            "combined_score": round(combined, 6),
            "mae": round(float(np.mean(np.abs(y_true - preds))), 6),
        }

    # ------------------------------------------------------------------
    # 5. Save model
    # ------------------------------------------------------------------

    def save_model(
        self,
        model: lgb.Booster,
        version: str,
        metrics: dict[str, Any],
    ) -> str:
        """Save model artifact and metadata JSON to disk.

        Serializes using pickle to match the prediction-service load pattern
        (ModelManager.load uses pickle.load on the same file format).
        Returns the absolute path to the saved model file.
        """
        output_dir = Path(settings.model_output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        model_path = output_dir / f"{version}.pkl"
        metadata_path = output_dir / f"{version}_metadata.json"

        with open(model_path, "wb") as fh:
            pickle.dump(model, fh, protocol=pickle.HIGHEST_PROTOCOL)

        metadata: dict[str, Any] = {
            "model_version": version,
            "model_path": str(model_path),
            "training_date": datetime.utcnow().isoformat(),
            "cv_score": metrics.get("combined_score"),
            "wape": metrics.get("wape"),
            "rbias": metrics.get("rbias"),
            "mae": metrics.get("mae"),
            "feature_count": metrics.get("feature_count"),
            "best_iteration": metrics.get("best_iteration"),
            "train_rows": metrics.get("train_rows"),
            "val_rows": metrics.get("val_rows"),
            "baseline": metrics.get("baseline"),
            "baseline_combined_score": metrics.get("baseline_combined_score"),
            "wape_vs_baseline": metrics.get("wape_vs_baseline"),
            "rbias_vs_baseline": metrics.get("rbias_vs_baseline"),
        }
        with open(metadata_path, "w") as fh:
            json.dump(metadata, fh, indent=2)

        logger.info("Saved model to %s", model_path)
        return str(model_path)

    # ------------------------------------------------------------------
    # 6. Champion / challenger comparison
    # ------------------------------------------------------------------

    def compare_champion_challenger(
        self,
        champion_score: float,
        challenger_score: float,
    ) -> bool:
        """Return True if challenger is better (lower combined score) than champion."""
        return challenger_score < champion_score

    # ------------------------------------------------------------------
    # 7. Save static aggregations and fill values
    # ------------------------------------------------------------------

    def save_static_aggs(
        self,
        raw_df: pd.DataFrame,
        features_df: pd.DataFrame,
        output_dir: str,
    ) -> None:
        """Compute and save static aggregations and fill values from training data.

        These artifacts are used by InferenceFeatureEngine to stay in sync with
        the latest training distribution, preventing training-serving feature skew.

        raw_df: raw route_status_history data (used for group agg computation)
        features_df: full feature matrix after build_features (used for fill values)
        output_dir: directory to write static_aggs.json and fill_values.json
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        # Build intermediate df with time + inventory features (matches InferenceFeatureEngine)
        prep_df = _add_time_features(raw_df.copy())
        prep_df = _add_total_status_features(prep_df)

        # Static agg feature sets (must match InferenceFeatureEngine constants)
        static_agg_features = [f"status_{i}" for i in range(1, 9)]
        static_group_keys_list = [
            ["route_id"], ["office_from_id"], ["route_id", "dow"], ["route_id", "pod"],
        ]
        total_inventory_agg_features = [
            "total_inventory", "early_inventory", "mid_inventory", "late_inventory",
            "early_share", "mid_share", "late_share", "status_entropy",
        ]
        total_inventory_group_keys_list = [
            ["route_id"], ["office_from_id"],
            ["route_id", "dow"], ["route_id", "pod"], ["route_id", "slot"],
        ]

        # Build per-key mapping: key_name → (group_keys, combined feature list)
        key_config: dict[str, tuple[list[str], list[str]]] = {}
        for group_keys in static_group_keys_list:
            key_name = "_and_".join(group_keys)
            feats = key_config.get(key_name, (group_keys, []))[1]
            new_feats = [f for f in static_agg_features if f in prep_df.columns and f not in feats]
            key_config[key_name] = (group_keys, feats + new_feats)
        for group_keys in total_inventory_group_keys_list:
            key_name = "_and_".join(group_keys)
            feats = key_config.get(key_name, (group_keys, []))[1]
            new_feats = [f for f in total_inventory_agg_features if f in prep_df.columns and f not in feats]
            key_config[key_name] = (group_keys, feats + new_feats)

        # Compute aggregations per key group
        static_aggs: dict[str, list] = {}
        for key_name, (group_keys, feature_cols) in key_config.items():
            # All group keys must exist — partial groupby would produce wrong semantics
            # since InferenceFeatureEngine parses the key name to determine merge columns.
            if not all(k in prep_df.columns for k in group_keys) or not feature_cols:
                logger.debug("Skipping static agg %s — missing group key columns", key_name)
                continue
            available_keys = group_keys
            agg_df = prep_df.groupby(available_keys)[feature_cols].agg(["mean", "std"])
            agg_df.columns = [f"{col}_{stat}" for col, stat in agg_df.columns]
            agg_df = agg_df.reset_index().fillna(0.0)
            static_aggs[key_name] = agg_df.to_dict(orient="records")

        static_aggs_path = output_path / "static_aggs.json"
        with open(static_aggs_path, "w") as fh:
            json.dump(static_aggs, fh)
        logger.info(
            "Saved %d static aggregation tables to %s", len(static_aggs), static_aggs_path
        )

        # Compute fill values: median of numeric features from full feature matrix
        drop_cols = {"timestamp", "route_id", "office_from_id", TARGET, "horizon_minutes"}
        numeric_cols = [
            c for c in features_df.select_dtypes(include="number").columns
            if c not in drop_cols
        ]
        fill_values: dict[str, float] = {}
        for col in numeric_cols:
            median_val = features_df[col].median()
            if not pd.isna(median_val):
                fill_values[col] = float(median_val)

        fill_values_path = output_path / "fill_values.json"
        with open(fill_values_path, "w") as fh:
            json.dump(fill_values, fh)
        logger.info(
            "Saved fill values (%d features) to %s", len(fill_values), fill_values_path
        )
