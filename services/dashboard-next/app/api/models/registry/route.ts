import { proxyGet } from "@/lib/api";

export async function GET() {
  return proxyGet("retraining", "/api/v1/models/registry");
}
