import { query } from "@/lib/db";
import { NextRequest } from "next/server";

interface StatusHistoryRow {
  [key: string]: unknown;
}

export async function GET(request: NextRequest) {
  try {
    const { searchParams } = request.nextUrl;
    const routeId = searchParams.get("route_id");
    const rawLimit = parseInt(searchParams.get("limit") ?? "200", 10);
    const limit = Number.isNaN(rawLimit) ? 200 : Math.min(Math.max(1, rawLimit), 1000);

    if (!routeId) {
      return Response.json({ error: "route_id is required" }, { status: 400 });
    }

    const rows = await query<StatusHistoryRow>(
      `SELECT * FROM route_status_history
       WHERE route_id = $1
       ORDER BY timestamp ASC
       LIMIT $2`,
      [routeId, limit]
    );

    return Response.json({ history: rows });
  } catch (error) {
    return Response.json(
      { error: error instanceof Error ? error.message : "Internal server error" },
      { status: 500 }
    );
  }
}
