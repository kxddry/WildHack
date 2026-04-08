import { proxyGet } from "@/lib/api";

// Proxy to dispatcher GET /api/v1/metrics/business (PRD §9.2).
export async function GET(request: Request) {
  const url = new URL(request.url);
  const params = new URLSearchParams();
  const from = url.searchParams.get("from");
  const to = url.searchParams.get("to");
  if (from) params.set("from", from);
  if (to) params.set("to", to);

  const query = params.toString();
  const path = query
    ? `/api/v1/metrics/business?${query}`
    : "/api/v1/metrics/business";
  return proxyGet("dispatcher", path);
}
