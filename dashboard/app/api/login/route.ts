import { NextRequest, NextResponse } from "next/server";

function safeNext(next: FormDataEntryValue | null, requestUrl: string): string {
  if (typeof next !== "string") return "/";
  let candidate: URL;
  try {
    candidate = new URL(next, requestUrl);
  } catch {
    return "/";
  }
  const base = new URL(requestUrl);
  if (candidate.origin !== base.origin) return "/";
  const path = candidate.pathname + candidate.search + candidate.hash;
  if (!path.startsWith("/") || path.startsWith("//")) return "/";
  return path;
}

export async function POST(request: NextRequest) {
  const formData = await request.formData();
  const password = formData.get("password");
  const next = safeNext(formData.get("next"), request.url);

  if (typeof password !== "string" || password !== process.env.DASHBOARD_PASSWORD) {
    const url = new URL("/login", request.url);
    url.searchParams.set("error", "1");
    url.searchParams.set("next", next);
    return NextResponse.redirect(url, { status: 303 });
  }

  const response = NextResponse.redirect(new URL(next, request.url), { status: 303 });
  response.cookies.set("dashboard_auth", password, {
    httpOnly: true,
    secure: true,
    sameSite: "lax",
    path: "/",
    maxAge: 60 * 60 * 24 * 30,
  });
  return response;
}
