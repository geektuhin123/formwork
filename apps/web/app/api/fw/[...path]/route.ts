// Server-side proxy to the Formwork API. The API key lives only in this process (FORMWORK_API_KEY) and never
// reaches the browser. Only /v1/* is forwarded.
import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
const API = (process.env.FORMWORK_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
const FORWARD_REQ = ["content-type", "idempotency-key", "accept"];
const FORWARD_RES = ["content-type", "content-disposition", "x-formwork-partial", "x-formwork-job-status", "x-request-id", "cache-control"];

async function handle(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  if (path[0] !== "v1" || path.some((p) => p === ".." || p.includes("/"))) {
    return Response.json({ error: { code: "not_found", message: "unknown path" } }, { status: 404 });
  }
  const key = process.env.FORMWORK_API_KEY;
  if (!key) {
    return Response.json({ error: { code: "console_not_configured", message: "Set FORMWORK_API_KEY for the web console." } }, { status: 503 });
  }
  const headers = new Headers({ Authorization: `Bearer ${key}` });
  for (const h of FORWARD_REQ) {
    const v = req.headers.get(h);
    if (v) headers.set(h, v);
  }
  const body = req.method === "GET" || req.method === "HEAD" ? undefined : await req.arrayBuffer();
  let upstream: Response;
  try {
    upstream = await fetch(`${API}/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`, { method: req.method, headers, body, cache: "no-store" });
  } catch {
    return Response.json({ error: { code: "api_unreachable", message: "The Formwork API is not reachable." } }, { status: 502 });
  }
  const out = new Headers();
  for (const h of FORWARD_RES) {
    const v = upstream.headers.get(h);
    if (v) out.set(h, v);
  }
  return new Response(upstream.body, { status: upstream.status, headers: out });
}

export { handle as GET, handle as POST, handle as PUT };
