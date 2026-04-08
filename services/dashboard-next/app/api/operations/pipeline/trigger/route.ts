import { missingInternalApiTokenResponse, proxyPost } from "@/lib/api";

export async function POST() {
  const missing = missingInternalApiTokenResponse();
  if (missing) {
    return missing;
  }

  return proxyPost(
    "scheduler",
    "/pipeline/trigger",
    {},
    {
      headers: {
        "X-Internal-Token": process.env.INTERNAL_API_TOKEN ?? "",
      },
    }
  );
}
