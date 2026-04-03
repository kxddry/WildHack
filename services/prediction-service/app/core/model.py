import json
import logging
from pathlib import Path
from typing import Any

import joblib  # required for loading LightGBM model artifacts
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class ModelManager:
    """Loads and serves a LightGBM model with optional metadata."""

    def __init__(self) -> None:
        self._model: Any | None = None
        self._metadata: dict[str, Any] = {}
        self._model_path: str | None = None

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

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
        """
        if self._model is None:
            raise RuntimeError("Model is not loaded. Call load() first.")

        raw_preds = self._model.predict(features)
        return np.clip(raw_preds, 0, None)

    def info(self) -> dict[str, Any]:
        """Return model metadata and introspected properties."""
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
