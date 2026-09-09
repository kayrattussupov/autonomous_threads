import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { POST } from "./route";

const ORIGINAL_PASSWORD = process.env.DASHBOARD_PASSWORD;

function loginRequest(fields: Record<string, string>): NextRequest {
  const body = new URLSearchParams(fields);
  return new NextRequest("https://dashboard.example.com/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  });
}

describe("POST /api/login", () => {
  beforeEach(() => {
    process.env.DASHBOARD_PASSWORD = "correct-horse-battery-staple";
  });

  afterEach(() => {
    process.env.DASHBOARD_PASSWORD = ORIGINAL_PASSWORD;
  });

  it("rejects a wrong password and redirects back to /login with an error flag", async () => {
    const response = await POST(loginRequest({ password: "wrong" }));

    expect(response.status).toBe(303);
    const location = new URL(response.headers.get("location")!);
    expect(location.pathname).toBe("/login");
    expect(location.searchParams.get("error")).toBe("1");
    expect(response.cookies.get("dashboard_auth")).toBeUndefined();
  });

  it("accepts the correct password, sets an httpOnly auth cookie, and redirects to /", async () => {
    const response = await POST(loginRequest({ password: "correct-horse-battery-staple" }));

    expect(response.status).toBe(303);
    expect(new URL(response.headers.get("location")!).pathname).toBe("/");

    const cookie = response.cookies.get("dashboard_auth");
    expect(cookie?.value).toBe("correct-horse-battery-staple");
    expect(cookie?.httpOnly).toBe(true);
    expect(cookie?.secure).toBe(true);
    expect(cookie?.sameSite).toBe("lax");
  });

  it("redirects to the requested next path on success", async () => {
    const response = await POST(
      loginRequest({ password: "correct-horse-battery-staple", next: "/posts/42" })
    );

    expect(new URL(response.headers.get("location")!).pathname).toBe("/posts/42");
  });

  it("falls back to / when next points at another origin (open-redirect guard)", async () => {
    const response = await POST(
      loginRequest({
        password: "correct-horse-battery-staple",
        next: "https://evil.example.com/steal",
      })
    );

    expect(new URL(response.headers.get("location")!).pathname).toBe("/");
  });

  it("falls back to / when next is a protocol-relative URL (open-redirect guard)", async () => {
    const response = await POST(
      loginRequest({ password: "correct-horse-battery-staple", next: "//evil.example.com/steal" })
    );

    expect(new URL(response.headers.get("location")!).pathname).toBe("/");
  });
});
