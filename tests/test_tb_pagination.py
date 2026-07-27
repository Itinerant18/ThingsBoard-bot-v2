"""TB device listing must follow `hasNext`.

BOI has 104 leaf devices against ThingsBoard's 100-row page, so a single-page fetch
drops real devices from /api/v1/data. API-TB.md: "Always paginate large datasets".
"""

from typing import Any

import pytest

from app.clients.thingsboard import fetch_all_pages


def _pager(total: int, page_size: int) -> tuple[list[dict[str, Any]], list[int]]:
    """Fake TB page endpoint; also records which page numbers were requested."""
    seen: list[int] = []

    async def get(path: str, params: dict[str, Any]) -> dict[str, Any]:
        page = int(params["page"])
        seen.append(page)
        start = page * page_size
        rows = [{"n": i} for i in range(start, min(start + page_size, total))]
        return {"data": rows, "hasNext": start + page_size < total, "totalElements": total}

    return get, seen  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_follows_has_next_across_pages() -> None:
    get, seen = _pager(total=104, page_size=100)
    body = await fetch_all_pages(get, "/api/customer/x/devices", 100)
    assert len(body["data"]) == 104  # the 4 devices a single page would have dropped
    assert seen == [0, 1]
    assert body["hasNext"] is False  # merged body is complete
    assert body["totalElements"] == 104  # other page fields survive the merge


@pytest.mark.asyncio
async def test_single_page_makes_one_call() -> None:
    get, seen = _pager(total=10, page_size=100)
    body = await fetch_all_pages(get, "/api/customer/x/devices", 100)
    assert len(body["data"]) == 10
    assert seen == [0]


@pytest.mark.asyncio
async def test_stops_at_max_pages_when_has_next_never_clears() -> None:
    calls = 0

    async def get(path: str, params: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"data": [{"n": calls}], "hasNext": True}

    body = await fetch_all_pages(get, "/api/customer/x/devices", 100)
    assert calls == 50  # bounded, not an infinite loop against a misbehaving TB
    assert len(body["data"]) == 50


@pytest.mark.asyncio
async def test_non_dict_body_passed_through() -> None:
    async def get(path: str, params: dict[str, Any]) -> list[Any]:
        return []

    assert await fetch_all_pages(get, "/api/customer/x/devices", 100) == []
