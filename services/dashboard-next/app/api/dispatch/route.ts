import { proxyPost } from "@/lib/api";
import { NextRequest } from "next/server";

export async function POST(request: NextRequest) {
  const body = await request.json();
  return proxyPost("dispatcher", "/dispatch", body);
}
