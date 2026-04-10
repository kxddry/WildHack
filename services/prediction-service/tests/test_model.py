"""Unit tests for ModelManager (hybrid envelope format)."""

import numpy as np
import pandas as pd
import pytest
import joblib

from app.core.model import ModelManager


def _make_envelope(tmp_path, n_features=5):
    """Create a minimal hybrid envelope with tiny LGBMRegressor-like mocks."""
    from unittest.mock import MagicMock

    feat_cols_step = [f"f{i}" for i in range(n_features)]
    feat_cols_global = feat_cols_step + ["horizon_step"]
    cat_cols = ["horizon_step"]

    models = {}
    for step in range(1, 6):
        m = MagicMock()
        m.n_features_ = n_features
        m.n_estimators_ = 100
        m.best_iteration_ = 80
        m.feature_name_ = feat_cols_step
        m.predict = MagicMock(return_value=np.array([1.0]))
        models[f"step_{step}"] = m

    m_global = MagicMock()
    m_global.n_features_ = n_features + 1
    m_global.n_estimators_ = 200
    m_global.best_iteration_ = 150
    m_global.feature_name_ = feat_cols_global
    m_global.predict = MagicMock(return_value=np.array([2.0]))
    models["global_6_10"] = m_global

    envelope = {
        "models": models,
        "feat_cols_step": feat_cols_step,
        "feat_cols_global": feat_cols_global,
        "cat_cols": cat_cols,
        "metadata": {
            "model_version": "test_v1",
            "training_date": "2026-01-01T00:00:00",
            "combined_score": 0.01,
        },
    }

    path = tmp_path / "model.pkl"
    joblib.dump(envelope, path)
    return str(path), envelope


class TestModelManagerLoad:
    def test_load_missing_file(self, tmp_path):
        manager = ModelManager()
        with pytest.raises(FileNotFoundError, match="Model file not found"):
            manager.load(str(tmp_path / "nonexistent.pkl"))

    def test_is_loaded_false_before_load(self):
        manager = ModelManager()
        assert manager.is_loaded is False

    def test_load_valid_envelope(self, tmp_path):
        path, _ = _make_envelope(tmp_path)
        manager = ModelManager()
        manager.load(path)
        assert manager.is_loaded is True
        assert len(manager._models) == 6


class TestModelManagerPredict:
    def test_predict_without_load(self):
        manager = ModelManager()
        df = pd.DataFrame({"f1": [1.0], "horizon_step": [1]})
        with pytest.raises(RuntimeError, match="Model is not loaded"):
            manager.predict(df)

    def test_predict_dispatches_by_horizon(self, tmp_path):
        path, _ = _make_envelope(tmp_path)
        manager = ModelManager()
        manager.load(path)

        rows = []
        for step in range(1, 11):
            rows.append({"f0": 1.0, "f1": 2.0, "f2": 3.0, "f3": 4.0, "f4": 5.0, "horizon_step": step})
        df = pd.DataFrame(rows)

        result = manager.predict(df)
        assert isinstance(result, np.ndarray)
        assert len(result) == 10
        assert all(result >= 0)

    def test_predict_requires_horizon_step(self, tmp_path):
        path, _ = _make_envelope(tmp_path)
        manager = ModelManager()
        manager.load(path)

        df = pd.DataFrame({"f0": [1.0], "f1": [2.0]})
        with pytest.raises(ValueError, match="horizon_step"):
            manager.predict(df)


class TestModelManagerInfo:
    def test_info_without_load(self):
        manager = ModelManager()
        with pytest.raises(RuntimeError, match="Model is not loaded"):
            manager.info()

    def test_info_returns_expected_keys(self, tmp_path):
        path, _ = _make_envelope(tmp_path)
        manager = ModelManager()
        manager.load(path)

        info = manager.info()
        assert info["model_type"] == "LightGBMHybrid"
        assert isinstance(info["feature_count"], int)
        assert "submodels" in info
        assert "step_1" in info["submodels"]
        assert "global_6_10" in info["submodels"]
        assert info["cv_score"] == 0.01


class TestModelManagerMock:
    def test_mock_mode(self):
        manager = ModelManager()
        manager.enable_mock_mode()
        assert manager.is_loaded is True
        assert manager.is_mock is True

        df = pd.DataFrame({
            "status_1": [5.0], "status_2": [3.0],
            "horizon_step": [1], "route_id": [101],
        })
        result = manager.predict(df)
        assert isinstance(result, np.ndarray)
        assert len(result) == 1
