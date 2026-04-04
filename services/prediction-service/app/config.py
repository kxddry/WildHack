from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env", protected_namespaces=("settings_",))

    database_url: str = "postgresql+asyncpg://wildhack:wildhack_dev@localhost:5432/wildhack"
    model_path: str = "models/model.pkl"
    model_version: str = "v1"
    history_window: int = 288  # 288 * 30min = 6 days of history
    forecast_steps: int = 10  # 10 steps * 30min = 5 hours ahead
    step_interval_minutes: int = 30


settings = Settings()
