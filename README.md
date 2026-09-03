# Autonomous Threads Agent

Autonomous Threads-posting agent. See `SPEC.md` for the full design.

## Getting Started

1. **Copy the env file and fill in credentials**

   ```
   cp .env.example .env
   ```

   Fill in real values for the LLM provider keys, Threads Graph API credentials,
   Airtable (one-time import only), and Telegram alert settings.

2. **Start Postgres**

   ```
   docker compose up -d postgres
   ```

   If ports `5432`, `80`, or `443` already conflict with something else running on
   your machine, create a `docker-compose.override.yml` next to `docker-compose.yml`
   (it's gitignored, so it stays local) and remap the host-side port there, e.g.:

   ```yaml
   services:
     postgres:
       ports:
         - "5433:5432"
   ```

   Compose automatically merges override files with the base file — no changes to
   `docker-compose.yml` itself are needed. Adjust `DATABASE_URL` in your `.env`
   accordingly if you remap Postgres.

3. **Start the rest of the stack**

   ```
   docker compose up -d --build
   ```

   The `worker` and `api` containers run `alembic upgrade head` automatically before
   starting their application process, so the schema is always current on startup —
   no manual migration step is needed in the normal Docker path. Alembic no-ops when
   already at head, so this is safe to run on every restart.

   For local host-side development or testing outside Docker, run migrations
   manually (use `python -m alembic`, not the bare `alembic` script, so the
   current directory — needed for `migrations/env.py`'s `from src.db.models import
   Base` — is on `sys.path`):

   ```
   python -m alembic upgrade head
   ```

4. **`THREADS_APP_PATH`**

   `ThreadsReadClient` (and the future `feed_miner` agent) shells out to a separate
   `threads_app` project for browser-based Threads scraping. That project is **not**
   vendored into this repo or the Docker image — set `THREADS_APP_PATH` in your
   `.env` to a local checkout of it. This is only required when code that actually
   calls `ThreadsReadClient.search_keyword()` runs; importing this codebase does not
   require it.

5. **Content generation (Block 3) setup**

   `content_agent` needs two one-time (idempotent) seed scripts run against
   the database before it can produce posts, and a search API key for its
   `category='news'` research tool:

   ```
   python -m scripts.seed_knowledge_base
   python -m scripts.seed_style_variant_v1
   ```

   `seed_knowledge_base` loads the Layer 2 starter knowledge base
   (`SPEC.md` §7). `seed_style_variant_v1` seeds a Layer 3 style genome so
   the pipeline is runnable end-to-end.

   Set `TAVILY_API_KEY` in `.env` — `web_search()` (used only for
   `category='news'` drafts) returns `[]` silently if it's unset, which is
   safe for testing but means news posts will never get sources in
   production.

   > **PLACEHOLDER GENOME — DO NOT SKIP:** the style genome seeded by
   > `seed_style_variant_v1` is explicitly a stand-in
   > (`style_variants.name='v1_placeholder'`), not the operator's real voice.
   > `SPEC.md` §7 requires this genome to be human-authored. **Do not let any
   > real (non-test) post go out until you have replaced it** — the seed
   > script itself prints the exact statement to run:
   >
   > ```
   > UPDATE style_variants SET genome = '...' WHERE id = <printed id>;
   > ```
   >
   > Replace `'...'` with your actual 300-800 word authored voice before
   > production posting begins.

6. **Running tests**

   Tests need a running Postgres and a `threads_agent_test` database, and read the
   `DATABASE_URL` env var (see `tests/conftest.py` for the default). Example:

   ```
   export DATABASE_URL=postgresql+psycopg://threads_agent:changeme@localhost:5432/threads_agent_test
   pytest
   ```

   Adjust the host/port to match wherever your Postgres container is actually
   reachable (see the port-remap note in step 2 if you're using a
   `docker-compose.override.yml`).

## Dashboard API

Once the stack is up (`docker compose up -d --build`), the dashboard endpoints are
reachable through Caddy at `https://<host>/posts`, `/runs`, `/runs/{id}/steps`,
`/styles`, `/playbook`, `/funnel`, `/spend`. Every route except `/health` requires
`Authorization: Bearer <API_BEARER_TOKEN>` (the same value as your `.env`).

Example (`-k`/`--insecure` is required locally because the dev Caddyfile uses a
self-signed certificate that curl won't trust by default):

```
curl -k -H "Authorization: Bearer $API_BEARER_TOKEN" https://localhost:8443/posts
```

See `docs/superpowers/specs/2026-09-01-block-4-dashboard-design.md` for the full
endpoint list and the frontend that consumes them.

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

> **Note:** the local Caddyfile serves `https://localhost:8443` with a self-signed
> certificate (`tls internal { on_demand }`). Node's `fetch()` (used by
> `dashboard/lib/api-client.ts`) rejects self-signed certs with
> `SELF_SIGNED_CERT_IN_CHAIN` and has no per-request CA override, so pointing
> `API_BASE_URL` straight at `https://localhost:8443` will make every dashboard
> page error out. Two ways around this:
>
> - **Preferred:** skip Caddy locally and point `API_BASE_URL` directly at the API
>   service — e.g. expose the `api` container's port directly (`http://localhost:8000`
>   if you've mapped it in a `docker-compose.override.yml`), or run
>   `uvicorn src.api.main:app --host 0.0.0.0 --port 8010` locally outside Docker
>   against your dev Postgres.
> - **If you specifically need to test through Caddy locally:** set
>   `NODE_TLS_REJECT_UNAUTHORIZED=0` in the environment running `npm run dev`. This
>   disables TLS certificate verification for the whole Node process — it is fine
>   for local-only testing but **must never be used in production or with any
>   real/deployed API endpoint**.

**Deploying to Vercel:**

1. Import this repository into a new Vercel project.
2. In the project's settings, set **Root Directory** to `dashboard`.
3. Before pointing `API_BASE_URL` at your production Caddy endpoint, make sure the
   production `Caddyfile` uses a real, resolvable hostname instead of the local-dev
   `:443 { tls internal { on_demand } }` block — Caddy needs a real domain to
   provision a proper ACME certificate (e.g. via Let's Encrypt), since Vercel's
   `fetch()` will reject a self-signed certificate the same way Node does locally.
   For example:
   ```
   your-real-domain.example.com {
       reverse_proxy api:8000
   }
   ```
   Then add three environment variables in the Vercel project settings: `API_BASE_URL`
   (your production Caddy endpoint, reachable from the internet, e.g.
   `https://your-real-domain.example.com`), `API_BEARER_TOKEN` (matching the
   production server's `.env`), and `DASHBOARD_PASSWORD` (a long, random password —
   generate one with `openssl rand -base64 24` — since `/api/login` has no rate
   limiting and this password is the only thing standing between the public internet
   and your dashboard, as Vercel deployment URLs are public by default).
4. Deploy. The five screens are reachable once you log in with `DASHBOARD_PASSWORD`.
