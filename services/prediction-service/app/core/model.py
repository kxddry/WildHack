import json
import logging
from pathlib import Path
from typing import Any

import joblib  # required for loading LightGBM model artifacts
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_STATUS_COLS = [f"status_{i}" for i in range(1, 9)]


class ModelManager:
    """Loads and serves a LightGBM model with optional metadata.

    Supports mock mode for local development without a trained model.
    """

    def __init__(self) -> None:
        self._model: Any | None = None
        self._metadata: dict[str, Any] = {}
        self._model_path: str | None = None
        self._mock_mode: bool = False

    @property
    def is_loaded(self) -> bool:
        return self._model is not None or self._mock_mode

    @property
    def is_mock(self) -> bool:
        return self._mock_mode

    def enable_mock_mode(self) -> None:
        """Enable mock predictions for local development."""
        self._mock_mode = True
        logger.info("Mock prediction mode enabled — returning synthetic forecasts")

    def load(self, path: str) -> None:
        """Load a LightGBM model from a joblib pickle file.

        Also loads model_metadata.json if it exists alongside the model.
        The model file is a trusted artifact produced by our training pipeline.
        """
        model_path = Path(path)
        if not model_path.exists():
            raise FileNotFoundError(
                f"Model file not found at {model_path.resolve()}. "
                "Ensure the model is trained and placed in the models/ directory."
            )

        logger.info("Loading model from %s", model_path)
        self._model = joblib.load(model_path)
        self._model_path = str(model_path.resolve())

        metadata_path = model_path.with_name("model_metadata.json")
        if metadata_path.exists():
            with open(metadata_path, "r") as f:
                self._metadata = json.load(f)
            logger.info("Loaded model metadata from %s", metadata_path)
        else:
            self._metadata = {}
            logger.info("No model_metadata.json found, using empty metadata")

        logger.info(
            "Model loaded successfully. Feature count: %d",
            self._model.n_features_,
        )

    def predict(self, features: pd.DataFrame) -> np.ndarray:
        """Run prediction on a feature DataFrame.

        Returns clipped (>=0) predictions.
        In mock mode returns synthetic values derived from input statuses.
        """
        if self._mock_mode:
            return self._mock_predict(features)

        if self._model is None:
            raise RuntimeError("Model is not loaded. Call load() first.")

        raw_preds = self._model.predict(features)
        return np.clip(raw_preds, 0, None)

    def _mock_predict(self, features: pd.DataFrame) -> np.ndarray:
        """Generate plausible synthetic predictions from input features."""
        n_rows = len(features)
        rng = np.random.default_rng(seed=42)

        available = [c for c in _STATUS_COLS if c in features.columns]
        if available:
            base = features[available].sum(axis=1).to_numpy(dtype=float)
        else:
            base = np.full(n_rows, 15.0)

        if "horizon_step" in features.columns:
            steps = features["horizon_step"].to_numpy(dtype=float)
            decay = 1.0 - 0.03 * (steps - 1)
        else:
            decay = np.linspace(1.0, 0.7, n_rows)

        preds = base * decay * 0.3 + rng.normal(0, 0.5, n_rows)
        return np.clip(preds, 0, None)

    def info(self) -> dict[str, Any]:
        """Return model metadata and introspected properties."""
        if self._mock_mode:
            return {
                "model_path": None,
                "model_type": "MockPredictor",
                "objective": "regression",
                "cv_score": None,
                "feature_count": 0,
                "feature_names": [],
                "n_estimators_fitted": 0,
                "training_date": "2025-05-01T00:00:00",
            }

        if self._model is None:
            raise RuntimeError("Model is not loaded. Call load() first.")

        booster = self._model.booster_
        params = booster.params if hasattr(booster, "params") else {}

        return {
            "model_path": self._model_path,
            "model_type": "LGBMRegressor",
            "objective": params.get("objective", self._metadata.get("objective", "unknown")),
            "cv_score": self._metadata.get("cv_score"),
            "feature_count": self._model.n_features_,
            "feature_names": list(self._model.feature_name_),
            "n_estimators_fitted": self._model.n_estimators_,
            "training_date": self._metadata.get("training_date"),
            **{k: v for k, v in self._metadata.items() if k not in ("cv_score", "training_date", "objective")},
        }
