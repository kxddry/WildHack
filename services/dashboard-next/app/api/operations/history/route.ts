import { NextRequest } from "next/server";
import { proxyGet } from "@/lib/api";

export async function GET(request: NextRequest) {
  const limit = request.nextUrl.searchParams.get("limit") ?? "20";
  const qs = new URLSearchParams({ limit });
  return proxyGet("scheduler", `/pipeline/history?${qs.toString()}`);
}
