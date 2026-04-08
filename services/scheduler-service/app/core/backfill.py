"""Background backfill runner: populates target_2h for past observations."""

import logging
from datetime import datetime
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


class BackfillRunner:
    """Periodically backfills target_2h labels from actual future observations."""

    def __init__(self) -> None:
        self._last_run: datetime | None = None
        self._total_updated: int = 0

    @property
    def status(self) -> dict[str, Any]:
        return {
            "last_run": self._last_run.isoformat() if self._last_run else None,
            "total_updated": self._total_updated,
        }

    async def run_backfill(self, from_db: Any) -> dict[str, Any]:
        """Run one backfill pass. Returns count of rows updated."""
        started_at = datetime.utcnow()
        try:
            target_rows = await from_db.backfill_target_2h()
            request_rows = await from_db.backfill_transport_request_actuals(
                settings.step_interval_minutes
            )
            updated = target_rows + request_rows
            self._total_updated += updated
            self._last_run = started_at
            logger.info(
                "Backfill pass: target_rows=%d request_rows=%d total=%d",
                target_rows,
                request_rows,
                updated,
            )
            return {
                "status": "ok",
                "rows_updated": updated,
                "target_rows_updated": target_rows,
                "request_rows_updated": request_rows,
                "ran_at": started_at.isoformat(),
            }
        except Exception:
            logger.exception("Backfill pass failed")
            return {"status": "failed", "ran_at": started_at.isoformat()}
