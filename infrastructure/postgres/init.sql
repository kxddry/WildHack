-- WildHack Automated Transport Dispatching Service — Database Schema

-- Route status history: stores last 288+ observations per route for inference-time feature engineering
-- CRITICAL: Without this table, prediction-service cannot compute lag/rolling features
CREATE TABLE IF NOT EXISTS route_status_history (
    id              BIGSERIAL PRIMARY KEY,
    route_id        INTEGER NOT NULL,
    warehouse_id    INTEGER NOT NULL,
    timestamp       TIMESTAMP NOT NULL,
    status_1        DOUBLE PRECISION NOT NULL DEFAULT 0,
    status_2        DOUBLE PRECISION NOT NULL DEFAULT 0,
    status_3        DOUBLE PRECISION NOT NULL DEFAULT 0,
    status_4        DOUBLE PRECISION NOT NULL DEFAULT 0,
    status_5        DOUBLE PRECISION NOT NULL DEFAULT 0,
    status_6        DOUBLE PRECISION NOT NULL DEFAULT 0,
    status_7        DOUBLE PRECISION NOT NULL DEFAULT 0,
    status_8        DOUBLE PRECISION NOT NULL DEFAULT 0,
    target_2h       DOUBLE PRECISION,
    UNIQUE (route_id, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_status_history_route_ts ON route_status_history (route_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_status_history_warehouse ON route_status_history (warehouse_id);

-- Forecasts table: stores prediction history (JSONB for simplicity)
CREATE TABLE IF NOT EXISTS forecasts (
    id              BIGSERIAL PRIMARY KEY,
    route_id        INTEGER NOT NULL,
    warehouse_id    INTEGER NOT NULL,
    anchor_ts       TIMESTAMP NOT NULL,
    forecasts       JSONB NOT NULL,
    model_version   VARCHAR(64) NOT NULL DEFAULT 'v1',
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_forecasts_warehouse_ts ON forecasts (warehouse_id, anchor_ts);
CREATE INDEX IF NOT EXISTS idx_forecasts_route_ts ON forecasts (route_id, anchor_ts);
CREATE INDEX IF NOT EXISTS idx_forecasts_created ON forecasts (created_at);

-- Transport requests table: dispatch decisions
CREATE TABLE IF NOT EXISTS transport_requests (
    id              BIGSERIAL PRIMARY KEY,
    warehouse_id    INTEGER NOT NULL,
    time_slot_start TIMESTAMP NOT NULL,
    time_slot_end   TIMESTAMP NOT NULL,
    total_containers DOUBLE PRECISION NOT NULL,
    truck_capacity  INTEGER NOT NULL,
    buffer_pct      DOUBLE PRECISION NOT NULL,
    trucks_needed   INTEGER NOT NULL,
    calculation     TEXT,
    status          VARCHAR(32) NOT NULL DEFAULT 'planned'
                    CHECK (status IN ('planned', 'dispatched', 'completed', 'cancelled')),
    actual_vehicles INTEGER,
    actual_units    DOUBLE PRECISION,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW(),
    UNIQUE (warehouse_id, time_slot_start, time_slot_end)
);

CREATE INDEX IF NOT EXISTS idx_requests_warehouse_slot ON transport_requests (warehouse_id, time_slot_start);
CREATE INDEX IF NOT EXISTS idx_requests_status ON transport_requests (status);
CREATE INDEX IF NOT EXISTS idx_requests_actuals
    ON transport_requests (warehouse_id, time_slot_start)
    WHERE actual_vehicles IS NOT NULL;

-- Model metadata table (includes known categorical values for inference)
CREATE TABLE IF NOT EXISTS model_metadata (
    id              SERIAL PRIMARY KEY,
    model_version   VARCHAR(64) NOT NULL UNIQUE,
    model_path      VARCHAR(256) NOT NULL,
    cv_score        DOUBLE PRECISION,
    training_date   TIMESTAMP,
    feature_count   INTEGER,
    config_json     JSONB,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Warehouse reference table (populated from training data)
CREATE TABLE IF NOT EXISTS warehouses (
    warehouse_id    INTEGER PRIMARY KEY,
    name            VARCHAR(128),
    route_count     INTEGER NOT NULL DEFAULT 0,
    first_seen      TIMESTAMP,
    last_seen       TIMESTAMP
);

-- Routes table: maps routes to warehouses
CREATE TABLE IF NOT EXISTS routes (
    route_id        INTEGER PRIMARY KEY,
    warehouse_id    INTEGER NOT NULL REFERENCES warehouses(warehouse_id)
);

CREATE INDEX IF NOT EXISTS idx_routes_warehouse ON routes (warehouse_id);

-- Pipeline runs table: scheduler service audit log
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id              BIGSERIAL PRIMARY KEY,
    run_type        VARCHAR(64) NOT NULL DEFAULT 'prediction_cycle',
    status          VARCHAR(32) NOT NULL DEFAULT 'unknown',
    started_at      TIMESTAMP NOT NULL,
    completed_at    TIMESTAMP,
    details         JSONB,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_started ON pipeline_runs (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_type ON pipeline_runs (run_type);

-- Prediction quality tracking: stores quality metrics computed by scheduler
CREATE TABLE IF NOT EXISTS prediction_quality (
    id              BIGSERIAL PRIMARY KEY,
    checked_at      TIMESTAMP NOT NULL,
    wape            DOUBLE PRECISION NOT NULL,
    rbias           DOUBLE PRECISION NOT NULL,
    combined_score  DOUBLE PRECISION NOT NULL,
    n_pairs         INTEGER NOT NULL,
    alert_triggered BOOLEAN NOT NULL DEFAULT FALSE,
    details_json    JSONB,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_quality_checked ON prediction_quality (checked_at DESC);

-- Retrain history: tracks model retraining runs
CREATE TABLE IF NOT EXISTS retrain_history (
    id              BIGSERIAL PRIMARY KEY,
    started_at      TIMESTAMP NOT NULL,
    completed_at    TIMESTAMP,
    status          VARCHAR(32) NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'success', 'failed', 'skipped')),
    training_rows   INTEGER,
    champion_score  DOUBLE PRECISION,
    challenger_score DOUBLE PRECISION,
    promoted        BOOLEAN NOT NULL DEFAULT FALSE,
    new_model_version VARCHAR(64),
    details_json    JSONB,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_retrain_status ON retrain_history (status, started_at DESC);
