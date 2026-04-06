"""Quality evaluation: compare predictions vs actuals."""

import logging
from datetime import datetime, timedelta
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


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


class QualityChecker:
    """Evaluates prediction quality by matching forecasts to actual values."""

    WAPE_ALERT_THRESHOLD = 0.5
    RBIAS_ALERT_THRESHOLD = 0.3

    def __init__(self, http_client=None) -> None:
        self._http_client = http_client
        self._last_check: datetime | None = None
        self._last_metrics: dict[str, Any] = {}
        self._alerts: list[dict[str, Any]] = []
        self._retrain_url: str | None = None

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

    @property
    def status(self) -> dict[str, Any]:
        return {
            "last_check": self._last_check.isoformat() if self._last_check else None,
            "last_metrics": self._last_metrics,
            "active_alerts": len(self._alerts),
        }

    async def run_quality_check(self, from_db: Any) -> dict[str, Any]:
        """Compare recent forecasts vs actual values from route_status_history."""
        check_start = datetime.utcnow()
        lookback = check_start - timedelta(hours=6)

        pairs = await from_db.get_forecast_actual_pairs(since=lookback)

        if not pairs:
            return {
                "status": "no_data",
                "message": "No forecast-actual pairs found in last 6 hours",
                "checked_at": check_start.isoformat(),
            }

        y_true = np.array([p["actual"] for p in pairs], dtype=float)
        y_pred = np.array([p["predicted"] for p in pairs], dtype=float)

        wape = compute_wape(y_true, y_pred)
        rbias = compute_rbias(y_true, y_pred)
        combined = wape + abs(rbias)

        metrics: dict[str, Any] = {
            "wape": round(wape, 4),
            "rbias": round(rbias, 4),
            "combined_score": round(combined, 4),
            "n_pairs": len(pairs),
            "checked_at": check_start.isoformat(),
        }

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

        return {
            "status": "alert" if new_alerts else "ok",
            "metrics": metrics,
            "alerts": new_alerts,
        }
