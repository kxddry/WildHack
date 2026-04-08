from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # extra="ignore" so sibling services' keys in the shared .env don't raise.
    # protected_namespaces muted because fields are prefixed with `model_`.
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        protected_namespaces=("settings_",),
    )

    database_url: str = "postgresql+asyncpg://wildhack:wildhack_dev@localhost:5432/wildhack"
    model_path: str = "models/model.pkl"
    # Legacy config label retained only as a mock/fallback version string.
    # Real runtime version now comes from ModelManager.runtime_version, which
    # prefers metadata.model_version, then the promoted artifact stem.
    model_version: str = "v1"
    history_window: int = 288  # 288 * 30min = 6 days of history
    forecast_steps: int = 10  # 10 steps * 30min = 5 hours ahead
    step_interval_minutes: int = 30
    static_aggs_path: str = "models/static_aggs.json"
    fill_values_path: str = "models/fill_values.json"

    # Shared internal secret — required on model control routes (/model/reload,
    # /model/reload-features, /model/shadow/*). Callers send it as
    # ``X-Internal-Token``. Empty string means the service is mis-configured
    # and the token dependency fails closed with 503.
    internal_api_token: str = ""

    # Local-dev synthetic fallback. When MOCK_MODE=1, missing model artifacts
    # cause the service to enable a deterministic mock predictor instead of
    # crashing at startup. When unset (default), the service fails fast — this
    # prevents silent data corruption in any environment that thinks it is
    # serving real predictions.
    mock_mode: bool = False


settings = Settings()
