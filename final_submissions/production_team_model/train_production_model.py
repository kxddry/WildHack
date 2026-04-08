# -*- coding: utf-8 -*-
"""Train production-style model for team prediction-service.

Uses the EXACT same training pipeline as services/retraining-service/app/core/trainer.py
but reads training data from a local parquet file instead of postgres
(route_status_history schema is identical to train_team_track.parquet).

Outputs (drop-in for services/prediction-service):
  - model.pkl              — pickle.dump of lgb.Booster
  - {version}_metadata.json
  - static_aggs.json       — for InferenceFeatureEngine
  - fill_values.json       — median fill values

These can be copied to a prediction-service container without retraining:
  docker compose cp model.pkl prediction-service:/app/models/model.pkl
  docker compose cp static_aggs.json prediction-service:/app/models/static_aggs.json
  docker compose cp fill_values.json prediction-service:/app/models/fill_values.json
"""

import json
import logging
import pickle
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

THIS_FILE = Path(__file__).resolve()
WB_ROOT = THIS_FILE.parents[2]  # WildHack/

# ---------------------------------------------------------------------------
# Constants and helpers — copied verbatim from
# services/retraining-service/app/core/trainer.py to avoid the dependency
# on app.config (pydantic) which may not be installed locally.
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

HOLIDAY_DATES = pd.to_datetime([
    "2025-05-01", "2025-05-02", "2025-05-08", "2025-05-09",
])

OOT_STEPS = 10


def _get_part_of_day(hour: int) -> str:
    if 0 <= hour < 6:
        return "night"
    if 6 <= hour < 12:
        return "morning"
    if 12 <= hour < 18:
        return "day"
    return "evening"


def _add_time_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["dow"] = df["timestamp"].dt.day_name()
    df["pod"] = df["timestamp"].dt.hour.map(_get_part_of_day)
    df["slot"] = df["timestamp"].dt.hour * 2 + df["timestamp"].dt.minute // 30
    df["is_hooliday"] = df["timestamp"].dt.normalize().isin(HOLIDAY_DATES).astype(int)
    return df


def _add_total_status_features(df: pd.DataFrame) -> pd.DataFrame:
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
    df["status_entropy"] = (
        -np.sum(shares * np.log(shares + 1e-8), axis=1) / np.log(len(available))
    )
    return df


def _add_grouped_lag_features(df, col, lags, group_col="route_id"):
    grouped = df.groupby(group_col, sort=False)[col]
    for lag in lags:
        df[f"{col}_lag_{lag}"] = grouped.shift(lag)
    return df


def _add_grouped_diff_features(df, col, periods, group_col="route_id"):
    grouped = df.groupby(group_col, sort=False)[col]
    for period in periods:
        df[f"{col}_diff_{period}"] = grouped.diff(period)
    return df


def _add_grouped_rolling_features(df, col, windows, stats, group_col="route_id"):
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
# Configuration (mirrors services/retraining-service/app/config.py defaults)
# ---------------------------------------------------------------------------

TRAINING_PARQUET = WB_ROOT / "Data" / "raw" / "train_team_track.parquet"
OUTPUT_DIR = THIS_FILE.parent / "models"

TRAINING_WINDOW_DAYS = 30
MIN_TRAINING_ROWS = 1000

LGB_PARAMS: dict[str, Any] = {
    "objective": "regression_l1",
    "metric": "mae",
    "learning_rate": 0.025,
    "num_leaves": 63,
    "max_depth": 9,
    "min_child_samples": 80,
    "subsample": 0.8,
    "colsample_bytree": 0.75,
    "reg_alpha": 0.5,
    "reg_lambda": 8.0,
    "verbose": -1,
}
N_ESTIMATORS = 5000
EARLY_STOPPING_ROUNDS = 100


logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def fetch_training_data(parquet_path: Path, window_days: int) -> pd.DataFrame:
    """Load parquet and slice to last `window_days` days."""
    logger.info("Loading parquet: %s", parquet_path)
    df = pd.read_parquet(parquet_path)
    logger.info("  raw shape: %s", df.shape)

    for c in df.select_dtypes("int64").columns:
        df[c] = df[c].astype(np.int32)
    for c in df.select_dtypes("float64").columns:
        df[c] = df[c].astype(np.float32)

    max_ts = df["timestamp"].max()
    cutoff = max_ts - pd.Timedelta(days=window_days)
    df = df[df["timestamp"] >= cutoff].copy().reset_index(drop=True)
    logger.info("  after %d-day window: %d rows", window_days, len(df))

    if len(df) < MIN_TRAINING_ROWS:
        raise ValueError(
            f"Insufficient training data: {len(df)} rows (minimum {MIN_TRAINING_ROWS})"
        )
    return df


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Same logic as ModelTrainer.build_features."""
    df = df.sort_values(["route_id", "timestamp"]).reset_index(drop=True)

    df = _add_time_features(df)
    df = _add_total_status_features(df)
    df["horizon_step"] = 1
    df["horizon_minutes"] = 30

    df = _add_grouped_lag_features(df, TARGET, TARGET_LAGS)
    df = _add_grouped_diff_features(df, TARGET, TARGET_DIFF_PERIODS)
    df = _add_grouped_rolling_features(
        df, TARGET, TARGET_ROLLING_WINDOWS, TARGET_ROLLING_STATS
    )

    for inv_feat in INVENTORY_FEATURES:
        if inv_feat in df.columns:
            df = _add_grouped_lag_features(df, inv_feat, INVENTORY_LAGS)
            df = _add_grouped_diff_features(df, inv_feat, INVENTORY_DIFF_PERIODS)
            df = _add_grouped_rolling_features(
                df, inv_feat, INVENTORY_ROLLING_WINDOWS, INVENTORY_ROLLING_STATS
            )

    for status_col in STATUS_COLS:
        if status_col in df.columns:
            df = _add_grouped_lag_features(df, status_col, DETAILED_STATUS_LAGS)

    df = df.dropna(subset=[TARGET]).reset_index(drop=True)

    for col in CAT_FEATURES:
        if col in df.columns:
            df[col] = df[col].astype("category")

    logger.info("Built features: %d rows × %d cols", len(df), len(df.columns))
    return df


def train_model(features_df: pd.DataFrame) -> tuple[lgb.Booster, dict[str, Any]]:
    """Out-of-time split + LightGBM with same params as ModelTrainer.train_model."""
    drop_cols = {"timestamp", "route_id", "office_from_id", TARGET, "horizon_minutes"}
    feature_cols = [c for c in features_df.columns if c not in drop_cols]

    unique_ts = sorted(features_df["timestamp"].unique())
    if len(unique_ts) <= OOT_STEPS:
        raise ValueError(
            f"Not enough time steps for OOT split: {len(unique_ts)} (need > {OOT_STEPS})"
        )
    cutoff_ts = unique_ts[-(OOT_STEPS + 1)]
    train_mask = features_df["timestamp"] <= cutoff_ts
    val_mask = features_df["timestamp"] > cutoff_ts

    X_tr = features_df.loc[train_mask, feature_cols]
    y_tr = features_df.loc[train_mask, TARGET]
    X_va = features_df.loc[val_mask, feature_cols]
    y_va = features_df.loc[val_mask, TARGET]

    logger.info(
        "Train split: %d train rows / %d val rows / %d features",
        len(X_tr), len(X_va), len(feature_cols),
    )

    lgb_train = lgb.Dataset(X_tr, label=y_tr, free_raw_data=False)
    lgb_val = lgb.Dataset(X_va, label=y_va, reference=lgb_train, free_raw_data=False)

    callbacks = [
        lgb.early_stopping(stopping_rounds=EARLY_STOPPING_ROUNDS, verbose=False),
        lgb.log_evaluation(period=100),
    ]
    model = lgb.train(
        LGB_PARAMS,
        lgb_train,
        num_boost_round=N_ESTIMATORS,
        valid_sets=[lgb_val],
        callbacks=callbacks,
    )

    preds = model.predict(X_va, num_iteration=model.best_iteration)
    y_true = y_va.to_numpy()

    total_actual = float(np.sum(np.abs(y_true)))
    wape = float(np.sum(np.abs(y_true - preds)) / total_actual) if total_actual else 0.0
    mean_actual = float(np.mean(y_true))
    rbias = float(abs((np.mean(preds) - mean_actual) / mean_actual)) if mean_actual else 0.0
    combined = wape + rbias

    metrics = {
        "wape": round(wape, 6),
        "rbias": round(rbias, 6),
        "combined_score": round(combined, 6),
        "mae": round(float(np.mean(np.abs(y_true - preds))), 6),
        "feature_count": len(feature_cols),
        "feature_names": feature_cols,
        "best_iteration": int(model.best_iteration),
        "train_rows": int(len(X_tr)),
        "val_rows": int(len(X_va)),
    }
    logger.info(
        "Train DONE  WAPE=%.4f  RBias=%.4f  combined=%.4f  best_iter=%d",
        wape, rbias, combined, model.best_iteration,
    )
    return model, metrics


def save_model(model: lgb.Booster, version: str, metrics: dict[str, Any]) -> Path:
    """Pickle the booster + write metadata JSON."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    model_path = OUTPUT_DIR / f"{version}.pkl"
    canonical_path = OUTPUT_DIR / "model.pkl"
    metadata_path = OUTPUT_DIR / f"{version}_metadata.json"

    with open(model_path, "wb") as fh:
        pickle.dump(model, fh, protocol=pickle.HIGHEST_PROTOCOL)
    # Also write canonical filename used by prediction-service ModelManager
    with open(canonical_path, "wb") as fh:
        pickle.dump(model, fh, protocol=pickle.HIGHEST_PROTOCOL)

    metadata = {
        "model_version": version,
        "model_path": str(model_path),
        "training_date": datetime.utcnow().isoformat(),
        "cv_score": metrics["combined_score"],
        "wape": metrics["wape"],
        "rbias": metrics["rbias"],
        "mae": metrics["mae"],
        "feature_count": metrics["feature_count"],
        "best_iteration": metrics["best_iteration"],
        "train_rows": metrics["train_rows"],
        "val_rows": metrics["val_rows"],
        "training_window_days": TRAINING_WINDOW_DAYS,
        "lgb_params": LGB_PARAMS,
    }
    with open(metadata_path, "w") as fh:
        json.dump(metadata, fh, indent=2)
    logger.info("Saved %s and %s", model_path, canonical_path)
    return model_path


def save_static_aggs(raw_df: pd.DataFrame, features_df: pd.DataFrame) -> None:
    """Compute and save static_aggs.json + fill_values.json (same as trainer)."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    prep_df = _add_time_features(raw_df.copy())
    prep_df = _add_total_status_features(prep_df)

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

    static_aggs: dict[str, list] = {}
    for key_name, (group_keys, feature_cols) in key_config.items():
        if not all(k in prep_df.columns for k in group_keys) or not feature_cols:
            continue
        agg_df = prep_df.groupby(group_keys)[feature_cols].agg(["mean", "std"])
        agg_df.columns = [f"{col}_{stat}" for col, stat in agg_df.columns]
        agg_df = agg_df.reset_index().fillna(0.0)
        static_aggs[key_name] = agg_df.to_dict(orient="records")

    static_path = OUTPUT_DIR / "static_aggs.json"
    with open(static_path, "w") as fh:
        json.dump(static_aggs, fh)
    logger.info("Saved %d static agg tables to %s", len(static_aggs), static_path)

    drop_cols = {"timestamp", "route_id", "office_from_id", TARGET, "horizon_minutes"}
    numeric_cols = [
        c for c in features_df.select_dtypes(include="number").columns if c not in drop_cols
    ]
    fill_values: dict[str, float] = {}
    for col in numeric_cols:
        median_val = features_df[col].median()
        if not pd.isna(median_val):
            fill_values[col] = float(median_val)

    fill_path = OUTPUT_DIR / "fill_values.json"
    with open(fill_path, "w") as fh:
        json.dump(fill_values, fh)
    logger.info("Saved %d fill values to %s", len(fill_values), fill_path)


def main() -> None:
    version = f"v{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    logger.info("=== Production team model training (%s) ===", version)

    raw_df = fetch_training_data(TRAINING_PARQUET, TRAINING_WINDOW_DAYS)
    features_df = build_features(raw_df)
    model, metrics = train_model(features_df)
    save_model(model, version, metrics)
    save_static_aggs(raw_df, features_df)

    logger.info("=== DONE ===")
    logger.info("Model: %s", OUTPUT_DIR / "model.pkl")
    logger.info("Static aggs: %s", OUTPUT_DIR / "static_aggs.json")
    logger.info("Fill values: %s", OUTPUT_DIR / "fill_values.json")
    logger.info("Metrics: WAPE=%.4f RBias=%.4f combined=%.4f",
                metrics["wape"], metrics["rbias"], metrics["combined_score"])


if __name__ == "__main__":
    main()
