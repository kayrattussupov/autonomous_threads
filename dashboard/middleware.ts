import { NextRequest, NextResponse } from "next/server";

const COOKIE_NAME = "dashboard_auth";
const PUBLIC_PATHS = ["/login", "/api/login"];

export function middleware(request: NextRequest) {
  if (PUBLIC_PATHS.some((path) => request.nextUrl.pathname === path || request.nextUrl.pathname.startsWith(path + "/"))) {
    return NextResponse.next();
  }

  const cookie = request.cookies.get(COOKIE_NAME);
  if (process.env.DASHBOARD_PASSWORD && cookie?.value === process.env.DASHBOARD_PASSWORD) {
    return NextResponse.next();
  }

  const loginUrl = new URL("/login", request.url);
  loginUrl.searchParams.set("next", request.nextUrl.pathname);
  return NextResponse.redirect(loginUrl);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
