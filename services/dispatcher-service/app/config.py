from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env")

    database_url: str = "postgresql+asyncpg://wildhack:wildhack_dev@localhost:5432/wildhack"
    truck_capacity: int = 33
    buffer_pct: float = 0.10
    min_trucks: int = 1
    adaptive_buffer: bool = False  # When True, buffer scales with forecast uncertainty
    min_buffer_pct: float = 0.05
    max_buffer_pct: float = 0.25


settings = Settings()
