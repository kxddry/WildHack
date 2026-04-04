import { proxyGet } from "@/lib/api";

export async function GET() {
  return proxyGet("prediction", "/model/info");
}
