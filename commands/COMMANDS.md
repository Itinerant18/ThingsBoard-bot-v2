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

PowerShell equivalent for env vars: `$env:APP_ROLE = "chat"; uv run uvicorn app.main:app`

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
