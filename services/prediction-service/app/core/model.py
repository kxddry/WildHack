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
    Supports shadow model for A/B comparison.
    """

    def __init__(self) -> None:
        self._model: Any | None = None
        self._metadata: dict[str, Any] = {}
        self._model_path: str | None = None
        self._mock_mode: bool = False
        self._shadow_model: Any | None = None
        self._shadow_metadata: dict[str, Any] = {}
        self._shadow_path: str | None = None
        self._shadow_version: str | None = None

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
        """Generate realistic synthetic predictions for local development.

        Uses route_id + slot as seed for reproducible but varied results.
        Applies time-of-day pattern: morning peak, night trough.
        """
        n_rows = len(features)

        # Seed from route_id + horizon_minutes for variation across routes and calls
        seed_val = 42
        if "route_id" in features.columns:
            rid = features["route_id"]
            seed_val = int(rid.cat.codes.iloc[0]) if hasattr(rid.dtype, "categories") else int(rid.iloc[0])
            seed_val = seed_val * 997 + 1
        if "horizon_minutes" in features.columns:
            seed_val += int(features["horizon_minutes"].iloc[0])
        rng = np.random.default_rng(seed=seed_val)

        # Base: sum of statuses (inventory signal)
        available = [c for c in _STATUS_COLS if c in features.columns]
        if available:
            base = features[available].sum(axis=1).to_numpy(dtype=float)
        else:
            base = np.full(n_rows, 15.0)

        # Time-of-day multiplier from horizon_step (simulates daily pattern)
        if "horizon_step" in features.columns:
            steps = features["horizon_step"].to_numpy(dtype=float)
            tod_mult = 0.7 + 0.3 * np.sin(np.pi * steps / 10)
        else:
            tod_mult = np.ones(n_rows)

        # Horizon decay: further steps = less certain
        if "horizon_step" in features.columns:
            steps = features["horizon_step"].to_numpy(dtype=float)
            decay = 1.0 - 0.025 * (steps - 1)
        else:
            decay = np.linspace(1.0, 0.75, n_rows)

        preds = base * tod_mult * decay * 0.4 + rng.normal(0, 0.8, n_rows)
        return np.clip(preds, 0, None)

    def reload(self, path: str | None = None) -> dict[str, Any]:
        """Hot-reload model from disk without restarting the service."""
        reload_path = path or self._model_path
        if reload_path is None:
            raise RuntimeError("No model path configured")

        old_info = self.info() if self.is_loaded else {}
        self.load(reload_path)
        new_info = self.info()

        return {"old": old_info, "new": new_info, "reloaded": True}

    def load_shadow(self, path: str) -> None:
        """Load a shadow/challenger model for A/B comparison."""
        shadow_path = Path(path)
        if not shadow_path.exists():
            raise FileNotFoundError(f"Shadow model not found at {shadow_path}")

        logger.info("Loading shadow model from %s", shadow_path)
        self._shadow_model = joblib.load(shadow_path)
        self._shadow_path = str(shadow_path.resolve())
        self._shadow_version = shadow_path.stem

        metadata_path = shadow_path.with_name(shadow_path.stem + "_metadata.json")
        if metadata_path.exists():
            with open(metadata_path, "r") as f:
                self._shadow_metadata = json.load(f)
        else:
            self._shadow_metadata = {}

        logger.info("Shadow model loaded successfully")

    def predict_shadow(self, features: pd.DataFrame) -> np.ndarray | None:
        """Run shadow model prediction. Returns None if no shadow model loaded."""
        if self._shadow_model is None:
            return None
        try:
            raw = self._shadow_model.predict(features)
            return np.clip(raw, 0, None)
        except Exception:
            logger.exception("Shadow model prediction failed")
            return None

    @property
    def has_shadow(self) -> bool:
        return self._shadow_model is not None

    @property
    def shadow_version(self) -> str | None:
        return self._shadow_version

    def promote_shadow(self) -> None:
        """Promote shadow model to primary, discard old primary."""
        if self._shadow_model is None:
            raise RuntimeError("No shadow model loaded")
        self._model = self._shadow_model
        self._metadata = self._shadow_metadata
        self._model_path = self._shadow_path
        self._shadow_model = None
        self._shadow_metadata = {}
        self._shadow_path = None
        self._shadow_version = None
        logger.info("Shadow model promoted to primary")

    def remove_shadow(self) -> None:
        """Remove the shadow model."""
        self._shadow_model = None
        self._shadow_metadata = {}
        self._shadow_path = None
        self._shadow_version = None
        logger.info("Shadow model removed")

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
