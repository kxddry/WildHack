"""Unit tests for scheduler backfill orchestration."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.core.backfill import BackfillRunner


@pytest.mark.asyncio
async def test_backfill_runner_updates_target_and_transport_request_actuals(monkeypatch) -> None:
    monkeypatch.setattr(settings, "step_interval_minutes", 30)

    db = SimpleNamespace(
        backfill_target_2h=AsyncMock(return_value=4),
        backfill_transport_request_actuals=AsyncMock(return_value=7),
    )

    runner = BackfillRunner()
    result = await runner.run_backfill(db)

    assert result["status"] == "ok"
    assert result["target_rows_updated"] == 4
    assert result["request_rows_updated"] == 7
    assert result["rows_updated"] == 11
    db.backfill_target_2h.assert_awaited_once_with()
    db.backfill_transport_request_actuals.assert_awaited_once_with(30)
    assert runner.status["total_updated"] == 11
