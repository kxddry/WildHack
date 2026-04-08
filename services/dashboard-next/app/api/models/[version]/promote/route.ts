import { missingInternalApiTokenResponse, proxyPost } from "@/lib/api";

export async function POST(
  _request: Request,
  context: { params: Promise<{ version: string }> }
) {
  const missing = missingInternalApiTokenResponse();
  if (missing) {
    return missing;
  }

  const { version } = await context.params;
  return proxyPost(
    "retraining",
    `/models/${encodeURIComponent(version)}/promote`,
    {},
    {
      headers: {
        "X-Internal-Token": process.env.INTERNAL_API_TOKEN ?? "",
      },
    }
  );
}
