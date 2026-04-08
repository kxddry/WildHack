import { proxyGet } from "@/lib/api";

/**
 * GET /api/warehouses
 *
 * Thin BFF proxy to the dispatcher service's /api/v1/warehouses endpoint.
 * The dashboard no longer holds a Postgres connection; all reads go through
 * the service that owns the underlying table.
 */
export async function GET() {
  return proxyGet("dispatcher", "/api/v1/warehouses");
}
