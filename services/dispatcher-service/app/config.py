from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://wildhack:wildhack_dev@localhost:5432/wildhack"
    prediction_service_url: str = "http://prediction-service:8000"
    truck_capacity: int = 33
    buffer_pct: float = 0.10
    min_trucks: int = 1

    class Config:
        env_file = ".env"


settings = Settings()
