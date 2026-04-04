import { query } from "@/lib/db";
import { NextRequest } from "next/server";

interface TransportRequestRow {
  [key: string]: unknown;
}

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = request.nextUrl;
    const warehouseId = searchParams.get("warehouse_id");
    const status = searchParams.get("status");
    const rawLimit = parseInt(searchParams.get("limit") ?? "50", 10);
    const limit = Number.isNaN(rawLimit) ? 50 : Math.min(Math.max(1, rawLimit), 1000);

    const conditions: string[] = [];
    const params: unknown[] = [];

    if (warehouseId) {
      params.push(warehouseId);
      conditions.push(`warehouse_id = $${params.length}`);
    }

    if (status) {
      params.push(status);
      conditions.push(`status = $${params.length}`);
    }

    const whereClause =
      conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";

    params.push(limit);
    const sql = `SELECT * FROM transport_requests ${whereClause} ORDER BY time_slot_start DESC LIMIT $${params.length}`;

    const rows = await query<TransportRequestRow>(sql, params);

    return Response.json({ requests: rows });
  } catch (error) {
    return Response.json(
      { error: error instanceof Error ? error.message : "Internal server error" },
      { status: 500 }
    );
  }
}
