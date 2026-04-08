"""Unit tests for canonical scheduler slot handling."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.core.pipeline import PipelineOrchestrator
from app.core.time_slots import snap_to_step


def test_snap_to_step_floors_to_lower_boundary() -> None:
    snapped = snap_to_step(datetime(2026, 4, 8, 21, 9, 17), 30)
    assert snapped == datetime(2026, 4, 8, 21, 0, 0)


@pytest.mark.asyncio
async def test_pipeline_uses_canonical_anchor_for_predictions_and_dispatch(monkeypatch) -> None:
    monkeypatch.setattr(settings, "step_interval_minutes", 30)
    monkeypatch.setattr(settings, "batch_size", 50)
    monkeypatch.setattr(settings, "forecast_hours_ahead", 6)
    monkeypatch.setattr(settings, "prediction_service_url", "http://prediction")
    monkeypatch.setattr(settings, "dispatcher_service_url", "http://dispatcher")

    http_client = AsyncMock()
    http_client.post.side_effect = [
        SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"total": 1},
            status_code=200,
            text="ok",
        ),
        SimpleNamespace(status_code=200, text="ok"),
    ]

    db = SimpleNamespace(
        get_active_routes=AsyncMock(return_value=[{"route_id": 101, "warehouse_id": 7}]),
        get_latest_statuses=AsyncMock(
            return_value=[
                {
                    "route_id": 101,
                    "warehouse_id": 7,
                    "status_1": 1.0,
                    "status_2": 2.0,
                    "status_3": 3.0,
                    "status_4": 4.0,
                    "status_5": 5.0,
                    "status_6": 6.0,
                    "status_7": 7.0,
                    "status_8": 8.0,
                }
            ]
        ),
        get_distinct_warehouses=AsyncMock(return_value=[7]),
        save_pipeline_run=AsyncMock(),
    )

    orchestrator = PipelineOrchestrator(http_client=http_client)
    reference_ts = datetime(2026, 4, 8, 21, 9, 17)
    result = await orchestrator.run_prediction_cycle(db, reference_ts=reference_ts)

    anchor_ts = datetime(2026, 4, 8, 21, 0, 0)
    assert result["status"] == "success"
    assert result["anchor_ts"] == anchor_ts.isoformat()
    db.get_latest_statuses.assert_awaited_once_with([101], as_of=anchor_ts)

    predict_call = http_client.post.await_args_list[0]
    assert predict_call.args[0] == "http://prediction/predict/batch"
    assert predict_call.kwargs["json"]["predictions"][0]["timestamp"] == anchor_ts.isoformat()

    dispatch_call = http_client.post.await_args_list[1]
    assert dispatch_call.args[0] == "http://dispatcher/dispatch"
    assert dispatch_call.kwargs["json"] == {
        "warehouse_id": 7,
        "time_range_start": anchor_ts.isoformat(),
        "time_range_end": datetime(2026, 4, 9, 3, 0, 0).isoformat(),
    }
