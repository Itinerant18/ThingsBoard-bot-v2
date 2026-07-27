from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict


def _split_csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    # Diagram's profile deployment. "all" = all-in-one (dev default); "chat" = chatbot
    # node (chat/data/admin routers + schedulers, no webhook); "ingestion" = webhook
    # receiver only. The consumer profile is the standalone worker:
    # python -m app.ingest.consumer
    app_role: Literal["all", "chat", "ingestion"] = "all"
    # Serve frontend/ at /ui (manual chat tester). Turn off for production nodes.
    serve_ui: bool = True
    # Default matches docker-compose; the postgres-only upsert in ingest/write.py
    # means sqlite is not a supported backend.
    database_url: str = (
        "postgresql+asyncpg://thingsboard:thingsboard@localhost:5432/thingsboard_chat"
    )
    redis_url: str = "redis://localhost:6379/0"
    rabbitmq_url: str = "amqp://guest:guest@localhost/"
    tb_url: str = "http://localhost:8080"
    tb_user: str = ""
    tb_password: str = ""
    device_id: str = ""
    # Comma-separated strings, NOT list[str]: pydantic-settings JSON-decodes complex
    # types straight from env and crashes on plain comma-separated values.
    tb_allowed_hosts: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    llm_max_tokens: int = 2000
    pinecone_api_key: str = ""
    pinecone_index_name: str = "thingsboard-logs"
    customer_prefixes: str = ""
    customers_sync_enabled: bool = False
    customers_title_mappings: str = ""
    max_context_tokens: int = 10000
    deterministic_answers_enabled: bool = True
    # "changed" writes a telemetry row only when a key's value differs from the last
    # observation — every key is still stored, just not duplicated 1440x/day per key.
    # "all" stores every observation (~212M rows/day at 60s across 128 devices).
    telemetry_write_mode: str = "changed"
    tb_scheduled_sync_enabled: bool = True
    tb_scheduled_sync_interval_ms: int = 60000
    reconciliation_enabled: bool = True
    reconciliation_auto_repair: bool = True  # Java app.reconciliation.auto-repair
    # Java ran a 2AM IST cron; v2 uses a fixed interval from boot (daily by default).
    reconciliation_interval_seconds: int = 24 * 60 * 60
    webhook_hmac_secret: str = ""
    # ThingsBoard rule chains do not send a tenant id; ingested events fall back to this
    # so the (tenant_id, event_id) dedup key and tenant-scoped alarm queries line up with
    # the tenantId claim in caller JWTs.
    webhook_default_tenant_id: str = ""
    # Diagram architecture: webhook publishes to RabbitMQ; consumer owns persistence.
    webhook_publish_to_queue: bool = True
    # Broker down -> write straight to Postgres instead of 503 (loses the per-event
    # Redis fold for those events; reconciliation/live-sync repairs the snapshot).
    webhook_direct_write_fallback: bool = True
    admin_token: str = ""
    allowed_origins: str = "http://localhost:5173"
    frame_ancestors: str = "'self'"
    jwt_signing_key: str = ""
    require_jwt_verification: bool = False
    # Intersect every answer's device scope with what ThingsBoard authorizes for the
    # caller. Off means answering from customer-prefix scope alone, which over-grants
    # whenever a prefix spans several TB customers — emergency rollback only.
    enforce_tb_device_acl: bool = True
    tb_acl_cache_seconds: int = 60
    require_webhook_hmac: bool = False
    require_admin_token: bool = False
    strict_customer_mapping: bool = False
    webhook_max_skew_ms: int = 300000
    timescale_init_enabled: bool = False
    metrics_port: int = 9090
    tracing_sample_rate: float = 0.1
    evaluation_enabled: bool = False
    evaluation_sample_rate: float = 1.0
    db_pool_size: int = 10
    db_pool_max_overflow: int = 5

    @property
    def origins(self) -> list[str]:
        return _split_csv(self.allowed_origins)

    @property
    def prefixes(self) -> list[str]:
        return _split_csv(self.customer_prefixes)

    def allowed_tb_hosts(self) -> set[str]:
        configured = set(_split_csv(self.tb_allowed_hosts))
        host = urlparse(self.tb_url).hostname
        return configured | ({host} if host else set())


@lru_cache
def get_settings() -> Settings:
    return Settings()
