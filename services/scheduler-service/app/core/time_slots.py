"""Helpers for canonical 30-minute slot alignment."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def strip_tz(value: datetime) -> datetime:
    """Return a naive timestamp for Postgres TIMESTAMP columns."""
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def snap_to_step(value: datetime, step_minutes: int) -> datetime:
    """Floor ``value`` to the nearest lower ``step_minutes`` boundary."""
    if step_minutes <= 0:
        raise ValueError(f"step_minutes must be positive, got {step_minutes}")

    naive_value = strip_tz(value)
    epoch = datetime(1970, 1, 1)
    total_seconds = int((naive_value - epoch).total_seconds())
    step_seconds = step_minutes * 60
    snapped_seconds = total_seconds - (total_seconds % step_seconds)
    return epoch + timedelta(seconds=snapped_seconds)
