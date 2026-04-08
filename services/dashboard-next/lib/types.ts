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

export interface ModelRegistryEntry {
  id: number;
  model_version: string;
  model_path: string;
  cv_score: number | null;
  training_date: string | null;
  feature_count: number;
  created_at: string;
  config_json: Record<string, unknown>;
  is_champion: boolean;
  evaluation_available: boolean;
}

export interface ModelRegistrySummary {
  models: ModelRegistryEntry[];
  champion_version: string | null;
  last_retrain: Record<string, unknown>;
}

export interface PipelineStatus {
  last_run: string | null;
  last_status: string;
  run_count: number;
}

export interface QualityStatus {
  last_check: string | null;
  last_metrics: Record<string, unknown>;
  active_alerts: number;
  shadow_win_streak: number;
  shadow_streak_version: string | null;
  promote_threshold: number;
}

export interface PipelineRun {
  id: number;
  run_type: string;
  status: string;
  started_at: string;
  completed_at: string | null;
  details: Record<string, unknown> | string | null;
}

export interface QualityAlert {
  type: string;
  value: number;
  threshold: number;
  message: string;
  timestamp: string;
}

export interface TeamTrackPreviewRow {
  id: number;
  route_id: number;
  timestamp: string;
  raw_forecast: number;
  y_pred: number;
}

export interface TeamTrackPreviewResponse {
  row_count: number;
  route_count: number;
  preview_count: number;
  model: {
    selected_version: string | null;
    resolved_version: string;
    source: string;
    model_path: string;
    static_aggs_path: string;
    fill_values_path: string;
    feature_count: number;
    evaluation_ready: boolean;
  };
  preview: TeamTrackPreviewRow[];
}

export type CheckStatus = "pass" | "warn" | "fail";

export interface HealthCheck {
  name: string;
  status: CheckStatus;
  detail: string;
}

// PRD §9.2 — business KPIs surfaced by GET /api/v1/metrics/business
export interface BusinessMetrics {
  order_accuracy: number;
  avg_truck_utilization: number;
  n_slots_evaluated: number;
  n_slots_total: number;
  truck_capacity: number;
  range_from: string | null;
  range_to: string | null;
  note: string | null;
}
