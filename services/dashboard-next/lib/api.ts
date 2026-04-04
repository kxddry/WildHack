const PREDICTION_URL =
  process.env.PREDICTION_SERVICE_URL || "http://localhost:8000";
const DISPATCHER_URL =
  process.env.DISPATCHER_SERVICE_URL || "http://localhost:8001";

async function fetchService(
  service: "prediction" | "dispatcher",
  path: string,
  options?: RequestInit
): Promise<Response> {
  const base = service === "prediction" ? PREDICTION_URL : DISPATCHER_URL;
  return fetch(`${base}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });
}

export async function proxyGet(
  service: "prediction" | "dispatcher",
  path: string
): Promise<Response> {
  try {
    const res = await fetchService(service, path);
    const data = await res.json();
    return Response.json(data, { status: res.status });
  } catch (error) {
    return Response.json(
      { error: error instanceof Error ? error.message : "Service unavailable" },
      { status: 502 }
    );
  }
}

export async function proxyPost(
  service: "prediction" | "dispatcher",
  path: string,
  body: unknown
): Promise<Response> {
  try {
    const res = await fetchService(service, path, {
      method: "POST",
      body: JSON.stringify(body),
    });
    const data = await res.json();
    return Response.json(data, { status: res.status });
  } catch (error) {
    return Response.json(
      { error: error instanceof Error ? error.message : "Service unavailable" },
      { status: 502 }
    );
  }
}
