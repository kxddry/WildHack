"""Unit tests for ModelRegistry._copy_canonical_pair.

Covers the Section 1 fix: promoting a challenger must atomically copy
both ``model.pkl`` AND its metadata sibling to the canonical paths, so
a prediction-service restart reloads the promoted version instead of
silently reverting to the previous metadata.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.config import settings
from app.core.registry import ModelRegistry


def _seed(tmp_path: Path, version: str, metadata: dict | None) -> Path:
    """Write a versioned artifact + optional metadata sibling."""
    artifact = tmp_path / f"{version}.pkl"
    artifact.write_bytes(b"fake-model-bytes")
    if metadata is not None:
        (tmp_path / f"{version}_metadata.json").write_text(json.dumps(metadata))
    return artifact



def test_copy_canonical_pair_writes_both_files(tmp_path):
    version = "v20250408_120000"
    metadata = {"model_version": version, "cv_score": 0.42}
    artifact = _seed(tmp_path, version, metadata)

    result = ModelRegistry._copy_canonical_pair(str(artifact))

    canonical_model = tmp_path / settings.canonical_model_filename
    canonical_metadata = tmp_path / settings.canonical_metadata_filename

    assert canonical_model.exists()
    assert canonical_model.read_bytes() == b"fake-model-bytes"
    assert canonical_metadata.exists()
    assert json.loads(canonical_metadata.read_text())["model_version"] == version
    assert result == {
        "model": str(canonical_model),
        "metadata": str(canonical_metadata),
    }


def test_copy_canonical_pair_skips_metadata_when_missing(tmp_path):
    """Legacy artifacts without a metadata sibling still get the model copy."""
    version = "v20250408_130000"
    artifact = _seed(tmp_path, version, metadata=None)

    result = ModelRegistry._copy_canonical_pair(str(artifact))

    canonical_model = tmp_path / settings.canonical_model_filename
    canonical_metadata = tmp_path / settings.canonical_metadata_filename

    assert canonical_model.exists()
    assert not canonical_metadata.exists()
    assert "model" in result
    assert "metadata" not in result


def test_copy_canonical_pair_is_idempotent(tmp_path):
    """Calling twice on the same source produces the same canonical bytes."""
    version = "v20250408_140000"
    metadata = {"model_version": version}
    artifact = _seed(tmp_path, version, metadata)

    ModelRegistry._copy_canonical_pair(str(artifact))
    ModelRegistry._copy_canonical_pair(str(artifact))

    canonical_model = tmp_path / settings.canonical_model_filename
    canonical_metadata = tmp_path / settings.canonical_metadata_filename

    assert canonical_model.read_bytes() == b"fake-model-bytes"
    assert json.loads(canonical_metadata.read_text())["model_version"] == version


