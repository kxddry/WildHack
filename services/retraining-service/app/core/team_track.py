"""Local team-track evaluation helpers for retraining-service.

This module intentionally does NOT call prediction-service. Team-test preview
and CSV generation must be isolated from the live primary/shadow runtime, so we
load the requested model artifact locally and reproduce the current inference
feature contract inside retraining-service.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import pickle  # noqa: S403 - trusted local artifacts produced by trainer
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi import HTTPException, UploadFile

from app.config import settings
from app.core.trainer import (
    DETAILED_STATUS_LAGS,
    HOLIDAY_DATES,
    INVENTORY_DIFF_PERIODS,
    INVENTORY_FEATURES,
    INVENTORY_LAGS,
    INVENTORY_ROLLING_STATS,
    INVENTORY_ROLLING_WINDOWS,
    STATUS_COLS,
    TARGET,
    TARGET_DIFF_PERIODS,
    TARGET_LAGS,
    TARGET_ROLLING_STATS,
    TARGET_ROLLING_WINDOWS,
    _add_time_features,
    _add_total_status_features,
)
from app.storage import postgres as db

logger = logging.getLogger(__name__)

TEAM_TRACK_REQUIRED_COLUMNS: tuple[str, ...] = ("id", "route_id", "timestamp")
HISTORY_SNAPSHOT_COLUMNS: tuple[str, ...] = (
    "office_from_id",
    "route_id",
    "timestamp",
    "status_1",
    "status_2",
    "status_3",
    "status_4",
    "status_5",
    "status_6",
    "status_7",
    "status_8",
    "target_2h",
)

WRONG_FLOW_HISTORY_MESSAGE = (
    "This looks like a history snapshot. Upload it in the History Ingest tab."
)
WRONG_FLOW_TEAM_TRACK_MESSAGE = (
    "This looks like the Team Track test template. Upload it in the Team Track Test tab."
)

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
MAX_CELLS = 50 * 1_000_000
MAX_CSV_ROWS = MAX_CELLS // 12
ALLOWED_EXTENSIONS: tuple[str, ...] = (".parquet", ".pq", ".csv", ".tsv", ".txt")
PREVIEW_LIMIT = 100
MERGE_KEY_COLS = ["route_id", "office_from_id"]
INFERENCE_CAT_FEATURES = ["dow", "pod", "slot", "is_hooliday", "horizon_step"]

STATIC_GROUP_KEYS_LIST = [
    ["route_id"],
    ["office_from_id"],
    ["route_id", "dow"],
    ["route_id", "pod"],
]
TOTAL_INVENTORY_GROUP_KEYS_LIST = [
    ["route_id"],
    ["office_from_id"],
    ["route_id", "dow"],
    ["route_id", "pod"],
    ["route_id", "slot"],
]
TARGET_HIST_GROUP_KEYS_LIST = [
    ["route_id"],
    ["route_id", "pod"],
    ["route_id", "dow"],
]


def _pick_extension(filename: str | None) -> str:
    name = (filename or "").lower()
    for ext in ALLOWED_EXTENSIONS:
        if name.endswith(ext):
            return ext
    raise HTTPException(
        status_code=415,
        detail=f"Unsupported file extension. Accepted: {', '.join(ALLOWED_EXTENSIONS)}",
    )


def _check_parquet_budget(path: str) -> None:
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail="Parquet support unavailable — server missing pyarrow",
        ) from exc

    try:
        meta = pq.ParquetFile(path).metadata
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parquet file: {exc}") from exc

    cells = int(meta.num_rows) * int(meta.num_columns)
    if cells > MAX_CELLS:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Parquet file too large after decompression: "
                f"{meta.num_rows} rows × {meta.num_columns} cols "
                f"(max {MAX_CELLS} cells allowed)"
            ),
        )


def _check_csv_budget(path: str) -> None:
    rows = 0
    with open(path, "rb") as fh:
        for _ in fh:
            rows += 1
            if rows > MAX_CSV_ROWS:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"CSV file too large: more than {MAX_CSV_ROWS} rows "
                        "(row budget enforced before parsing)"
                    ),
                )


def _read_dataframe(path: str, ext: str) -> pd.DataFrame:
    if ext in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    sep = "\t" if ext == ".tsv" else ","
    return pd.read_csv(path, sep=sep)


def _looks_like_history_snapshot(columns: list[str]) -> bool:
    return all(col in columns for col in HISTORY_SNAPSHOT_COLUMNS)


def _looks_like_team_track(columns: list[str]) -> bool:
    return all(col in columns for col in TEAM_TRACK_REQUIRED_COLUMNS) and not any(
        col in columns for col in STATUS_COLS + [TARGET, "office_from_id"]
    )


def _coerce_template_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        raise HTTPException(status_code=422, detail="Uploaded file is empty")

    columns = list(df.columns)
    if _looks_like_history_snapshot(columns):
        raise HTTPException(status_code=422, detail=WRONG_FLOW_HISTORY_MESSAGE)

    missing = [col for col in TEAM_TRACK_REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Missing required columns: {missing}. "
                f"Expected schema: {list(TEAM_TRACK_REQUIRED_COLUMNS)}"
            ),
        )

    try:
        out = df[list(TEAM_TRACK_REQUIRED_COLUMNS)].copy()
        out["id"] = pd.to_numeric(out["id"], errors="raise").astype("int64")
        out["route_id"] = pd.to_numeric(out["route_id"], errors="raise").astype("int64")
        timestamps = pd.to_datetime(out["timestamp"], errors="raise")
        if getattr(timestamps.dt, "tz", None) is not None:
            timestamps = timestamps.dt.tz_localize(None)
        out["timestamp"] = timestamps
    except (TypeError, ValueError, KeyError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Failed to coerce Team Track columns: {exc}",
        ) from exc

    if out["id"].isna().any():
        raise HTTPException(status_code=422, detail="Column 'id' must not contain nulls")
    if out["id"].duplicated().any():
        dupes = out.loc[out["id"].duplicated(), "id"].astype(str).tolist()[:10]
        raise HTTPException(
            status_code=422,
            detail=f"Duplicate ids are not allowed in Team Track input: {dupes}",
        )

    return out.sort_values(["route_id", "timestamp", "id"]).reset_index(drop=True)


async def read_template_upload(upload: UploadFile) -> pd.DataFrame:
    """Stream an uploaded test template to disk, then parse and coerce it."""
    ext = _pick_extension(upload.filename)
    tmp_path: str | None = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            tmp_path = tmp.name
            written = 0
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB "
                            "stream limit"
                        ),
                    )
                tmp.write(chunk)

        if tmp_path is None or written == 0:
            raise HTTPException(status_code=422, detail="Uploaded file is empty")

        if ext in {".parquet", ".pq"}:
            _check_parquet_budget(tmp_path)
        else:
            _check_csv_budget(tmp_path)

        raw_df = _read_dataframe(tmp_path, ext)
        return _coerce_template_df(raw_df)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                logger.warning("Failed to clean up tmp Team Track upload at %s", tmp_path)


def _introspect_sklearn(model: Any) -> dict[str, Any]:
    booster = model.booster_
    params = booster.params if hasattr(booster, "params") else {}
    return {
        "n_features": int(model.n_features_),
        "feature_names": list(model.feature_name_),
        "n_estimators": int(model.n_estimators_),
        "params": params or {},
    }


def _introspect_booster(model: Any) -> dict[str, Any]:
    params = getattr(model, "params", {}) or {}
    try:
        n_estimators = int(model.current_iteration())
    except (AttributeError, TypeError, ValueError):
        n_estimators = int(model.num_trees())
    return {
        "n_features": int(model.num_feature()),
        "feature_names": list(model.feature_name()),
        "n_estimators": n_estimators,
        "params": params,
    }


def _introspect_lgb(model: Any) -> dict[str, Any]:
    try:
        import lightgbm as lgb

        if isinstance(model, lgb.LGBMModel):
            return _introspect_sklearn(model)
        if isinstance(model, lgb.Booster):
            return _introspect_booster(model)
    except ImportError:
        pass

    if hasattr(model, "n_features_") and hasattr(model, "booster_"):
        return _introspect_sklearn(model)

    if (
        hasattr(model, "num_feature")
        and callable(getattr(model, "num_feature"))
        and hasattr(model, "feature_name")
        and callable(getattr(model, "feature_name"))
    ):
        return _introspect_booster(model)

    raise TypeError(
        f"Unsupported model type: {type(model).__name__}. "
        "Expected lightgbm.LGBMModel or lightgbm.Booster."
    )


def _add_lag_features(series: pd.Series, lags: list[int], prefix: str) -> dict[str, float]:
    result: dict[str, float] = {}
    values = series.to_numpy()
    n = len(values)
    for lag in lags:
        col_name = f"{prefix}_lag_{lag}"
        result[col_name] = values[n - 1 - lag] if lag < n else np.nan
    return result


def _add_diff_features(series: pd.Series, periods: list[int], prefix: str) -> dict[str, float]:
    result: dict[str, float] = {}
    values = series.to_numpy()
    n = len(values)
    current = values[n - 1]
    for period in periods:
        col_name = f"{prefix}_diff_{period}"
        result[col_name] = current - values[n - 1 - period] if period < n else np.nan
    return result


def _add_rolling_features(
    series: pd.Series,
    windows: list[int],
    statistics: tuple[str, ...],
    prefix: str,
) -> dict[str, float]:
    result: dict[str, float] = {}
    values = series.to_numpy()
    shifted = values[:-1] if len(values) > 1 else np.array([], dtype=float)

    for window in windows:
        window_data = shifted[-window:] if len(shifted) >= 1 else np.array([])
        if "mean" in statistics:
            result[f"{prefix}_roll_{window}_mean"] = (
                float(np.mean(window_data)) if len(window_data) >= 1 else np.nan
            )
        if "std" in statistics:
            result[f"{prefix}_roll_{window}_std"] = (
                float(np.std(window_data, ddof=1)) if len(window_data) >= 2 else 0.0
            )
        if "max" in statistics:
            result[f"{prefix}_roll_{window}_max"] = (
                float(np.max(window_data)) if len(window_data) >= 1 else np.nan
            )
        if "min" in statistics:
            result[f"{prefix}_roll_{window}_min"] = (
                float(np.min(window_data)) if len(window_data) >= 1 else np.nan
            )

    return result


def _compute_target_hist_features(target_series: pd.Series) -> dict[str, float]:
    result: dict[str, float] = {}
    values = target_series.to_numpy()
    hist_values = values[:-1] if len(values) > 1 else np.array([])
    n = len(hist_values)
    global_mean = float(np.mean(values)) if len(values) > 0 else 0.0

    for group_keys in TARGET_HIST_GROUP_KEYS_LIST:
        group_name = "_".join(group_keys)
        result[f"{group_name}_target_mean_hist"] = (
            float(np.mean(hist_values)) if n > 0 else global_mean
        )
        if n >= 2:
            mean_val = np.mean(hist_values)
            mean_sq = np.mean(hist_values ** 2)
            var_val = max(mean_sq - mean_val ** 2, 0.0)
            result[f"{group_name}_target_std_hist"] = float(np.sqrt(var_val))
        else:
            result[f"{group_name}_target_std_hist"] = 0.0
        result[f"{group_name}_target_zero_rate_hist"] = (
            float(np.mean(hist_values == 0)) if n > 0 else 0.0
        )
        result[f"{group_name}_target_count_hist"] = float(n)

    return result


class LocalInferenceFeatureEngine:
    """Local clone of prediction-service feature generation."""

    def __init__(self) -> None:
        self._static_aggs: dict[str, pd.DataFrame] = {}
        self._fill_values: dict[str, float] = {}

    def load_static_aggregations(self, path: str) -> None:
        agg_path = Path(path)
        if not agg_path.exists():
            raise FileNotFoundError(f"Static aggregations file not found at {agg_path}")

        with open(agg_path, "r") as fh:
            raw = json.load(fh)

        for key, records in raw.items():
            self._static_aggs[key] = pd.DataFrame(records)

    def load_fill_values(self, path: str) -> None:
        fill_path = Path(path)
        if not fill_path.exists():
            raise FileNotFoundError(f"Fill values file not found at {fill_path}")

        with open(fill_path, "r") as fh:
            self._fill_values = json.load(fh)

    def build_features(
        self,
        history_df: pd.DataFrame,
        route_id: int,
        warehouse_id: int,
        forecast_steps: int,
    ) -> pd.DataFrame:
        df = history_df.sort_values("timestamp").reset_index(drop=True)
        df = _add_time_features(df)
        df = _add_total_status_features(df)

        anchor_row = df.iloc[-1].to_dict()
        anchor_ts = anchor_row["timestamp"]
        ts_features = self._compute_ts_features(df)
        hist_features = _compute_target_hist_features(df[TARGET])

        rows: list[dict[str, Any]] = []
        for step in range(1, forecast_steps + 1):
            future_ts = anchor_ts + pd.Timedelta(
                minutes=settings.step_interval_minutes * step
            )
            row: dict[str, Any] = {
                "route_id": route_id,
                "office_from_id": warehouse_id,
                "dow": future_ts.day_name(),
                "pod": self._get_part_of_day(future_ts.hour),
                "slot": future_ts.hour * 2 + future_ts.minute // 30,
                "is_hooliday": int(future_ts.normalize() in HOLIDAY_DATES),
                "horizon_step": step,
                "horizon_minutes": settings.step_interval_minutes * step,
            }

            for status_col in STATUS_COLS:
                row[status_col] = anchor_row.get(status_col, 0.0)

            for feat in [
                "total_inventory",
                "status_early",
                "status_mid",
                "status_late",
                "early_inventory",
                "mid_inventory",
                "late_inventory",
                "early_share",
                "mid_share",
                "late_share",
                "status_entropy",
            ] + [f"status_{i}_share" for i in range(1, 9)]:
                row[feat] = anchor_row.get(feat, 0.0)

            row.update(ts_features)
            row.update(hist_features)
            rows.append(row)

        features_df = pd.DataFrame(rows)
        features_df = self._merge_static_aggs(features_df)
        features_df = self._fill_na(features_df)
        return self._set_cat_dtypes(features_df)

    @staticmethod
    def _get_part_of_day(hour: int) -> str:
        if 0 <= hour < 6:
            return "night"
        if 6 <= hour < 12:
            return "morning"
        if 12 <= hour < 18:
            return "day"
        return "evening"

    def _compute_ts_features(self, df: pd.DataFrame) -> dict[str, float]:
        features: dict[str, float] = {}

        if TARGET in df.columns:
            target_series = df[TARGET]
            features.update(_add_lag_features(target_series, TARGET_LAGS, TARGET))
            features.update(_add_diff_features(target_series, TARGET_DIFF_PERIODS, TARGET))
            features.update(
                _add_rolling_features(
                    target_series,
                    TARGET_ROLLING_WINDOWS,
                    TARGET_ROLLING_STATS,
                    TARGET,
                )
            )

        for inv_feat in INVENTORY_FEATURES:
            if inv_feat in df.columns:
                series = df[inv_feat]
                features.update(_add_lag_features(series, INVENTORY_LAGS, inv_feat))
                features.update(
                    _add_diff_features(series, INVENTORY_DIFF_PERIODS, inv_feat)
                )
                features.update(
                    _add_rolling_features(
                        series,
                        INVENTORY_ROLLING_WINDOWS,
                        INVENTORY_ROLLING_STATS,
                        inv_feat,
                    )
                )

        for status_col in STATUS_COLS:
            if status_col in df.columns:
                features.update(
                    _add_lag_features(df[status_col], DETAILED_STATUS_LAGS, status_col)
                )

        return features

    def _merge_static_aggs(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        for key, agg_df in self._static_aggs.items():
            merge_keys = key.split("_and_")
            available_keys = [
                k for k in merge_keys if k in df.columns and k in agg_df.columns
            ]
            if not available_keys:
                continue

            local_agg_df = agg_df
            for merge_key in available_keys:
                if local_agg_df[merge_key].dtype != df[merge_key].dtype:
                    try:
                        local_agg_df = local_agg_df.copy()
                        local_agg_df[merge_key] = local_agg_df[merge_key].astype(
                            df[merge_key].dtype
                        )
                    except (TypeError, ValueError):
                        pass

            existing_cols = [
                col
                for col in local_agg_df.columns
                if col in df.columns and col not in available_keys
            ]
            if existing_cols:
                df = df.drop(columns=existing_cols)

            df = df.merge(local_agg_df, how="left", on=available_keys)

        return df

    def _fill_na(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        excluded = set(INFERENCE_CAT_FEATURES) | set(MERGE_KEY_COLS)
        numeric_cols = [col for col in df.columns if col not in excluded]

        for col in numeric_cols:
            if col in self._fill_values and df[col].isna().any():
                df[col] = df[col].fillna(self._fill_values[col])

        remaining = [col for col in numeric_cols if df[col].isna().any()]
        if remaining:
            df[remaining] = df[remaining].fillna(0.0)
        return df

    def _set_cat_dtypes(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        for col in INFERENCE_CAT_FEATURES:
            if col in df.columns:
                df[col] = df[col].astype("category")
        return df


@dataclass(frozen=True)
class LocalModelBundle:
    model: Any
    model_path: str
    model_version: str
    source: str
    static_aggs_path: str
    fill_values_path: str
    metadata: dict[str, Any]
    feature_names: list[str]
    feature_engine: LocalInferenceFeatureEngine

    def predict(self, features: pd.DataFrame) -> np.ndarray:
        aligned = features.reindex(columns=self.feature_names)
        raw = self.model.predict(aligned)
        return np.clip(np.asarray(raw, dtype=float), 0, None)


def _read_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, "r") as fh:
        data = json.load(fh)
    return data if isinstance(data, dict) else {}


async def resolve_model_bundle(model_version: str | None) -> LocalModelBundle:
    """Load the requested model artifact plus its paired inference artifacts."""
    if model_version is None or not model_version.strip():
        model_path = Path(settings.model_output_dir) / settings.canonical_model_filename
        metadata_path = Path(settings.model_output_dir) / settings.canonical_metadata_filename
        static_aggs_path = (
            Path(settings.model_output_dir) / settings.canonical_static_aggs_filename
        )
        fill_values_path = (
            Path(settings.model_output_dir) / settings.canonical_fill_values_filename
        )
        metadata = _read_json_if_exists(metadata_path)
        resolved_version = (
            str(metadata.get("model_version")).strip()
            if metadata.get("model_version")
            else "active_primary"
        )
        source = "active_primary"
    else:
        row = await db.get_model_by_version(model_version.strip())
        if row is None:
            raise HTTPException(
                status_code=404,
                detail=f"Model version '{model_version}' not found",
            )
        config = row.get("config_json") or {}
        if not config.get("evaluation_ready"):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Model version '{model_version}' is unavailable for Team Track "
                    "test runs because it does not have versioned inference artifacts."
                ),
            )
        model_path = Path(row["model_path"])
        static_aggs_raw = str(config.get("static_aggs_path") or "").strip()
        fill_values_raw = str(config.get("fill_values_path") or "").strip()
        if not static_aggs_raw or not fill_values_raw:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Model version '{model_version}' is missing versioned inference artifacts."
                ),
            )
        static_aggs_path = Path(static_aggs_raw)
        fill_values_path = Path(fill_values_raw)
        metadata_path = model_path.with_name(f"{model_path.stem}_metadata.json")
        metadata = _read_json_if_exists(metadata_path)
        resolved_version = row["model_version"]
        source = "registry"

    if not model_path.exists():
        raise HTTPException(
            status_code=422,
            detail=f"Model artifact not found at {model_path}",
        )
    if not static_aggs_path.exists() or not fill_values_path.exists():
        raise HTTPException(
            status_code=422,
            detail=(
                f"Evaluation artifacts are missing for model '{resolved_version}'. "
                "Retrain the model to generate versioned inference artifacts."
            ),
        )

    with open(model_path, "rb") as fh:
        model = pickle.load(fh)

    intro = _introspect_lgb(model)
    feature_engine = LocalInferenceFeatureEngine()
    feature_engine.load_static_aggregations(str(static_aggs_path))
    feature_engine.load_fill_values(str(fill_values_path))

    return LocalModelBundle(
        model=model,
        model_path=str(model_path),
        model_version=resolved_version,
        source=source,
        static_aggs_path=str(static_aggs_path),
        fill_values_path=str(fill_values_path),
        metadata=metadata,
        feature_names=intro["feature_names"],
        feature_engine=feature_engine,
    )


def _ensure_template_matches_live_history(
    template_df: pd.DataFrame,
    history_df: pd.DataFrame,
) -> None:
    available_routes = set(history_df["route_id"].unique().tolist())
    requested_routes = set(template_df["route_id"].unique().tolist())
    missing_routes = sorted(requested_routes - available_routes)
    if missing_routes:
        preview = ", ".join(str(route_id) for route_id in missing_routes[:10])
        suffix = "..." if len(missing_routes) > 10 else ""
        raise HTTPException(
            status_code=422,
            detail=(
                f"Live snapshot has no history for route_id(s): {preview}{suffix}. "
                "Team Track test runs require an existing route_status_history window "
                "for every route in the file."
            ),
        )

    for route_id, group in template_df.groupby("route_id", sort=True):
        route_history = history_df.loc[history_df["route_id"] == route_id].sort_values(
            "timestamp"
        )
        anchor_ts = route_history["timestamp"].iloc[-1]
        sorted_group = group.sort_values("timestamp").reset_index(drop=True)
        actual_timestamps = sorted_group["timestamp"].tolist()
        expected_timestamps = [
            anchor_ts + pd.Timedelta(minutes=settings.step_interval_minutes * step)
            for step in range(1, settings.forecast_steps + 1)
        ]

        if len(sorted_group) != settings.forecast_steps:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Route {route_id} must contain exactly {settings.forecast_steps} "
                    "future timestamps in the Team Track template."
                ),
            )

        if any(ts <= anchor_ts for ts in actual_timestamps):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Route {route_id} contains timestamps that are not in the future "
                    f"relative to the live snapshot anchor {anchor_ts.isoformat()}."
                ),
            )

        deltas = [
            int((actual_timestamps[i] - actual_timestamps[i - 1]).total_seconds() // 60)
            for i in range(1, len(actual_timestamps))
        ]
        if any(delta != settings.step_interval_minutes for delta in deltas):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Route {route_id} must use a {settings.step_interval_minutes}-minute grid."
                ),
            )

        if actual_timestamps != expected_timestamps:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Route {route_id} must contain consecutive timestamps from "
                    f"{expected_timestamps[0].isoformat()} to "
                    f"{expected_timestamps[-1].isoformat()} based on the live snapshot."
                ),
            )


def _history_to_dataframe(rows: list[dict[str, Any]]) -> pd.DataFrame:
    history_df = pd.DataFrame(rows)
    if history_df.empty:
        return history_df
    history_df["timestamp"] = pd.to_datetime(history_df["timestamp"], errors="raise")
    if getattr(history_df["timestamp"].dt, "tz", None) is not None:
        history_df["timestamp"] = history_df["timestamp"].dt.tz_localize(None)
    history_df["route_id"] = history_df["route_id"].astype("int64")
    history_df["office_from_id"] = history_df["office_from_id"].astype("int64")
    return history_df.sort_values(["route_id", "timestamp"]).reset_index(drop=True)


@dataclass(frozen=True)
class TeamTrackEvaluation:
    row_count: int
    route_count: int
    model: dict[str, Any]
    preview_rows: list[dict[str, Any]]
    submission_rows: list[dict[str, Any]]

    def to_preview_response(self) -> dict[str, Any]:
        return {
            "row_count": self.row_count,
            "route_count": self.route_count,
            "preview_count": len(self.preview_rows),
            "model": self.model,
            "preview": self.preview_rows,
        }


async def evaluate_team_track(
    template_df: pd.DataFrame,
    model_version: str | None,
) -> TeamTrackEvaluation:
    route_ids = sorted(template_df["route_id"].unique().tolist())
    history_rows = await db.get_route_history_windows(route_ids, settings.history_window)
    history_df = _history_to_dataframe(history_rows)
    if history_df.empty:
        raise HTTPException(
            status_code=422,
            detail="Live snapshot is empty. Team Track test requires route_status_history.",
        )

    _ensure_template_matches_live_history(template_df, history_df)
    bundle = await resolve_model_bundle(model_version)

    result_rows: list[dict[str, Any]] = []
    for route_id, group in template_df.groupby("route_id", sort=True):
        route_history = history_df.loc[history_df["route_id"] == route_id].copy()
        route_history = route_history.sort_values("timestamp").reset_index(drop=True)
        route_history.loc[route_history.index[-1], TARGET] = 0.0
        warehouse_id = int(route_history["office_from_id"].iloc[-1])

        features = bundle.feature_engine.build_features(
            route_history,
            route_id=route_id,
            warehouse_id=warehouse_id,
            forecast_steps=settings.forecast_steps,
        )
        predictions = bundle.predict(features)
        ordered_group = group.sort_values("timestamp").reset_index(drop=True)

        for idx, row in ordered_group.iterrows():
            raw_forecast = float(predictions[idx])
            result_rows.append(
                {
                    "id": int(row["id"]),
                    "route_id": int(route_id),
                    "timestamp": row["timestamp"].isoformat(),
                    "raw_forecast": round(raw_forecast, 4),
                    "y_pred": max(0, int(round(raw_forecast))),
                }
            )

    submission_rows = sorted(result_rows, key=lambda row: row["id"])
    preview_rows = submission_rows[:PREVIEW_LIMIT]

    model_payload = {
        "selected_version": model_version.strip() if model_version else None,
        "resolved_version": bundle.model_version,
        "source": bundle.source,
        "model_path": bundle.model_path,
        "static_aggs_path": bundle.static_aggs_path,
        "fill_values_path": bundle.fill_values_path,
        "feature_count": len(bundle.feature_names),
        "evaluation_ready": True,
    }

    return TeamTrackEvaluation(
        row_count=len(submission_rows),
        route_count=len(route_ids),
        model=model_payload,
        preview_rows=preview_rows,
        submission_rows=submission_rows,
    )


def render_submission_csv(rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["id", "y_pred"])
    for row in sorted(rows, key=lambda item: item["id"]):
        writer.writerow([int(row["id"]), int(row["y_pred"])])
    return buffer.getvalue()
