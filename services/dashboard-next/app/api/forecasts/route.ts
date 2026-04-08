import { NextRequest } from "next/server";
import { proxyGet } from "@/lib/api";

/**
 * GET /api/forecasts?warehouse_id=...&limit=...
 *
 * Proxies to the prediction-service read API. The dashboard no longer
 * queries the forecasts table directly — prediction-service owns the
 * canonical JSON normalisation (legacy {ts,step,value} → {timestamp,
 * horizon_step, predicted_value}) and exposes it via /api/v1/forecasts.
 */
export async function GET(request: NextRequest) {
  const { searchParams } = request.nextUrl;
  const warehouseId = searchParams.get("warehouse_id");
  const limit = searchParams.get("limit") ?? "100";

  if (!warehouseId) {
    return Response.json(
      { error: "warehouse_id is required" },
      { status: 400 }
    );
  }

  const qs = new URLSearchParams({ warehouse_id: warehouseId, limit });
  return proxyGet("prediction", `/api/v1/forecasts?${qs.toString()}`);
}
