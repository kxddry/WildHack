"""Unit tests for the upload endpoint helpers.

These tests exercise the pure parsing/validation/transformation helpers in
app.api.upload without touching Postgres. The full refresh-snapshot path
(retention prune + force-promote retrain) is covered by the integration
tests that run under docker compose.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api import upload as upload_module
from app.api.upload import (
    ALLOWED_EXTENSIONS,
    MAX_CELLS,
    MAX_CSV_ROWS,
    REQUIRED_COLUMNS,
    _authenticate,
    _check_csv_budget,
    _coerce_types,
    _derive_upload_window,
    _iter_history_chunks,
    _pick_extension,
    _read_dataframe,
    _resolve_auto_refresh,
    _validate_schema,
)
from app.config import settings


def _good_df() -> pd.DataFrame:
    """A 4-row, 2-warehouse, 2-route synthetic dataset matching the schema."""
    return pd.DataFrame(
        {
            "office_from_id": [1, 1, 2, 2],
            "route_id": [10, 10, 20, 20],
            "timestamp": [
                "2025-01-01T00:00:00",
                "2025-01-01T00:30:00",
                "2025-01-01T00:00:00",
                "2025-01-01T00:30:00",
            ],
            "status_1": [1, 2, 3, 4],
            "status_2": [0, 0, 0, 0],
            "status_3": [0, 0, 0, 0],
            "status_4": [0, 0, 0, 0],
            "status_5": [5, 6, 7, 8],
            "status_6": [0, 0, 0, 0],
            "status_7": [0, 0, 0, 0],
            "status_8": [0, 0, 0, 0],
            "target_2h": [10.0, 11.0, 12.0, None],
        }
    )


# ---------------------------------------------------------------------------
# _pick_extension — filename whitelist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", [
    "data.parquet",
    "DATA.PARQUET",
    "team.pq",
    "team_track.csv",
    "big.tsv",
    "notes.txt",
])
def test_pick_extension_accepts_whitelisted(name: str) -> None:
    assert _pick_extension(name).startswith(".")


@pytest.mark.parametrize("name", [
    None,
    "",
    "data",
    "data.xls",
    "data.parquet.exe",
    "foo.parquet/../../etc/passwd",  # trailing segment is 'passwd' — 415
])
def test_pick_extension_rejects_unknown(name: str | None) -> None:
    with pytest.raises(HTTPException) as exc:
        _pick_extension(name)
    assert exc.value.status_code == 415


def test_pick_extension_safe_suffix_for_traversal_attempt() -> None:
    """Even when the filename LOOKS like path traversal, _pick_extension only
    returns the whitelisted extension — never a segment from the raw path.
    This is what prevents NamedTemporaryFile(suffix=...) from escaping /tmp.
    """
    # This filename ends with '.parquet' so it matches the whitelist, but
    # the returned suffix is the fixed '.parquet' constant, not the literal
    # trailing substring of the filename.
    assert _pick_extension("evil/../../tmp/bad.parquet") == ".parquet"


# ---------------------------------------------------------------------------
# _validate_schema
# ---------------------------------------------------------------------------


def test_validate_schema_passes_on_full_columns() -> None:
    _validate_schema(_good_df())  # should not raise


def test_validate_schema_rejects_empty_dataframe() -> None:
    with pytest.raises(HTTPException) as exc:
        _validate_schema(pd.DataFrame())
    assert exc.value.status_code == 422
    assert "empty" in exc.value.detail.lower()


def test_validate_schema_rejects_missing_columns() -> None:
    df = _good_df().drop(columns=["status_3", "target_2h"])
    with pytest.raises(HTTPException) as exc:
        _validate_schema(df)
    assert exc.value.status_code == 422
    assert "status_3" in exc.value.detail
    assert "target_2h" in exc.value.detail


def test_validate_schema_rejects_team_track_template_with_targeted_message() -> None:
    df = pd.DataFrame(
        {
            "id": [1, 2],
            "route_id": [10, 10],
            "timestamp": ["2025-01-01T01:00:00", "2025-01-01T01:30:00"],
        }
    )

    with pytest.raises(HTTPException) as exc:
        _validate_schema(df)

    assert exc.value.status_code == 422
    assert "Team Track Test" in str(exc.value.detail)


# ---------------------------------------------------------------------------
# _coerce_types
# ---------------------------------------------------------------------------


def test_coerce_types_normalizes_string_timestamps() -> None:
    df = _coerce_types(_good_df())
    assert pd.api.types.is_datetime64_any_dtype(df["timestamp"])
    assert df["timestamp"].iloc[0].year == 2025


def test_coerce_types_keeps_target_2h_nullable() -> None:
    df = _coerce_types(_good_df())
    assert pd.isna(df["target_2h"].iloc[-1])
    assert df["target_2h"].iloc[0] == 10.0


def test_coerce_types_projects_only_required_columns() -> None:
    extended = _good_df().assign(extra_col="ignored")
    df = _coerce_types(extended)
    assert "extra_col" not in df.columns
    assert set(REQUIRED_COLUMNS).issubset(set(df.columns))


def test_coerce_types_rejects_unparseable_timestamp() -> None:
    bad = _good_df()
    bad.loc[0, "timestamp"] = "not-a-date"
    with pytest.raises(HTTPException) as exc:
        _coerce_types(bad)
    assert exc.value.status_code == 422


def test_coerce_types_strips_timezone_info() -> None:
    tz_df = _good_df()
    tz_df["timestamp"] = [
        "2025-01-01T00:00:00+03:00",
        "2025-01-01T00:30:00+03:00",
        "2025-01-01T00:00:00+03:00",
        "2025-01-01T00:30:00+03:00",
    ]

    df = _coerce_types(tz_df)

    assert df["timestamp"].dt.tz is None


# ---------------------------------------------------------------------------
# row streaming
# ---------------------------------------------------------------------------
#
# ``_build_warehouse_rows`` / ``_build_route_rows`` were removed when the
# upload flow switched to the ``refresh_snapshot`` path — the retained
# history slice now drives the warehouse + routes rebuild in pure SQL
# (see ``app.storage.postgres.refresh_snapshot``). Keeping the tests for
# deleted helpers would only guard against a regression we cannot
# introduce.


def test_iter_history_chunks_preserves_count_and_nulls() -> None:
    df = _coerce_types(_good_df())
    chunks = list(_iter_history_chunks(df, chunk_size=3))
    # 4 rows + chunk_size=3 = 2 chunks
    assert len(chunks) == 2
    assert len(chunks[0]) == 3
    assert len(chunks[1]) == 1
    all_rows = [r for c in chunks for r in c]
    assert len(all_rows) == 4
    # Last row originally had NaN target_2h — must come out as Python None
    assert all_rows[-1]["target_2h"] is None


def test_iter_history_chunks_streams_lazily() -> None:
    """Generator must not materialize all chunks up front."""
    df = _coerce_types(_good_df())
    gen = _iter_history_chunks(df, chunk_size=2)
    # Pull one chunk, check the generator is not exhausted
    first = next(gen)
    assert len(first) == 2
    # Pull the next to make sure iteration continues
    second = next(gen)
    assert len(second) == 2
    with pytest.raises(StopIteration):
        next(gen)


def test_iter_history_chunks_applies_min_timestamp_filter() -> None:
    df = _coerce_types(_good_df())

    chunks = list(
        _iter_history_chunks(
            df,
            chunk_size=4,
            min_timestamp=datetime(2025, 1, 1, 0, 30, 0),
        )
    )

    assert len(chunks) == 1
    assert len(chunks[0]) == 2
    assert all(
        row["timestamp"] >= datetime(2025, 1, 1, 0, 30, 0)
        for row in chunks[0]
    )


# ---------------------------------------------------------------------------
# _read_dataframe — round-trip CSV through a temp file
# ---------------------------------------------------------------------------


def test_read_dataframe_csv_round_trip(tmp_path) -> None:
    path = tmp_path / "sample.csv"
    _good_df().to_csv(path, index=False)
    df = _read_dataframe(str(path), ".csv")
    assert list(df.columns)[:3] == ["office_from_id", "route_id", "timestamp"]
    assert len(df) == 4


# ---------------------------------------------------------------------------
# _check_csv_budget — DoS via many-row CSV
# ---------------------------------------------------------------------------


def test_check_csv_budget_accepts_small_file(tmp_path) -> None:
    path = tmp_path / "small.csv"
    path.write_text("\n".join(["a,b,c"] * 100))
    _check_csv_budget(str(path))  # should not raise


def test_check_csv_budget_rejects_bomb(tmp_path, monkeypatch) -> None:
    # Override the row budget to a tiny value so the test file is manageable
    monkeypatch.setattr("app.api.upload.MAX_CSV_ROWS", 50)
    path = tmp_path / "bomb.csv"
    path.write_text("\n".join(["row"] * 100))
    with pytest.raises(HTTPException) as exc:
        _check_csv_budget(str(path))
    assert exc.value.status_code == 413


# ---------------------------------------------------------------------------
# _authenticate — shared-secret header
# ---------------------------------------------------------------------------
#
# _authenticate now reads from settings.data_ingest_token (not os.environ),
# so these tests mutate the pydantic settings object directly via
# monkeypatch. The restore is automatic — monkeypatch tracks every
# setattr and reverts them during teardown.


def test_authenticate_rejects_missing_setting(monkeypatch) -> None:
    monkeypatch.setattr(settings, "data_ingest_token", "")
    with pytest.raises(HTTPException) as exc:
        _authenticate("anything")
    assert exc.value.status_code == 503


def test_authenticate_rejects_missing_header(monkeypatch) -> None:
    monkeypatch.setattr(settings, "data_ingest_token", "sekret")
    with pytest.raises(HTTPException) as exc:
        _authenticate(None)
    assert exc.value.status_code == 401


def test_authenticate_rejects_wrong_token(monkeypatch) -> None:
    monkeypatch.setattr(settings, "data_ingest_token", "sekret")
    with pytest.raises(HTTPException) as exc:
        _authenticate("wrong")
    assert exc.value.status_code == 401


def test_authenticate_accepts_correct_token(monkeypatch) -> None:
    monkeypatch.setattr(settings, "data_ingest_token", "sekret")
    _authenticate("sekret")  # should not raise


def test_derive_upload_window_uses_upload_max_timestamp() -> None:
    df = _coerce_types(_good_df())

    upload_max_ts, cutoff = _derive_upload_window(df, retention_days=7)

    assert upload_max_ts == datetime(2025, 1, 1, 0, 30, 0)
    assert cutoff == datetime(2024, 12, 25, 0, 30, 0)


@pytest.mark.parametrize(
    ("auto_refresh", "auto_predict", "expected"),
    [
        (None, None, True),
        (False, None, False),
        (None, False, False),
        (True, False, True),
        (False, True, False),
    ],
)
def test_resolve_auto_refresh_prefers_canonical_param(
    auto_refresh: bool | None,
    auto_predict: bool | None,
    expected: bool,
) -> None:
    assert _resolve_auto_refresh(auto_refresh, auto_predict) is expected


# ---------------------------------------------------------------------------
# sanity contract
# ---------------------------------------------------------------------------


def test_required_columns_match_status_range() -> None:
    """Guard against accidental drift between REQUIRED_COLUMNS and the
    status_1..8 contract used by the rest of the pipeline."""
    expected = {f"status_{i}" for i in range(1, 9)}
    assert expected.issubset(set(REQUIRED_COLUMNS))


def test_max_cells_and_csv_rows_are_consistent() -> None:
    """CSV row budget should match the cell budget for 12 required columns."""
    assert MAX_CSV_ROWS == MAX_CELLS // 12


class _FakeOutcome:
    def __init__(self, version: str = "v20250408_120000") -> None:
        self.version = version

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "promotion_status": "primary_promoted",
            "status": "success",
        }


@pytest.fixture
def upload_client(monkeypatch):
    monkeypatch.setattr(settings, "data_ingest_token", "sekret")
    monkeypatch.setattr(settings, "upload_retention_days", 7)

    app = FastAPI()
    app.state.trainer = object()
    app.state.registry = object()
    app.state.http_client = object()
    app.include_router(upload_module.router)
    upload_module.set_app(app)

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client

    monkeypatch.setattr(upload_module, "_app_ref", None)


def _csv_bytes() -> bytes:
    return _good_df().to_csv(index=False).encode("utf-8")


def _refresh_summary() -> dict[str, object]:
    return {
        "rows_inserted": 3,
        "rows_after": 3,
        "warehouses_after": 2,
        "routes_after": 2,
        "retention_cutoff": "2024-12-25T00:30:00",
        "retention_days": 7,
        "pruned_history_rows": 4,
        "cleared_forecasts": 1,
        "cleared_transport_requests": 2,
    }


def test_upload_dataset_passes_reference_ts_to_retrain(upload_client, monkeypatch) -> None:
    refresh_mock = AsyncMock(return_value=_refresh_summary())
    retrain_mock = AsyncMock(return_value=_FakeOutcome())
    pipeline_mock = AsyncMock(return_value={"status": "ok"})

    monkeypatch.setattr(upload_module.db, "refresh_snapshot", refresh_mock)
    monkeypatch.setattr(upload_module, "run_retrain_cycle", retrain_mock)
    monkeypatch.setattr(upload_module, "_trigger_pipeline", pipeline_mock)
    monkeypatch.setattr(upload_module, "record_last_retrain_result", lambda _: None)

    response = upload_client.post(
        "/upload-dataset",
        headers={"X-Ingest-Token": "sekret"},
        files={"file": ("sample.csv", _csv_bytes(), "text/csv")},
    )

    assert response.status_code == 200
    refresh_kwargs = refresh_mock.await_args.kwargs
    assert refresh_kwargs["retention_cutoff"] == datetime(2024, 12, 25, 0, 30, 0)
    assert refresh_kwargs["retention_days"] == 7
    retrain_kwargs = retrain_mock.await_args.kwargs
    assert retrain_kwargs["reference_ts"] == datetime(2025, 1, 1, 0, 30, 0)
    assert retrain_kwargs["training_window_days"] == 7
    assert pipeline_mock.await_count == 1


def test_upload_dataset_returns_409_before_refresh_when_lock_is_held(
    upload_client,
    monkeypatch,
) -> None:
    class _Locked:
        def locked(self) -> bool:
            return True

    refresh_mock = AsyncMock()
    monkeypatch.setattr(upload_module.db, "refresh_snapshot", refresh_mock)
    monkeypatch.setattr(upload_module, "get_retrain_lock", lambda: _Locked())

    response = upload_client.post(
        "/upload-dataset",
        headers={"X-Ingest-Token": "sekret"},
        files={"file": ("sample.csv", _csv_bytes(), "text/csv")},
    )

    assert response.status_code == 409
    assert refresh_mock.await_count == 0


def test_upload_dataset_honours_deprecated_auto_predict_alias(
    upload_client,
    monkeypatch,
) -> None:
    monkeypatch.setattr(upload_module.db, "refresh_snapshot", AsyncMock(return_value=_refresh_summary()))
    retrain_mock = AsyncMock(return_value=_FakeOutcome())
    monkeypatch.setattr(upload_module, "run_retrain_cycle", retrain_mock)
    monkeypatch.setattr(upload_module, "_trigger_pipeline", AsyncMock(return_value=None))
    monkeypatch.setattr(upload_module, "record_last_retrain_result", lambda _: None)

    response = upload_client.post(
        "/upload-dataset?auto_predict=false",
        headers={"X-Ingest-Token": "sekret"},
        files={"file": ("sample.csv", _csv_bytes(), "text/csv")},
    )

    assert response.status_code == 200
    assert retrain_mock.await_count == 0
    assert response.json()["retrain_result"] is None


def test_upload_dataset_prefers_auto_refresh_over_auto_predict(
    upload_client,
    monkeypatch,
) -> None:
    monkeypatch.setattr(upload_module.db, "refresh_snapshot", AsyncMock(return_value=_refresh_summary()))
    retrain_mock = AsyncMock(return_value=_FakeOutcome())
    monkeypatch.setattr(upload_module, "run_retrain_cycle", retrain_mock)
    monkeypatch.setattr(upload_module, "_trigger_pipeline", AsyncMock(return_value=None))
    monkeypatch.setattr(upload_module, "record_last_retrain_result", lambda _: None)

    response = upload_client.post(
        "/upload-dataset?auto_refresh=true&auto_predict=false",
        headers={"X-Ingest-Token": "sekret"},
        files={"file": ("sample.csv", _csv_bytes(), "text/csv")},
    )

    assert response.status_code == 200
    assert retrain_mock.await_count == 1
