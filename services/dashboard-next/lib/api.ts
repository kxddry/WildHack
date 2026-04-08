const PREDICTION_URL =
  process.env.PREDICTION_SERVICE_URL || "http://localhost:8000";
const DISPATCHER_URL =
  process.env.DISPATCHER_SERVICE_URL || "http://localhost:8001";
const SCHEDULER_URL =
  process.env.SCHEDULER_SERVICE_URL || "http://localhost:8002";
const RETRAINING_URL =
  process.env.RETRAINING_SERVICE_URL || "http://localhost:8003";

export type Service = "prediction" | "dispatcher" | "scheduler" | "retraining";

export function serviceBaseUrl(service: Service): string {
  switch (service) {
    case "prediction":
      return PREDICTION_URL;
    case "dispatcher":
      return DISPATCHER_URL;
    case "scheduler":
      return SCHEDULER_URL;
    case "retraining":
      return RETRAINING_URL;
  }
}

function withDefaultHeaders(options?: RequestInit): Headers {
  const headers = new Headers(options?.headers);
  if (options?.body != null && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  return headers;
}

async function fetchService(
  service: Service,
  path: string,
  options?: RequestInit
): Promise<Response> {
  const base = serviceBaseUrl(service);
  return fetch(`${base}${path}`, {
    ...options,
    headers: withDefaultHeaders(options),
  });
}

async function readPayload(res: Response): Promise<unknown> {
  const contentType = res.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    return res.json();
  }
  const text = await res.text();
  return text ? { detail: text } : {};
}

export async function proxyGet(
  service: Service,
  path: string
): Promise<Response> {
  try {
    const res = await fetchService(service, path);
    const data = await readPayload(res);
    return Response.json(data, { status: res.status });
  } catch (error) {
    return Response.json(
      { error: error instanceof Error ? error.message : "Service unavailable" },
      { status: 502 }
    );
  }
}

export async function proxyPost(
  service: Service,
  path: string,
  body: unknown,
  init?: RequestInit
): Promise<Response> {
  try {
    const res = await fetchService(service, path, {
      ...init,
      method: "POST",
      body: JSON.stringify(body),
    });
    const data = await readPayload(res);
    return Response.json(data, { status: res.status });
  } catch (error) {
    return Response.json(
      { error: error instanceof Error ? error.message : "Service unavailable" },
      { status: 502 }
    );
  }
}

export function internalApiToken(): string {
  return (process.env.INTERNAL_API_TOKEN ?? "").trim();
}

export function missingInternalApiTokenResponse(): Response | null {
  if (internalApiToken()) {
    return null;
  }
  return Response.json(
    {
      error:
        "INTERNAL_API_TOKEN is not configured on the dashboard. Set it in .env and restart.",
    },
    { status: 503 }
  );
}
