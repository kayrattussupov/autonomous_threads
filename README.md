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

5. **Running tests**

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
