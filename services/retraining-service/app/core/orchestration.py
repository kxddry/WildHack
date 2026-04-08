"""Shared retrain + promotion orchestration for the retraining service.

Two call sites used to each carry their own copy of fetch→build→train→
promote logic:

* ``POST /retrain`` — scheduled or manual retrain. Promotes to shadow only
  if the challenger beats the current champion on the combined score.
* ``POST /upload-dataset`` — operator brings fresh data and wants the
  system to treat it as the new reality; no A/B step, force-promote.

Both paths now go through ``run_retrain_cycle`` with a single
``PromotionPolicy`` value so the only difference between them lives in
this module and not sprinkled across two endpoint handlers.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from app.config import settings
from app.storage import postgres as db

logger = logging.getLogger(__name__)


class PromotionPolicy(str, Enum):
    """How to treat the trained challenger relative to the current primary.

    ``SHADOW_IF_BETTER``
        Legacy ``/retrain`` behaviour. Register the challenger, and if its
        combined score is strictly lower than the champion's, load it as
        shadow. A human or the scheduler's streak watcher then decides
        when to promote shadow → primary.

    ``FORCE_PRIMARY``
        Upload-dataset behaviour. The incoming data is authoritative for
        the current snapshot, so the challenger MUST become primary — no
        A/B step, no streak gate. Guarantees the dispatcher sees
        predictions from the just-trained model immediately.
    """

    SHADOW_IF_BETTER = "shadow_if_better"
    FORCE_PRIMARY = "force_primary"


@dataclass(frozen=True)
class RetrainOutcome:
    """Structured result surfaced back to the endpoint caller.

    Frozen dataclass so callers cannot mutate the record and accidentally
    change values already persisted to ``retrain_history``.
    """

    version: str
    model_path: str
    metrics: dict[str, Any]
    is_better_than_champion: bool
    promotion_status: str
    started_at: str
    finished_at: str
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "model_path": self.model_path,
            "metrics": self.metrics,
            "is_better_than_champion": self.is_better_than_champion,
            "promotion_status": self.promotion_status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
        }


async def run_retrain_cycle(
    trainer,
    registry,
    *,
    policy: PromotionPolicy,
    training_window_days: int | None = None,
    reference_ts: datetime | None = None,
) -> RetrainOutcome:
    """Execute a full retrain cycle under the given promotion policy.

    Steps (same as the legacy ``/retrain`` handler, now shared):
      1. Fetch training data from ``route_status_history``.
      2. Build features via ``ModelTrainer.build_features``.
      3. Train LightGBM on the OOT split.
      4. Save the artifact + metadata JSON to ``settings.model_output_dir``.
      5. Recompute static aggregations / fill values from the fresh data.
      6. Register the challenger in ``model_metadata``.
      7. Apply the promotion policy.

    The trainer.save_model path is stamped with an ISO-compact version
    string so each run produces a distinct artifact on disk that survives
    promote/rollback cycles.
    """
    version = f"v{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    started_at = datetime.utcnow().isoformat()

    window = (
        training_window_days
        if training_window_days is not None
        else settings.training_window_days
    )

    loop = asyncio.get_running_loop()

    logger.info(
        "Retrain cycle started — version=%s policy=%s window_days=%d reference_ts=%s",
        version,
        policy.value,
        window,
        reference_ts.isoformat() if reference_ts is not None else "now",
    )

    # 1. Fetch
    raw_df = await loop.run_in_executor(
        None, trainer.fetch_training_data, window, reference_ts
    )

    # 2. Build features
    features_df = await loop.run_in_executor(None, trainer.build_features, raw_df)

    # 3. Train
    model, metrics = await loop.run_in_executor(
        None, trainer.train_model, features_df
    )

    # 4. Save artifact + metadata JSON
    model_path = await loop.run_in_executor(
        None, trainer.save_model, model, version, metrics
    )

    # 5. Recompute static aggs — keep inference features in sync with the
    #    fresh training distribution. Non-fatal: a failure here means
    #    predictions stay on the previous aggregation table, not silent
    #    corruption.
    artifact_paths: dict[str, Any] = {}
    evaluation_ready = False
    try:
        artifact_paths = await loop.run_in_executor(
            None,
            trainer.save_static_aggs,
            raw_df,
            features_df,
            settings.model_output_dir,
            version,
        )
    except Exception:
        logger.exception(
            "Failed to save static aggs — retrain continues without update"
        )
    else:
        evaluation_ready = True

    # 6. Register challenger
    champion = await registry.get_champion()
    challenger_score = metrics["combined_score"]
    is_better = True
    champion_score: float | None = None
    if champion is not None:
        champion_score = champion.get("cv_score", float("inf"))
        is_better = trainer.compare_champion_challenger(
            champion_score, challenger_score
        )
        logger.info(
            "Champion score=%.4f, challenger score=%.4f, is_better=%s",
            champion_score,
            challenger_score,
            is_better,
        )

    config = {
        "training_window_days": window,
        "reference_ts": reference_ts.isoformat() if reference_ts is not None else None,
        "policy": policy.value,
        "n_estimators": settings.n_estimators,
        "learning_rate": settings.learning_rate,
        "num_leaves": settings.num_leaves,
        "max_depth": settings.max_depth,
        "min_child_samples": settings.min_child_samples,
        "best_iteration": metrics.get("best_iteration"),
        "wape": metrics.get("wape"),
        "rbias": metrics.get("rbias"),
        "evaluation_ready": evaluation_ready,
        "static_aggs_path": artifact_paths.get("static_aggs_path"),
        "fill_values_path": artifact_paths.get("fill_values_path"),
        "canonical_static_aggs_path": artifact_paths.get("canonical_static_aggs_path"),
        "canonical_fill_values_path": artifact_paths.get("canonical_fill_values_path"),
    }
    await registry.register_model(
        version=version,
        model_path=model_path,
        cv_score=challenger_score,
        feature_count=metrics.get("feature_count", 0),
        config=config,
    )

    # 7. Apply promotion policy.
    pending = _apply_policy(policy, is_better)
    promotion_status = await _execute_promotion(registry, model_path, pending)

    finished_at = datetime.utcnow().isoformat()
    outcome = RetrainOutcome(
        version=version,
        model_path=model_path,
        metrics=metrics,
        is_better_than_champion=is_better,
        promotion_status=promotion_status,
        started_at=started_at,
        finished_at=finished_at,
        status="success",
    )

    # Persist retrain history. Same logic the legacy /retrain handler had,
    # now owned by the shared orchestration so both upload and scheduled
    # retrain paths produce consistent audit rows.
    try:
        promoted = promotion_status in {"shadow_loaded", "primary_promoted"}
        await db.save_retrain_history(
            started_at=started_at,
            completed_at=finished_at,
            status="success",
            training_rows=metrics.get("train_rows"),
            champion_score=champion_score,
            challenger_score=challenger_score,
            promoted=promoted,
            new_model_version=version,
            details=outcome.to_dict(),
        )
    except Exception:
        logger.exception("Failed to persist retrain history")

    return outcome


def _apply_policy(policy: PromotionPolicy, is_better: bool) -> str:
    """Translate the policy + champion comparison into an initial promotion status.

    ``skipped``         — policy said "only if better" and challenger lost.
    ``needs_shadow``    — shadow_if_better, challenger won.
    ``needs_primary``   — force_primary, always.
    """
    if policy is PromotionPolicy.FORCE_PRIMARY:
        return "needs_primary"
    # shadow_if_better
    return "needs_shadow" if is_better else "skipped"


async def _execute_promotion(
    registry, model_path: str, pending_status: str
) -> str:
    """Drive the actual prediction-service calls for the pending status.

    Keeps the error handling consolidated: a failure in shadow-load or
    primary-promote is logged but does NOT mark the retrain as failed —
    the artifact is on disk, registered in ``model_metadata``, and can be
    promoted manually via the existing ``POST /models/{version}/promote``
    endpoint.
    """
    if pending_status == "skipped":
        return "skipped"

    if pending_status == "needs_shadow":
        try:
            await registry.promote_to_shadow(model_path)
            return "shadow_loaded"
        except Exception:
            logger.exception(
                "Failed to load challenger as shadow — model registered but not promoted"
            )
            return "promotion_failed"

    if pending_status == "needs_primary":
        # FORCE_PRIMARY semantics require shadow-load then shadow→primary
        # swap so the prediction-service's in-memory state transitions
        # atomically. The registry already copies canonical artifact and
        # metadata inside promote_to_primary.
        try:
            await registry.promote_to_shadow(model_path)
        except Exception:
            logger.exception(
                "force_primary: shadow load failed — aborting promotion"
            )
            return "promotion_failed"
        try:
            await registry.promote_to_primary(model_path)
            return "primary_promoted"
        except Exception:
            logger.exception(
                "force_primary: shadow → primary swap failed"
            )
            return "promotion_failed"

    return pending_status
