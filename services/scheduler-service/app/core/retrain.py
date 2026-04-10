"""Scheduled retrain orchestrator."""
import logging
from typing import Any

logger = logging.getLogger(__name__)


class RetrainOrchestrator:
    """Triggers periodic retrain via HTTP POST to retraining-service."""

    def __init__(self) -> None:
        self._http_client: Any = None
        self._retrain_url: str | None = None
        self._internal_token: str = ""

    async def run_retrain_tick(self) -> None:
        if self._http_client is None or self._retrain_url is None:
            logger.warning("Retrain tick skipped — no HTTP client or retrain URL configured")
            return
        try:
            headers: dict[str, str] = {}
            if self._internal_token:
                headers["X-Internal-Token"] = self._internal_token
            resp = await self._http_client.post(
                f"{self._retrain_url}/retrain",
                headers=headers,
                timeout=600.0,
            )
            logger.info("Retrain triggered — status=%d", resp.status_code)
        except Exception:
            logger.exception("Failed to trigger scheduled retrain")
