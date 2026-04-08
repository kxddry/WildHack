"""Internal-token FastAPI dependency for protected scheduler routes.

The manual control routes (``/pipeline/trigger`` and ``/quality/trigger``)
need protection: both can mutate live system state by launching prediction,
dispatch, retraining, or shadow-promotion flows. Read routes
(``/pipeline/status``, ``/pipeline/history``, ``/quality/alerts``) stay
open to internal monitoring.
"""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException

from app.config import settings


def require_internal_token(
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> None:
    """Reject scheduler control requests without a valid X-Internal-Token."""
    expected = (settings.internal_api_token or "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail=(
                "Scheduler is not configured for internal auth "
                "(INTERNAL_API_TOKEN is empty)."
            ),
        )
    if not x_internal_token or not secrets.compare_digest(
        x_internal_token, expected
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing X-Internal-Token",
        )
