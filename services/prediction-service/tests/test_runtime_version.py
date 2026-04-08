"""Unit tests for ModelManager.runtime_version precedence rules.

These tests cover the split-brain fix: /model/info, /predict, and
forecast row writes must all report the *runtime* version (metadata or
artifact stem), never the static settings label.
"""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np

from app.core.model import ModelManager


class _FakeBooster:
    """Plain-old-object stand-in so joblib can actually pickle us."""

    params: dict = {"objective": "regression"}


class _FakeModel:
    """Picklable fake that mimics a lightgbm sklearn wrapper.

    ``ModelManager.load`` → ``_introspect_lgb`` duck-types against
    ``booster_`` + ``n_features_`` when the real lightgbm classes are not
    available at import time. A MagicMock trips joblib's pickle guard
    ("can't pickle <class 'unittest.mock.MagicMock'>"), so we use a plain
    class instead — same protocol surface, but round-trippable.
    """

    n_features_ = 3
    feature_name_ = ["f1", "f2", "f3"]
    n_estimators_ = 10
    booster_ = _FakeBooster()

    def predict(self, features):  # noqa: D401 — test stub
        return np.zeros(5)


def _write_fake_model(tmp_path: Path, name: str, metadata: dict | None = None) -> Path:
    """Persist a fake model via joblib — matches ModelManager.load's codec.

    ``metadata`` is written to the canonical ``model_metadata.json`` path
    (ModelManager.load looks for ``<path>.with_name('model_metadata.json')``),
    not the versioned ``<stem>_metadata.json`` used during training. This
    mirrors the on-disk layout prediction-service sees after a promote.
    """
    path = tmp_path / f"{name}.pkl"
    joblib.dump(_FakeModel(), path)

    if metadata is not None:
        (tmp_path / "model_metadata.json").write_text(json.dumps(metadata))

    return path


def test_runtime_version_prefers_metadata(tmp_path):
    """metadata.model_version wins over the artifact stem and config label."""
    model_path = _write_fake_model(
        tmp_path,
        "v20250408_120000",
        metadata={"model_version": "v20250408_120000", "cv_score": 0.25},
    )

    mgr = ModelManager()
    mgr.load(str(model_path))

    assert mgr.runtime_version == "v20250408_120000"
    info = mgr.info()
    assert info["model_version"] == "v20250408_120000"


def test_runtime_version_falls_back_to_stem(tmp_path):
    """Without metadata, the artifact stem (excluding 'model') is used."""
    model_path = _write_fake_model(
        tmp_path, "v20250408_120000", metadata=None
    )

    mgr = ModelManager()
    mgr.load(str(model_path))

    # Stem is "v20250408_120000" — the date-stamped filename.
    assert mgr.runtime_version == "v20250408_120000"


def test_runtime_version_metadata_beats_stem(tmp_path):
    """Metadata wins even when the stem is a different value.

    Captures the split-brain case: an artifact named ``model.pkl`` (the
    canonical symlink stem) with a sibling metadata JSON that names a
    specific version. Before the fix, /model/info would echo
    ``settings.model_version`` (legacy static label). After the fix, it
    must echo the metadata value.
    """
    model_path = _write_fake_model(
        tmp_path,
        "model",
        metadata={"model_version": "v20250408_120000"},
    )

    mgr = ModelManager()
    mgr.load(str(model_path))

    assert mgr.runtime_version == "v20250408_120000"


def test_runtime_version_ignores_canonical_stem(tmp_path):
    """The literal stem "model" must NOT be reported as a version.

    Prevents consumers from ever seeing the string "model" as a runtime
    version — it's the canonical filename, not a real label.
    """
    model_path = _write_fake_model(tmp_path, "model", metadata=None)

    mgr = ModelManager()
    mgr.load(str(model_path))

    # Falls through to settings.model_version (legacy), which is "v1" by
    # default in the prediction-service config.
    assert mgr.runtime_version == "v1"
