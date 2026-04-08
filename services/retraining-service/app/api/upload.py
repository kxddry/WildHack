"""Dataset upload endpoint — synchronous full-refresh workflow.

Contract
--------
``POST /upload-dataset`` treats the uploaded file as the *authoritative*
rolling snapshot for the current system. A single request executes the
entire refresh sequence synchronously:

1. Stream and validate the file (extension whitelist, decompression-bomb
   check, schema required columns).
2. Inside a single Postgres transaction:
   a. Compute ``upload_max_ts`` and the retention cutoff from the uploaded
      data before any DB mutation.
   b. Clear the live snapshot tables so the upload becomes authoritative
      instead of being merged with pre-existing history.
   c. Insert only the retained upload rows
      (``timestamp >= upload_max_ts - retention_days``).
   d. Rebuild ``routes`` and warehouse aggregates from the retained
      history, then clear ``forecasts`` and ``transport_requests``.
3. Retrain on the retained window anchored to ``upload_max_ts``
   (``training_window_days`` is aligned with ``upload_retention_days``).
4. Force-promote the new model to primary (shadow-load → promote).
5. Trigger the scheduler pipeline so fresh forecasts + dispatch requests
   appear immediately.

Failures at steps 3-5 are non-fatal to the *data* ingest — the
transaction in step 2 has already committed. The response surfaces the
first failure so operators can investigate.

Security model
--------------
* Shared-secret auth via the ``X-Ingest-Token`` header. Browsers can't
  forge this header on a cross-origin multipart POST (CORS-simple request
  + custom header → preflight, which we do not allow upstream).
* Whitelisted extensions only — suffix injection is impossible.
* Decompression-bomb protection:
    - parquet: pyarrow metadata cell-count check before full read.
    - csv: raw line count before parsing.
* 200 MB streaming cap on the temp file.
* All database writes in step 2 are in a single transaction so partial
  failures never leave a split-brain state.
* Endpoint shares the retrain lock with ``POST /retrain`` — uploads and
  retrains are serialized before any snapshot mutation so the retained
  dataset, active model, and derived tables stay aligned.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import tempfile
import time
from datetime import datetime, timedelta
from typing import Any, Iterator

import httpx
import pandas as pd
from fastapi import APIRouter, File, Header, HTTPException, Query, UploadFile

from app.api.routes import get_retrain_lock, record_last_retrain_result
from app.config import settings
from app.core.orchestration import PromotionPolicy, run_retrain_cycle
from app.storage import postgres as db

logger = logging.getLogger(__name__)

router = APIRouter()

# Hard limit for upload size. 200 MB covers a year of half-hourly observations
# for a few hundred routes — enough for the demo dataset shipped with the repo
# without exposing the service to accidental DoS via huge files.
MAX_UPLOAD_BYTES = 200 * 1024 * 1024

# Cell-count budget enforced after metadata inspection. 50M cells ~= 4M rows
# at 12 cols, roughly matching the largest realistic training set (4.3M rows).
# Decompression bombs blow through this limit long before they OOM the
# service, so we reject them here.
MAX_CELLS = 50 * 1_000_000

# Row budget for CSV uploads (which do not carry metadata like parquet).
# Derived from MAX_CELLS / len(REQUIRED_COLUMNS).
MAX_CSV_ROWS = MAX_CELLS // 12

# Chunk size for streaming row dicts into the DB. Small enough that a single
# executemany batch stays under asyncpg's parameter-count ceiling and keeps
# peak memory bounded even on huge uploads.
DB_CHUNK_ROWS = 5000

REQUIRED_COLUMNS: tuple[str, ...] = (
    "office_from_id",
    "route_id",
    "timestamp",
    "status_1",
    "status_2",
    "status_3",
    "status_4",
    "status_5",
    "status_6",
    "status_7",
    "status_8",
    "target_2h",
)

STATUS_COLUMNS: tuple[str, ...] = tuple(f"status_{i}" for i in range(1, 9))

# Whitelist of accepted extensions. ANY filename not ending in one of these
# is rejected up-front — no splitext() on untrusted input, no NamedTemporaryFile
# suffix injection vector.
ALLOWED_EXTENSIONS: tuple[str, ...] = (".parquet", ".pq", ".csv", ".tsv", ".txt")


def _pick_extension(filename: str | None) -> str:
    """Return the whitelisted extension for `filename` or raise 415.

    Only considers lowercased endings. Any filename that doesn't match is
    rejected before we touch the filesystem.
    """
    name = (filename or "").lower()
    for ext in ALLOWED_EXTENSIONS:
        if name.endswith(ext):
            return ext
    raise HTTPException(
        status_code=415,
        detail=(
            f"Unsupported file extension. Accepted: {', '.join(ALLOWED_EXTENSIONS)}"
        ),
    )


def _check_parquet_budget(path: str) -> None:
    """Reject parquet files that would exceed the cell budget after decompression."""
    try:
        import pyarrow.parquet as pq  # imported lazily so CSV-only deploys still work
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail="Parquet support unavailable — server missing pyarrow",
        ) from exc

    try:
        meta = pq.ParquetFile(path).metadata
    except Exception as exc:
        raise HTTPException(
            status_code=422, detail=f"Invalid parquet file: {exc}"
        ) from exc

    cells = int(meta.num_rows) * int(meta.num_columns)
    if cells > MAX_CELLS:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Parquet file too large after decompression: "
                f"{meta.num_rows} rows × {meta.num_columns} cols "
                f"(max {MAX_CELLS} cells allowed)"
            ),
        )


def _check_csv_budget(path: str) -> None:
    """Reject CSV files whose raw line count would blow the cell budget."""
    rows = 0
    with open(path, "rb") as fh:
        for _ in fh:
            rows += 1
            if rows > MAX_CSV_ROWS:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"CSV file too large: more than {MAX_CSV_ROWS} rows "
                        "(row budget enforced before parsing)"
                    ),
                )


def _read_dataframe(path: str, ext: str) -> pd.DataFrame:
    """Parse the uploaded file based on its whitelisted extension."""
    if ext in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    sep = "\t" if ext == ".tsv" else ","
    return pd.read_csv(path, sep=sep)


def _validate_schema(df: pd.DataFrame) -> None:
    """Reject the upload if required columns are missing or empty."""
    if df.empty:
        raise HTTPException(status_code=422, detail="Uploaded file is empty")

    if {
        "id",
        "route_id",
        "timestamp",
    }.issubset(set(df.columns)) and not any(
        col in df.columns for col in STATUS_COLUMNS + ("target_2h", "office_from_id")
    ):
        raise HTTPException(
            status_code=422,
            detail="This looks like the Team Track test template. Upload it in the Team Track Test tab.",
        )

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Missing required columns: {missing}. "
                f"Expected schema: {list(REQUIRED_COLUMNS)}"
            ),
        )


def _coerce_types(df: pd.DataFrame) -> pd.DataFrame:
    """Best-effort cast to the column types Postgres expects."""
    try:
        out = df[list(REQUIRED_COLUMNS)]
        timestamps = pd.to_datetime(out["timestamp"], errors="raise")
        if getattr(timestamps.dt, "tz", None) is not None:
            timestamps = timestamps.dt.tz_localize(None)
        out = out.assign(
            office_from_id=out["office_from_id"].astype("int64"),
            route_id=out["route_id"].astype("int64"),
            timestamp=timestamps,
            target_2h=pd.to_numeric(out["target_2h"], errors="coerce"),
        )
        for col in STATUS_COLUMNS:
            out[col] = pd.to_numeric(out[col], errors="raise").fillna(0)
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(
            status_code=422, detail=f"Failed to coerce column types: {exc}"
        ) from exc
    return out


def _iter_history_chunks(
    df: pd.DataFrame,
    chunk_size: int = DB_CHUNK_ROWS,
    min_timestamp: datetime | None = None,
) -> Iterator[list[dict[str, Any]]]:
    """Yield lists of history row dicts in bounded chunks.

    Streaming matters because a 4M-row DataFrame converted to a single
    Python ``list[dict]`` is ~2–3 GB of object overhead. Chunking keeps the
    live dict footprint at ``chunk_size`` rows.
    """
    cutoff = pd.Timestamp(min_timestamp) if min_timestamp is not None else None
    for start in range(0, len(df), chunk_size):
        slice_df = df.iloc[start : start + chunk_size]
        if cutoff is not None:
            slice_df = slice_df[slice_df["timestamp"] >= cutoff]
        if slice_df.empty:
            continue
        yield [
            {
                "route_id": int(r.route_id),
                "warehouse_id": int(r.office_from_id),
                "timestamp": r.timestamp.to_pydatetime(),
                "status_1": float(r.status_1),
                "status_2": float(r.status_2),
                "status_3": float(r.status_3),
                "status_4": float(r.status_4),
                "status_5": float(r.status_5),
                "status_6": float(r.status_6),
                "status_7": float(r.status_7),
                "status_8": float(r.status_8),
                "target_2h": (
                    None if pd.isna(r.target_2h) else float(r.target_2h)
                ),
            }
            for r in slice_df.itertuples(index=False)
        ]


def _authenticate(provided_token: str | None) -> None:
    """Validate the shared-secret header against ``settings.data_ingest_token``.

    Moved off ``os.getenv`` so a future env var rename doesn't silently
    break auth — the settings object is the single source of truth for
    every secret this service reads. Fails closed when the setting is
    empty so a mis-configured deployment can never serve the endpoint.
    """
    expected = (settings.data_ingest_token or "").strip()
    if not expected:
        logger.error("DATA_INGEST_TOKEN is unset — rejecting upload")
        raise HTTPException(
            status_code=503,
            detail="Upload service is not configured (DATA_INGEST_TOKEN missing)",
        )
    if not provided_token or not secrets.compare_digest(provided_token, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing ingest token")


def _as_naive_datetime(value: datetime) -> datetime:
    """Strip timezone info to match Postgres TIMESTAMP semantics."""
    if hasattr(value, "tzinfo") and value.tzinfo is not None:
        return value.replace(tzinfo=None)
    return value


def _derive_upload_window(
    df: pd.DataFrame,
    retention_days: int,
) -> tuple[datetime, datetime]:
    """Return the upload max timestamp and the inclusive retention cutoff."""
    max_ts = df["timestamp"].max()
    if pd.isna(max_ts):
        raise HTTPException(
            status_code=422,
            detail="Uploaded file contains no valid timestamps",
        )
    if isinstance(max_ts, pd.Timestamp):
        max_ts = max_ts.to_pydatetime()
    upload_max_ts = _as_naive_datetime(max_ts)
    return upload_max_ts, upload_max_ts - timedelta(days=retention_days)


def _resolve_auto_refresh(
    auto_refresh: bool | None,
    auto_predict: bool | None,
) -> bool:
    """Resolve the refresh flag, preferring the canonical query param."""
    if auto_refresh is not None:
        return auto_refresh
    if auto_predict is not None:
        return auto_predict
    return True


async def _trigger_pipeline(http_client: httpx.AsyncClient) -> dict[str, Any] | None:
    """Call scheduler /pipeline/trigger with the internal token.

    Best-effort: a failure here does NOT fail the upload — the data is
    already in Postgres and the force-promoted model is live. The next
    scheduler tick will pick the new data up regardless.
    """
    url = settings.scheduler_service_url
    token = (settings.internal_api_token or "").strip()
    headers = {"X-Internal-Token": token} if token else {}
    try:
        r = await http_client.post(
            f"{url}/pipeline/trigger", headers=headers, timeout=120.0
        )
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        logger.warning("Pipeline trigger after upload failed: %s", exc)
        return None


@router.post("/upload-dataset")
async def upload_dataset(
    file: UploadFile = File(...),
    auto_refresh: bool | None = Query(
        None,
        description=(
            "Run the full refresh workflow (retrain → force-promote → "
            "scheduler trigger) after ingest. Defaults to true — disable "
            "only for bulk backfills where you want to run the retrain "
            "manually."
        ),
    ),
    auto_predict: bool | None = Query(
        None,
        include_in_schema=False,
        description="Deprecated alias for auto_refresh.",
    ),
    x_ingest_token: str | None = Header(default=None, alias="X-Ingest-Token"),
) -> dict[str, Any]:
    """Ingest a user-supplied dataset and refresh the live snapshot."""
    _authenticate(x_ingest_token)

    started = time.monotonic()
    effective_auto_refresh = _resolve_auto_refresh(auto_refresh, auto_predict)
    ext = _pick_extension(file.filename)
    lock = get_retrain_lock()
    if lock.locked():
        raise HTTPException(
            status_code=409,
            detail="A retrain or upload refresh is already in progress",
        )

    retrain_result: dict[str, Any] | None = None
    pipeline_result: dict[str, Any] | None = None
    active_model_version: str | None = None

    async with lock:
        tmp_path: str | None = None
        try:
            # Stream-write to a temp file so we never hold the entire upload in RAM.
            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                tmp_path = tmp.name
                written = 0
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_UPLOAD_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail=(
                                f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB "
                                "stream limit"
                            ),
                        )
                    tmp.write(chunk)

            if written == 0:
                raise HTTPException(status_code=422, detail="Uploaded file is empty")

            if ext in {".parquet", ".pq"}:
                _check_parquet_budget(tmp_path)
            else:
                _check_csv_budget(tmp_path)

            loop = asyncio.get_running_loop()
            df_raw = await loop.run_in_executor(None, _read_dataframe, tmp_path, ext)
            _validate_schema(df_raw)
            df = await loop.run_in_executor(None, _coerce_types, df_raw)
            del df_raw

            upload_max_ts, retention_cutoff = _derive_upload_window(
                df,
                settings.upload_retention_days,
            )

            # Stage 1: authoritative snapshot replacement inside one DB transaction.
            ingest_summary = await db.refresh_snapshot(
                history_chunks=_iter_history_chunks(
                    df,
                    min_timestamp=retention_cutoff,
                ),
                retention_cutoff=retention_cutoff,
                retention_days=settings.upload_retention_days,
                rows_received=int(len(df)),
            )
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    logger.warning("Failed to clean up tmp upload at %s", tmp_path)

        if effective_auto_refresh:
            # This branch deliberately drops to the global app state because
            # upload.py is mounted separately and importing these handles at
            # module import time would create a cycle against routes.py.
            app = _current_app()
            trainer = app.state.trainer
            registry = app.state.registry
            http_client: httpx.AsyncClient = app.state.http_client

            try:
                outcome = await run_retrain_cycle(
                    trainer,
                    registry,
                    policy=PromotionPolicy.FORCE_PRIMARY,
                    training_window_days=settings.upload_retention_days,
                    reference_ts=upload_max_ts,
                )
                retrain_result = outcome.to_dict()
                record_last_retrain_result(retrain_result)
                active_model_version = outcome.version
            except ValueError as exc:
                logger.warning(
                    "Upload-triggered retrain skipped — insufficient data: %s", exc
                )
                retrain_result = {
                    "status": "skipped",
                    "reason": str(exc),
                }
            except Exception:
                logger.exception("Upload-triggered retrain failed")
                retrain_result = {"status": "failed"}

            # Stage 3: scheduler trigger only if retrain + promote succeeded.
            if (
                retrain_result
                and retrain_result.get("promotion_status") == "primary_promoted"
            ):
                pipeline_result = await _trigger_pipeline(http_client)

    elapsed = round(time.monotonic() - started, 2)
    logger.info(
        "Upload refresh complete: file=%s rows=%d pruned=%d retention_cutoff=%s elapsed=%.2fs",
        file.filename,
        int(len(df)),
        ingest_summary.get("pruned_history_rows", 0),
        ingest_summary.get("retention_cutoff"),
        elapsed,
    )

    return {
        "status": "ok",
        "filename": file.filename,
        "rows_received": int(len(df)),
        "rows_inserted": int(ingest_summary["rows_inserted"]),
        "rows_total": int(ingest_summary["rows_after"]),
        "warehouses": int(ingest_summary.get("warehouses_after", 0)),
        "routes": int(ingest_summary.get("routes_after", 0)),
        "elapsed_seconds": elapsed,
        "retention_cutoff": ingest_summary.get("retention_cutoff"),
        "retention_days": settings.upload_retention_days,
        "pruned_history_rows": ingest_summary.get("pruned_history_rows", 0),
        "cleared_forecasts": ingest_summary.get("cleared_forecasts", 0),
        "cleared_transport_requests": ingest_summary.get(
            "cleared_transport_requests", 0
        ),
        "active_model_version": active_model_version,
        "retrain_result": retrain_result,
        "pipeline_triggered": pipeline_result is not None,
        "pipeline_result": pipeline_result,
    }


# ---------------------------------------------------------------------------
# FastAPI app singleton access — set by main.lifespan so upload.py can
# reach the trainer/registry/http_client without circular imports.
# ---------------------------------------------------------------------------

_app_ref: Any = None


def set_app(app: Any) -> None:
    """Called from ``main.lifespan`` to publish the FastAPI app instance."""
    global _app_ref
    _app_ref = app


def _current_app() -> Any:
    if _app_ref is None:
        raise RuntimeError(
            "Upload router not initialised — app reference missing. "
            "Ensure set_app(app) is called from lifespan startup."
        )
    return _app_ref
