# ThingsBoard IoT Chatbot Backend (v2)

The `Thingsboard-Bot-v2` project is a full-stack AI-powered chatbot backend and integration layer for the ThingsBoard IoT platform. It provides a natural language interface for users to query real-time device statuses, telemetry, and alarms, while enforcing strict, region-aware access control.

## 🌟 Key Features

* **AI-Powered Queries**: Understands user intent via OpenAI models (with keyword-based fallback) and answers complex questions about IoT device fleets.
* **Tenant & Role-Safe**: Strictly enforces ThingsBoard ACLs and custom regional scopes. Users only see data they are authorized to access.
* **Real-time Data Ingestion**: Processes live webhook events from ThingsBoard via RabbitMQ, ensuring immediate updates to device telemetry.
* **Fleet Synchronization**: Background tasks keep a live snapshot of fleet state cached in Redis for fast query responses.
* **Full-Stack Implementation**: Features a Python FastAPI backend for core logic and an integrated React (Vite/TypeScript) frontend for the chat widget.
* **Modern Stack**: Fully asynchronous Python (`asyncio`, `asyncpg`, `httpx`), PostgreSQL (TimescaleDB) for time-series persistence, and Docker orchestration.

---

## 🏗 Architecture Overview

The system follows a modular architecture within a single application deployment:

* **FastAPI Backend (`app/`)**: Serves API endpoints, handles authentication, routes chat intents, integrates with ThingsBoard, and manages database interactions.
* **React Frontend (`frontend/`)**: A chat UI built with React and TailwindCSS. It is built as static assets and served by the FastAPI application.
* **RabbitMQ Consumer (`app/ingest/consumer.py`)**: A dedicated asynchronous worker that processes telemetry webhooks, enforces idempotency, and updates Redis live states.
* **PostgreSQL + TimescaleDB**: Handles structured persistence of `DeviceEvent`, `DeviceTelemetry`, and device hierarchies.
* **Redis**: Used heavily for caching fleet snapshots, thingsboard authentication states, and ingestion idempotency checks.

---

## 📁 Directory Structure

```text
├── app/                  # Core Python FastAPI backend
│   ├── api/              # API Endpoints (Chat, Webhooks, Admin)
│   ├── auth/             # Authentication & ThingsBoard ACL enforcement
│   ├── clients/          # External integrations (ThingsBoard API clients)
│   ├── db/               # SQLAlchemy models and database session management
│   ├── ingest/           # Webhook parsing, RabbitMQ publisher, and consumer workers
│   ├── llm/              # LLM integration and Intent extraction
│   ├── query/            # Query orchestrators and intent handlers
│   └── tasks/            # Background tasks (Fleet sync, reconciliation)
├── frontend/             # React chat widget source code (Vite + TypeScript)
├── mcp/                  # Model Context Protocol integrations and server configs
├── agent/                # Scripts and rules for local LLM agents
├── data/ & json_data/    # Local JSON data dumps and hierarchy mapping structures
├── docs/                 # API specifications, key mappings, PRDs, and codebase analysis
├── scripts/              # Utility scripts for data backfilling, extraction, and seeding
├── tests/                # Unit and integration tests (pytest, pytest-asyncio)
├── alembic.ini           # Alembic migration configuration
├── Caddyfile             # Reverse proxy configuration for production (handles SSE)
├── docker-compose.yml    # Development environment orchestration
└── pyproject.toml        # Python project configuration (uv dependencies)
```

---

## 🚀 Getting Started (Development)

### Prerequisites

* [Docker Desktop](https://www.docker.com/products/docker-desktop/) or Docker Engine with Docker Compose.
* Python 3.12+ (if developing locally without containers).
* [uv](https://github.com/astral-sh/uv) package manager (for Python dependencies).

### 1. Environment Setup

Copy the example environment file and configure your local settings, ThingsBoard API keys, and OpenAI credentials:

```bash
cp .env.example .env
```

Ensure you configure the `THINGSBOARD_URL`, `OPENAI_API_KEY`, and any necessary database connection strings in `.env`.

### 2. Run the Stack (Docker Compose)

Start the entire stack (FastAPI, RabbitMQ, PostgreSQL, Redis) in detached mode:

```bash
docker compose up --build -d
```

### 3. Initialize Database Schema

Once the containers are running, execute Alembic migrations to set up the TimescaleDB tables:

```bash
docker compose exec app alembic upgrade head
```

### 4. Verify Health

Check if the application is running correctly:

```bash
curl http://localhost:8083/health
```

You can view the interactive API documentation at: [http://localhost:8083/docs](http://localhost:8083/docs)

### 5. Seed Test Data (Optional)

To seed a basic testing customer and hierarchy:

```bash
curl -X POST http://localhost:8083/api/v1/admin/import \
  -H 'Content-Type: application/json' \
  -d '[{"id":"00000000-0000-0000-0000-000000000001","name":"BOI-BRANCH","telemetry":{"full_path":"BOI HO → ZO East → BOI-BRANCH"}}]'
```

---

## 🛠 Testing

The project uses `pytest` and heavily tests the asynchronous workflows. To run tests locally (make sure dev dependencies are installed via `uv sync`):

```bash
uv run pytest
```

*(Note: Some tests may require Docker for `testcontainers` (Postgres, Redis) to run properly).*

---

## 📚 Further Reading

For deep-dive documentation on specific systems, refer to the `docs/` directory:

* **[Codebase Analysis](docs/Codebase-Analysis/CODEBASE_ANALYSIS.md)**: Detailed breakdown of architectural patterns, scopes, data flow, and gotchas.
* **[PRD](docs/PRD/product_requirements_document.md)**: Product Requirements Document.
* **[FAQ System Prompt](docs/Question%20&%20Answer/thingsboard-chatbot-faq.md)**: Details on expected chatbot Q&A handling and rules.
