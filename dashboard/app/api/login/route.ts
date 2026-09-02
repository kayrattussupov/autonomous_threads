import { NextRequest, NextResponse } from "next/server";

function safeNext(next: FormDataEntryValue | null, requestUrl: string): string {
  if (typeof next !== "string") return "/";
  try {
    const candidate = new URL(next, requestUrl);
    const base = new URL(requestUrl);
    if (candidate.origin === base.origin) {
      return candidate.pathname + candidate.search + candidate.hash;
    }
  } catch {
    // malformed URL — fall through to default
  }
  return "/";
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
