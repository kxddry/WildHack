-- WildHack Automated Transport Dispatching Service — Database Schema

-- Route status history: stores last 288+ observations per route for inference-time feature engineering
-- CRITICAL: Without this table, prediction-service cannot compute lag/rolling features
CREATE TABLE route_status_history (
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

CREATE INDEX idx_status_history_route_ts ON route_status_history (route_id, timestamp DESC);
CREATE INDEX idx_status_history_warehouse ON route_status_history (warehouse_id);

-- Forecasts table: stores prediction history (JSONB for simplicity)
CREATE TABLE forecasts (
    id              BIGSERIAL PRIMARY KEY,
    route_id        INTEGER NOT NULL,
    warehouse_id    INTEGER NOT NULL,
    anchor_ts       TIMESTAMP NOT NULL,
    forecasts       JSONB NOT NULL,
    model_version   VARCHAR(64) NOT NULL DEFAULT 'v1',
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_forecasts_warehouse_ts ON forecasts (warehouse_id, anchor_ts);
CREATE INDEX idx_forecasts_route_ts ON forecasts (route_id, anchor_ts);
CREATE INDEX idx_forecasts_created ON forecasts (created_at);

-- Transport requests table: dispatch decisions
CREATE TABLE transport_requests (
    id              BIGSERIAL PRIMARY KEY,
    warehouse_id    INTEGER NOT NULL,
    time_slot_start TIMESTAMP NOT NULL,
    time_slot_end   TIMESTAMP NOT NULL,
    total_containers DOUBLE PRECISION NOT NULL,
    truck_capacity  INTEGER NOT NULL,
    buffer_pct      DOUBLE PRECISION NOT NULL,
    trucks_needed   INTEGER NOT NULL,
    status          VARCHAR(32) NOT NULL DEFAULT 'planned',
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_requests_warehouse_slot ON transport_requests (warehouse_id, time_slot_start);
CREATE INDEX idx_requests_status ON transport_requests (status);

-- Model metadata table (includes known categorical values for inference)
CREATE TABLE model_metadata (
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
CREATE TABLE warehouses (
    warehouse_id    INTEGER PRIMARY KEY,
    route_count     INTEGER NOT NULL DEFAULT 0,
    first_seen      TIMESTAMP,
    last_seen       TIMESTAMP
);
