# Block 4 Dashboard Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Next.js 15 App Router dashboard in `dashboard/` — five screens (Воронка, Посты, Агенты, Стили, Playbook) behind a shared-password gate, deployed to Vercel — consuming the API built in the companion API plan.

**Architecture:** All data fetching happens server-side (Server Components, Server Actions, one Route Handler) via a typed client in `lib/api-client.ts` that attaches `Authorization: Bearer <API_BEARER_TOKEN>` — the token and `API_BASE_URL` live only in server-side env vars and never reach the browser bundle. A `middleware.ts` gates every route except `/login` and `/api/login` behind a `dashboard_auth` cookie compared to `DASHBOARD_PASSWORD`. Screens live under an `app/(dashboard)/` route group whose `layout.tsx` renders the nav and the persistent spend widget — kept out of the root layout so the unauthenticated `/login` page never fetches or displays spend data.

**Tech Stack:** Next.js 15 (App Router), React 19, TypeScript 5, Node.js ≥ 18.18 (this machine has v22). No CSS framework, no ESLint config — kept out of scope, nothing in the design doc calls for either.

**Spec:** [docs/superpowers/specs/2026-09-01-block-4-dashboard-design.md](../specs/2026-09-01-block-4-dashboard-design.md) §4, §5, §6, §7 — parent: [SPEC.md](../../../SPEC.md) §10, §11 Block 4 (T4.2). Depends on the endpoints built in [docs/superpowers/plans/2026-09-01-block-4-dashboard-api.md](2026-09-01-block-4-dashboard-api.md) — this plan assumes that API is deployed and reachable via `API_BASE_URL` for its final manual smoke test (Task 8), but every earlier task's `tsc`/`next build` check works without a live backend.

## Global Constraints

- All data fetching happens in Server Components, Server Actions, or Route Handlers — **never** client-side `fetch` — so `API_BEARER_TOKEN` never ships to the browser (design doc §2, §4).
- Every route except `/login` and `/api/login` requires the `dashboard_auth` cookie to equal `DASHBOARD_PASSWORD`; otherwise redirect to `/login` (design doc §5).
- Response shapes consumed here must match `src/api/schemas.py` from the API plan exactly — field names and nullability copied 1:1 into `lib/api-types.ts`.
- Next.js 15 changed `params`/`searchParams` page props to `Promise`s that must be `await`ed — every page reading them (`/login`, `/posts`) uses `await searchParams`, not the Next 14 synchronous form.
- Approve/reject buttons render only when a row is in its pending state (`draft` for styles, `proposed` for playbook) — mirrors the API's own state-transition guard rather than relying on the button to fail loudly.
- Every task's deliverable must typecheck cleanly (`npx tsc --noEmit` for Tasks 1–2, before any route exists; `npm run build` from Task 3 onward, once a real page exists at `/`) — no task may leave the project in a broken state for the next task to inherit.

---

## File Structure

```
dashboard/
├── package.json
├── tsconfig.json
├── next.config.ts
├── next-env.d.ts
├── .gitignore
├── .env.example
├── middleware.ts
├── lib/
│   ├── api-types.ts          # TS mirror of src/api/schemas.py
│   └── api-client.ts         # apiFetch + one function per endpoint
└── app/
    ├── layout.tsx             # minimal <html><body> — no data fetching
    ├── globals.css
    ├── login/page.tsx
    ├── api/login/route.ts
    └── (dashboard)/
        ├── layout.tsx         # nav + spend widget
        ├── page.tsx           # Воронка (home)
        ├── posts/page.tsx
        ├── agents/
        │   ├── page.tsx
        │   ├── RunRow.tsx     # client component, expandable step trace
        │   └── actions.ts     # "use server" — fetchStepsAction
        ├── styles/page.tsx
        └── playbook/page.tsx
```

**Why this split:** `api-types.ts` and `api-client.ts` are separate for the same reason `schemas.py` and the routers are separate on the backend — types change when the API's shape changes, fetch logic changes when caching/error-handling needs change. The `(dashboard)/` route group exists specifically to keep the nav/spend-widget `layout.tsx` from wrapping `/login` — a route group adds no URL segment but does scope a layout to only the routes inside it.

---

### Task 1: Project scaffold + typed API client

**Files:**
- Create: `dashboard/package.json`
- Create: `dashboard/tsconfig.json`
- Create: `dashboard/next.config.ts`
- Create: `dashboard/next-env.d.ts`
- Create: `dashboard/.gitignore`
- Create: `dashboard/app/layout.tsx`
- Create: `dashboard/app/globals.css`
- Create: `dashboard/lib/api-types.ts`
- Create: `dashboard/lib/api-client.ts`

**Interfaces:**
- Produces: every type in `lib/api-types.ts` (`Post`, `PostsPage`, `AgentRun`, `AgentStep`, `StyleVariant`, `PlaybookRule`, `FunnelMonth`, `Spend`) and every function in `lib/api-client.ts` (`getPosts`, `getRuns`, `getRunSteps`, `getStyles`, `approveStyle`, `rejectStyle`, `getPlaybook`, `approvePlaybookRule`, `rejectPlaybookRule`, `getFunnel`, `getSpend`, plus the `ApiError` class) — every later task in this plan imports from these two files.

- [ ] **Step 1: Write `dashboard/package.json`**

```json
{
  "name": "threads-agent-dashboard",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "typecheck": "tsc --noEmit"
  },
  "dependencies": {
    "next": "^15.1.0",
    "react": "^19.0.0",
    "react-dom": "^19.0.0"
  },
  "devDependencies": {
    "typescript": "^5.7.0",
    "@types/node": "^22.10.0",
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0"
  }
}
```

- [ ] **Step 2: Write `dashboard/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2017",
    "lib": ["dom", "dom.iterable", "esnext"],
    "allowJs": true,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "preserve",
    "incremental": true,
    "plugins": [{ "name": "next" }],
    "paths": { "@/*": ["./*"] }
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
  "exclude": ["node_modules"]
}
```

- [ ] **Step 3: Write `dashboard/next.config.ts`**

```ts
import type { NextConfig } from "next";

const nextConfig: NextConfig = {};

export default nextConfig;
```

- [ ] **Step 4: Write `dashboard/next-env.d.ts`**

```ts
/// <reference types="next" />
/// <reference types="next/image-types/global" />

// NOTE: This file should not be edited
// see https://nextjs.org/docs/app/api-reference/config/typescript for more information.
```

- [ ] **Step 5: Write `dashboard/.gitignore`**

```
node_modules/
.next/
.env.local
```

- [ ] **Step 6: Write `dashboard/lib/api-types.ts`**

```ts
export type Post = {
  id: number;
  text: string;
  category: string;
  status: string;
  source_url: string | null;
  style_variant_id: number | null;
  experiment_id: string | null;
  playbook_version: number | null;
  model_used: string | null;
  scheduled_at: string | null;
  posted_at: string | null;
  threads_media_id: string | null;
  views: number | null;
  likes: number | null;
  replies_count: number | null;
  quotes: number | null;
  score: number | null;
  metrics_updated_at: string | null;
  created_at: string;
};

export type PostsPage = {
  items: Post[];
  total: number;
  page: number;
  page_size: number;
  median_score: number | null;
};

export type AgentRun = {
  id: number;
  agent: string;
  trigger: string;
  started_at: string;
  finished_at: string | null;
  status: string;
  steps_count: number | null;
  tokens_in: number | null;
  tokens_out: number | null;
  cost_usd: number | null;
  error: string | null;
  output_ref: string | null;
};

export type AgentStep = {
  id: number;
  run_id: number;
  step_no: number;
  thought: string | null;
  tool_name: string | null;
  tool_args: Record<string, unknown> | null;
  tool_result: Record<string, unknown> | null;
  tool_ok: boolean | null;
  tool_ms: number | null;
  created_at: string;
};

export type StyleVariant = {
  id: number;
  name: string;
  genome: string;
  status: string;
  created_by: string;
  parent_id: number | null;
  rationale: string | null;
  posts_n: number | null;
  median_score: number | null;
  created_at: string;
};

export type PlaybookRule = {
  id: number;
  rule_text: string;
  status: string;
  hypothesis: string | null;
  target_metric: string | null;
  evidence_n: number | null;
  median_before: number | null;
  median_after: number | null;
  version: number;
  introduced_at: string;
};

export type FunnelMonth = {
  month: string;
  posts: number;
  views: number;
  replies: number;
  conversations: number;
  leads: number;
};

export type Spend = {
  month_to_date_usd: number;
  cap_usd: number;
};
```

- [ ] **Step 7: Write `dashboard/lib/api-client.ts`**

```ts
import type {
  AgentRun,
  AgentStep,
  FunnelMonth,
  PlaybookRule,
  Post,
  PostsPage,
  Spend,
  StyleVariant,
} from "./api-types";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const baseUrl = process.env.API_BASE_URL;
  const token = process.env.API_BEARER_TOKEN;
  if (!baseUrl || !token) {
    throw new Error("API_BASE_URL and API_BEARER_TOKEN must be set");
  }

  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    headers: {
      ...init?.headers,
      Authorization: `Bearer ${token}`,
    },
    cache: "no-store",
  });

  if (!response.ok) {
    const body = await response.text();
    throw new ApiError(response.status, body || response.statusText);
  }

  return response.json() as Promise<T>;
}

export function getPosts(params: {
  category?: string;
  style_variant_id?: number;
  model_used?: string;
  status?: string;
  page?: number;
  page_size?: number;
}): Promise<PostsPage> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") query.set(key, String(value));
  }
  return apiFetch<PostsPage>(`/posts?${query.toString()}`);
}

export function getRuns(limit = 50): Promise<AgentRun[]> {
  return apiFetch<AgentRun[]>(`/runs?limit=${limit}`);
}

export function getRunSteps(runId: number): Promise<AgentStep[]> {
  return apiFetch<AgentStep[]>(`/runs/${runId}/steps`);
}

export function getStyles(): Promise<StyleVariant[]> {
  return apiFetch<StyleVariant[]>("/styles");
}

export function approveStyle(id: number): Promise<StyleVariant> {
  return apiFetch<StyleVariant>(`/styles/${id}/approve`, { method: "POST" });
}

export function rejectStyle(id: number): Promise<StyleVariant> {
  return apiFetch<StyleVariant>(`/styles/${id}/reject`, { method: "POST" });
}

export function getPlaybook(): Promise<PlaybookRule[]> {
  return apiFetch<PlaybookRule[]>("/playbook");
}

export function approvePlaybookRule(id: number): Promise<PlaybookRule> {
  return apiFetch<PlaybookRule>(`/playbook/${id}/approve`, { method: "POST" });
}

export function rejectPlaybookRule(id: number): Promise<PlaybookRule> {
  return apiFetch<PlaybookRule>(`/playbook/${id}/reject`, { method: "POST" });
}

export function getFunnel(months = 6): Promise<FunnelMonth[]> {
  return apiFetch<FunnelMonth[]>(`/funnel?months=${months}`);
}

export function getSpend(): Promise<Spend> {
  return apiFetch<Spend>("/spend");
}

export type {
  AgentRun,
  AgentStep,
  FunnelMonth,
  PlaybookRule,
  Post,
  PostsPage,
  Spend,
  StyleVariant,
};
```

- [ ] **Step 8: Write `dashboard/app/globals.css`**

```css
* {
  box-sizing: border-box;
}

body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  color: #1a1a1a;
}

table {
  border-collapse: collapse;
  width: 100%;
}

th,
td {
  text-align: left;
  padding: 8px;
  border-bottom: 1px solid #e0e0e0;
  vertical-align: top;
}

button {
  cursor: pointer;
}
```

- [ ] **Step 9: Write `dashboard/app/layout.tsx`**

Kept deliberately minimal — no nav, no data fetching. The authenticated nav/spend-widget shell lives in Task 3's `app/(dashboard)/layout.tsx`, scoped so `/login` never renders it.

```tsx
import type { ReactNode } from "react";
import "./globals.css";

export const metadata = {
  title: "Threads Agent Dashboard",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
```

- [ ] **Step 10: Install dependencies and typecheck**

```bash
cd dashboard
npm install
npm run typecheck
```

Expected: `npm install` completes without errors; `npm run typecheck` (`tsc --noEmit`) reports no errors. No route exists yet at this point (`app/layout.tsx` only), so `next build` is not run until Task 3 — `tsc --noEmit` is the deliverable check for this task and the next one.

- [ ] **Step 11: Commit**

`dashboard/package-lock.json` was generated by `npm install` in the previous step — include it in this same commit:

```bash
cd ..
git add dashboard/package.json dashboard/package-lock.json dashboard/tsconfig.json dashboard/next.config.ts dashboard/next-env.d.ts dashboard/.gitignore dashboard/app/layout.tsx dashboard/app/globals.css dashboard/lib/api-types.ts dashboard/lib/api-client.ts
git commit -m "feat: scaffold Next.js 15 dashboard project with typed API client"
```

---

### Task 2: Password-gate middleware

**Files:**
- Create: `dashboard/middleware.ts`
- Create: `dashboard/app/login/page.tsx`
- Create: `dashboard/app/api/login/route.ts`
- Create: `dashboard/.env.example`

**Interfaces:**
- Consumes: nothing from Task 1's exports directly (this task is self-contained plumbing).
- Produces: the `dashboard_auth` cookie contract — set by `POST /api/login` to the literal value of `DASHBOARD_PASSWORD`, checked by `middleware.ts` on every other route. Tasks 3–7 rely on this middleware already gating their routes; they don't reference it directly.

- [ ] **Step 1: Write `dashboard/middleware.ts`**

```ts
import { NextRequest, NextResponse } from "next/server";

const COOKIE_NAME = "dashboard_auth";
const PUBLIC_PATHS = ["/login", "/api/login"];

export function middleware(request: NextRequest) {
  if (PUBLIC_PATHS.some((path) => request.nextUrl.pathname.startsWith(path))) {
    return NextResponse.next();
  }

  const cookie = request.cookies.get(COOKIE_NAME);
  if (cookie?.value === process.env.DASHBOARD_PASSWORD) {
    return NextResponse.next();
  }

  const loginUrl = new URL("/login", request.url);
  loginUrl.searchParams.set("next", request.nextUrl.pathname);
  return NextResponse.redirect(loginUrl);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
```

- [ ] **Step 2: Write `dashboard/app/api/login/route.ts`**

```ts
import { NextRequest, NextResponse } from "next/server";

export async function POST(request: NextRequest) {
  const formData = await request.formData();
  const password = formData.get("password");
  const next = (formData.get("next") as string | null) || "/";

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
```

- [ ] **Step 3: Write `dashboard/app/login/page.tsx`**

Next.js 15 makes `searchParams` a `Promise` — this page must `await` it (Global Constraints).

```tsx
export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; error?: string }>;
}) {
  const params = await searchParams;

  return (
    <main style={{ maxWidth: 320, margin: "80px auto", fontFamily: "sans-serif" }}>
      <h1>Threads Agent Dashboard</h1>
      <form method="POST" action="/api/login">
        <input type="hidden" name="next" value={params.next ?? "/"} />
        <label htmlFor="password">Password</label>
        <input
          id="password"
          name="password"
          type="password"
          autoFocus
          style={{ display: "block", width: "100%", marginTop: 8, marginBottom: 12 }}
        />
        <button type="submit">Sign in</button>
      </form>
      {params.error && <p style={{ color: "crimson" }}>Wrong password.</p>}
    </main>
  );
}
```

- [ ] **Step 4: Write `dashboard/.env.example`**

```
API_BASE_URL=https://your-vps-host:8443
API_BEARER_TOKEN=changeme
DASHBOARD_PASSWORD=changeme
```

- [ ] **Step 5: Typecheck**

```bash
cd dashboard
npm run typecheck
```

Expected: no errors.

- [ ] **Step 6: Manual smoke test against the dev server**

There's still no protected page to redirect *to* yet (that arrives in Task 3), but the login flow itself is fully testable now:

```bash
cd dashboard
DASHBOARD_PASSWORD=testpass npm run dev &
sleep 3
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3000/login
curl -s -i -X POST http://localhost:3000/api/login -d "password=wrong&next=/" | head -n 1
curl -s -i -X POST http://localhost:3000/api/login -d "password=testpass&next=/" | grep -i set-cookie
kill %1
```

Expected: first `curl` prints `200`; second prints an HTTP redirect status (`303`) with `error=1` in the `Location` header (visible via `-i` on a follow-up run if you want to inspect it); third prints a `Set-Cookie: dashboard_auth=testpass; ...` line.

- [ ] **Step 7: Commit**

```bash
cd ..
git add dashboard/middleware.ts dashboard/app/login/page.tsx dashboard/app/api/login/route.ts dashboard/.env.example
git commit -m "feat: add shared-password gate for the dashboard"
```

---

### Task 3: Authenticated shell (nav + spend widget) and Funnel screen

**Files:**
- Create: `dashboard/app/(dashboard)/layout.tsx`
- Create: `dashboard/app/(dashboard)/page.tsx`

**Interfaces:**
- Consumes: `getSpend`, `getFunnel`, `Spend`, `FunnelMonth` from `dashboard/lib/api-client.ts` (Task 1).
- Produces: the `(dashboard)` route group's shared layout — Tasks 4–7 place their `page.tsx` files inside this same group and automatically inherit the nav/spend widget without importing anything.

- [ ] **Step 1: Write `dashboard/app/(dashboard)/layout.tsx`**

```tsx
import type { ReactNode } from "react";
import Link from "next/link";
import { getSpend } from "@/lib/api-client";

export const dynamic = "force-dynamic";

export default async function DashboardLayout({ children }: { children: ReactNode }) {
  const spend = await getSpend();

  return (
    <>
      <header
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: 16,
          borderBottom: "1px solid #e0e0e0",
        }}
      >
        <nav style={{ display: "flex", gap: 16 }}>
          <Link href="/">Воронка</Link>
          <Link href="/posts">Посты</Link>
          <Link href="/agents">Агенты</Link>
          <Link href="/styles">Стили</Link>
          <Link href="/playbook">Playbook</Link>
        </nav>
        <div>
          Расход: ${spend.month_to_date_usd.toFixed(2)} / ${spend.cap_usd.toFixed(2)}
        </div>
      </header>
      <div style={{ padding: 16 }}>{children}</div>
    </>
  );
}
```

- [ ] **Step 2: Write `dashboard/app/(dashboard)/page.tsx`**

```tsx
import { getFunnel } from "@/lib/api-client";

export const dynamic = "force-dynamic";

export default async function FunnelPage() {
  const months = await getFunnel(6);

  return (
    <main>
      <h1>Воронка</h1>
      <table>
        <thead>
          <tr>
            <th>Месяц</th>
            <th>Посты</th>
            <th>Просмотры</th>
            <th>Ответы</th>
            <th>Диалоги</th>
            <th>Лиды</th>
          </tr>
        </thead>
        <tbody>
          {months.map((m) => (
            <tr key={m.month}>
              <td>{m.month}</td>
              <td>{m.posts}</td>
              <td>{m.views}</td>
              <td>{m.replies}</td>
              <td>{m.conversations}</td>
              <td>{m.leads}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
```

- [ ] **Step 3: Build**

```bash
cd dashboard
API_BASE_URL=http://localhost:8000 API_BEARER_TOKEN=test DASHBOARD_PASSWORD=testpass npm run build
```

Expected: `Compiled successfully`. Every page in this and every later task sets `export const dynamic = "force-dynamic"`, so `next build` never attempts to statically prerender a page by actually calling the (possibly unreachable) API at build time — the env vars above only need to be *present* (non-empty strings) to satisfy `lib/api-client.ts`'s `apiFetch` guard, not point at a real server, for the build itself to succeed.

- [ ] **Step 4: Commit**

```bash
cd ..
git add "dashboard/app/(dashboard)/layout.tsx" "dashboard/app/(dashboard)/page.tsx"
git commit -m "feat: add authenticated dashboard shell and Funnel screen"
```

---

### Task 4: Posts screen

**Files:**
- Create: `dashboard/app/(dashboard)/posts/page.tsx`

**Interfaces:**
- Consumes: `getPosts`, `Post` from `dashboard/lib/api-client.ts` (Task 1).
- Produces: nothing consumed elsewhere in this plan.

- [ ] **Step 1: Write `dashboard/app/(dashboard)/posts/page.tsx`**

```tsx
import { getPosts } from "@/lib/api-client";

export const dynamic = "force-dynamic";

export default async function PostsPage({
  searchParams,
}: {
  searchParams: Promise<{
    category?: string;
    status?: string;
    model_used?: string;
    page?: string;
  }>;
}) {
  const params = await searchParams;
  const page = Number(params.page ?? "1");

  const data = await getPosts({
    category: params.category,
    status: params.status,
    model_used: params.model_used,
    page,
    page_size: 25,
  });

  return (
    <main>
      <h1>Посты</h1>
      <form method="GET" style={{ display: "flex", gap: 8, marginBottom: 16 }}>
        <select name="status" defaultValue={params.status ?? ""}>
          <option value="">Все статусы</option>
          <option value="draft">draft</option>
          <option value="needs_review">needs_review</option>
          <option value="scheduled">scheduled</option>
          <option value="published">published</option>
          <option value="failed">failed</option>
        </select>
        <input type="text" name="category" placeholder="Категория" defaultValue={params.category ?? ""} />
        <input type="text" name="model_used" placeholder="Модель" defaultValue={params.model_used ?? ""} />
        <button type="submit">Фильтр</button>
      </form>
      <p>
        Всего: {data.total} · Медиана score: {data.median_score ?? "—"}
      </p>
      <table>
        <thead>
          <tr>
            <th>Текст</th>
            <th>Категория</th>
            <th>Статус</th>
            <th>Модель</th>
            <th>Просмотры</th>
            <th>Score</th>
            <th>Запланирован</th>
            <th>Опубликован</th>
          </tr>
        </thead>
        <tbody>
          {data.items.map((post) => (
            <tr key={post.id}>
              <td>{post.text.slice(0, 80)}</td>
              <td>{post.category}</td>
              <td>{post.status}</td>
              <td>{post.model_used ?? "—"}</td>
              <td>{post.views ?? "—"}</td>
              <td>{post.score ?? "—"}</td>
              <td>{post.scheduled_at ?? "—"}</td>
              <td>{post.posted_at ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
```

- [ ] **Step 2: Build**

```bash
cd dashboard
API_BASE_URL=http://localhost:8000 API_BEARER_TOKEN=test DASHBOARD_PASSWORD=testpass npm run build
```

Expected: `Compiled successfully`.

- [ ] **Step 3: Commit**

```bash
cd ..
git add "dashboard/app/(dashboard)/posts/page.tsx"
git commit -m "feat: add Posts screen with category/status/model filters"
```

---

### Task 5: Agents screen (list + expandable step trace)

**Files:**
- Create: `dashboard/app/(dashboard)/agents/page.tsx`
- Create: `dashboard/app/(dashboard)/agents/actions.ts`
- Create: `dashboard/app/(dashboard)/agents/RunRow.tsx`

**Interfaces:**
- Consumes: `getRuns`, `getRunSteps`, `AgentRun`, `AgentStep` from `dashboard/lib/api-client.ts` (Task 1).
- Produces: nothing consumed elsewhere in this plan. `fetchStepsAction` must stay a named export from a `"use server"` file — client components (`RunRow.tsx`) can only call Server Actions imported from a file marked `"use server"` at the top, not inline functions.

- [ ] **Step 1: Write `dashboard/app/(dashboard)/agents/actions.ts`**

```ts
"use server";

import { getRunSteps, type AgentStep } from "@/lib/api-client";

export async function fetchStepsAction(runId: number): Promise<AgentStep[]> {
  return getRunSteps(runId);
}
```

- [ ] **Step 2: Write `dashboard/app/(dashboard)/agents/RunRow.tsx`**

```tsx
"use client";

import { useState } from "react";
import type { AgentRun, AgentStep } from "@/lib/api-client";
import { fetchStepsAction } from "./actions";

export function RunRow({ run }: { run: AgentRun }) {
  const [expanded, setExpanded] = useState(false);
  const [steps, setSteps] = useState<AgentStep[] | null>(null);
  const [loading, setLoading] = useState(false);

  async function toggle() {
    if (!expanded && steps === null) {
      setLoading(true);
      const result = await fetchStepsAction(run.id);
      setSteps(result);
      setLoading(false);
    }
    setExpanded((value) => !value);
  }

  return (
    <>
      <tr onClick={toggle} style={{ cursor: "pointer" }}>
        <td>{run.agent}</td>
        <td>{run.trigger}</td>
        <td>{run.started_at}</td>
        <td>{run.status}</td>
        <td>{run.steps_count ?? 0}</td>
        <td>{(run.tokens_in ?? 0) + (run.tokens_out ?? 0)}</td>
        <td>{run.cost_usd ?? 0}</td>
        <td>{expanded ? "▲" : "▼"}</td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={8}>
            {loading && <p>Загрузка...</p>}
            {steps?.map((step) => (
              <div key={step.id} style={{ borderTop: "1px solid #e0e0e0", padding: 8 }}>
                <strong>
                  Шаг {step.step_no}: {step.tool_name ?? "—"}
                </strong>{" "}
                ({step.tool_ok === false ? "ошибка" : "ok"}, {step.tool_ms ?? 0} мс)
                <p>{step.thought}</p>
              </div>
            ))}
          </td>
        </tr>
      )}
    </>
  );
}
```

- [ ] **Step 3: Write `dashboard/app/(dashboard)/agents/page.tsx`**

```tsx
import { getRuns } from "@/lib/api-client";
import { RunRow } from "./RunRow";

export const dynamic = "force-dynamic";

export default async function AgentsPage() {
  const runs = await getRuns(50);

  return (
    <main>
      <h1>Агенты</h1>
      <table>
        <thead>
          <tr>
            <th>Агент</th>
            <th>Триггер</th>
            <th>Начало</th>
            <th>Статус</th>
            <th>Шаги</th>
            <th>Токены</th>
            <th>Стоимость</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <RunRow key={run.id} run={run} />
          ))}
        </tbody>
      </table>
    </main>
  );
}
```

- [ ] **Step 4: Build**

```bash
cd dashboard
API_BASE_URL=http://localhost:8000 API_BEARER_TOKEN=test DASHBOARD_PASSWORD=testpass npm run build
```

Expected: `Compiled successfully`.

- [ ] **Step 5: Commit**

```bash
cd ..
git add "dashboard/app/(dashboard)/agents/page.tsx" "dashboard/app/(dashboard)/agents/actions.ts" "dashboard/app/(dashboard)/agents/RunRow.tsx"
git commit -m "feat: add Agents screen with expandable step trace"
```

---

### Task 6: Styles screen (list + rationale + approve/reject)

**Files:**
- Create: `dashboard/app/(dashboard)/styles/page.tsx`

**Interfaces:**
- Consumes: `getStyles`, `approveStyle`, `rejectStyle`, `StyleVariant` from `dashboard/lib/api-client.ts` (Task 1).
- Produces: nothing consumed elsewhere in this plan.

- [ ] **Step 1: Write `dashboard/app/(dashboard)/styles/page.tsx`**

```tsx
import { revalidatePath } from "next/cache";
import { approveStyle, getStyles, rejectStyle, type StyleVariant } from "@/lib/api-client";

export const dynamic = "force-dynamic";

async function approveAction(formData: FormData) {
  "use server";
  const id = Number(formData.get("id"));
  await approveStyle(id);
  revalidatePath("/styles");
}

async function rejectAction(formData: FormData) {
  "use server";
  const id = Number(formData.get("id"));
  await rejectStyle(id);
  revalidatePath("/styles");
}

function VariantRow({ variant }: { variant: StyleVariant }) {
  return (
    <tr>
      <td>{variant.name}</td>
      <td>{variant.status}</td>
      <td>{variant.median_score ?? "—"}</td>
      <td>{variant.posts_n ?? 0}</td>
      <td>{variant.rationale ?? "—"}</td>
      <td>{variant.parent_id ?? "—"}</td>
      <td>
        {variant.status === "draft" && (
          <>
            <form action={approveAction} style={{ display: "inline" }}>
              <input type="hidden" name="id" value={variant.id} />
              <button type="submit">Принять</button>
            </form>{" "}
            <form action={rejectAction} style={{ display: "inline" }}>
              <input type="hidden" name="id" value={variant.id} />
              <button type="submit">Отклонить</button>
            </form>
          </>
        )}
      </td>
    </tr>
  );
}

export default async function StylesPage() {
  const variants = await getStyles();

  return (
    <main>
      <h1>Стили</h1>
      <table>
        <thead>
          <tr>
            <th>Название</th>
            <th>Статус</th>
            <th>Медиана score</th>
            <th>Постов</th>
            <th>Обоснование</th>
            <th>Родитель</th>
            <th>Действия</th>
          </tr>
        </thead>
        <tbody>
          {variants.map((variant) => (
            <VariantRow key={variant.id} variant={variant} />
          ))}
        </tbody>
      </table>
    </main>
  );
}
```

- [ ] **Step 2: Build**

```bash
cd dashboard
API_BASE_URL=http://localhost:8000 API_BEARER_TOKEN=test DASHBOARD_PASSWORD=testpass npm run build
```

Expected: `Compiled successfully`.

- [ ] **Step 3: Commit**

```bash
cd ..
git add "dashboard/app/(dashboard)/styles/page.tsx"
git commit -m "feat: add Styles screen with rationale display and approve/reject"
```

---

### Task 7: Playbook screen (list + hypothesis + approve/reject)

**Files:**
- Create: `dashboard/app/(dashboard)/playbook/page.tsx`

**Interfaces:**
- Consumes: `getPlaybook`, `approvePlaybookRule`, `rejectPlaybookRule`, `PlaybookRule` from `dashboard/lib/api-client.ts` (Task 1).
- Produces: nothing consumed elsewhere in this plan.

- [ ] **Step 1: Write `dashboard/app/(dashboard)/playbook/page.tsx`**

```tsx
import { revalidatePath } from "next/cache";
import { approvePlaybookRule, getPlaybook, rejectPlaybookRule, type PlaybookRule } from "@/lib/api-client";

export const dynamic = "force-dynamic";

async function approveAction(formData: FormData) {
  "use server";
  const id = Number(formData.get("id"));
  await approvePlaybookRule(id);
  revalidatePath("/playbook");
}

async function rejectAction(formData: FormData) {
  "use server";
  const id = Number(formData.get("id"));
  await rejectPlaybookRule(id);
  revalidatePath("/playbook");
}

function RuleRow({ rule }: { rule: PlaybookRule }) {
  return (
    <tr>
      <td>{rule.rule_text}</td>
      <td>{rule.status}</td>
      <td>{rule.hypothesis ?? "—"}</td>
      <td>{rule.evidence_n ?? 0}</td>
      <td>
        {rule.median_before ?? "—"} → {rule.median_after ?? "—"}
      </td>
      <td>
        {rule.status === "proposed" && (
          <>
            <form action={approveAction} style={{ display: "inline" }}>
              <input type="hidden" name="id" value={rule.id} />
              <button type="submit">Принять</button>
            </form>{" "}
            <form action={rejectAction} style={{ display: "inline" }}>
              <input type="hidden" name="id" value={rule.id} />
              <button type="submit">Отклонить</button>
            </form>
          </>
        )}
      </td>
    </tr>
  );
}

export default async function PlaybookPage() {
  const rules = await getPlaybook();

  return (
    <main>
      <h1>Playbook</h1>
      <table>
        <thead>
          <tr>
            <th>Правило</th>
            <th>Статус</th>
            <th>Гипотеза</th>
            <th>Evidence</th>
            <th>Медиана до → после</th>
            <th>Действия</th>
          </tr>
        </thead>
        <tbody>
          {rules.map((rule) => (
            <RuleRow key={rule.id} rule={rule} />
          ))}
        </tbody>
      </table>
    </main>
  );
}
```

- [ ] **Step 2: Build**

```bash
cd dashboard
API_BASE_URL=http://localhost:8000 API_BEARER_TOKEN=test DASHBOARD_PASSWORD=testpass npm run build
```

Expected: `Compiled successfully`.

- [ ] **Step 3: Commit**

```bash
cd ..
git add "dashboard/app/(dashboard)/playbook/page.tsx"
git commit -m "feat: add Playbook screen with hypothesis display and approve/reject"
```

---

### Task 8: Local end-to-end smoke test, README, Vercel deployment notes

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: the full local stack — `docker compose up` (from the API plan and Block 0/1) plus `npm run dev` in `dashboard/`.
- Produces: nothing — this is the plan's final verification and documentation task.

- [ ] **Step 1: Start the backend stack**

```bash
docker compose up -d --build
```

Expected: `postgres`, `worker`, `api`, `caddy` all report `Up` in `docker compose ps`.

- [ ] **Step 2: Seed at least one row per table the dashboard reads**

Without seed data every screen renders an empty table, which doesn't exercise the rendering code paths. Run this one-off script against the running stack (adjust `DATABASE_URL`/port per your local override, per the README's existing port-remap note):

```bash
python -c "
from datetime import datetime, timezone
from src.db.engine import session_scope
from src.db.models import AgentRun, AgentStep, Lead, PlaybookRule, Post, Reply, StyleVariant

with session_scope() as s:
    variant = StyleVariant(name='v1', genome='dry engineer voice', status='active', created_by='human', posts_n=25, median_score=42)
    draft_variant = StyleVariant(name='v2-radical', genome='short punchy posts', status='draft', created_by='analyst', rationale='shorter posts tested better in swipe file')
    s.add_all([variant, draft_variant])
    s.flush()

    post = Post(text='Building an autonomous agent, one boring commit at a time.', category='educational', status='published', style_variant_id=variant.id, views=340, score=12, posted_at=datetime.now(timezone.utc))
    s.add(post)
    s.flush()

    s.add(Reply(threads_reply_id='seed-1', post_id=post.id, kind='question', text='how long did this take?', responded_at=datetime.now(timezone.utc), received_at=datetime.now(timezone.utc), status='sent'))
    s.add(Lead(threads_username='seed_user', score=80, status='scored'))
    s.add(PlaybookRule(rule_text='post at 9am local time', status='proposed', version=1, hypothesis='morning posts get more replies'))

    run = AgentRun(agent='content', trigger='cron', started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc), status='ok', steps_count=2, tokens_in=1200, tokens_out=300, cost_usd=0.01)
    s.add(run)
    s.flush()
    s.add(AgentStep(run_id=run.id, step_no=1, thought='check playbook', tool_name='get_playbook', tool_ok=True, tool_ms=120))
    s.add(AgentStep(run_id=run.id, step_no=2, thought='save the draft', tool_name='save_draft', tool_ok=True, tool_ms=80))
print('seeded')
"
```

- [ ] **Step 3: Start the dashboard dev server against the local stack**

```bash
cd dashboard
cat > .env.local <<'EOF'
API_BASE_URL=https://localhost:8443
API_BEARER_TOKEN=changeme
DASHBOARD_PASSWORD=testpass
EOF
npm run dev &
sleep 3
```

Use the same `API_BEARER_TOKEN` value as your root `.env`'s `API_BEARER_TOKEN`, and adjust `API_BASE_URL`'s port to match your local Caddy port remap if you have one (see the README's existing port-remap note from Block 0/1).

- [ ] **Step 4: Walk through the login gate and all five screens**

```bash
curl -s -o /dev/null -w "unauthenticated / -> %{http_code}\n" http://localhost:3000/
curl -s -c /tmp/dashboard-cookies.txt -X POST http://localhost:3000/api/login -d "password=testpass&next=/" -o /dev/null -w "login -> %{http_code}\n"
for path in / /posts /agents /styles /playbook; do
  curl -s -b /tmp/dashboard-cookies.txt -o /dev/null -w "$path -> %{http_code}\n" "http://localhost:3000$path"
done
```

Expected: the first line shows a redirect status (`307` or similar, not `200`) since there's no cookie yet; the login line shows `303`; all five authenticated paths show `200`.

- [ ] **Step 5: Manually verify the approve/reject flow in a browser**

This part needs a real browser (forms + Server Actions aren't meaningfully exercised by `curl`): open `http://localhost:3000/styles` in a browser, log in with `testpass` if prompted, click "Принять" or "Отклонить" on the seeded `v2-radical` draft row, and confirm the row's status updates after the page reloads. Repeat on `/playbook` for the seeded `proposed` rule.

- [ ] **Step 6: Stop the dev server**

```bash
kill %1
cd ..
```

- [ ] **Step 7: Add a "Dashboard (frontend)" section to `README.md`**

Add this after the "Dashboard API" section (added by the companion API plan's Task 7):

```markdown
## Dashboard (frontend)

`dashboard/` is a separate Next.js 15 project, deployed independently to Vercel.

**Local development:**

```
cd dashboard
cp .env.example .env.local
# fill in API_BASE_URL (your local Caddy endpoint), API_BEARER_TOKEN (must match
# the root .env's API_BEARER_TOKEN), and DASHBOARD_PASSWORD (any value you choose)
npm install
npm run dev
```

Open `http://localhost:3000`, log in with the `DASHBOARD_PASSWORD` you set.

**Deploying to Vercel:**

1. Import this repository into a new Vercel project.
2. In the project's settings, set **Root Directory** to `dashboard`.
3. Add three environment variables in the Vercel project settings: `API_BASE_URL`
   (your production Caddy endpoint, reachable from the internet), `API_BEARER_TOKEN`
   (matching the production server's `.env`), and `DASHBOARD_PASSWORD` (a password
   only you know — this is the only thing standing between the public internet and
   your dashboard, since Vercel deployment URLs are public by default).
4. Deploy. The five screens are reachable once you log in with `DASHBOARD_PASSWORD`.
```

- [ ] **Step 8: Commit**

```bash
git add README.md
git commit -m "docs: document dashboard frontend local dev and Vercel deployment"
```
