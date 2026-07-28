# CODEBASE_ANALYSIS.md

## High-Level Overview

The `Thingsboard-Bot-v2` project is a full-stack application designed to provide an AI-powered chatbot backend for ThingsBoard IoT platform integration. It features a Python FastAPI backend for core logic and API exposure, and a React-based frontend for the chat widget. The system is containerized using Docker and orchestrated with Docker Compose, leveraging PostgreSQL (TimescaleDB), Redis, and RabbitMQ for data persistence, caching, and message queuing, respectively.

**Tech Stack Summary:**

* **Backend:** Python 3.12, FastAPI, SQLAlchemy (asyncpg), Pydantic, Redis (asyncio), aio-pika (RabbitMQ client), httpx, OpenAI API client.
* **Frontend:** React, Vite, TypeScript, TailwindCSS.
* **Database:** PostgreSQL with TimescaleDB extension (for `DeviceEvent` and `DeviceTelemetry`), managed with Alembic for migrations.
* **Caching/Messaging:** Redis for idempotency and fleet state snapshots, RabbitMQ for event ingestion.
* **Containerization:** Docker, Docker Compose.
* **Reverse Proxy:** Caddy (for production deployment).
* **LLM Integration:** OpenAI API for intent extraction and query orchestration.

**Overall Architecture:**

The architecture follows a microservice-like pattern, though deployed as a single application with distinct roles (`all`, `chat`, `ingestion`) configurable via `app_role` environment variable. It consists of:

* **FastAPI Backend:** The core application (`app/main.py`) serving API endpoints for chat, admin, data, and webhooks. It integrates with ThingsBoard, LLMs, Redis, and PostgreSQL.
* **Frontend Chat Widget:** A React application (`frontend/`) built with Vite, served statically by the FastAPI backend at `/ui`.
* **Asynchronous Workers:** A RabbitMQ consumer (`app/ingest/consumer.py`) processes events for persistence and state updates, running as a separate Docker service.
* **Scheduled Tasks:** Background tasks (`app/tasks/`) for live synchronization of fleet states and reconciliation, managed by `app/tasks/scheduler.py`.
* **ThingsBoard Integration:** Dedicated clients (`app/clients/thingsboard.py`) for interacting with the ThingsBoard API, handling authentication, pagination, and data retrieval.

**Main Business Domains / Feature Areas:**

* **IoT Chatbot:** Answering natural language queries about IoT device status, telemetry, and alarms, integrated with ThingsBoard data.
* **ThingsBoard Data Ingestion:** Processing webhook events from ThingsBoard via RabbitMQ, persisting them, and updating live fleet states.
* **Fleet Management:** Synchronizing device hierarchy and telemetry data from ThingsBoard, and reconciling data consistency.
* **Access Control:** Implementing granular access control based on user tokens and regional scope, ensuring users only access authorized device data.
* **LLM-powered Intent Extraction:** Using large language models (or a keyword-based fallback) to understand user queries and route them to appropriate handlers.

## Directory Map & Responsibilities

The project root contains several key directories and files, each with a specific role in the application's overall structure and functionality:

* **`app/`**: This directory houses the core Python FastAPI application logic. It is further subdivided into modules responsible for API endpoints, authentication, caching, client integrations (e.g., ThingsBoard), database interactions, hierarchy management, data ingestion, LLM integration, query processing, and background tasks.
* **`frontend/`**: Contains the React-based chat widget frontend. This includes source code (`src/`), build artifacts (`dist/`), and Node.js-related configuration and dependencies.
* **`commands/`**: This directory likely contains standalone command-line utilities or scripts for various operational tasks, though its contents were not explicitly listed in the initial exploration.
* **`data/`**: Potentially used for storing static data, fixtures, or configuration files, but its exact purpose requires further investigation.
* **`docs/`**: Holds documentation files, including API specifications (`API-TB.md`, `API-TB.pdf`), real device key mappings (`real-device-keys.md`), and telemetry/attribute key details (`Telimetry-Attribute-key.docx`, `Telimetry-Attribute-key.md`, `thingsboard-key-map.md`).
* **`scripts/`**: Contains various utility scripts, such as `backfill_telemetry.py`, `backup_v1.py`, `deploy.sh`, `extract_tb_data.py`, `purge_v1.py`, and `seed_customers.py`, used for maintenance, data operations, and deployment.
* **`tests/`**: Dedicated to housing unit and integration tests for the Python backend, ensuring code quality and correctness.
* **`alembic.ini`**: Configuration file for Alembic, the database migration tool, used to manage schema changes for PostgreSQL.
* **`Caddyfile`**: Configuration for the Caddy web server, used as a reverse proxy in production deployments, handling aspects like security headers and SSE streaming.
* **`Dockerfile`**: Defines the Docker image build process for the application, including dependencies, build stages for frontend and backend, and the final runtime environment.
* **`docker-compose.yml`**: Defines the multi-container Docker application for development, orchestrating services like the FastAPI app, RabbitMQ consumer, PostgreSQL, Redis, and RabbitMQ.
* **`docker-compose.prod.yml`**: A production-specific Docker Compose file, likely overriding or extending `docker-compose.yml` for production deployment concerns.
* **`pyproject.toml`**: Python project configuration, including dependencies, project metadata, and tool-specific settings (e.g., pytest, ruff, mypy).
* **`uv.lock`**: A lock file generated by `uv`, a Python package installer and resolver, ensuring deterministic dependency resolution.

**Core Modules:**

* **`app/db/`**: Manages database connections, SQLAlchemy models (`models.py`), and session management. It also contains `migrations/` for Alembic scripts.
* **`app/auth/`**: Handles authentication and authorization concerns, including JWT processing, customer/tenant context, and ThingsBoard ACL enforcement.
* **`app/clients/`**: Provides client implementations for external services, primarily `thingsboard.py` for interacting with the ThingsBoard API.
* **`app/query/`**: Contains the core logic for processing natural language queries, extracting intents, orchestrating handlers, and managing chat memory.
* **`app/ingest/`**: Responsible for the data ingestion pipeline, including parsing webhook payloads, publishing to RabbitMQ, consuming messages, and persisting data.
* **`app/tasks/`**: Defines background tasks and schedulers for operations like live synchronization of ThingsBoard data and reconciliation of fleet states.

## Key Files & Components (Per Folder)

### `app/` (Core Application Logic)

* **`main.py`**: The primary entry point for the FastAPI application. It configures logging, initializes the FastAPI app, sets up middleware (CORS, security headers), manages the application lifespan (database engine, Redis, ThingsBoard client, RabbitMQ publisher, background tasks), and mounts the various API routers and the static frontend UI.
* **`config.py`**: Defines the application's configuration settings using `pydantic-settings`. It loads environment variables from a `.env` file and provides a structured `Settings` class with properties for database URLs, API keys, application roles, and various feature flags.

### `app/api/` (API Endpoints)

* **`chat.py`**: Defines the endpoints for the chat interface (`/api/v1/chat`, `/ask`, `/ask/stream`). It handles incoming chat requests, manages session context, and delegates query processing to the `QueryOrchestrator`. The `/ask/stream` endpoint implements Server-Sent Events (SSE) for real-time response streaming to the frontend widget.
* **`webhooks.py`**: Provides the endpoint (`/webhooks/thingsboard`) for receiving webhook events from ThingsBoard. It verifies HMAC signatures, parses payloads, and publishes events to RabbitMQ (or writes directly to the database as a fallback).

### `app/auth/` (Authentication & Authorization)

* **`tb_acl.py`**: Implements the logic for determining the set of devices a caller is authorized to access based on their ThingsBoard token. It interacts with the ThingsBoard API (`/api/auth/user`, `/api/tenant/devices`, `/api/customer/{customerId}/devices`) to resolve permissions and caches the results in Redis.
* **`scope_resolver.py`**: Provides the `resolved_scope` function, which intersects the local hierarchy scope (derived from the customer prefix and regional claims) with the ThingsBoard-authorized device set. This ensures that users only access data they are permitted to see in both systems.

### `app/clients/` (External Service Clients)

* **`thingsboard.py`**: Contains the `ThingsBoardClient` and `UserAwareThingsBoardClient` classes. The former uses service account credentials for general API access, while the latter uses the caller's token for permission-aware operations. It handles authentication, token refresh, and pagination for various ThingsBoard API endpoints.

### `app/db/` (Database Management)

* **`models.py`**: Defines the SQLAlchemy ORM models for the application's database schema, including `Customer`, `DeviceEvent`, `DeviceTelemetry`, `HierarchyNode`, `BranchAncestorPath`, and `BranchIdentity`. It incorporates specific design choices for TimescaleDB and hierarchical queries.

### `app/ingest/` (Data Ingestion Pipeline)

* **`consumer.py`**: Implements the RabbitMQ consumer worker. It processes incoming events, performs idempotency checks using Redis, persists events to the database, flattens telemetry data, and updates the live fleet state in Redis. It can run as a catch-all worker or a dedicated per-customer worker.
* **`parse.py`**: Provides utilities for parsing and normalizing ThingsBoard rule-chain events. It defines the `EventParse` model and functions for extracting tenant, customer, device, and event information, as well as flattening nested telemetry payloads.
* **`publisher.py`**: Manages the RabbitMQ publisher and topology. It defines exchange, queue, and dead-letter exchange names, and provides the `RabbitPublisher` class for publishing messages to the topic exchange.

### `app/llm/` (LLM Integration)

* **`intent.py`**: Contains the `LlmIntentExtractor` class, which uses an LLM (via OpenAI API) to classify user queries into predefined intents and extract relevant entities (e.g., device ID, subsystem). It includes a fail-closed mechanism that falls back to a deterministic keyword extractor if the LLM fails or returns an invalid intent.

### `app/query/` (Query Processing & Orchestration)

* **`orchestrate.py`**: Defines the `QueryOrchestrator` class, which manages the lifecycle of a chat query. It applies scope gates, extracts intents (using `LlmIntentExtractor` or `KeywordIntentExtractor`), and routes the query to the appropriate handler (e.g., `GlobalOverview`, `DeviceInventory`, `MetricHandler`).
* **`handlers.py`**: Contains the specific handler classes for different intents. For example, `MetricHandler` processes queries about device metrics by fetching data from ThingsBoard (using the caller's token) and formatting the response based on the intent and available data.
* **`extract.py`**: Provides the `KeywordIntentExtractor`, a deterministic fallback mechanism for intent extraction based on keyword matching. It is used when the LLM extractor is unavailable or fails.

### `app/tasks/` (Background Tasks)

* **`live_sync.py`**: Implements the scheduled task for synchronizing fleet states from ThingsBoard. It fetches device attributes and telemetry, updates the local database, and maintains a live snapshot of the fleet state in Redis for quick access by the chat handlers.

### `frontend/src/` (React Frontend)

* **`context/ChatContext.tsx`**: The main React context provider for the chat widget. It manages message state, loading indicators, JWT and ThingsBoard host bootstrap, and handles the SSE connection for streaming responses from the backend.
* **`App.tsx`**: The minimal application shell that wraps the `ChatWindow` component in an `ErrorBoundary` and the `ChatProvider`.
* **`types/index.ts`**: Defines TypeScript interfaces for chat messages, request/response payloads, and SSE events, ensuring type safety across the frontend application.

## Data & Integration Flow

### End-to-End Data Flow for Main Workflows

**1. User Query to Chatbot Response:**

1. A user interacts with the frontend chat widget, sending a natural language query. The widget sends this query to the FastAPI backend via the `/ask` or `/ask/stream` endpoint in `app/api/chat.py`.
2. The `QueryOrchestrator` (`app/query/orchestrate.py`) receives the query. It first applies a security gate (`_default_gate`) to check if the user is authorized to access any mentioned branches or devices, leveraging the `branch_scope` and `resolved_scope` functions in `app/hierarchy/scope.py` and `app/auth/scope_resolver.py`.
3. The orchestrator then uses an `IntentExtractor` (either `LlmIntentExtractor` from `app/llm/intent.py` or `KeywordIntentExtractor` from `app/query/extract.py`) to determine the user's intent and extract relevant entities like device IDs or subsystems.
4. Based on the extracted intent, the orchestrator dispatches the query to the appropriate `Handler` (e.g., `GlobalOverview`, `DeviceInventory`, `MetricHandler` in `app/query/handlers.py`).
5. These handlers interact with ThingsBoard via `UserAwareThingsBoardClient` (`app/clients/thingsboard.py`) using the user's token to fetch specific device telemetry or attributes. They also query the local PostgreSQL database for hierarchy information and Redis for cached fleet states.
6. The handlers process the retrieved data, format an answer, and return it to the `chat.py` endpoint.
7. For streaming requests, `chat.py` sends SSE events (`token`, `done`, `error`) back to the frontend widget, which updates the UI in real-time.
8. The orchestrator also records the conversation turn and updates the active branch context in Redis for follow-up questions.

**2. ThingsBoard Webhook Event Ingestion:**

1. ThingsBoard sends webhook events to the FastAPI backend's `/webhooks/thingsboard` endpoint (`app/api/webhooks.py`).
2. `webhooks.py` performs HMAC verification for security and then parses the incoming payload using `EventParse.from_payload` (`app/ingest/parse.py`).
3. The parsed event is then published to a RabbitMQ topic exchange via `RabbitPublisher` (`app/ingest/publisher.py`). If RabbitMQ is unavailable, a direct write to PostgreSQL can be used as a fallback.
4. A separate `consumer` service (`app/ingest/consumer.py`) consumes messages from the RabbitMQ queue.
5. For each message, the consumer performs an idempotency check using Redis to prevent duplicate processing.
6. It then persists the raw event to the PostgreSQL database (`DeviceEvent` table) and flattens relevant fields into the `DeviceTelemetry` table (`app/ingest/write.py`, `app/ingest/telemetry.py`).
7. Crucially, the consumer also merges the event's payload into the customer's live fleet snapshot stored in Redis, but only if the device is part of the customer's hierarchy (`_device_in_customer_hierarchy` in `app/ingest/consumer.py`). This ensures that the Redis cache reflects the most up-to-date device states.

**3. Scheduled Fleet Synchronization and Reconciliation:**

1. Background tasks, managed by `app/tasks/scheduler.py`, periodically trigger `live_sync.py` and `reconcile.py`.
2. `live_sync.py` fetches all attributes and latest telemetry for authorized devices from ThingsBoard using the service account client. It then updates the Redis fleet snapshots with this data, ensuring the chat handlers have fresh information.
3. `reconcile.py` performs data consistency checks between the ThingsBoard data and the local database, with an option for auto-repair to correct discrepancies.

### Interaction with External Services

* **ThingsBoard:** The application heavily relies on the ThingsBoard API for device data (telemetry, attributes), user authentication, and authorization. Interactions are managed through `app/clients/thingsboard.py`, which handles API calls, authentication, and pagination.
* **OpenAI:** Integrated for advanced natural language understanding. The `LlmIntentExtractor` in `app/llm/intent.py` makes calls to the OpenAI API to classify user intents and extract entities from queries.
* **PostgreSQL (TimescaleDB):** The primary persistent data store for `DeviceEvent` and `DeviceTelemetry` records, as well as the hierarchy structure (`HierarchyNode`, `BranchAncestorPath`, `BranchIdentity`). SQLAlchemy is used for ORM interactions.
* **Redis:** Used as a high-speed cache for idempotency checks during event ingestion, storing live fleet snapshots for quick chat responses, and caching ThingsBoard ACL results.
* **RabbitMQ:** Serves as the message broker for asynchronous event ingestion, decoupling the webhook receiver from the persistence and state update logic.

## Patterns, Conventions & Gotchas

### Architectural Patterns and Conventions

* **FastAPI Application Structure:** The Python backend is built with FastAPI, following a modular structure where different concerns (API endpoints, authentication, database, LLM integration, query processing, ingestion, tasks) are organized into separate Python packages within the `app/` directory. This promotes separation of concerns and maintainability.
* **Dependency Injection:** FastAPI's dependency injection system is extensively used (e.g., `Depends(current_tenant)`, `Depends(get_db)`, `Depends(get_redis)` in `app/api/chat.py`) to manage resources like database sessions, Redis connections, and tenant context, making components easier to test and reuse.
* **Asynchronous Programming:** The entire backend is built using `asyncio` and `await` for non-blocking I/O operations, which is crucial for performance in a high-throughput environment dealing with external APIs (ThingsBoard, OpenAI) and message queues (RabbitMQ, Redis).
* **Pydantic for Configuration and Data Validation:** `pydantic-settings` is used in `app/config.py` for robust configuration management, loading settings from environment variables and `.env` files. Pydantic models are also used for request and response validation in FastAPI endpoints (e.g., `ChatRequest` in `app/api/chat.py`).
* **SQLAlchemy ORM for Database Interactions:** SQLAlchemy 2.0 with `asyncpg` is used for interacting with PostgreSQL. The ORM models are defined in `app/db/models.py`, and Alembic (`alembic.ini`) manages database migrations, ensuring schema evolution is controlled.
* **Redis for Caching and Idempotency:** Redis is strategically employed for fast data access and to ensure idempotency in message processing. For instance, `app/ingest/consumer.py` uses Redis `SETNX` to prevent duplicate processing of webhook events, and `app/tasks/live_sync.py` stores fleet snapshots in Redis for quick retrieval by chat handlers.
* **RabbitMQ for Asynchronous Messaging:** RabbitMQ acts as a reliable message broker for the ingestion pipeline. Webhook events are published to a topic exchange, and consumers (e.g., `app/ingest/consumer.py`) subscribe to queues, allowing for decoupled and scalable event processing.
* **LLM Integration Strategy:** The application uses a
fail-closed strategy for LLM integration (`app/llm/intent.py`). If the OpenAI API is unavailable, returns an invalid response, or an unhandled intent, the system gracefully falls back to a deterministic keyword-based intent extractor, ensuring the chatbot remains functional.
* **Security and Access Control:** A multi-layered security approach is implemented. ThingsBoard ACLs are enforced (`app/auth/tb_acl.py`, `app/auth/scope_resolver.py`) to ensure that users can only access devices they are authorized for. HMAC verification is used for incoming webhooks (`app/api/webhooks.py`). JWT tokens are used for user authentication.
* **Hierarchy and Scoping:** The system maintains a hierarchy of devices and customers, allowing for regional scoping of data. This is critical for multi-tenant environments where users should only see data relevant to their assigned scope.

### Non-Obvious or "Tricky" Parts

* **ThingsBoard ACL Enforcement:** The `resolved_scope` function in `app/auth/scope_resolver.py` is a critical security gate. It explicitly intersects the local hierarchy scope with what ThingsBoard itself authorizes for a given user token. This prevents data leakage that could occur if only one source of truth for permissions was relied upon. A key insight is that the local hierarchy *alone* can over-grant, and ThingsBoard *alone* might ignore regional scoping, hence the intersection is vital.
* **Idempotency in Event Ingestion:** The RabbitMQ consumer (`app/ingest/consumer.py`) uses Redis `SETNX` for idempotency. While this provides a fast-path deduplication, the authoritative deduplication relies on the PostgreSQL unique constraint on `(tenant_id, event_id)` in the `DeviceEvent` table. This layered approach ensures robustness even if Redis is temporarily unavailable.
* **ThingsBoard Client Modes:** There are two distinct ThingsBoard client implementations (`ThingsBoardClient` and `UserAwareThingsBoardClient` in `app/clients/thingsboard.py`). Understanding when to use each (service account vs. user token) is crucial for correct authorization and data access. The `UserAwareThingsBoardClient` is used for permission-aware reads on behalf of the caller, while the `ThingsBoardClient` is for broader, service-level operations.
* **Frontend UI Serving:** The frontend chat widget is built as a static asset and served directly by the FastAPI backend from the `frontend/dist` directory. This simplifies deployment by avoiding a separate web server for the frontend and ensures same-origin policy for JWTs, but requires the frontend build step to be part of the Docker image creation.
* **Logging Configuration:** The `configure_logging` function in `app/main.py` explicitly configures logging for the application. This is important because Uvicorn's default logging might not capture all application-level logs, potentially obscuring critical information during debugging or monitoring.

### Duplicated Patterns or Inconsistent Conventions

* **Intent Extraction:** The presence of both `LlmIntentExtractor` and `KeywordIntentExtractor` highlights a dual approach to intent recognition. While the LLM-based approach is more flexible, the keyword-based one serves as a deterministic fallback. Future development might aim to unify or more tightly integrate these for consistent behavior and easier maintenance, or clearly delineate their roles and limitations.
* **ThingsBoard API Calls:** While `app/clients/thingsboard.py` centralizes ThingsBoard API interactions, there might be opportunities to further abstract common patterns (e.g., error handling, retry logic) to reduce boilerplate in individual handlers.

## Infra & Operations

### Docker/Kubernetes and Deployment Setup

The project is designed for containerized deployment using Docker and Docker Compose, with a clear separation of build and runtime environments.

* **`Dockerfile`**: This multi-stage Dockerfile optimizes image size and build times. It first builds the frontend chat widget in a `node:22-alpine` stage, ensuring that Node.js toolchains are not included in the final runtime image. The backend application is then built on a `python:3.12-slim` base, using `uv` for dependency management. The frontend build output (`frontend/dist`) is copied into the final Python image, allowing the FastAPI application to serve the static UI. The image is configured to run as a non-root `appuser` for security and exposes port `8083`.
* **`docker-compose.yml`**: This file defines the development environment, orchestrating several services:
  * **`app`**: The main FastAPI application, built from the `Dockerfile`, exposing port `8083`, and depending on `postgres`, `redis`, and `rabbitmq`.
  * **`consumer`**: A separate service also built from the `Dockerfile`, but running the `python -m app.ingest.consumer` command. This allows the RabbitMQ consumer to run as a distinct worker process.
  * **`postgres`**: Uses `timescale/timescaledb:latest-pg16` for the database, with configured user, password, and database name. A named volume `postgres_data` ensures data persistence.
  * **`redis`**: Uses `redis:7-alpine` for the Redis cache.
  * **`rabbitmq`**: Uses `rabbitmq:3-management-alpine` for the message broker, exposing both the AMQP port (`5672`) and the management UI port (`15672`).
* **`docker-compose.prod.yml`**: While not fully explored, this file is intended for production deployments. It likely extends or overrides `docker-compose.yml` with production-specific configurations, such as different image tags, resource limits, or network settings.
* **`Caddyfile`**: Used for the Caddy reverse proxy in production. It handles HTTPs termination (via Let's Encrypt), applies security headers, and configures proxying rules. Notably, it includes specific settings for Server-Sent Events (SSE) endpoints (`/ask/stream`) to disable response buffering, ensuring real-time streaming of chat tokens.

### Scripts and Automation

The `scripts/` directory contains various operational and utility scripts:

* **`backfill_telemetry.py`**: Likely used for populating historical telemetry data.
* **`backup_v1.py`**: Suggests a script for backing up data from a previous version of the system.
* **`deploy.sh`**: A shell script for deployment automation, which could encapsulate steps like building Docker images, pushing to a registry, and deploying to a target environment.
* **`extract_tb_data.py`**: For extracting data from ThingsBoard, possibly for migration or analysis purposes.
* **`purge_v1.py`**: Indicates a script for cleaning up data or resources related to a previous version.
* **`seed_customers.py`**: Used for populating the database with initial customer data, useful for development or testing environments.

These scripts are designed to be run inside the Docker container (e.g., `docker exec ... python -m scripts.<name>`), providing a consistent execution environment for operational tasks.

## Testing & Quality Signals

### Testing Approach

The project includes a dedicated `tests/` directory, indicating a commitment to automated testing. The `pyproject.toml` file configures `pytest` with `asyncio_mode = "auto"`, suggesting that asynchronous tests are well-supported. The presence of `pytest-asyncio` in `dev` dependencies further confirms this.

Based on the file names observed in the `tests/` directory, the testing strategy appears to cover:

* **Unit/Integration Tests for Core Logic:** Files like `test_answer_support.py`, `test_webhook_queue.py`, `test_tb_payload_adapter.py`, `test_regional_scope.py`, `test_charts.py`, `test_chat_memory.py`, and `test_chat_stream_contract.py` suggest focused testing of specific modules and their interactions. These likely cover the intent extraction, query orchestration, ThingsBoard client interactions, and data processing pipelines.
* **Normalization Tests:** The `tests/normalization/` subdirectory, with files like `test_resolver.py`, `test_snapshot.py`, and `test_values.py`, indicates thorough testing of the data normalization and snapshot building processes, which are critical for consistent data interpretation.

### Obvious Gaps or Fragile Areas

* **Documentation for Tests:** While tests exist, there isn't an explicit `TESTING.md` or similar document outlining the testing strategy, coverage goals, or how to run/contribute to tests. This could be a gap for new contributors.
* **End-to-End (E2E) Testing:** The current file structure primarily suggests unit and integration tests. The absence of a clear E2E testing framework (e.g., Playwright, Cypress) might indicate a gap in verifying the complete system flow, from frontend interaction to backend processing and ThingsBoard integration, in a deployed environment.
* **Performance Testing:** There's no immediate indication of performance or load testing scripts, which could be crucial for an IoT platform integration handling potentially high volumes of data and chat requests.
* **Frontend Testing:** While `frontend/` contains `package.json` with build scripts, there are no visible dedicated test files (e.g., `*.test.tsx`) within the `frontend/src/` directory, suggesting that frontend unit or integration tests might be missing or not explicitly stored in a standard location.

## Practical Navigation Guide

This section provides a quick reference for a new senior engineer looking to make specific changes or explore particular functionalities within the codebase.

* **If you want to change authentication logic:**
  * Start in: `app/auth/jwt.py` (for JWT token handling), `app/auth/tb_acl.py` (for ThingsBoard ACL enforcement), and `app/auth/scope_resolver.py` (for combining local and ThingsBoard scopes).
  * Review: `app/config.py` for related settings like `jwt_signing_key` and `require_jwt_verification`.
  * Dependencies: `app/deps.py` for how `TenantContext` is injected into API endpoints.

* **If you want to add a new API endpoint:**
  * Start in: `app/api/` to create a new Python module for your endpoint(s) or extend an existing one.
  * Review: `app/main.py` to see how existing routers are included in the FastAPI application.
  * Define: Pydantic models for request and response bodies, potentially in `app/api/` or `app/query/contracts.py`.
  * Utilize: Dependencies from `app/deps.py` (e.g., `get_db`, `get_redis`, `current_tenant`) for database access, caching, and user context.

* **If you want to modify RAG behavior or agents:**
  * Start in: `app/query/orchestrate.py` to understand how intents are dispatched to handlers.
  * Explore: `app/llm/intent.py` for LLM-based intent extraction and its fallback mechanism.
  * Examine: `app/query/extract.py` for the keyword-based intent extraction logic.
  * Review: `app/query/handlers.py` to see how specific intents are processed and how data is fetched and formatted.
  * Check: `app/config.py` for LLM-related settings such as `openai_api_key`, `openai_model`, and `llm_max_tokens`.

* **If you want to touch the chat widget frontend:**
  * Start in: `frontend/src/` for the React application source code.
  * Focus on: `frontend/src/context/ChatContext.tsx` for chat state management, message handling, and SSE communication with the backend.
  * Review: `frontend/src/App.tsx` for the main application shell and `frontend/src/types/index.ts` for shared TypeScript interfaces.
  * Build process: The `Dockerfile` includes a stage for building the frontend, which is then served statically by the FastAPI backend.
