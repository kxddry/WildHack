from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env")

    database_url: str = "postgresql+asyncpg://wildhack:wildhack_dev@localhost:5432/wildhack"
    prediction_service_url: str = "http://prediction-service:8000"
    dispatcher_service_url: str = "http://dispatcher-service:8001"
    retraining_service_url: str = "http://retraining-service:8003"

    # Schedule intervals in minutes
    prediction_interval_minutes: int = 30
    quality_check_interval_minutes: int = 60

    # Pipeline config
    batch_size: int = 50  # routes per batch call
    forecast_hours_ahead: int = 6  # hours ahead for dispatch window

    # Shadow model auto-promotion: promote after this many consecutive wins
    shadow_promote_streak_threshold: int = 3


settings = Settings()
