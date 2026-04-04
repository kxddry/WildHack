import { query } from "@/lib/db";
import { NextRequest } from "next/server";

interface ForecastRow {
  id: number;
  route_id: string;
  warehouse_id: string;
  anchor_ts: string;
  forecasts: unknown;
  model_version: string;
  created_at: string;
}

interface RawStep {
  timestamp?: string;
  ts?: string;
  predicted_value?: number;
  value?: number;
  horizon_step?: number;
  step?: number;
}

interface NormalizedStep {
  horizon_step: number;
  timestamp: string;
  predicted_value: number;
}

/** Normalize forecast steps from legacy {ts, step, value} to {timestamp, horizon_step, predicted_value}. */
function normalizeSteps(raw: unknown): NormalizedStep[] {
  if (!Array.isArray(raw)) return [];
  return raw.map((s: RawStep) => ({
    horizon_step: s.horizon_step ?? s.step ?? 0,
    timestamp: s.timestamp ?? s.ts ?? "",
    predicted_value: s.predicted_value ?? s.value ?? 0,
  }));
}

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = request.nextUrl;
    const warehouseId = searchParams.get("warehouse_id");
    const rawLimit = parseInt(searchParams.get("limit") ?? "100", 10);
    const limit = Number.isNaN(rawLimit) ? 100 : Math.min(Math.max(1, rawLimit), 1000);

    if (!warehouseId) {
      return Response.json({ error: "warehouse_id is required" }, { status: 400 });
    }

    const rows = await query<ForecastRow>(
      `SELECT id, route_id, warehouse_id, anchor_ts, forecasts, model_version, created_at
       FROM forecasts
       WHERE warehouse_id = $1
       ORDER BY created_at DESC
       LIMIT $2`,
      [warehouseId, limit]
    );

    const normalized = rows.map((row) => ({
      ...row,
      forecasts: normalizeSteps(row.forecasts),
    }));

    return Response.json({ forecasts: normalized });
  } catch (error) {
    return Response.json(
      { error: error instanceof Error ? error.message : "Internal server error" },
      { status: 500 }
    );
  }
}
