# ThingsBoard-Bot v2 — Command Reference

All commands run from the project root (`Thingsboard-Bot-v2/`) unless noted.
Secrets come from `.env` (copy `.env.example`); never put them on the command line.

## Setup

```bash
# Install/sync all dependencies into .venv (uses uv, not pip)
uv sync

# Copy the environment template, then fill in TB_URL / TB_USER / TB_PASSWORD etc.
cp .env.example .env
```

## Infrastructure (Docker)

```bash
# Full local stack: Postgres + Redis + RabbitMQ (+ app services)
docker compose up -d postgres redis rabbitmq

# Just a local Redis (needed by tests/test_device_access.py)
docker run -d --name tb-bot-redis -p 6379:6379 redis:7-alpine

# Status / logs / stop
docker ps
docker logs -f tb-bot-redis
docker stop tb-bot-redis && docker rm tb-bot-redis
```

## Run the application

```bash
# All-in-one node (default: APP_ROLE=all) — chat + data + admin + webhook + schedulers
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

# Chat node only (no webhook surface; runs live-sync + reconcile schedulers)
APP_ROLE=chat uv run uvicorn app.main:app --port 8000

# Ingestion node only (webhook + health; publishes to RabbitMQ)
APP_ROLE=ingestion uv run uvicorn app.main:app --port 8001

# Consumer worker — catch-all queue (all customers)
uv run python -m app.ingest.consumer

# Consumer worker — dedicated per-customer queue (topic-exchange binding)
uv run python -m app.ingest.consumer --customer BOI
```

### Broker topology (v2 is namespaced separately from Java)

| Object | Name | Purpose |
|---|---|---|
| Topic exchange | `v2.events.topic` | webhook publishes here, key `customer.<PREFIX>` |
| Catch-all queue | `v2.events` | bound `customer.#` — every customer |
| Per-customer queue | `v2.events.<PREFIX>` | bound `customer.<PREFIX>` |
| Dead-letter exchange | `v2.dlx` | rejected messages |
| Dead-letter queue | `v2.events.dead` | bound to `v2.dlx` |

The Java stack's `iot.events` / `iot.dlx` are deliberately NOT reused: Java publishes a
different payload shape (`deviceId`, `tbMessageId`, no `tenant_id`) that `EventParse`
rejects, so sharing a queue would dead-letter everything and make both consumers compete.
Separate names let both stacks run in parallel during cutover.

**Run the catch-all worker OR per-customer workers, not both** — a topic exchange delivers
to every matching queue, so `customer.#` and `customer.BOI` both receive BOI messages
(idempotency makes that safe, but it is wasted work). Do not leave a per-customer queue
declared with no worker attached; it will accumulate messages forever.

PowerShell equivalent for env vars: `$env:APP_ROLE = "chat"; uv run uvicorn app.main:app`

## Chat tester UI (frontend/)

Served by the app itself at `/ui`, so it is same-origin with the API (no CORS setup
needed). Mounted only for `APP_ROLE=all|chat`, and only when `SERVE_UI=true`.

```bash
# Start a dev server for manual testing (background jobs off so it boots fast)
TIMESCALE_INIT_ENABLED=false TB_SCHEDULED_SYNC_ENABLED=false \
RECONCILIATION_ENABLED=false WEBHOOK_PUBLISH_TO_QUEUE=false \
  uv run uvicorn app.main:app --port 8077

# then open  http://127.0.0.1:8077/ui/
```

Set the ThingsBoard JWT from the browser console (or the "set token" button):

```js
localStorage.setItem('tb_jwt', 'PASTE_YOUR_TB_JWT')   // then reload
setToken('PASTE_YOUR_TB_JWT')                          // helper, no reload needed
clearToken()
```

The header shows the decoded `sub` / `firstName` / `customerTitle` claims so you can
see which scope you are testing as. The `conv` box controls `conversation_id` — keep it
to test follow-up memory ("battery voltage of Liluah" → "and its cctv status?"), press
**new** to start a fresh context. Every reply has a collapsible raw-response view.

## One-time setup for a fresh database

Point `DATABASE_URL` at the instance first. Tiger/Timescale Cloud hands out a
`postgres://…?sslmode=require` URL; asyncpg needs it rewritten as
`postgresql+asyncpg://…?ssl=require`.

```bash
uv run alembic upgrade head                       # 1. create the v2 schema
uv run python scripts/extract_tb_data.py --out data/tb_extract_full.json   # 2. pull TB
curl -X POST http://127.0.0.1:8077/api/v1/admin/import \
  -H "Content-Type: application/json" --data @data/tb_extract_full.json    # 3. hierarchy
PYTHONPATH=. uv run python scripts/seed_customers.py                       # 4. prefixes
```

Step 4 is required: `current_tenant` maps a JWT's `customerId` to a customer prefix via
the `customer` table. Skip it and every answer is "your token is not mapped to a customer".

## Quality gate (run before every commit)

```bash
uv run ruff check app tests          # lint
uv run ruff check --fix app tests    # lint with autofix
uv run mypy app                      # strict type check
uv run pytest -q                     # full test suite (needs Docker for testcontainers)

# Suite without the Docker-dependent tests
uv run pytest -q --ignore=tests/test_device_access.py --ignore=tests/test_hierarchy_store.py

# One file / one test
uv run pytest -q tests/test_metric_handlers.py
uv run pytest -q tests/test_branch_names.py::test_gate_flags_out_of_scope_branch
```

## Data extraction (ThingsBoard → JSON dump)

```bash
# Pull ALL tenant devices: latest telemetry + server/client/shared attributes.
# 429-aware (exponential backoff honoring Retry-After); output is gitignored.
uv run python scripts/extract_tb_data.py --out data/tb_extract_full.json

# Tune throttling if ThingsBoard rate-limits differently
uv run python scripts/extract_tb_data.py --out data/tb_extract.json --concurrency 2
```

## Admin API (X-Admin-Token header required when ADMIN_TOKEN is set)

```bash
# Import hierarchy from an extraction dump (builds hierarchy_node + closure table)
curl -X POST http://localhost:8000/api/v1/admin/import \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  --data @data/tb_extract_full.json

# Inspect an imported hierarchy
curl "http://localhost:8000/api/v1/admin/hierarchy?customer_id=BOI" \
  -H "X-Admin-Token: $ADMIN_TOKEN"

# Bootstrap the Redis fleet snapshot NOW (one synchronous live-sync cycle)
curl -X POST http://localhost:8000/api/v1/admin/init -H "X-Admin-Token: $ADMIN_TOKEN"

# Rebuild fleet snapshot from stored DeviceEvent history (defaults: last 7 days)
curl -X POST http://localhost:8000/api/v1/admin/replay \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -d '{"customer_id": "BOI"}'

# Replay every customer over an explicit window
curl -X POST http://localhost:8000/api/v1/admin/replay \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -d '{"customer_id": "ALL", "start_time": "2026-07-01T00:00:00Z", "end_time": "2026-07-27T00:00:00Z"}'
```

## Chat / data API (Bearer = ThingsBoard user JWT)

```bash
# Ask a question (branch names work: gate + resolution are token-scoped)
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TB_USER_JWT" \
  -d '{"message": "battery voltage of Liluah", "conversation_id": "default"}'

# Scoped device list / telemetry / chart
curl http://localhost:8000/api/v1/data -H "Authorization: Bearer $TB_USER_JWT"
curl http://localhost:8000/device/<device-uuid>/telemetry -H "Authorization: Bearer $TB_USER_JWT"
curl "http://localhost:8000/device/<device-uuid>/chart?key=battery_status&hours=24" \
  -H "Authorization: Bearer $TB_USER_JWT"
```

## Key environment flags (see app/config.py for the full list)

| Variable | Default | Meaning |
|---|---|---|
| `APP_ROLE` | `all` | `all` / `chat` / `ingestion` node profile |
| `TB_SCHEDULED_SYNC_ENABLED` | `true` | 60s live sync of fleet snapshot |
| `RECONCILIATION_ENABLED` | `true` | daily drift check + auto-repair replay |
| `WEBHOOK_PUBLISH_TO_QUEUE` | `true` | webhook enqueues to RabbitMQ |
| `WEBHOOK_DIRECT_WRITE_FALLBACK` | `true` | broker down → write DB directly |
| `REQUIRE_ADMIN_TOKEN` / `ADMIN_TOKEN` | off / empty | gate admin endpoints |
| `REQUIRE_WEBHOOK_HMAC` / `WEBHOOK_HMAC_SECRET` | off / empty | webhook HMAC verification |
| `OPENAI_API_KEY` | empty | enables LLM intent extraction (falls back to keywords) |

## Production deploy (EC2)

The host runs `chatbot-v2` + `chatbot-v2-consumer` behind the existing `caddy-proxy`.
`~/ThingsBoard-Bot-v2/` is a git checkout of this repo, so deploying is pull + rebuild —
do NOT scp files in, or the host silently diverges from what the repo says is running.

`.env` lives only on the host (gitignored, never committed). `git reset --hard` leaves
it alone because it is untracked.

```bash
ssh -i <key>.pem ubuntu@<host>

cd ~/ThingsBoard-Bot-v2
docker tag thingsboard-bot-v2:latest thingsboard-bot-v2:rollback   # keep a way back
git pull --ff-only origin main
docker compose -f docker-compose.prod.yml up -d --build
```

Verify the deploy — an app INFO line proves logging works, and the per-customer
sync summary proves the scheduler ran:

```bash
docker ps --format '{{.Names}} | {{.Status}}'
docker logs chatbot-v2 2>&1 | grep 'LIVE-SYNC'      # expect "synced N/N" per customer
docker logs chatbot-v2 2>&1 | grep -ci traceback    # expect 0
docker exec chatbot-v2 python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8083/health').status)"
```

Rollback:

```bash
docker compose -f docker-compose.prod.yml down
docker tag thingsboard-bot-v2:rollback thingsboard-bot-v2:latest
docker compose -f docker-compose.prod.yml up -d      # no --build: reuse the old image
```

Confirm what is actually deployed (guards against host/repo drift):

```bash
cd ~/ThingsBoard-Bot-v2 && git log --oneline -1 && git status --short   # expect clean
```

### Log levels

App loggers get a handler from `configure_logging()` in `app/main.py`; uvicorn only
configures its own, leaving root at WARNING, so without it every `[LIVE-SYNC]`,
`[REPLAY]` and `[SCHEDULER]` line is discarded before reaching container stdout.
`httpx` is pinned to WARNING — at INFO it logs one line per request, which is ~740k
lines/day from live sync alone.

```bash
LOG_LEVEL=DEBUG    # override in .env when debugging; default INFO
```
