from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env")

    database_url: str = "postgresql+asyncpg://wildhack:wildhack_dev@localhost:5432/wildhack"
    sync_database_url: str = "postgresql://wildhack:wildhack_dev@localhost:5432/wildhack"
    prediction_service_url: str = "http://prediction-service:8000"

    model_output_dir: str = "/app/models"
    canonical_model_filename: str = "model.pkl"
    training_window_days: int = 7
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


settings = Settings()
