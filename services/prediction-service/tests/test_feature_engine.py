"""Unit tests for InferenceFeatureEngine."""

import pytest
import pandas as pd

from app.core.feature_engine import (
    InferenceFeatureEngine,
    CAT_FEATURES,
    _get_part_of_day,
)


@pytest.fixture
def engine() -> InferenceFeatureEngine:
    """A bare InferenceFeatureEngine with no static aggs or fill values."""
    return InferenceFeatureEngine()


class TestBuildFeaturesShape:
    def test_build_features_shape(self, engine, sample_history_df):
        """Output must have exactly forecast_steps rows."""
        result = engine.build_features(
            history_df=sample_history_df,
            route_id=101,
            warehouse_id=5,
            forecast_steps=10,
        )
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 10

    def test_build_features_custom_steps(self, engine, sample_history_df):
        """forecast_steps parameter controls number of output rows."""
        for steps in (1, 5, 10):
            result = engine.build_features(
                history_df=sample_history_df,
                route_id=101,
                warehouse_id=5,
                forecast_steps=steps,
            )
            assert len(result) == steps


class TestBuildFeaturesCategoricals:
    def test_build_features_categoricals(self, engine, sample_history_df):
        """Categorical columns must have pandas category dtype."""
        result = engine.build_features(
            history_df=sample_history_df,
            route_id=101,
            warehouse_id=5,
            forecast_steps=10,
        )
        for col in CAT_FEATURES:
            if col in result.columns:
                assert result[col].dtype.name == "category", (
                    f"Column '{col}' should be category dtype, got {result[col].dtype}"
                )

    def test_build_features_expected_cat_columns_present(self, engine, sample_history_df):
        """Key categorical columns must be present in the output."""
        result = engine.build_features(
            history_df=sample_history_df,
            route_id=101,
            warehouse_id=5,
            forecast_steps=10,
        )
        for col in ("dow", "pod", "slot", "horizon_step"):
            assert col in result.columns, f"Expected column '{col}' in output"


class TestBuildFeaturesNoCriticalNans:
    def test_build_features_no_nans_in_critical(self, engine, sample_history_df):
        """Lag features (up to history length) and time features must not be NaN."""
        result = engine.build_features(
            history_df=sample_history_df,
            route_id=101,
            warehouse_id=5,
            forecast_steps=10,
        )
        # Time features must always be present
        time_cols = ["dow", "pod", "slot", "is_hooliday"]
        for col in time_cols:
            assert col in result.columns
            # category columns: check no null categories
            if result[col].dtype.name == "category":
                assert result[col].isna().sum() == 0, f"NaN found in time feature '{col}'"
            else:
                assert result[col].isna().sum() == 0, f"NaN found in time feature '{col}'"

        # Short lags (lag_1) must be available with 20 rows of history
        lag_cols = [c for c in result.columns if c.endswith("_lag_1")]
        for col in lag_cols:
            assert result[col].isna().sum() == 0, f"NaN found in short lag '{col}'"

    def test_build_features_numeric_no_nan_after_fill(self, engine, sample_history_df):
        """After building features, no numeric column should contain NaN (fill fallback)."""
        result = engine.build_features(
            history_df=sample_history_df,
            route_id=101,
            warehouse_id=5,
            forecast_steps=10,
        )
        numeric_cols = result.select_dtypes(include="number").columns
        for col in numeric_cols:
            assert result[col].isna().sum() == 0, f"NaN in numeric column '{col}'"


class TestBuildFeaturesTimeFeatures:
    def test_build_features_time_features(self, engine, sample_history_df):
        """dow, pod, slot values must correspond to the future forecast timestamps."""
        result = engine.build_features(
            history_df=sample_history_df,
            route_id=101,
            warehouse_id=5,
            forecast_steps=10,
        )
        import pandas as pd

        anchor_ts = pd.Timestamp(sample_history_df["timestamp"].iloc[-1])

        for step in range(1, 11):
            row = result.iloc[step - 1]
            future_ts = anchor_ts + pd.Timedelta(minutes=30 * step)

            expected_dow = future_ts.day_name()
            expected_slot = future_ts.hour * 2 + future_ts.minute // 30
            expected_pod = _get_part_of_day(future_ts.hour)

            assert str(row["dow"]) == expected_dow, f"step {step}: dow mismatch"
            assert int(row["slot"]) == expected_slot, f"step {step}: slot mismatch"
            assert str(row["pod"]) == expected_pod, f"step {step}: pod mismatch"

    def test_build_features_is_holiday_is_int_like(self, engine, sample_history_df):
        """is_hooliday must be 0 or 1."""
        result = engine.build_features(
            history_df=sample_history_df,
            route_id=101,
            warehouse_id=5,
            forecast_steps=10,
        )
        vals = result["is_hooliday"].astype(int).unique()
        assert set(vals).issubset({0, 1}), f"Unexpected is_hooliday values: {vals}"


class TestBuildFeaturesHorizonSteps:
    def test_build_features_horizon_steps(self, engine, sample_history_df):
        """horizon_step column must contain values 1..forecast_steps in order."""
        result = engine.build_features(
            history_df=sample_history_df,
            route_id=101,
            warehouse_id=5,
            forecast_steps=10,
        )
        assert "horizon_step" in result.columns
        steps = result["horizon_step"].astype(int).tolist()
        assert steps == list(range(1, 11))

    def test_build_features_horizon_minutes(self, engine, sample_history_df):
        """horizon_minutes must be horizon_step * 30."""
        result = engine.build_features(
            history_df=sample_history_df,
            route_id=101,
            warehouse_id=5,
            forecast_steps=10,
        )
        assert "horizon_minutes" in result.columns
        for _, row in result.iterrows():
            assert int(row["horizon_minutes"]) == int(row["horizon_step"]) * 30


class TestGetPartOfDay:
    @pytest.mark.parametrize("hour,expected", [
        (0, "night"),
        (3, "night"),
        (5, "night"),
        (6, "morning"),
        (11, "morning"),
        (12, "day"),
        (17, "day"),
        (18, "evening"),
        (23, "evening"),
    ])
    def test_get_part_of_day(self, hour, expected):
        assert _get_part_of_day(hour) == expected
