"""Internal-token FastAPI dependency for protected control routes.

Why a dependency and not a middleware
-------------------------------------
Only the ``/model/*`` control endpoints are privileged — ``/predict``,
``/predict/batch``, ``/health``, ``/metrics`` must remain open to internal
callers on the Docker network without incurring an auth round trip. A
middleware would either gate everything or need URL-prefix whitelists
duplicated from the router; a Depends-based guard colocates the policy
with the handler it protects.

Token source
------------
Read from ``settings.internal_api_token`` (populated from the
``INTERNAL_API_TOKEN`` env var). Empty setting → endpoint fails closed with
503 so a missing secret is never confused with successful auth. Matches the
existing DATA_INGEST_TOKEN pattern on the upload endpoint.
"""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException

from app.config import settings


def require_internal_token(
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
) -> None:
    """FastAPI dependency: reject requests without a valid X-Internal-Token."""
    expected = (settings.internal_api_token or "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail=(
                "Service is not configured for internal auth "
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
