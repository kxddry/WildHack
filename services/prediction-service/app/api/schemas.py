from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PredictRequest(BaseModel):
    route_id: int
    timestamp: datetime
    status_1: float
    status_2: float
    status_3: float
    status_4: float
    status_5: float
    status_6: float
    status_7: float
    status_8: float


class ForecastStep(BaseModel):
    horizon_step: int
    timestamp: datetime
    predicted_value: float


class PredictResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    route_id: int
    warehouse_id: int
    anchor_timestamp: datetime
    forecasts: list[ForecastStep]
    model_version: str
    shadow_forecasts: list[ForecastStep] | None = None


class BatchPredictRequest(BaseModel):
    predictions: list[PredictRequest]


class BatchPredictResponse(BaseModel):
    results: list[PredictResponse]
    total: int
    processing_time_ms: float


class HealthResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    status: str
    model_loaded: bool
    database_connected: bool
    uptime_seconds: float


class ModelInfoResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    model_version: str
    model_type: str
    objective: str
    cv_score: float | None = None
    feature_count: int
    feature_names: list[str] | None = None
    training_date: str | None = None
    forecast_horizon: int = Field(description="Number of forecast steps")
    step_interval_minutes: int = Field(description="Minutes between each step")
    submodels: dict[str, dict] | None = None
