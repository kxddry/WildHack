export interface Warehouse {
  warehouse_id: number;
  name: string | null;
  route_count: number;
  latest_forecast_at: string | null;
  upcoming_trucks: number;
}

export interface ForecastStep {
  horizon_step: number;
  timestamp: string;
  predicted_value: number;
}

export interface Forecast {
  id: number;
  route_id: number;
  warehouse_id: number;
  anchor_ts: string;
  forecasts: ForecastStep[];
  model_version: string;
  created_at: string;
}

export interface TransportRequest {
  id: number;
  warehouse_id: number;
  time_slot_start: string;
  time_slot_end: string;
  total_containers: number;
  truck_capacity: number;
  buffer_pct: number;
  trucks_needed: number;
  calculation: string;
  status: "planned" | "dispatched" | "completed" | "cancelled";
  created_at: string;
}

export interface RouteStatusHistory {
  id: number;
  route_id: number;
  warehouse_id: number;
  timestamp: string;
  status_1: number;
  status_2: number;
  status_3: number;
  status_4: number;
  status_5: number;
  status_6: number;
  status_7: number;
  status_8: number;
  target_2h: number | null;
}

export interface ModelInfo {
  model_version: string;
  model_type: string;
  objective: string;
  cv_score: number | null;
  feature_count: number;
  training_date: string | null;
  forecast_horizon: number;
  step_interval_minutes: number;
}

export type CheckStatus = "pass" | "warn" | "fail";

export interface HealthCheck {
  name: string;
  status: CheckStatus;
  detail: string;
}
