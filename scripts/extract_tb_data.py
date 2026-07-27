"""Extract ALL device data from ThingsBoard into a JSON dump.

Pulls every tenant device (service credentials from .env) with latest telemetry and
all three attribute scopes. Output entries are shaped so the dump can be POSTed
straight to /api/v1/admin/import (name / id / telemetry / serverAttributes), while
also carrying client/shared attributes and per-key timestamps for analysis.

Usage:
    uv run python scripts/extract_tb_data.py [--out data/tb_extract.json] [--concurrency 8]
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

ATTRIBUTE_SCOPES = ("SERVER_SCOPE", "CLIENT_SCOPE", "SHARED_SCOPE")
_SCOPE_FIELD = {
    "SERVER_SCOPE": "serverAttributes",
    "CLIENT_SCOPE": "clientAttributes",
    "SHARED_SCOPE": "sharedAttributes",
}


async def get_with_retry(
    http: httpx.AsyncClient,
    path: str,
    headers: dict[str, str],
    params: dict[str, Any] | None = None,
    attempts: int = 6,
) -> httpx.Response:
    """GET with exponential backoff on 429 (TB tenant-level rate limit)."""
    delay = 1.0
    for attempt in range(attempts):
        response = await http.get(path, params=params, headers=headers)
        if response.status_code != 429:
            response.raise_for_status()
            return response
        retry_after = response.headers.get("Retry-After")
        wait = float(retry_after) if retry_after and retry_after.isdigit() else delay
        await asyncio.sleep(wait)
        delay = min(delay * 2, 30)
    response.raise_for_status()
    return response  # pragma: no cover


async def login(http: httpx.AsyncClient, user: str, password: str) -> dict[str, str]:
    response = await http.post("/api/auth/login", json={"username": user, "password": password})
    response.raise_for_status()
    return {"X-Authorization": f"Bearer {response.json()['token']}"}


async def all_devices(http: httpx.AsyncClient, headers: dict[str, str]) -> list[dict[str, Any]]:
    devices: list[dict[str, Any]] = []
    page = 0
    while True:
        response = await get_with_retry(
            http, "/api/tenant/devices", headers, {"pageSize": 100, "page": page}
        )
        body = response.json()
        devices.extend(body.get("data", []))
        if not body.get("hasNext"):
            return devices
        page += 1


async def extract_device(
    http: httpx.AsyncClient,
    headers: dict[str, str],
    device: dict[str, Any],
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    device_id = device["id"]["id"]
    record: dict[str, Any] = {
        "id": device_id,
        "name": device.get("name"),
        "type": device.get("type"),
        "label": device.get("label"),
        "customerId": (device.get("customerId") or {}).get("id"),
        "telemetry": {},
        "telemetry_ts": {},
        "serverAttributes": {},
        "clientAttributes": {},
        "sharedAttributes": {},
        "errors": [],
    }
    async with semaphore:
        try:
            response = await get_with_retry(
                http, f"/api/plugins/telemetry/DEVICE/{device_id}/values/timeseries", headers
            )
            for key, points in response.json().items():
                if isinstance(points, list) and points:
                    record["telemetry"][key] = points[0].get("value")
                    record["telemetry_ts"][key] = points[0].get("ts")
        except Exception as exc:  # noqa: BLE001 — per-device failure must not kill the run
            record["errors"].append(f"telemetry: {exc}")
        for scope in ATTRIBUTE_SCOPES:
            try:
                response = await get_with_retry(
                    http,
                    f"/api/plugins/telemetry/DEVICE/{device_id}/values/attributes/{scope}",
                    headers,
                )
                for item in response.json():
                    if isinstance(item, dict) and "key" in item:
                        record[_SCOPE_FIELD[scope]][str(item["key"])] = item.get("value")
            except Exception as exc:  # noqa: BLE001
                record["errors"].append(f"{scope}: {exc}")
    return record


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=f"data/tb_extract_{time.strftime('%Y%m%d_%H%M%S')}.json")
    parser.add_argument("--concurrency", type=int, default=2)
    args = parser.parse_args()

    load_dotenv(".env")
    import os

    url = os.environ["TB_URL"].rstrip("/")
    async with httpx.AsyncClient(base_url=url, timeout=30) as http:
        headers = await login(http, os.environ["TB_USER"], os.environ["TB_PASSWORD"])
        devices = await all_devices(http, headers)
        print(f"devices: {len(devices)}")
        semaphore = asyncio.Semaphore(args.concurrency)
        records = await asyncio.gather(
            *(extract_device(http, headers, d, semaphore) for d in devices)
        )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(list(records), indent=1, default=str), encoding="utf-8")

    with_errors = sum(1 for r in records if r["errors"])
    key_count = len({k for r in records for k in (*r["telemetry"], *r["serverAttributes"])})
    print(f"wrote {out_path} ({out_path.stat().st_size / 1024:.0f} KiB)")
    print(f"devices with fetch errors: {with_errors}")
    print(f"distinct telemetry+server-attribute keys: {key_count}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
