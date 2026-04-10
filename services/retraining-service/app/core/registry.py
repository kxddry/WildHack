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

    def _internal_headers(self) -> dict[str, str]:
        """Build the X-Internal-Token header for protected prediction routes.

        Returns an empty dict if the secret is not configured so unit tests
        and local dev without an env var still exercise the happy path.
        Production compose must populate the var explicitly.
        """
        token = (settings.internal_api_token or "").strip()
        return {"X-Internal-Token": token} if token else {}

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
            headers=self._internal_headers(),
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _copy_canonical_pair(model_path: str) -> dict[str, str]:
        """Atomically copy the versioned artifact and its metadata sibling.

        Two files must survive a service restart: the model artifact and
        the metadata JSON next to it. If we only copy the artifact the old
        behaviour a restart reloads the promoted artifact but reads the
        *previous* metadata JSON, and since ``runtime_version`` prefers
        ``metadata.model_version``, the model would silently identify
        itself as the old version forever.

        Both files are written to a ``.tmp`` sibling first and then
        ``os.replace``-d so a crash mid-copy never leaves the canonical
        pair in a split state.
        """
        result: dict[str, str] = {}
        src_model = Path(model_path)
        canonical_model = src_model.parent / settings.canonical_model_filename
        try:
            tmp = canonical_model.with_suffix(canonical_model.suffix + ".tmp")
            shutil.copy2(src_model, tmp)
            os.replace(tmp, canonical_model)
            result["model"] = str(canonical_model)
        except Exception:
            logger.exception(
                "Failed to copy model to canonical path — promotion succeeded in-memory"
            )

        # Metadata sibling. Trainer writes it as ``<version>_metadata.json``;
        # promoted versions must live at ``model_metadata.json`` so the
        # prediction-service lifespan loader reads the correct blob.
        metadata_src = src_model.with_name(src_model.stem + "_metadata.json")
        canonical_metadata = src_model.parent / settings.canonical_metadata_filename
        if metadata_src.exists():
            try:
                tmp = canonical_metadata.with_suffix(canonical_metadata.suffix + ".tmp")
                shutil.copy2(metadata_src, tmp)
                os.replace(tmp, canonical_metadata)
                result["metadata"] = str(canonical_metadata)
            except Exception:
                logger.exception(
                    "Failed to copy metadata sibling %s — version metadata will "
                    "be stale on next restart",
                    metadata_src,
                )
        else:
            logger.warning(
                "Metadata sibling %s not found; skipping canonical metadata copy",
                metadata_src,
            )
        return result

    async def promote_to_primary(self, model_path: str) -> dict:
        """Promote a model to primary in the prediction service.

        Order of operations:
        1. Tell prediction-service to swap the in-memory shadow → primary.
        2. Copy artifact + metadata JSON to canonical paths atomically
           so a restart loads the same pair.
        """
        resp = await self._client.post(
            f"{self._prediction_url}/model/shadow/promote",
            headers=self._internal_headers(),
            timeout=30.0,
        )
        resp.raise_for_status()
        result = resp.json()

        copy_result = self._copy_canonical_pair(model_path)
        result["canonical_copy"] = copy_result

        return result

    async def trigger_reload(self) -> dict:
        """Tell prediction service to reload model from disk."""
        resp = await self._client.post(
            f"{self._prediction_url}/model/reload",
            headers=self._internal_headers(),
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()
