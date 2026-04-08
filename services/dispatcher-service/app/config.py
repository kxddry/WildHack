from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://wildhack:wildhack_dev@localhost:5432/wildhack"
    truck_capacity: int = 33
    buffer_pct: float = 0.10
    min_trucks: int = 1
    adaptive_buffer: bool = False  # When True, buffer scales with forecast uncertainty
    min_buffer_pct: float = 0.05
    max_buffer_pct: float = 0.25

    # Half-open slot width used when expanding forecast steps into dispatch
    # slots. Must match the upstream prediction-service step interval; we keep
    # it as a dedicated setting on the dispatcher so the two services can be
    # tuned independently during migration.
    step_interval_minutes: int = 30

    # Shared internal secret (reserved; dispatcher has no protected control
    # routes today, but we accept the env so compose can pass a single token
    # uniformly to every service).
    internal_api_token: str = ""


settings = Settings()
