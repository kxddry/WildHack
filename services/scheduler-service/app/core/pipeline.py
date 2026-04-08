"""Pipeline orchestrator: coordinates the full predict → dispatch cycle."""

import logging
from datetime import datetime, timedelta
from typing import Any

import httpx

from app.config import settings
from app.core.time_slots import snap_to_step

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    """Runs the full prediction → dispatch pipeline."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._client = http_client
        self._last_run: datetime | None = None
        self._last_status: str = "idle"
        self._run_count: int = 0

    @property
    def status(self) -> dict[str, Any]:
        return {
            "last_run": self._last_run.isoformat() if self._last_run else None,
            "last_status": self._last_status,
            "run_count": self._run_count,
        }

    async def run_prediction_cycle(
        self,
        from_db: Any,
        reference_ts: datetime | None = None,
    ) -> dict[str, Any]:
        """Execute: fetch latest statuses → batch predict → auto-dispatch."""
        self._run_count += 1
        run_start = datetime.utcnow()
        anchor_ts = snap_to_step(
            reference_ts or run_start,
            settings.step_interval_minutes,
        )
        result: dict[str, Any] = {
            "run_id": self._run_count,
            "started_at": run_start.isoformat(),
            "reference_ts": reference_ts.isoformat() if reference_ts else None,
            "anchor_ts": anchor_ts.isoformat(),
            "steps": [],
        }

        try:
            # Step 1: Get active routes and their latest statuses
            routes = await from_db.get_active_routes()
            if not routes:
                result["steps"].append(
                    {"step": "fetch_routes", "status": "skip", "detail": "No active routes"}
                )
                self._last_status = "no_routes"
                return result

            route_ids = [r["route_id"] for r in routes]
            statuses = await from_db.get_latest_statuses(route_ids, as_of=anchor_ts)
            result["steps"].append(
                {
                    "step": "fetch_statuses",
                    "status": "ok",
                    "anchor_ts": anchor_ts.isoformat(),
                    "routes_found": len(routes),
                    "statuses_found": len(statuses),
                }
            )

            # Step 2: Build batch prediction request
            predictions = [
                {
                    "route_id": row["route_id"],
                    "timestamp": anchor_ts.isoformat(),
                    "status_1": row.get("status_1", 0.0),
                    "status_2": row.get("status_2", 0.0),
                    "status_3": row.get("status_3", 0.0),
                    "status_4": row.get("status_4", 0.0),
                    "status_5": row.get("status_5", 0.0),
                    "status_6": row.get("status_6", 0.0),
                    "status_7": row.get("status_7", 0.0),
                    "status_8": row.get("status_8", 0.0),
                }
                for row in statuses
            ]

            # Step 3: Send batch prediction (in chunks)
            total_predicted = 0
            batch_size = settings.batch_size
            for i in range(0, len(predictions), batch_size):
                chunk = predictions[i : i + batch_size]
                resp = await self._client.post(
                    f"{settings.prediction_service_url}/predict/batch",
                    json={"predictions": chunk},
                    timeout=120.0,
                )
                resp.raise_for_status()
                batch_result = resp.json()
                total_predicted += batch_result.get("total", 0)

            result["steps"].append(
                {
                    "step": "batch_predict",
                    "status": "ok",
                    "routes_submitted": len(predictions),
                    "routes_predicted": total_predicted,
                }
            )

            # Step 4: Auto-dispatch for each warehouse
            warehouses = await from_db.get_distinct_warehouses()
            dispatch_count = 0
            time_range_start = anchor_ts
            time_range_end = anchor_ts + timedelta(hours=settings.forecast_hours_ahead)

            for wh_id in warehouses:
                try:
                    resp = await self._client.post(
                        f"{settings.dispatcher_service_url}/dispatch",
                        json={
                            "warehouse_id": wh_id,
                            "time_range_start": time_range_start.isoformat(),
                            "time_range_end": time_range_end.isoformat(),
                        },
                        timeout=30.0,
                    )
                    if resp.status_code == 200:
                        dispatch_count += 1
                    elif resp.status_code == 404:
                        pass  # No forecasts yet for this warehouse
                    else:
                        logger.warning(
                            "Dispatch failed for warehouse %d: %s", wh_id, resp.text
                        )
                except Exception:
                    logger.exception("Dispatch request failed for warehouse %d", wh_id)

            result["steps"].append(
                {
                    "step": "auto_dispatch",
                    "status": "ok",
                    "anchor_ts": anchor_ts.isoformat(),
                    "warehouses_total": len(warehouses),
                    "warehouses_dispatched": dispatch_count,
                }
            )

            self._last_status = "success"
            result["status"] = "success"

        except Exception as e:
            logger.exception("Pipeline run failed")
            self._last_status = "failed"
            result["status"] = "failed"
            result["error"] = str(e)

        self._last_run = run_start
        result["completed_at"] = datetime.utcnow().isoformat()

        # Persist pipeline run to database
        try:
            await from_db.save_pipeline_run({
                "run_type": "prediction_cycle",
                "status": result.get("status", "unknown"),
                "started_at": run_start,
                "completed_at": datetime.utcnow(),
                "details": result,
            })
        except Exception:
            logger.exception("Failed to persist pipeline run record")

        return result
