import { NextRequest } from "next/server";
import { proxyGet } from "@/lib/api";

/**
 * GET /api/status-history?route_id=&limit=
 *
 * Proxies to prediction-service's /api/v1/routes/{route_id}/status-history
 * endpoint. Keeping the flat /api/status-history shape preserves the
 * query string the old /api/db/status-history endpoint exposed, so the
 * quality page needs no URL migration.
 */
export async function GET(request: NextRequest) {
  const { searchParams } = request.nextUrl;
  const routeId = searchParams.get("route_id");
  const limit = searchParams.get("limit") ?? "200";

  if (!routeId) {
    return Response.json({ error: "route_id is required" }, { status: 400 });
  }

  const qs = new URLSearchParams({ limit });
  return proxyGet(
    "prediction",
    `/api/v1/routes/${encodeURIComponent(routeId)}/status-history?${qs.toString()}`
  );
}
