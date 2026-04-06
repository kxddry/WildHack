"""Quality evaluation: compare predictions vs actuals.

Supports A/B comparison between primary and shadow model versions.
Tracks a shadow win streak per version and auto-promotes shadow to primary after
`_promote_threshold` consecutive quality checks where shadow WAPE < primary WAPE.
"""

import logging
import re
from datetime import datetime, timedelta
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Allowed model version format: v20260406_143022
_VERSION_RE = re.compile(r"^v\d{8}_\d{6}$")


def compute_wape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Weighted Absolute Percentage Error."""
    total = np.sum(np.abs(y_true))
    if total == 0:
        return 0.0
    return float(np.sum(np.abs(y_pred - y_true)) / total)


def compute_rbias(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Relative Bias (signed)."""
    total = np.sum(y_true)
    if total == 0:
        return 0.0
    return float(np.sum(y_pred) / total - 1.0)


def _metrics_for_pairs(pairs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Compute WAPE + RBias for a list of forecast-actual pairs. Returns None if empty."""
    if not pairs:
        return None
    y_true = np.array([p["actual"] for p in pairs], dtype=float)
    y_pred = np.array([p["predicted"] for p in pairs], dtype=float)
    wape = compute_wape(y_true, y_pred)
    rbias = compute_rbias(y_true, y_pred)
    return {
        "wape": round(wape, 4),
        "rbias": round(rbias, 4),
        "combined_score": round(wape + abs(rbias), 4),
        "n_pairs": len(pairs),
    }


def _split_by_model_version(
    pairs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    """Split pairs into primary and shadow groups.

    Primary = most common model_version (has been running the full lookback window).
    Shadow = most recent non-primary version (fewest pairs = deployed most recently).

    When multiple shadow versions exist in the window (e.g., two retrains happened
    within 6 hours), we evaluate only the latest one to avoid mixing metrics from
    different challenger models.

    Returns (primary_pairs, shadow_pairs, shadow_version_string).
    """
    if not pairs:
        return pairs, [], None

    version_counts: dict[str, int] = {}
    for p in pairs:
        v = p.get("model_version") or "unknown"
        version_counts[v] = version_counts.get(v, 0) + 1

    if len(version_counts) < 2:
        return pairs, [], None

    # Primary = most pairs (running longest in the 6-hour window)
    primary_version = max(version_counts, key=lambda v: version_counts[v])

    # Shadow = the non-primary version with the fewest pairs (most recently deployed)
    shadow_version = min(
        (v for v in version_counts if v != primary_version),
        key=lambda v: version_counts[v],
    )

    primary_pairs = [p for p in pairs if (p.get("model_version") or "unknown") == primary_version]
    shadow_pairs = [p for p in pairs if (p.get("model_version") or "unknown") == shadow_version]

    return primary_pairs, shadow_pairs, shadow_version


class QualityChecker:
    """Evaluates prediction quality by matching forecasts to actual values.

    Also performs A/B comparison when a shadow model is active, tracking
    a win streak per shadow version and auto-promoting after enough wins.
    """

    WAPE_ALERT_THRESHOLD = 0.5
    RBIAS_ALERT_THRESHOLD = 0.3

    def __init__(self, http_client=None) -> None:
        self._http_client = http_client
        self._last_check: datetime | None = None
        self._last_metrics: dict[str, Any] = {}
        self._alerts: list[dict[str, Any]] = []
        self._retrain_url: str | None = None

        # Shadow auto-promotion state (in-memory, resets on service restart)
        self._shadow_win_streak: int = 0
        self._shadow_streak_version: str | None = None  # version the streak belongs to
        self._promote_threshold: int = 3

    async def _trigger_retrain(self, reason: str) -> None:
        if self._http_client is None or self._retrain_url is None:
            logger.warning("Retrain not triggered — no HTTP client or URL configured")
            return
        try:
            resp = await self._http_client.post(
                f"{self._retrain_url}/retrain",
                timeout=10.0,
            )
            if resp.status_code == 200:
                logger.info("Retrain triggered successfully: %s", reason)
            elif resp.status_code == 409:
                logger.info("Retrain already in progress, skipping trigger")
            else:
                logger.warning("Retrain trigger returned %d: %s", resp.status_code, resp.text)
        except Exception:
            logger.exception("Failed to trigger retrain")

    async def _trigger_shadow_promote(self, shadow_version: str) -> None:
        """Auto-promote shadow model to primary after enough consecutive wins.

        Validates shadow_version format before constructing the URL to prevent
        path injection from DB-sourced version strings.
        """
        if self._http_client is None or self._retrain_url is None:
            logger.warning("Shadow promote skipped — no HTTP client or retraining URL configured")
            return

        if not _VERSION_RE.match(shadow_version):
            logger.warning(
                "Shadow auto-promote rejected: invalid version format %r — expected v{date}_{time}",
                shadow_version,
            )
            return

        try:
            resp = await self._http_client.post(
                f"{self._retrain_url}/models/{shadow_version}/promote",
                timeout=30.0,
            )
            if resp.status_code == 200:
                logger.info(
                    "Shadow model %s auto-promoted to primary after %d consecutive wins",
                    shadow_version,
                    self._shadow_win_streak,
                )
                self._shadow_win_streak = 0
                self._shadow_streak_version = None
            else:
                logger.warning(
                    "Shadow auto-promote returned %d: %s", resp.status_code, resp.text
                )
        except Exception:
            logger.exception("Failed to auto-promote shadow model %s", shadow_version)

    @property
    def status(self) -> dict[str, Any]:
        return {
            "last_check": self._last_check.isoformat() if self._last_check else None,
            "last_metrics": self._last_metrics,
            "active_alerts": len(self._alerts),
            "shadow_win_streak": self._shadow_win_streak,
            "shadow_streak_version": self._shadow_streak_version,
            "promote_threshold": self._promote_threshold,
        }

    async def run_quality_check(self, from_db: Any) -> dict[str, Any]:
        """Compare recent forecasts vs actual values from route_status_history.

        When a shadow model is active, also computes shadow WAPE, tracks
        the per-version win streak, and auto-promotes when threshold is reached.
        """
        check_start = datetime.utcnow()
        lookback = check_start - timedelta(hours=6)

        pairs = await from_db.get_forecast_actual_pairs(since=lookback)

        if not pairs:
            return {
                "status": "no_data",
                "message": "No forecast-actual pairs found in last 6 hours",
                "checked_at": check_start.isoformat(),
            }

        # Split primary vs shadow (most recent shadow version only)
        primary_pairs, shadow_pairs, shadow_version = _split_by_model_version(pairs)

        # Primary metrics (always computed)
        y_true = np.array([p["actual"] for p in primary_pairs], dtype=float)
        y_pred = np.array([p["predicted"] for p in primary_pairs], dtype=float)

        wape = compute_wape(y_true, y_pred)
        rbias = compute_rbias(y_true, y_pred)
        combined = wape + abs(rbias)

        metrics: dict[str, Any] = {
            "wape": round(wape, 4),
            "rbias": round(rbias, 4),
            "combined_score": round(combined, 4),
            "n_pairs": len(primary_pairs),
            "checked_at": check_start.isoformat(),
        }

        # Shadow A/B metrics
        shadow_metrics = _metrics_for_pairs(shadow_pairs)
        if shadow_metrics is not None:
            metrics["shadow_wape"] = shadow_metrics["wape"]
            metrics["shadow_rbias"] = shadow_metrics["rbias"]
            metrics["shadow_combined_score"] = shadow_metrics["combined_score"]
            metrics["shadow_n_pairs"] = shadow_metrics["n_pairs"]
            metrics["shadow_version"] = shadow_version

            # Reset streak if a different shadow version appeared
            if shadow_version != self._shadow_streak_version:
                if self._shadow_win_streak > 0:
                    logger.info(
                        "Shadow version changed %s → %s — resetting streak from %d to 0",
                        self._shadow_streak_version,
                        shadow_version,
                        self._shadow_win_streak,
                    )
                self._shadow_win_streak = 0
                self._shadow_streak_version = shadow_version

            shadow_wins = shadow_metrics["wape"] < wape
            if shadow_wins:
                self._shadow_win_streak += 1
                logger.info(
                    "Shadow model %s beats primary: shadow_wape=%.4f < primary_wape=%.4f "
                    "(streak=%d/%d)",
                    shadow_version,
                    shadow_metrics["wape"],
                    wape,
                    self._shadow_win_streak,
                    self._promote_threshold,
                )
            else:
                if self._shadow_win_streak > 0:
                    logger.info(
                        "Shadow model %s lost this round (shadow_wape=%.4f >= primary_wape=%.4f) "
                        "— resetting streak from %d to 0",
                        shadow_version,
                        shadow_metrics["wape"],
                        wape,
                        self._shadow_win_streak,
                    )
                self._shadow_win_streak = 0

            metrics["shadow_win_streak"] = self._shadow_win_streak

        # Quality alerts for primary model
        new_alerts: list[dict[str, Any]] = []
        if wape > self.WAPE_ALERT_THRESHOLD:
            new_alerts.append(
                {
                    "type": "high_wape",
                    "value": wape,
                    "threshold": self.WAPE_ALERT_THRESHOLD,
                    "message": f"WAPE {wape:.4f} exceeds threshold {self.WAPE_ALERT_THRESHOLD}",
                    "timestamp": check_start.isoformat(),
                }
            )
        if abs(rbias) > self.RBIAS_ALERT_THRESHOLD:
            new_alerts.append(
                {
                    "type": "high_rbias",
                    "value": rbias,
                    "threshold": self.RBIAS_ALERT_THRESHOLD,
                    "message": f"RBias {rbias:.4f} exceeds threshold {self.RBIAS_ALERT_THRESHOLD}",
                    "timestamp": check_start.isoformat(),
                }
            )

        self._last_check = check_start
        self._last_metrics = metrics
        self._alerts = new_alerts

        metrics["alert_triggered"] = bool(new_alerts)

        if new_alerts:
            await self._trigger_retrain(reason=f"alerts={[a['type'] for a in new_alerts]}")

        # Auto-promote shadow if streak reached threshold (only after version format validation)
        if (
            shadow_version is not None
            and self._shadow_win_streak >= self._promote_threshold
        ):
            await self._trigger_shadow_promote(shadow_version)

        return {
            "status": "alert" if new_alerts else "ok",
            "metrics": metrics,
            "alerts": new_alerts,
        }
