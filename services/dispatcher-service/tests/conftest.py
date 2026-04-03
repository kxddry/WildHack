"""Pytest fixtures for dispatcher-service tests."""

import pytest
from datetime import datetime, timezone
from types import SimpleNamespace


@pytest.fixture
def dispatch_config():
    """Default dispatcher config matching app/config.py defaults."""
    return SimpleNamespace(
        truck_capacity=33,
        buffer_pct=0.10,
        min_trucks=1,
    )


@pytest.fixture
def make_forecast_slot():
    """Factory for creating forecast slot dicts."""

    def _make(start: datetime, end: datetime, containers: float) -> dict:
        return {
            "time_slot_start": start,
            "time_slot_end": end,
            "total_containers": containers,
        }

    return _make


@pytest.fixture
def base_dt():
    """A fixed base datetime for tests."""
    return datetime(2025, 6, 1, 8, 0, 0, tzinfo=timezone.utc)
