import { query } from "@/lib/db";
import { NextRequest } from "next/server";

interface WarehouseRow {
  warehouse_id: string;
  name: string;
  route_count: number;
  latest_forecast_at: string | null;
  upcoming_trucks: number;
}

export async function GET(_request: NextRequest) {
  try {
    const rows = await query<WarehouseRow>(
      `SELECT
         w.warehouse_id,
         w.name,
         COALESCE(r.route_count, 0) as route_count,
         f.latest_forecast_at,
         COALESCE(t.upcoming_trucks, 0) as upcoming_trucks
       FROM warehouses w
       LEFT JOIN (
         SELECT warehouse_id, COUNT(*) as route_count
         FROM routes GROUP BY warehouse_id
       ) r ON w.warehouse_id = r.warehouse_id
       LEFT JOIN (
         SELECT warehouse_id, MAX(created_at) as latest_forecast_at
         FROM forecasts GROUP BY warehouse_id
       ) f ON w.warehouse_id = f.warehouse_id
       LEFT JOIN (
         SELECT warehouse_id, SUM(trucks_needed) as upcoming_trucks
         FROM transport_requests
         WHERE status IN ('planned', 'dispatched')
         GROUP BY warehouse_id
       ) t ON w.warehouse_id = t.warehouse_id
       ORDER BY w.warehouse_id`
    );

    return Response.json({ warehouses: rows });
  } catch (error) {
    return Response.json(
      { error: error instanceof Error ? error.message : "Internal server error" },
      { status: 500 }
    );
  }
}
