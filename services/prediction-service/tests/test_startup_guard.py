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
    """Redirect the settings object to a tmp_path-backed artifact layout.

    Returns the three Path objects that the guard will inspect. Tests
    create or delete these files to exercise each branch.
    """
    model = tmp_path / "model.pkl"
    aggs = tmp_path / "static_aggs.json"
    fills = tmp_path / "fill_values.json"

    # Import inside the fixture so monkeypatching the settings module happens
    # on the live instance that ``app.main`` already imported.
    from app import main as main_module

    monkeypatch.setattr(main_module.settings, "model_path", str(model))
    monkeypatch.setattr(main_module.settings, "static_aggs_path", str(aggs))
    monkeypatch.setattr(main_module.settings, "fill_values_path", str(fills))
    return {"model": model, "aggs": aggs, "fills": fills}


def test_all_artifacts_present_returns_empty(artifact_paths: dict[str, Path]) -> None:
    """Happy path: every artifact exists → no missing files."""
    for p in artifact_paths.values():
        p.write_bytes(b"")  # content does not matter, only existence

    assert _check_required_artifacts() == []


def test_single_missing_artifact_is_reported(artifact_paths: dict[str, Path]) -> None:
    """Pinpoint reporting: exactly the missing file must be listed."""
    artifact_paths["model"].write_bytes(b"")
    artifact_paths["fills"].write_bytes(b"")
    # aggs deliberately absent

    missing = _check_required_artifacts()

    assert len(missing) == 1
    assert missing[0] == artifact_paths["aggs"]


def test_all_artifacts_missing_returns_all(artifact_paths: dict[str, Path]) -> None:
    """Worst-case: nothing on disk → every required path is listed."""
    missing = _check_required_artifacts()

    assert set(missing) == {
        artifact_paths["model"],
        artifact_paths["aggs"],
        artifact_paths["fills"],
    }


def test_return_type_is_list_of_path(artifact_paths: dict[str, Path]) -> None:
    """Consumers pattern-match on Path instances; guard the contract."""
    missing = _check_required_artifacts()

    assert isinstance(missing, list)
    for item in missing:
        assert isinstance(item, Path)
