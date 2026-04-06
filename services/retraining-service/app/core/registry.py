"""Model registry: tracks model versions, promotes champions."""

import logging
import os
import shutil
from pathlib import Path
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class ModelRegistry:
    """Manages model versions and promotions."""

    def __init__(
        self,
        db,
        http_client: httpx.AsyncClient,
        prediction_url: str,
    ) -> None:
        self._db = db
        self._client = http_client
        self._prediction_url = prediction_url

    async def register_model(
        self,
        version: str,
        model_path: str,
        cv_score: float,
        feature_count: int,
        config: dict[str, Any],
    ) -> None:
        """Register a new model version in the database."""
        await self._db.register_model(
            version=version,
            model_path=model_path,
            cv_score=cv_score,
            feature_count=feature_count,
            config=config,
        )
        logger.info("Registered model version %s (score=%.4f)", version, cv_score)

    async def get_champion(self) -> dict[str, Any] | None:
        """Get the current champion model (best cv_score)."""
        return await self._db.get_best_model()

    async def get_all_versions(self) -> list[dict[str, Any]]:
        """List all registered model versions."""
        return await self._db.get_all_models()

    async def promote_to_shadow(self, model_path: str) -> dict:
        """Load a model as shadow in the prediction service for A/B comparison."""
        resp = await self._client.post(
            f"{self._prediction_url}/model/shadow/load",
            params={"path": model_path},
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()

    async def promote_to_primary(self, model_path: str) -> dict:
        """Promote a model to primary in the prediction service."""
        resp = await self._client.post(
            f"{self._prediction_url}/model/shadow/promote",
            timeout=30.0,
        )
        resp.raise_for_status()
        result = resp.json()

        # Copy to canonical model.pkl so prediction-service reloads correctly on restart
        src = Path(model_path)
        canonical = src.parent / settings.canonical_model_filename
        try:
            tmp = canonical.with_suffix(".tmp")
            shutil.copy2(src, tmp)
            os.replace(tmp, canonical)
            logger.info("Copied %s → %s", src.name, canonical.name)
        except Exception:
            logger.exception("Failed to copy model to canonical path — promotion succeeded in-memory")

        # Reload static aggregations in prediction-service so feature statistics
        # match the newly promoted model's training distribution.
        try:
            reload_resp = await self._client.post(
                f"{self._prediction_url}/model/reload-features",
                timeout=30.0,
            )
            if reload_resp.status_code == 200:
                logger.info("Feature aggregations reloaded in prediction-service")
            else:
                logger.warning(
                    "Feature reload returned %d: %s", reload_resp.status_code, reload_resp.text
                )
        except Exception:
            logger.exception("Failed to reload features — predictions will use previous agg stats")

        return result

    async def trigger_reload(self) -> dict:
        """Tell prediction service to reload model from disk."""
        resp = await self._client.post(
            f"{self._prediction_url}/model/reload",
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()
