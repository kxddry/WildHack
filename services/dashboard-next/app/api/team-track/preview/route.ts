import { NextRequest } from "next/server";
import {
  internalApiToken,
  missingInternalApiTokenResponse,
  serviceBaseUrl,
} from "@/lib/api";

export const runtime = "nodejs";
export const maxDuration = 300;

export async function POST(request: NextRequest) {
  const contentType = request.headers.get("content-type") ?? "";
  if (!contentType.startsWith("multipart/form-data")) {
    return Response.json(
      { error: "Expected multipart/form-data upload" },
      { status: 415 }
    );
  }

  const missing = missingInternalApiTokenResponse();
  if (missing) {
    return missing;
  }

  const modelVersion = request.nextUrl.searchParams.get("model_version");
  const qs = new URLSearchParams();
  if (modelVersion) {
    qs.set("model_version", modelVersion);
  }
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  const target = `${serviceBaseUrl("retraining")}/team-track/preview${suffix}`;

  try {
    const upstream = await fetch(target, {
      method: "POST",
      headers: {
        "Content-Type": contentType,
        "Content-Length": request.headers.get("content-length") ?? "",
        "X-Internal-Token": internalApiToken(),
      },
      body: request.body,
      // @ts-expect-error Node fetch supports duplex, libdom typing still lags.
      duplex: "half",
    });

    return new Response(upstream.body, {
      status: upstream.status,
      headers: {
        "Content-Type":
          upstream.headers.get("content-type") ?? "application/json",
      },
    });
  } catch (error) {
    return Response.json(
      {
        error:
          error instanceof Error
            ? `Upstream Team Track preview failed: ${error.message}`
            : "Upstream Team Track preview failed",
      },
      { status: 502 }
    );
  }
}
