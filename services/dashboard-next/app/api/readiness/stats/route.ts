import { proxyGet } from "@/lib/api";

/**
 * GET /api/readiness/stats
 *
 * Proxies to retraining-service's /api/v1/readiness/table-counts endpoint,
 * which returns the same flat {warehouses, routes, route_status_history,
 * forecasts, transport_requests} shape the old /api/db/stats endpoint did.
 * The dashboard readiness page consumes this map directly.
 */
export async function GET() {
  return proxyGet("retraining", "/api/v1/readiness/table-counts");
}
