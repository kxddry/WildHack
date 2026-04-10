"""Unit tests for the prediction-service startup safety guard.

The guard in ``app/main.py`` exists to make one specific class of bug
impossible: serving synthetic predictions to downstream consumers without
them knowing. Before this guard, a missing ``model.pkl`` would silently
enable mock mode inside the lifespan handler; now, missing artifacts raise
``FileNotFoundError`` unless ``MOCK_MODE=1`` is explicitly set.

This test file pins that behaviour so the guard cannot regress unnoticed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.main import _check_required_artifacts


@pytest.fixture
def artifact_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Redirect the settings object to a tmp_path-backed artifact layout."""
    model = tmp_path / "model.pkl"

    from app import main as main_module

    monkeypatch.setattr(main_module.settings, "model_path", str(model))
    return {"model": model}


def test_all_artifacts_present_returns_empty(artifact_paths: dict[str, Path]) -> None:
    """Happy path: every artifact exists -> no missing files."""
    for p in artifact_paths.values():
        p.write_bytes(b"")

    assert _check_required_artifacts() == []


def test_model_missing_is_reported(artifact_paths: dict[str, Path]) -> None:
    """Pinpoint reporting: exactly the missing file must be listed."""
    missing = _check_required_artifacts()

    assert len(missing) == 1
    assert missing[0] == artifact_paths["model"]


def test_return_type_is_list_of_path(artifact_paths: dict[str, Path]) -> None:
    """Consumers pattern-match on Path instances; guard the contract."""
    missing = _check_required_artifacts()

    assert isinstance(missing, list)
    for item in missing:
        assert isinstance(item, Path)
