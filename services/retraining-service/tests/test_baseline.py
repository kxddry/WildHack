"""Unit tests for the naive seasonal baseline (PRD §9.3)."""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from app.core.baseline import NaiveSeasonalBaseline


def _make_history(num_routes: int = 2, days: int = 7) -> pd.DataFrame:
    """Build a synthetic history with a stable per-(route, hour, dow) target."""
    rows = []
    base = datetime(2025, 6, 2, 0, 0, 0)  # Monday
    for route_id in range(1, num_routes + 1):
        for day in range(days):
            for hour in range(24):
                ts = base + timedelta(days=day, hours=hour)
                # Deterministic mean: depends on (route, hour, dow)
                mean = float(route_id * 10 + hour + ts.weekday())
                # Inject mild noise so mean estimation is non-trivial.
                rows.append(
                    {
                        "route_id": route_id,
                        "timestamp": ts,
                        "target_2h": mean + 0.5,
                    }
                )
                rows.append(
                    {
                        "route_id": route_id,
                        "timestamp": ts,
                        "target_2h": mean - 0.5,
                    }
                )
    return pd.DataFrame(rows)


class TestNaiveSeasonalBaseline:
    def test_fit_builds_lookup_for_all_groups(self):
        df = _make_history()
        baseline = NaiveSeasonalBaseline().fit(df)
        # 2 routes x 24 hours x 7 distinct dow values
        assert baseline.is_fitted
        assert baseline.n_groups == 2 * 24 * 7

    def test_predict_recovers_group_mean(self):
        df = _make_history()
        baseline = NaiveSeasonalBaseline().fit(df)
        preds, coverage = baseline.predict(df)
        # Each duplicated row pair averages exactly to the deterministic mean.
        np.testing.assert_allclose(
            preds[::2], df.iloc[::2].apply(
                lambda r: float(r["route_id"] * 10 + r["timestamp"].hour + r["timestamp"].weekday()),
                axis=1,
            ).to_numpy(),
            rtol=1e-9,
            atol=1e-9,
        )
        assert coverage == 1.0

    def test_unknown_group_falls_back_to_global_mean(self):
        df = _make_history(num_routes=1)
        baseline = NaiveSeasonalBaseline().fit(df)
        unseen = pd.DataFrame(
            [
                {
                    "route_id": 999,
                    "timestamp": datetime(2025, 7, 1, 12, 0, 0),
                    "target_2h": 0.0,
                }
            ]
        )
        preds, coverage = baseline.predict(unseen)
        assert preds[0] == pytest.approx(df["target_2h"].mean())
        assert coverage == 0.0

    def test_evaluate_returns_zero_error_on_clean_data(self):
        # If target_2h is exactly equal to the per-group mean, error should be 0.
        rows = []
        base = datetime(2025, 6, 2, 0, 0, 0)
        for hour in range(24):
            for dow in range(7):
                ts = base + timedelta(days=dow, hours=hour)
                rows.append(
                    {"route_id": 1, "timestamp": ts, "target_2h": float(hour + dow)}
                )
        df = pd.DataFrame(rows)
        baseline = NaiveSeasonalBaseline().fit(df)
        metrics = baseline.evaluate(df)
        assert metrics.wape == pytest.approx(0.0, abs=1e-9)
        assert metrics.rbias == pytest.approx(0.0, abs=1e-9)
        assert metrics.combined_score == pytest.approx(0.0, abs=1e-9)
        assert metrics.coverage == 1.0

    def test_evaluate_handles_empty_validation(self):
        df = _make_history(num_routes=1, days=2)
        baseline = NaiveSeasonalBaseline().fit(df)
        empty = pd.DataFrame(columns=["route_id", "timestamp", "target_2h"])
        metrics = baseline.evaluate(empty)
        assert metrics.n_val == 0
        assert metrics.wape == 0.0
        assert metrics.rbias == 0.0

    def test_predict_before_fit_raises(self):
        baseline = NaiveSeasonalBaseline()
        with pytest.raises(RuntimeError):
            baseline.predict(pd.DataFrame({"route_id": [1], "timestamp": [datetime.now()]}))

    def test_fit_requires_target_column(self):
        df = pd.DataFrame({"route_id": [1], "timestamp": [datetime(2025, 6, 1)]})
        with pytest.raises(ValueError):
            NaiveSeasonalBaseline().fit(df)

    def test_metrics_to_dict_serializable(self):
        df = _make_history(num_routes=1, days=3)
        baseline = NaiveSeasonalBaseline().fit(df)
        metrics = baseline.evaluate(df)
        as_dict = metrics.to_dict()
        assert {"wape", "rbias", "combined_score", "mae", "n_val", "n_groups", "coverage"} <= as_dict.keys()
