"""Pytest fixtures for prediction-service tests."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def base_dt() -> datetime:
    """A fixed base datetime for tests."""
    return datetime(2025, 6, 2, 10, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def sample_history_df(base_dt) -> pd.DataFrame:
    """A minimal history DataFrame with 20 rows for a single route.

    Contains all columns required by InferenceFeatureEngine.build_features:
    timestamp, target_2h, status_1..status_8, route_id, office_from_id.
    """
    n = 20
    timestamps = [base_dt + timedelta(minutes=30 * i) for i in range(n)]
    rng = np.random.default_rng(42)

    data = {
        "timestamp": pd.to_datetime(timestamps),
        "route_id": [101] * n,
        "office_from_id": [5] * n,
        "target_2h": rng.uniform(0, 50, n).astype(float),
    }
    for i in range(1, 9):
        data[f"status_{i}"] = rng.uniform(0, 10, n).astype(float)

    return pd.DataFrame(data)


@pytest.fixture
def mock_model():
    """A mock LightGBM-like model that returns zeros for any input."""
    model = MagicMock()
    model.n_features_ = 5
    model.feature_name_ = ["f1", "f2", "f3", "f4", "f5"]
    model.n_estimators_ = 100

    booster = MagicMock()
    booster.params = {"objective": "regression"}
    model.booster_ = booster

    model.predict = MagicMock(return_value=np.zeros(10))
    return model


@pytest.fixture
def prediction_settings():
    """Settings object matching prediction-service defaults."""
    return SimpleNamespace(
        model_path="models/model.pkl",
        model_version="v1",
        history_window=288,
        forecast_steps=10,
        step_interval_minutes=30,
        database_url="postgresql+asyncpg://wildhack:wildhack_dev@localhost:5432/wildhack",
    )
