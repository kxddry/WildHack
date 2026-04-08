import { NextRequest } from "next/server";
import { proxyGet } from "@/lib/api";

/**
 * GET /api/transport-requests?warehouse_id=&status=&limit=
 *
 * Proxies to the dispatcher service's /api/v1/transport-requests/recent
 * endpoint. Distinct from the PRD /api/v1/transport-requests contract
 * which requires office_id + from + to — this proxy is the dashboard's
 * "recent rows" list view and takes a different parameter shape.
 */
export async function GET(request: NextRequest) {
  const { searchParams } = request.nextUrl;
  const warehouseId = searchParams.get("warehouse_id");
  const status = searchParams.get("status");
  const limit = searchParams.get("limit") ?? "50";

  const upstream = new URLSearchParams({ limit });
  if (warehouseId) upstream.set("warehouse_id", warehouseId);
  if (status) upstream.set("status", status);

  return proxyGet(
    "dispatcher",
    `/api/v1/transport-requests/recent?${upstream.toString()}`
  );
}
