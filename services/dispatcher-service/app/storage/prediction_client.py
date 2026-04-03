from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0


async def predict_batch(
    base_url: str,
    routes: list[dict],
    *,
    timeout: float = _DEFAULT_TIMEOUT,
) -> list[dict]:
    if not routes:
        return []

    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
            response = await client.post("/predict/batch", json=routes)
            response.raise_for_status()
            return response.json()
    except httpx.ConnectError:
        logger.warning(
            "Cannot reach prediction-service at %s — returning empty predictions",
            base_url,
        )
        return []
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "prediction-service returned %s — returning empty predictions",
            exc.response.status_code,
        )
        return []
    except httpx.TimeoutException:
        logger.warning(
            "prediction-service timed out after %.1fs — returning empty predictions",
            timeout,
        )
        return []
