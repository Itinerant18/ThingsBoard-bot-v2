from app.config import Settings
from app.main import create_app


def _paths(role: str) -> set[str]:
    app = create_app(Settings(database_url="postgresql+asyncpg://unused/unused", app_role=role))  # type: ignore[arg-type]
    # FastAPI defers include_router; the OpenAPI schema forces full route resolution.
    return set(app.openapi()["paths"])


def test_all_role_mounts_everything() -> None:
    paths = _paths("all")
    assert "/webhooks/thingsboard" in paths
    assert "/api/v1/chat" in paths
    assert "/api/v1/admin/replay" in paths


def test_chat_role_has_no_webhook_surface() -> None:
    paths = _paths("chat")
    assert "/webhooks/thingsboard" not in paths
    assert "/api/v1/chat" in paths
    assert "/api/v1/admin/replay" in paths


def test_ingestion_role_is_webhook_only() -> None:
    paths = _paths("ingestion")
    assert "/webhooks/thingsboard" in paths
    assert "/api/v1/chat" not in paths
    assert "/api/v1/admin/replay" not in paths
    assert any("health" in p for p in paths)
