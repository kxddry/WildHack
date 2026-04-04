import { proxyGet } from "@/lib/api";

export async function GET(
  _request: Request,
  context: { params: Promise<{ service: string }> }
) {
  const { service } = await context.params;
  if (service !== "prediction" && service !== "dispatcher") {
    return Response.json({ error: `Unknown service: ${service}` }, { status: 400 });
  }
  return proxyGet(service, "/health");
}
