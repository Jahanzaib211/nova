import type { NextRequest } from "next/server";

// Server-side proxy: honour the same internal gateway override next.config.js
// uses for its rewrites, so an e2e build pointed at a closed port never
// reaches the live gateway (a 401 from there redirects the browser to /login).
const BACKEND_BASE_URL =
  process.env.DEER_FLOW_INTERNAL_GATEWAY_BASE_URL ??
  process.env.NEXT_PUBLIC_BACKEND_BASE_URL ??
  "http://127.0.0.1:8001";

function buildBackendUrl(pathname: string) {
  return new URL(pathname, BACKEND_BASE_URL);
}

async function proxyRequest(request: NextRequest, pathname: string) {
  const headers = new Headers(request.headers);
  headers.delete("host");
  headers.delete("connection");
  headers.delete("content-length");

  const hasBody = !["GET", "HEAD"].includes(request.method);
  const response = await fetch(buildBackendUrl(pathname), {
    method: request.method,
    headers,
    body: hasBody ? await request.arrayBuffer() : undefined,
  });

  return new Response(await response.arrayBuffer(), {
    status: response.status,
    headers: response.headers,
  });
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  return proxyRequest(request, `/api/memory/${(await params).path.join("/")}`);
}

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  return proxyRequest(request, `/api/memory/${(await params).path.join("/")}`);
}

export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  return proxyRequest(request, `/api/memory/${(await params).path.join("/")}`);
}

export async function PATCH(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  return proxyRequest(request, `/api/memory/${(await params).path.join("/")}`);
}
