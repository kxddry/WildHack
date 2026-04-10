"""Model retraining engine — LightGBM Hybrid (6 sub-models).

Trains one LGBMRegressor per horizon step 1-5 (no-horizon feature set)
and one global model for steps 6-10 (full feature set including
horizon_step).  Feature engineering is delegated to
``team_pipeline.DatasetBuilder`` + ``team_pipeline.kaggle_features``.

Serialisation uses joblib (compatible with LGBMRegressor sklearn API).
The saved artifact is an envelope dict containing all six sub-models
plus the metadata required for inference.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor

from team_pipeline.data import DatasetBuilder
from team_pipeline.kaggle_features import BUILD_KWARGS, add_kaggle_features, get_extra_names
from team_pipeline.metric import WapePlusRbias  # noqa: F401 — available for callers

from app.config import settings
from app.storage import postgres as db

logger = logging.getLogger(__name__)


class ModelTrainer:
    """Full retraining pipeline: data → features → train 6 models → save."""

    def __init__(self) -> None:
        self._last_metrics: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # 1. Fetch training data
    # ------------------------------------------------------------------

    def fetch_training_data(
        self,
        window_days: int,
        reference_ts: datetime | None = None,
    ) -> pd.DataFrame:
        """Fetch route_status_history for the last ``window_days`` days.

        Uses a synchronous SQLAlchemy engine so pandas.read_sql works
        without a running asyncio event loop (training is CPU-bound).
        """
        df = db.fetch_training_data(
            settings.sync_database_url,
            window_days,
            reference_ts=reference_ts,
        )
        if df.empty:
            raise ValueError(
                f"No training data available for the last {window_days} days"
            )
        if len(df) < settings.min_training_rows:
            raise ValueError(
                f"Insufficient training data: {len(df)} rows "
                f"(minimum {settings.min_training_rows})"
            )
        return df

    # ------------------------------------------------------------------
    # 2. Train hybrid model from raw DataFrame
    # ------------------------------------------------------------------

    def train_from_dataframe(
        self,
        raw_df: pd.DataFrame,
        oot_steps: int = 10,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Build features and train 6 LGBMRegressor sub-models.

        Parameters
        ----------
        raw_df:
            Raw route_status_history DataFrame as returned by
            ``fetch_training_data``.
        oot_steps:
            Number of unique timestamps held out as the OOT validation
            set (default 10 matches the competition forecast horizon).

        Returns
        -------
        envelope : dict
            ``{'models', 'feat_cols_step', 'feat_cols_global',
               'cat_cols', 'build_kwargs'}``
        metrics : dict
            Aggregate and per-submodel evaluation metrics.
        """
        # Step 1 — add kaggle-style features in-place
        raw_df = add_kaggle_features(raw_df)
        extra_feats = get_extra_names(raw_df)

        # Step 2 — OOT split: last oot_steps unique timestamps → test metadata
        unique_ts = sorted(raw_df["timestamp"].unique())
        if len(unique_ts) <= oot_steps:
            raise ValueError(
                f"Not enough time steps for OOT split: {len(unique_ts)} "
                f"(need > {oot_steps})"
            )
        cutoff = unique_ts[-(oot_steps + 1)]
        test_df = (
            raw_df[raw_df["timestamp"] > cutoff][["route_id", "timestamp", "target_2h"]]
            .copy()
        )

        # Step 3 — build feature matrices via DatasetBuilder
        builder = DatasetBuilder(train=raw_df, test=test_df, config="team")
        build_kwargs = {k: v for k, v in BUILD_KWARGS.items() if k != "train_days"}
        X_full, y_full, X_test, y_test, meta_test = builder.build_train_test(
            train_days=None,
            return_y_test=True,
            return_meta_test=True,
            extra_numeric_features=extra_feats,
            **build_kwargs,
        )

        # Step 4 — cast categorical columns
        cat_cols = builder.cat_features
        cats_nh = [c for c in cat_cols if c != "horizon_step"]
        for c in cat_cols:
            if c in X_full.columns:
                X_full[c] = X_full[c].astype("category")
            if c in X_test.columns:
                X_test[c] = X_test[c].astype("category")

        # Step 5 — build no-horizon views (steps 1-5 models exclude horizon_step)
        X_full_nh = X_full.drop(columns=["horizon_step"], errors="ignore")
        X_test_nh = X_test.drop(columns=["horizon_step"], errors="ignore")

        # Step 6 — extract horizon_step arrays before dropping
        hs_full = X_full["horizon_step"].astype(int).values
        hs_test = meta_test["horizon_step"].astype(int).values

        # Step 7 — shared LightGBM hyper-parameters
        base_lgb = dict(
            objective="regression_l1",
            learning_rate=settings.learning_rate,
            num_leaves=settings.num_leaves,
            max_depth=settings.max_depth,
            min_child_samples=settings.min_child_samples,
            min_child_weight=settings.min_child_weight,
            min_split_gain=settings.min_split_gain,
            subsample=settings.subsample,
            subsample_freq=settings.subsample_freq,
            colsample_bytree=settings.colsample_bytree,
            reg_alpha=settings.reg_alpha,
            reg_lambda=settings.reg_lambda,
            subsample_for_bin=settings.subsample_for_bin,
            random_state=settings.random_state,
            n_jobs=settings.n_jobs,
            importance_type=settings.importance_type,
            verbosity=settings.verbosity,
        )

        models: dict[str, LGBMRegressor] = {}
        submodel_metrics: dict[str, dict] = {}
        all_preds_test = np.zeros(len(X_test))

        # Step 8a — per-step models (steps 1-5, no horizon_step feature)
        for step in range(1, 6):
            mt = hs_full == step
            mte = hs_test == step
            if not mt.sum() or not mte.sum():
                continue

            Xt = X_full_nh[mt].copy()
            Xte = X_test_nh[mte].copy()
            for c in cats_nh:
                if c in Xt.columns:
                    Xt[c] = Xt[c].astype("category")
                if c in Xte.columns:
                    Xte[c] = Xte[c].astype("category")

            m = LGBMRegressor(**base_lgb, n_estimators=settings.n_estimators)
            m.fit(
                Xt,
                y_full.values[mt],
                eval_set=[(Xte, y_test.values[mte])],
                categorical_feature=cats_nh,
                callbacks=[
                    lgb.early_stopping(settings.early_stopping_rounds, verbose=False),
                    lgb.log_evaluation(period=200),
                ],
            )
            preds = np.clip(m.predict(Xte), 0, None)
            all_preds_test[mte] = preds
            models[f"step_{step}"] = m
            submodel_metrics[f"step_{step}"] = {
                "n_features": m.n_features_,
                "n_estimators": m.n_estimators_,
                "best_iteration": m.best_iteration_,
            }
            logger.info(
                "step_%d: best_iter=%d  train=%d  val=%d",
                step, m.best_iteration_, mt.sum(), mte.sum(),
            )

        # Step 8b — global model for steps 6-10 (includes horizon_step)
        mt_g = hs_full >= 6
        mte_g = hs_test >= 6
        if mt_g.sum() and mte_g.sum():
            Xt = X_full[mt_g].copy()
            Xte = X_test[mte_g].copy()
            for c in cat_cols:
                if c in Xt.columns:
                    Xt[c] = Xt[c].astype("category")
                if c in Xte.columns:
                    Xte[c] = Xte[c].astype("category")

            m = LGBMRegressor(**base_lgb, n_estimators=settings.n_estimators)
            m.fit(
                Xt,
                y_full.values[mt_g],
                eval_set=[(Xte, y_test.values[mte_g])],
                categorical_feature=cat_cols,
                callbacks=[
                    lgb.early_stopping(settings.early_stopping_rounds, verbose=False),
                    lgb.log_evaluation(period=200),
                ],
            )
            preds = np.clip(m.predict(Xte), 0, None)
            all_preds_test[mte_g] = preds
            models["global_6_10"] = m
            submodel_metrics["global_6_10"] = {
                "n_features": m.n_features_,
                "n_estimators": m.n_estimators_,
                "best_iteration": m.best_iteration_,
            }
            logger.info(
                "global_6_10: best_iter=%d  train=%d  val=%d",
                m.best_iteration_, mt_g.sum(), mte_g.sum(),
            )

        # Step 9 — aggregate OOT metrics
        y_true = y_test.values
        total_actual = np.sum(np.abs(y_true))
        wape = (
            float(np.sum(np.abs(y_true - all_preds_test)) / total_actual)
            if total_actual > 0
            else 0.0
        )
        mean_true = np.mean(y_true)
        rbias = (
            float(abs((np.mean(all_preds_test) - mean_true) / mean_true))
            if mean_true != 0
            else 0.0
        )
        combined = wape + rbias

        logger.info(
            "Hybrid training complete — WAPE=%.4f  RBias=%.4f  combined=%.4f",
            wape, rbias, combined,
        )

        # Step 10 — build envelope and metrics
        feat_cols_step = list(X_full_nh.columns)
        feat_cols_global = list(X_full.columns)

        envelope: dict[str, Any] = {
            "models": models,
            "feat_cols_step": feat_cols_step,
            "feat_cols_global": feat_cols_global,
            "cat_cols": cat_cols,
            "build_kwargs": {k: v for k, v in BUILD_KWARGS.items() if k != "train_days"},
        }

        metrics: dict[str, Any] = {
            "wape": round(wape, 6),
            "rbias": round(rbias, 6),
            "combined_score": round(combined, 6),
            "mae": round(float(np.mean(np.abs(y_true - all_preds_test))), 6),
            "feature_count": len(feat_cols_global),
            "feature_names": feat_cols_global,
            "train_rows": int(len(X_full)),
            "val_rows": int(len(X_test)),
            "submodels": submodel_metrics,
        }

        self._last_metrics = metrics
        return envelope, metrics

    # ------------------------------------------------------------------
    # 3. Save model
    # ------------------------------------------------------------------

    def save_model(
        self,
        envelope: dict[str, Any],
        version: str,
        metrics: dict[str, Any],
    ) -> str:
        """Persist the hybrid model envelope to disk using joblib.

        Writes ``<version>.pkl`` (full envelope) and
        ``<version>_metadata.json`` (standalone registry record) to
        ``settings.model_output_dir``.

        Returns the absolute path to the saved ``.pkl`` file.
        """
        output_dir = Path(settings.model_output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        model_path = output_dir / f"{version}.pkl"
        metadata_path = output_dir / f"{version}_metadata.json"

        # Embed metadata into the envelope so the prediction-service can
        # read it without a separate sidecar file if needed.
        envelope["metadata"] = {
            "model_version": version,
            "training_date": datetime.utcnow().isoformat(),
            "combined_score": metrics.get("combined_score"),
            "wape": metrics.get("wape"),
            "rbias": metrics.get("rbias"),
            "mae": metrics.get("mae"),
            "feature_count": metrics.get("feature_count"),
            "train_rows": metrics.get("train_rows"),
            "val_rows": metrics.get("val_rows"),
            "submodels": metrics.get("submodels", {}),
        }

        joblib.dump(envelope, model_path)

        # Standalone metadata JSON for the model registry.
        with open(metadata_path, "w") as fh:
            json.dump(envelope["metadata"], fh, indent=2)

        logger.info("Saved hybrid model envelope to %s", model_path)
        return str(model_path)

    # ------------------------------------------------------------------
    # 4. Champion / challenger comparison
    # ------------------------------------------------------------------

    def compare_champion_challenger(
        self,
        champion_score: float,
        challenger_score: float,
    ) -> bool:
        """Return True if challenger is better (lower combined score) than champion."""
        return challenger_score < champion_score
