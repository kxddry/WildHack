from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # extra="ignore" is mandatory: every WildHack service shares the same
    # repo-root .env which contains keys for OTHER services (POSTGRES_USER,
    # DASHBOARD_PORT, etc). Without this, BaseSettings raises extra_forbidden
    # the moment a sibling service adds a new env var.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://wildhack:wildhack_dev@localhost:5432/wildhack"
    sync_database_url: str = "postgresql://wildhack:wildhack_dev@localhost:5432/wildhack"
    prediction_service_url: str = "http://prediction-service:8000"
    # Used by /upload-dataset to trigger an immediate prediction+dispatch cycle
    # after a successful ingest. Failures here are non-fatal — the next
    # scheduler tick will pick the new data up regardless.
    scheduler_service_url: str = "http://scheduler-service:8002"

    model_output_dir: str = "/app/models"
    canonical_model_filename: str = "model.pkl"
    canonical_metadata_filename: str = "model_metadata.json"
    canonical_static_aggs_filename: str = "static_aggs.json"
    canonical_fill_values_filename: str = "fill_values.json"
    training_window_days: int = 30
    history_window: int = 288
    forecast_steps: int = 10
    step_interval_minutes: int = 30
    # Upload flow retention window: data older than this many days (measured
    # from max(upload.timestamp)) is pruned in the same transaction as the
    # ingest. The training window for the force-promote retrain that follows
    # uses the same value by design — the two must stay in sync so we train
    # on exactly what the dispatcher will see.
    upload_retention_days: int = 7
    min_training_rows: int = 1000

    # LightGBM hyperparameters
    n_estimators: int = 5000
    learning_rate: float = 0.025
    num_leaves: int = 63
    max_depth: int = 9
    min_child_samples: int = 80
    subsample: float = 0.8
    colsample_bytree: float = 0.75
    reg_alpha: float = 0.5
    reg_lambda: float = 8.0
    early_stopping_rounds: int = 100

    # Shared internal secret. Required when retraining-service calls
    # prediction-service control routes (/model/reload-features,
    # /model/shadow/load, /model/shadow/promote) and scheduler control routes
    # (/pipeline/trigger). Dashboard still uses DATA_INGEST_TOKEN for the
    # separate upload path — see upload.py.
    internal_api_token: str = ""

    # Shared-secret auth for POST /upload-dataset. Matches the value injected
    # by the dashboard proxy's X-Ingest-Token header. Empty → endpoint fails
    # closed.
    data_ingest_token: str = ""


settings = Settings()
