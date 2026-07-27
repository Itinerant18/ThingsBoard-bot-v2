import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api import admin, chat, data, health, webhooks
from app.cache.redis import create_redis
from app.clients.thingsboard import ThingsBoardClient
from app.config import Settings, get_settings
from app.db.session import build_engine
from app.db.timescale import initialize_timescale
from app.ingest.publisher import RabbitPublisher
from app.llm.client import LlmClient
from app.llm.intent import LlmIntentExtractor
from app.query.extract import KeywordIntentExtractor
from app.query.orchestrate import QueryOrchestrator
from app.tasks.live_sync import sync_all_customers
from app.tasks.reconcile import reconcile_all
from app.tasks.scheduler import run_periodic


def configure_logging() -> None:
    """Give the app's own loggers a handler.

    Uvicorn configures only its own loggers and leaves root at WARNING, so every
    app-level logger.info ([LIVE-SYNC], [REPLAY], [SCHEDULER]) was dropped before
    reaching container stdout — a healthy sync looked identical to one that never
    ran. force=True because uvicorn may already have touched the root handlers.
    """
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the FastAPI app; tests pass custom settings."""
    configure_logging()
    test_settings = settings or get_settings()

    @asynccontextmanager
    async def test_lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = test_settings
        engine = build_engine(test_settings)
        app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
        if test_settings.timescale_init_enabled:
            await initialize_timescale(engine)
        app.state.redis = await create_redis(test_settings.redis_url)
        app.state.tb = ThingsBoardClient(test_settings)
        # Lazy — no broker connection is made until the first webhook publish.
        # Only roles that mount the webhook router need a publisher.
        app.state.publisher = (
            RabbitPublisher(test_settings.rabbitmq_url)
            if test_settings.webhook_publish_to_queue
            and test_settings.app_role in ("all", "ingestion")
            else None
        )
        keyword_extractor = KeywordIntentExtractor()
        extractor = (
            LlmIntentExtractor(LlmClient(test_settings), keyword_extractor)
            if test_settings.openai_api_key
            else keyword_extractor
        )
        app.state.orchestrator = QueryOrchestrator(extractor)
        # Schedulers belong to the node that serves chat reads (Java: scheduled sync
        # "enable only in the JVM that serves chat"); a pure webhook receiver runs none.
        run_schedulers = test_settings.app_role in ("all", "chat")
        background: list[asyncio.Task[None]] = []
        if run_schedulers and test_settings.tb_scheduled_sync_enabled:
            background.append(
                asyncio.create_task(
                    run_periodic(
                        "live-sync",
                        interval_seconds=test_settings.tb_scheduled_sync_interval_ms / 1000,
                        job=partial(
                            sync_all_customers,
                            app.state.session_factory,
                            app.state.redis,
                            app.state.tb,
                        ),
                        initial_delay_seconds=5,
                    )
                )
            )
        if run_schedulers and test_settings.reconciliation_enabled:
            background.append(
                asyncio.create_task(
                    run_periodic(
                        "reconcile",
                        interval_seconds=test_settings.reconciliation_interval_seconds,
                        job=partial(
                            reconcile_all,
                            app.state.session_factory,
                            app.state.redis,
                            test_settings.reconciliation_auto_repair,
                        ),
                        # Let the first live-sync cycles land before checking for drift.
                        initial_delay_seconds=600,
                    )
                )
            )
        try:
            yield
        finally:
            for task in background:
                task.cancel()
            for task in background:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            if app.state.publisher is not None:
                await app.state.publisher.close()
            await app.state.tb.close()
            await app.state.redis.aclose()
            await engine.dispose()

    app = FastAPI(title="ThingsBoard IoT Chatbot", version="0.1.0", lifespan=test_lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=test_settings.origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Admin-Token",
            "X-HMAC-SHA256",
            "X-Timestamp",
        ],
    )

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = f"frame-ancestors {test_settings.frame_ancestors}"
        return response

    # Router mounting per profile (diagram): chat node has no webhook surface at all;
    # the ingestion node exposes only health + webhook.
    app.include_router(health.router)
    if test_settings.app_role in ("all", "chat"):
        app.include_router(chat.router)
        app.include_router(data.router)
        app.include_router(admin.router)
    if test_settings.app_role in ("all", "ingestion"):
        app.include_router(webhooks.router)

    # Manual chat tester at /ui. Served from the app so it is same-origin with
    # /api/v1/chat — no CORS entry needed for the browser to send the JWT.
    ui_dir = Path(__file__).resolve().parent.parent / "frontend"
    if test_settings.serve_ui and ui_dir.is_dir() and test_settings.app_role in ("all", "chat"):
        app.mount("/ui", StaticFiles(directory=ui_dir, html=True), name="ui")

    return app


# Production app
app = create_app()