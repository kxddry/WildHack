"""StatusHistoryManager wraps PostgreSQL storage for route status history."""

import logging
from datetime import datetime

import pandas as pd

from app.storage import postgres

logger = logging.getLogger(__name__)


class StatusHistoryManager:
    """Manages route status history retrieval and insertion."""

    async def get_route_history(self, route_id: int, limit: int = 288) -> pd.DataFrame:
        """Get route history as a DataFrame ready for InferenceFeatureEngine.

        Returns DataFrame sorted chronologically with columns:
        timestamp, route_id, office_from_id, status_1..8, target_2h.
        """
        return await postgres.get_route_status_history(route_id, limit=limit)

    async def append_observation(
        self,
        route_id: int,
        warehouse_id: int,
        timestamp: datetime,
        statuses: dict[str, float],
    ) -> None:
        """Append a new status observation for a route."""
        await postgres.append_status_observation(
            route_id=route_id,
            warehouse_id=warehouse_id,
            timestamp=timestamp.isoformat(),
            statuses=statuses,
        )

    async def get_warehouse_for_route(self, route_id: int) -> int:
        """Look up the warehouse_id (office_from_id) for a route.

        Since route->warehouse is a static mapping, we fetch the most recent
        row from route_status_history for this route.
        """
        df = await postgres.get_route_status_history(route_id, limit=1)
        if df.empty:
            raise ValueError(f"No history found for route_id={route_id}, cannot determine warehouse")
        return int(df["office_from_id"].iloc[0])
