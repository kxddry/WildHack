"""Tests for Team Track evaluation and versioned inference artifacts."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import pickle
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pandas.testing as pdt
import pytest
from fastapi import HTTPException

from app.config import settings
from app.core import team_track


class _FakeBooster:
    params: dict = {"objective": "regression"}


class _FakeModel:
    def __init__(self, feature_names: list[str], multiplier: float) -> None:
        self.n_features_ = len(feature_names)
        self.feature_name_ = feature_names
        self.n_estimators_ = 10
        self.booster_ = _FakeBooster()
        self._multiplier = multiplier

    def predict(self, features: pd.DataFrame) -> np.ndarray:
        return np.arange(1, len(features) + 1, dtype=float) * self._multiplier


def _history_rows(route_id: int = 101, office_from_id: int = 7) -> list[dict[str, object]]:
    start = datetime(2025, 1, 1, 0, 0, 0)
    rows: list[dict[str, object]] = []
    for step in range(12):
        ts = start + timedelta(minutes=30 * step)
        rows.append(
            {
                "route_id": route_id,
                "office_from_id": office_from_id,
                "timestamp": ts,
                "status_1": float(step + 1),
                "status_2": float(step % 3),
                "status_3": 0.0,
                "status_4": 1.0,
                "status_5": 2.0,
                "status_6": 3.0,
                "status_7": 4.0,
                "status_8": 5.0,
                "target_2h": float(step + 10) if step < 11 else None,
            }
        )
    return rows


def _history_df(route_id: int = 101, office_from_id: int = 7) -> pd.DataFrame:
    df = pd.DataFrame(_history_rows(route_id, office_from_id))
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def _template_df(route_id: int = 101, anchor_ts: datetime | None = None) -> pd.DataFrame:
    anchor = anchor_ts or _history_rows(route_id)[-1]["timestamp"]
    assert isinstance(anchor, datetime)
    return pd.DataFrame(
        {
            "id": list(range(10, 0, -1)),
            "route_id": [route_id] * settings.forecast_steps,
            "timestamp": [
                anchor + timedelta(minutes=settings.step_interval_minutes * step)
                for step in range(1, settings.forecast_steps + 1)
            ],
        }
    )


def _seed_artifacts(
    tmp_path: Path,
    feature_names: list[str],
    *,
    model_filename: str,
    metadata_filename: str,
    static_aggs_filename: str,
    fill_values_filename: str,
    multiplier: float,
    model_version: str,
) -> None:
    with open(tmp_path / model_filename, "wb") as fh:
        pickle.dump(_FakeModel(feature_names, multiplier), fh, protocol=pickle.HIGHEST_PROTOCOL)

    (tmp_path / metadata_filename).write_text(
        json.dumps({"model_version": model_version, "cv_score": 0.1234})
    )
    (tmp_path / static_aggs_filename).write_text(json.dumps({}))
    (tmp_path / fill_values_filename).write_text(json.dumps({}))


def _prediction_feature_engine_module():
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / "services/prediction-service/app/core/feature_engine.py"
    spec = importlib.util.spec_from_file_location("prediction_feature_engine", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _feature_names_for_sample_history() -> list[str]:
    engine = team_track.LocalInferenceFeatureEngine()
    history_df = _history_df()
    history_df.loc[history_df.index[-1], "target_2h"] = 0.0
    features = engine.build_features(
        history_df=history_df,
        route_id=101,
        warehouse_id=7,
        forecast_steps=settings.forecast_steps,
    )
    return list(features.columns)


@pytest.fixture
def model_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "model_output_dir", str(tmp_path))
    monkeypatch.setattr(settings, "forecast_steps", 10)
    monkeypatch.setattr(settings, "step_interval_minutes", 30)
    monkeypatch.setattr(settings, "history_window", 288)
    return tmp_path


def test_coerce_template_rejects_history_snapshot_with_targeted_message() -> None:
    history_like = pd.DataFrame(
        {
            "office_from_id": [1],
            "route_id": [10],
            "timestamp": ["2025-01-01T00:00:00"],
            "status_1": [1],
            "status_2": [0],
            "status_3": [0],
            "status_4": [0],
            "status_5": [0],
            "status_6": [0],
            "status_7": [0],
            "status_8": [0],
            "target_2h": [1],
        }
    )

    with pytest.raises(HTTPException) as exc:
        team_track._coerce_template_df(history_like)

    assert exc.value.status_code == 422
    assert "History Ingest" in str(exc.value.detail)


def test_local_feature_engine_matches_prediction_contract() -> None:
    prediction_module = _prediction_feature_engine_module()
    local_engine = team_track.LocalInferenceFeatureEngine()
    prediction_engine = prediction_module.InferenceFeatureEngine()

    history_df = _history_df()
    history_df.loc[history_df.index[-1], "target_2h"] = 0.0

    local = local_engine.build_features(history_df, 101, 7, settings.forecast_steps)
    remote = prediction_engine.build_features(history_df, 101, 7, settings.forecast_steps)

    pdt.assert_frame_equal(local, remote)


def test_evaluate_team_track_returns_preview_and_submission_csv(model_dir, monkeypatch) -> None:
    feature_names = _feature_names_for_sample_history()
    _seed_artifacts(
        model_dir,
        feature_names,
        model_filename=settings.canonical_model_filename,
        metadata_filename=settings.canonical_metadata_filename,
        static_aggs_filename=settings.canonical_static_aggs_filename,
        fill_values_filename=settings.canonical_fill_values_filename,
        multiplier=1.0,
        model_version="v_active",
    )

    async def _fake_history(route_ids: list[int], limit: int):
        assert route_ids == [101]
        assert limit == settings.history_window
        return _history_rows()

    monkeypatch.setattr(team_track.db, "get_route_history_windows", _fake_history)

    evaluation = asyncio.run(team_track.evaluate_team_track(_template_df(), None))
    preview = evaluation.to_preview_response()
    csv_body = team_track.render_submission_csv(evaluation.submission_rows)

    assert preview["row_count"] == 10
    assert preview["route_count"] == 1
    assert preview["model"]["resolved_version"] == "v_active"
    assert preview["preview"][0]["raw_forecast"] == 10.0
    assert preview["preview"][0]["y_pred"] == 10
    assert csv_body.splitlines()[0] == "id,y_pred"
    assert csv_body.splitlines()[1] == "1,10"


def test_evaluate_team_track_rejects_routes_absent_from_live_snapshot(
    model_dir,
    monkeypatch,
) -> None:
    feature_names = _feature_names_for_sample_history()
    _seed_artifacts(
        model_dir,
        feature_names,
        model_filename=settings.canonical_model_filename,
        metadata_filename=settings.canonical_metadata_filename,
        static_aggs_filename=settings.canonical_static_aggs_filename,
        fill_values_filename=settings.canonical_fill_values_filename,
        multiplier=1.0,
        model_version="v_active",
    )

    async def _fake_history(route_ids: list[int], limit: int):
        assert limit == settings.history_window
        return _history_rows(route_id=101)

    monkeypatch.setattr(team_track.db, "get_route_history_windows", _fake_history)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(team_track.evaluate_team_track(_template_df(route_id=202), None))

    assert exc.value.status_code == 422
    assert "no history for route_id" in str(exc.value.detail)


def test_registry_version_uses_its_own_artifacts(model_dir, monkeypatch) -> None:
    feature_names = _feature_names_for_sample_history()
    _seed_artifacts(
        model_dir,
        feature_names,
        model_filename=settings.canonical_model_filename,
        metadata_filename=settings.canonical_metadata_filename,
        static_aggs_filename=settings.canonical_static_aggs_filename,
        fill_values_filename=settings.canonical_fill_values_filename,
        multiplier=1.0,
        model_version="v_active",
    )

    version = "v20250408_120000"
    _seed_artifacts(
        model_dir,
        feature_names,
        model_filename=f"{version}.pkl",
        metadata_filename=f"{version}_metadata.json",
        static_aggs_filename=f"{version}_static_aggs.json",
        fill_values_filename=f"{version}_fill_values.json",
        multiplier=5.0,
        model_version=version,
    )

    async def _fake_history(route_ids: list[int], limit: int):
        return _history_rows()

    async def _fake_model_by_version(requested: str):
        assert requested == version
        return {
            "model_version": version,
            "model_path": str(model_dir / f"{version}.pkl"),
            "config_json": {
                "evaluation_ready": True,
                "static_aggs_path": str(model_dir / f"{version}_static_aggs.json"),
                "fill_values_path": str(model_dir / f"{version}_fill_values.json"),
            },
        }

    monkeypatch.setattr(team_track.db, "get_route_history_windows", _fake_history)
    monkeypatch.setattr(team_track.db, "get_model_by_version", _fake_model_by_version)

    active = asyncio.run(team_track.evaluate_team_track(_template_df(), None))
    selected = asyncio.run(team_track.evaluate_team_track(_template_df(), version))

    assert active.model["resolved_version"] == "v_active"
    assert selected.model["resolved_version"] == version
    assert active.submission_rows[0]["y_pred"] == 10
    assert selected.submission_rows[0]["y_pred"] == 50
    assert selected.model["static_aggs_path"].endswith(f"{version}_static_aggs.json")


def test_old_registry_version_without_artifacts_is_marked_unavailable(monkeypatch) -> None:
    async def _fake_model_by_version(requested: str):
        return {
            "model_version": requested,
            "model_path": "/tmp/missing.pkl",
            "config_json": {"evaluation_ready": False},
        }

    monkeypatch.setattr(team_track.db, "get_model_by_version", _fake_model_by_version)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(team_track.resolve_model_bundle("v_old"))

    assert exc.value.status_code == 422
    assert "unavailable for Team Track test runs" in str(exc.value.detail)
