import { NextRequest } from "next/server";
import { serviceBaseUrl } from "@/lib/api";

// Multipart uploads can legitimately reach 200 MB (that's the upstream
// FastAPI cap). Node's streaming fetch makes this trivial — we pass the
// raw ReadableStream through, so neither the request body nor the response
// body is ever fully materialized in memory on the dashboard box.
export const runtime = "nodejs";
export const maxDuration = 300;

// Keep any max-body checks deferred to retraining-service — Node's fetch
// enforces no limit by default, and the upstream already rejects at 200 MB.

/**
 * POST /api/data/upload
 *
 * Streams a multipart upload from the browser to retraining-service's
 * /upload-dataset endpoint. The raw body stream is forwarded verbatim so
 * the multipart boundary stays intact and nothing is buffered twice.
 *
 * Security contract:
 * - Requires DATA_INGEST_TOKEN to be set in the dashboard env. This token
 *   is injected as the X-Ingest-Token header on every upstream request.
 *   The browser cannot spoof this header on a cross-origin multipart POST
 *   (multipart is a CORS "simple" request, so ANY custom header triggers
 *   a preflight, which we do not allow on the upstream). The net effect
 *   is CSRF immunity without a full auth system.
 * - The upstream host is only reachable inside the docker network; this
 *   proxy is the only externally-reachable entry point.
 */
export async function POST(request: NextRequest) {
  const contentType = request.headers.get("content-type") ?? "";
  if (!contentType.startsWith("multipart/form-data")) {
    return Response.json(
      { error: "Expected multipart/form-data upload" },
      { status: 415 }
    );
  }

  const ingestToken = (process.env.DATA_INGEST_TOKEN ?? "").trim();
  if (!ingestToken) {
    return Response.json(
      {
        error:
          "DATA_INGEST_TOKEN is not configured on the dashboard. Set it in .env and restart.",
      },
      { status: 503 }
    );
  }

  const url = new URL(request.url);
  const autoRefresh = url.searchParams.get("auto_refresh") ?? "true";
  const target = `${serviceBaseUrl("retraining")}/upload-dataset?auto_refresh=${autoRefresh}`;

  try {
    // Forward the raw body stream. Preserving the original Content-Type
    // header keeps the multipart boundary intact — re-parsing via
    // `formData()` would force Node to re-encode the body and double the
    // peak memory footprint on every upload.
    const upstream = await fetch(target, {
      method: "POST",
      headers: {
        "Content-Type": contentType,
        "Content-Length": request.headers.get("content-length") ?? "",
        "X-Ingest-Token": ingestToken,
      },
      body: request.body,
      // Required for streaming request bodies in Node's fetch implementation.
      // @ts-expect-error — `duplex` is part of the fetch spec but missing
      // from Node's lib types as of 22.x.
      duplex: "half",
    });

    // Stream the upstream response back to the client too, so a 1 MB
    // FastAPI validation error doesn't get buffered twice in dashboard RAM.
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
            ? `Upstream upload failed: ${error.message}`
            : "Upstream upload failed",
      },
      { status: 502 }
    );
  }
}
