"""Back up the Java v1 chatbot state (Timescale tables + Upstash Redis keys).

Run BEFORE any destructive cleanup. Writes newline-delimited JSON per table so a
167k-row event table streams instead of building one giant list in memory.

Usage:
    uv run python scripts/backup_v1.py --out backups/v1_<stamp>
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

V1_TABLES = ("customers", "hierarchy_nodes", "branch_ancestor_paths", "device_events")


async def backup_postgres(url: str, out_dir: Path) -> dict[str, int]:
    engine = create_async_engine(url)
    counts: dict[str, int] = {}
    try:
        async with engine.connect() as conn:
            for table in V1_TABLES:
                exists = (
                    await conn.execute(
                        text(
                            "SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename=:t"
                        ),
                        {"t": table},
                    )
                ).first()
                if not exists:
                    print(f"  {table}: absent, skipped")
                    continue
                path = out_dir / f"{table}.jsonl"
                written = 0
                with path.open("w", encoding="utf-8") as handle:
                    result = await conn.stream(text(f'SELECT * FROM "{table}"'))
                    async for row in result.mappings():
                        handle.write(json.dumps(dict(row), default=str) + "\n")
                        written += 1
                counts[table] = written
                print(f"  {table}: {written} rows -> {path.name}")
    finally:
        await engine.dispose()
    return counts


async def backup_redis(url: str, out_dir: Path) -> int:
    import redis.asyncio as aioredis

    client = aioredis.from_url(url, decode_responses=True)
    dump: dict[str, object] = {}
    try:
        async for key in client.scan_iter(count=1000):
            kind = await client.type(key)
            if kind == "hash":
                value: object = await client.hgetall(key)
            elif kind == "string":
                value = await client.get(key)
            elif kind == "list":
                value = await client.lrange(key, 0, -1)
            elif kind == "set":
                value = sorted(await client.smembers(key))
            elif kind == "zset":
                value = await client.zrange(key, 0, -1, withscores=True)
            else:
                value = f"<unsupported type {kind}>"
            dump[key] = {"type": kind, "ttl": await client.ttl(key), "value": value}
    finally:
        await client.aclose()
    path = out_dir / "redis_keys.json"
    path.write_text(json.dumps(dump, indent=1, default=str), encoding="utf-8")
    print(f"  redis: {len(dump)} keys -> {path.name}")
    return len(dump)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=f"backups/v1_{time.strftime('%Y%m%d_%H%M%S')}")
    args = parser.parse_args()
    load_dotenv(".env")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"backup -> {out_dir}")

    print("postgres:")
    counts = await backup_postgres(os.environ["DATABASE_URL"], out_dir)
    print("redis:")
    redis_keys = await backup_redis(os.environ["REDIS_URL"], out_dir)

    (out_dir / "manifest.json").write_text(
        json.dumps(
            {
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "postgres_tables": counts,
                "redis_keys": redis_keys,
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    total = sum(counts.values())
    print(f"\nDONE: {total} DB rows + {redis_keys} redis keys backed up to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
