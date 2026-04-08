"""Naive baseline model for benchmarking the LightGBM trainer.

PRD §9.3 — comparison baseline.

The baseline predicts ``target_2h`` as the historical mean grouped by
``(route_id, hour_of_day, day_of_week)``. Anything the LightGBM model does
must beat this naive lookup table to justify its complexity. Computing
WAPE and Relative Bias for both models on the same out-of-time split lets
us quote a concrete delta during the defence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class BaselineMetrics:
    """Container for baseline evaluation metrics."""

    wape: float
    rbias: float
    combined_score: float
    mae: float
    n_val: int
    n_groups: int
    coverage: float  # share of val rows covered by group lookup (vs global fallback)

    def to_dict(self) -> dict[str, float]:
        return {
            "wape": round(self.wape, 6),
            "rbias": round(self.rbias, 6),
            "combined_score": round(self.combined_score, 6),
            "mae": round(self.mae, 6),
            "n_val": int(self.n_val),
            "n_groups": int(self.n_groups),
            "coverage": round(self.coverage, 6),
        }


class NaiveSeasonalBaseline:
    """Mean target_2h by (route_id, hour_of_day, day_of_week).

    The model is intentionally trivial: it stores the per-group historical
    mean and falls back to the global training mean when an unseen group
    appears at inference time. No external dependencies, no learned weights.
    """

    GROUP_KEYS = ("route_id", "hour", "dow")

    def __init__(self) -> None:
        self._lookup: dict[tuple[Any, int, int], float] = {}
        self._global_mean: float = 0.0
        self._fitted: bool = False

    @staticmethod
    def _ensure_keys(df: pd.DataFrame) -> pd.DataFrame:
        """Add hour and day-of-week columns derived from ``timestamp``."""
        df = df.copy()
        ts = pd.to_datetime(df["timestamp"])
        df["hour"] = ts.dt.hour.astype(int)
        df["dow"] = ts.dt.dayofweek.astype(int)
        return df

    def fit(self, train_df: pd.DataFrame, target: str = "target_2h") -> "NaiveSeasonalBaseline":
        """Build the (route_id, hour, dow) → mean target lookup table."""
        if target not in train_df.columns:
            raise ValueError(f"Training frame missing target column '{target}'")
        if "route_id" not in train_df.columns or "timestamp" not in train_df.columns:
            raise ValueError("Training frame must contain 'route_id' and 'timestamp'")

        prepared = self._ensure_keys(train_df.dropna(subset=[target]))
        if prepared.empty:
            raise ValueError("Training frame is empty after dropping NaN targets")

        self._global_mean = float(prepared[target].mean())
        grouped = (
            prepared.groupby(["route_id", "hour", "dow"])[target]
            .mean()
            .reset_index()
        )

        self._lookup = {
            (row.route_id, int(row.hour), int(row.dow)): float(getattr(row, target))
            for row in grouped.itertuples(index=False)
        }
        self._fitted = True

        logger.info(
            "NaiveSeasonalBaseline fitted: %d groups, global_mean=%.4f",
            len(self._lookup),
            self._global_mean,
        )
        return self

    def predict(self, df: pd.DataFrame) -> tuple[np.ndarray, float]:
        """Return predictions and the share of rows resolved via the lookup."""
        if not self._fitted:
            raise RuntimeError("NaiveSeasonalBaseline must be fitted before predict()")

        prepared = self._ensure_keys(df)
        preds = np.empty(len(prepared), dtype=float)
        hits = 0
        for i, row in enumerate(prepared.itertuples(index=False)):
            key = (row.route_id, int(row.hour), int(row.dow))
            value = self._lookup.get(key)
            if value is None:
                preds[i] = self._global_mean
            else:
                preds[i] = value
                hits += 1

        coverage = hits / len(prepared) if len(prepared) > 0 else 0.0
        return preds, coverage

    def evaluate(
        self,
        val_df: pd.DataFrame,
        target: str = "target_2h",
    ) -> BaselineMetrics:
        """Compute WAPE + |RBias| on the validation set.

        Uses the same metric definitions as ``ModelTrainer.evaluate_model``
        so the numbers are directly comparable.
        """
        if not self._fitted:
            raise RuntimeError("NaiveSeasonalBaseline must be fitted before evaluate()")
        if target not in val_df.columns:
            raise ValueError(f"Validation frame missing target column '{target}'")

        prepared = val_df.dropna(subset=[target])
        if prepared.empty:
            return BaselineMetrics(
                wape=0.0,
                rbias=0.0,
                combined_score=0.0,
                mae=0.0,
                n_val=0,
                n_groups=len(self._lookup),
                coverage=0.0,
            )

        preds, coverage = self.predict(prepared)
        y_true = prepared[target].to_numpy(dtype=float)

        total_actual = float(np.sum(np.abs(y_true)))
        wape = float(np.sum(np.abs(y_true - preds)) / total_actual) if total_actual else 0.0

        mean_actual = float(np.mean(y_true))
        rbias = (
            float(abs((float(np.mean(preds)) - mean_actual) / mean_actual))
            if mean_actual
            else 0.0
        )

        mae = float(np.mean(np.abs(y_true - preds)))
        return BaselineMetrics(
            wape=wape,
            rbias=rbias,
            combined_score=wape + rbias,
            mae=mae,
            n_val=len(prepared),
            n_groups=len(self._lookup),
            coverage=coverage,
        )

    @property
    def is_fitted(self) -> bool:
        return self._fitted

    @property
    def n_groups(self) -> int:
        return len(self._lookup)
