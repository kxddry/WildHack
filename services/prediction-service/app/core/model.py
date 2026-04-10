import logging
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_STATUS_COLS = [f"status_{i}" for i in range(1, 9)]


class ModelManager:
    """Loads and serves a hybrid LightGBM envelope artifact.

    The artifact is a dict produced by the training pipeline with keys:
        models          — dict of fitted LGBMRegressor/Booster submodels
        feat_cols_step  — feature list for per-step models (steps 1-5)
        feat_cols_global— feature list for the global model (steps 6-10)
        cat_cols        — categorical column names
        metadata        — optional dict with version / score / date info

    Supports mock mode for local development without a trained model.
    Supports shadow model for A/B comparison.
    """

    def __init__(self) -> None:
        self._envelope: dict[str, Any] | None = None
        self._models: dict[str, Any] = {}
        self._feat_cols_step: list[str] = []
        self._feat_cols_global: list[str] = []
        self._cat_cols: list[str] = []
        self._metadata: dict[str, Any] = {}
        self._model_path: str | None = None
        self._mock_mode: bool = False

        # Shadow model state
        self._shadow_envelope: dict[str, Any] | None = None
        self._shadow_models: dict[str, Any] = {}
        self._shadow_feat_cols_step: list[str] = []
        self._shadow_feat_cols_global: list[str] = []
        self._shadow_cat_cols: list[str] = []
        self._shadow_metadata: dict[str, Any] = {}
        self._shadow_path: str | None = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_loaded(self) -> bool:
        return bool(self._models) or self._mock_mode

    @property
    def is_mock(self) -> bool:
        return self._mock_mode

    @property
    def feat_cols_step(self) -> list[str]:
        return self._feat_cols_step

    @property
    def feat_cols_global(self) -> list[str]:
        return self._feat_cols_global

    @property
    def cat_cols(self) -> list[str]:
        return self._cat_cols

    @property
    def runtime_version(self) -> str:
        """Return the active model version with deterministic precedence.

        Precedence (highest to lowest):
        1. metadata.model_version — written by the trainer, authoritative.
        2. Stem of the loaded artifact path — covers legacy artifacts.
        3. settings.model_version — last-resort fallback for mock/tests.
        """
        meta_version = self._metadata.get("model_version") if self._metadata else None
        if isinstance(meta_version, str) and meta_version.strip():
            return meta_version.strip()

        if self._model_path:
            stem = Path(self._model_path).stem
            if stem and stem != "model":
                return stem

        try:
            from app.config import settings  # local import by design
            return settings.model_version
        except Exception:
            return "unknown"

    @property
    def has_shadow(self) -> bool:
        return bool(self._shadow_models)

    @property
    def shadow_version(self) -> str | None:
        if self._shadow_path:
            return Path(self._shadow_path).stem
        return None

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load_envelope(self, path: str) -> tuple[dict[str, Any], dict[str, Any], list[str], list[str], list[str], dict[str, Any]]:
        """Load and validate a hybrid envelope artifact from *path*.

        Returns (envelope, models, feat_cols_step, feat_cols_global, cat_cols, metadata).
        Raises FileNotFoundError or ValueError on bad input.
        """
        model_path = Path(path)
        if not model_path.exists():
            raise FileNotFoundError(
                f"Model file not found at {model_path.resolve()}. "
                "Ensure the model is trained and placed in the models/ directory."
            )

        logger.info("Loading model from %s", model_path)
        envelope = joblib.load(model_path)

        required = ("models", "feat_cols_step", "feat_cols_global", "cat_cols")
        missing = [k for k in required if k not in envelope]
        if missing:
            raise ValueError(
                f"Artifact at {path} is missing required keys: {missing}. "
                "Expected a hybrid envelope dict produced by the training pipeline."
            )

        models: dict[str, Any] = envelope["models"]
        feat_cols_step: list[str] = list(envelope["feat_cols_step"])
        feat_cols_global: list[str] = list(envelope["feat_cols_global"])
        cat_cols: list[str] = list(envelope["cat_cols"])
        metadata: dict[str, Any] = dict(envelope.get("metadata") or {})

        return envelope, models, feat_cols_step, feat_cols_global, cat_cols, metadata

    def load(self, path: str) -> None:
        """Load a hybrid LightGBM envelope artifact from *path*."""
        (
            self._envelope,
            self._models,
            self._feat_cols_step,
            self._feat_cols_global,
            self._cat_cols,
            self._metadata,
        ) = self._load_envelope(path)
        self._model_path = str(Path(path).resolve())

        logger.info(
            "Model loaded successfully. submodels=%s feat_cols_step=%d feat_cols_global=%d",
            sorted(self._models.keys()),
            len(self._feat_cols_step),
            len(self._feat_cols_global),
        )

    def load_shadow(self, path: str) -> None:
        """Load a shadow/challenger model for A/B comparison."""
        (
            self._shadow_envelope,
            self._shadow_models,
            self._shadow_feat_cols_step,
            self._shadow_feat_cols_global,
            self._shadow_cat_cols,
            self._shadow_metadata,
        ) = self._load_envelope(path)
        self._shadow_path = str(Path(path).resolve())
        logger.info("Shadow model loaded from %s", path)

    def reload(self, path: str | None = None) -> dict[str, Any]:
        """Hot-reload the primary model from disk without restarting the service."""
        reload_path = path or self._model_path
        if reload_path is None:
            raise RuntimeError("No model path configured")

        old_info = self.info() if self.is_loaded else {}
        self.load(reload_path)
        new_info = self.info()

        return {"old": old_info, "new": new_info, "reloaded": True}

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------

    def _dispatch_predict(
        self,
        features_df: pd.DataFrame,
        models: dict[str, Any],
        feat_cols_step: list[str],
        feat_cols_global: list[str],
        cat_cols: list[str],
    ) -> np.ndarray:
        """Dispatch rows to per-step or global submodel based on horizon_step.

        Rows with horizon_step 1-5  → models['step_N'] using feat_cols_step
        Rows with horizon_step 6-10 → models['global_6_10'] using feat_cols_global

        Returns predictions aligned to the original row order.
        """
        if "horizon_step" not in features_df.columns:
            raise ValueError("features_df must contain a 'horizon_step' column")

        preds = np.full(len(features_df), np.nan, dtype=float)
        steps = features_df["horizon_step"].to_numpy()

        for step in range(1, 6):
            mask = steps == step
            if not mask.any():
                continue
            key = f"step_{step}"
            if key not in models:
                logger.warning("Submodel '%s' not found in envelope, skipping", key)
                continue
            subset = features_df.loc[mask].drop(columns=["horizon_step"], errors="ignore")
            aligned = subset.reindex(columns=feat_cols_step)
            # Cast categoricals (without horizon_step)
            cat_no_step = [c for c in cat_cols if c != "horizon_step"]
            for c in cat_no_step:
                if c in aligned.columns:
                    aligned[c] = aligned[c].astype("category")
            preds[mask] = models[key].predict(aligned)

        global_mask = (steps >= 6) & (steps <= 10)
        if global_mask.any():
            key = "global_6_10"
            if key not in models:
                logger.warning("Submodel '%s' not found in envelope, skipping", key)
            else:
                subset = features_df.loc[global_mask]
                aligned = subset.reindex(columns=feat_cols_global)
                for c in cat_cols:
                    if c in aligned.columns:
                        aligned[c] = aligned[c].astype("category")
                preds[global_mask] = models[key].predict(aligned)

        return np.clip(preds, 0, None)

    def predict(self, features_df: pd.DataFrame) -> np.ndarray:
        """Run prediction on a feature DataFrame.

        features_df must contain a 'horizon_step' column.
        Returns clipped (>=0) predictions.
        In mock mode returns synthetic values derived from input statuses.
        """
        if self._mock_mode:
            return self._mock_predict(features_df)

        if not self._models:
            raise RuntimeError("Model is not loaded. Call load() first.")

        return self._dispatch_predict(
            features_df,
            self._models,
            self._feat_cols_step,
            self._feat_cols_global,
            self._cat_cols,
        )

    def predict_shadow(self, features_df: pd.DataFrame) -> np.ndarray | None:
        """Run shadow model prediction. Returns None if no shadow model is loaded."""
        if not self._shadow_models:
            return None
        try:
            return self._dispatch_predict(
                features_df,
                self._shadow_models,
                self._shadow_feat_cols_step,
                self._shadow_feat_cols_global,
                self._shadow_cat_cols,
            )
        except Exception:
            logger.exception("Shadow model prediction failed")
            return None

    # ------------------------------------------------------------------
    # Shadow management
    # ------------------------------------------------------------------

    def promote_shadow(self) -> None:
        """Promote shadow model to primary, discard old primary."""
        if not self._shadow_models:
            raise RuntimeError("No shadow model loaded")
        self._envelope = self._shadow_envelope
        self._models = self._shadow_models
        self._feat_cols_step = self._shadow_feat_cols_step
        self._feat_cols_global = self._shadow_feat_cols_global
        self._cat_cols = self._shadow_cat_cols
        self._metadata = self._shadow_metadata
        self._model_path = self._shadow_path
        self._shadow_envelope = None
        self._shadow_models = {}
        self._shadow_feat_cols_step = []
        self._shadow_feat_cols_global = []
        self._shadow_cat_cols = []
        self._shadow_metadata = {}
        self._shadow_path = None
        logger.info("Shadow model promoted to primary")

    def remove_shadow(self) -> None:
        """Remove the shadow model."""
        self._shadow_envelope = None
        self._shadow_models = {}
        self._shadow_feat_cols_step = []
        self._shadow_feat_cols_global = []
        self._shadow_cat_cols = []
        self._shadow_metadata = {}
        self._shadow_path = None
        logger.info("Shadow model removed")

    # ------------------------------------------------------------------
    # Mock mode
    # ------------------------------------------------------------------

    def enable_mock_mode(self) -> None:
        """Enable mock predictions for local development."""
        self._mock_mode = True
        logger.info("Mock prediction mode enabled — returning synthetic forecasts")

    def _mock_predict(self, features: pd.DataFrame) -> np.ndarray:
        """Generate realistic synthetic predictions for local development.

        Uses route_id + slot as seed for reproducible but varied results.
        Applies time-of-day pattern: morning peak, night trough.
        """
        n_rows = len(features)

        seed_val = 42
        if "route_id" in features.columns:
            rid = features["route_id"]
            seed_val = int(rid.cat.codes.iloc[0]) if hasattr(rid.dtype, "categories") else int(rid.iloc[0])
            seed_val = seed_val * 997 + 1
        if "horizon_minutes" in features.columns:
            seed_val += int(features["horizon_minutes"].iloc[0])
        rng = np.random.default_rng(seed=seed_val)

        available = [c for c in _STATUS_COLS if c in features.columns]
        if available:
            base = features[available].sum(axis=1).to_numpy(dtype=float)
        else:
            base = np.full(n_rows, 15.0)

        if "horizon_step" in features.columns:
            steps = features["horizon_step"].to_numpy(dtype=float)
            tod_mult = 0.7 + 0.3 * np.sin(np.pi * steps / 10)
            decay = 1.0 - 0.025 * (steps - 1)
        else:
            tod_mult = np.ones(n_rows)
            decay = np.linspace(1.0, 0.75, n_rows)

        preds = base * tod_mult * decay * 0.4 + rng.normal(0, 0.8, n_rows)
        return np.clip(preds, 0, None)

    # ------------------------------------------------------------------
    # Info
    # ------------------------------------------------------------------

    def info(self) -> dict[str, Any]:
        """Return model metadata and introspected properties."""
        if self._mock_mode:
            return {
                "model_path": None,
                "model_version": self.runtime_version,
                "model_type": "MockPredictor",
                "objective": "regression",
                "cv_score": None,
                "feature_count": 0,
                "feature_names": [],
                "n_estimators_fitted": 0,
                "training_date": "2025-05-01T00:00:00",
            }

        if not self._models:
            raise RuntimeError("Model is not loaded. Call load() first.")

        return {
            "model_path": self._model_path,
            "model_version": self.runtime_version,
            "model_type": "LightGBMHybrid",
            "objective": "regression_l1",
            "feature_count": len(self._feat_cols_global),
            "feature_names": self._feat_cols_global,
            "n_estimators_fitted": sum(
                m.n_estimators_ for m in self._models.values()
            ),
            "cv_score": self._metadata.get("combined_score"),
            "training_date": self._metadata.get("training_date"),
            "submodels": {
                name: {
                    "n_features": m.n_features_,
                    "n_estimators": m.n_estimators_,
                    "best_iteration": getattr(m, "best_iteration_", None),
                }
                for name, m in sorted(self._models.items())
            },
        }
