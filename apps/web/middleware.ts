// Optional gate for the console: set FORMWORK_CONSOLE_PASSWORD to require HTTP Basic auth (any username).
// This is a stop-gap for pilots; put the console behind your SSO for anything real.
import { NextRequest, NextResponse } from "next/server";

export function middleware(req: NextRequest) {
  const password = process.env.FORMWORK_CONSOLE_PASSWORD;
  if (!password) return NextResponse.next();
  const header = req.headers.get("authorization") ?? "";
  if (header.startsWith("Basic ")) {
    try {
      const given = atob(header.slice(6)).split(":").slice(1).join(":");
      if (given.length === password.length) {
        let diff = 0;
        for (let i = 0; i < given.length; i++) diff |= given.charCodeAt(i) ^ password.charCodeAt(i);
        if (diff === 0) return NextResponse.next();
      }
    } catch { /* fall through */ }
  }
  return new NextResponse("Authentication required", { status: 401, headers: { "WWW-Authenticate": 'Basic realm="Formwork"' } });
}

export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };
