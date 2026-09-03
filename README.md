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

4. **Browser-based Threads login (`src/threads/browser`)**

   `ThreadsReadClient` / `feed_miner` read the public feed via a headless-Chrome
   Selenium login (`src/threads/browser`), fully vendored into this repo — no
   external checkout required. Login is cookie-first: run
   `python -m scripts.threads_setup_session` once, locally, on a machine with a
   real display, to log in by hand and save a session to
   `THREADS_COOKIES_PATH` (`data/threads_cookies.json` by default). Only if that
   session is missing or expired does `login()` fall back to
   `THREADS_USERNAME`/`THREADS_PASSWORD` from `.env`, driving the real login
   form via Selenium.

   The `worker` service mounts a `threads_session` volume at `/app/data` so the
   cookie file survives `docker compose down`/`up` instead of forcing a fresh
   login on every redeploy. If you ever need to seed that volume from a
   session saved outside Docker, copy `data/threads_cookies.json` into it, e.g.:
   `docker compose cp data/threads_cookies.json worker:/app/data/threads_cookies.json`.

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

Example:

```
curl -H "Authorization: Bearer $API_BEARER_TOKEN" https://localhost:8443/posts
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

**Deploying to Vercel:**

1. Import this repository into a new Vercel project.
2. In the project's settings, set **Root Directory** to `dashboard`.
3. Add three environment variables in the Vercel project settings: `API_BASE_URL`
   (your production Caddy endpoint, reachable from the internet), `API_BEARER_TOKEN`
   (matching the production server's `.env`), and `DASHBOARD_PASSWORD` (a password
   only you know — this is the only thing standing between the public internet and
   your dashboard, since Vercel deployment URLs are public by default).
4. Deploy. The five screens are reachable once you log in with `DASHBOARD_PASSWORD`.

## Deployment

The backend (`postgres`, `worker`, `api`, `caddy`) runs on a single AWS EC2
instance via Docker Compose; the dashboard frontend (`dashboard/`) deploys to
Vercel as described above.

- **`docker-compose.yml`** (this repo, used locally) builds the app image from
  the local `Dockerfile` via `build: .`.
- **`docker-compose.prod.yml`** is the server-only counterpart: `worker`/`api`
  use `image: ${ECR_REPOSITORY}:${IMAGE_TAG:-latest}` instead of `build: .`.
  It is not built locally — GitHub Actions builds the image, pushes it to
  Amazon ECR, and the server only ever runs `docker compose -f
  docker-compose.prod.yml pull && ... up -d` against it.
- Pushing to `main` triggers `.github/workflows/deploy-backend.yml`, which
  builds the image, pushes it to ECR, then deploys over SSH.
  `.github/workflows/ci.yml` runs the pytest suite (with a Postgres service
  container) on every PR and push. `.github/workflows/ci-frontend.yml`
  typechecks and builds `dashboard/` on PRs that touch it; the actual
  frontend deploy is handled by Vercel's own GitHub integration, not Actions.
- The server's `.env` (all real secrets from `.env.example`, plus
  `ECR_REPOSITORY`) is created once by hand on the EC2 instance and is never
  written by CI. GitHub Secrets hold only deploy mechanics: the AWS OIDC role
  ARN, `AWS_REGION`, `ECR_REPOSITORY`, `EC2_HOST`, `EC2_SSH_USER`, and
  `EC2_SSH_KEY`.
- `Caddyfile` currently uses `tls internal { on_demand }` (self-signed, for
  local/dev use). Once a real domain points at the server's IP, change the
  `:443` block to that domain so Caddy requests a real Let's Encrypt
  certificate.
