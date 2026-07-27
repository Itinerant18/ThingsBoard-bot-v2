"""Purge the Java v1 chatbot state from Timescale + Upstash Redis.

DESTRUCTIVE AND IRREVERSIBLE. Run scripts/backup_v1.py first and keep the output.

Drops only the four Java v1 tables in `public` (device_events is a hypertable; CASCADE
takes its sequence with it). TimescaleDB internal schemas are never touched. Redis
cleanup deletes the Java key namespaces (<CUSTOMER>:device|node|global:*), leaving any
v2 namespace (fleet:v1, chatmem:v1, evt:seen:v1, branch_scope) intact.

Requires --yes to actually execute; without it, it reports what WOULD be removed.

Usage:
    uv run python scripts/purge_v1.py            # dry run
    uv run python scripts/purge_v1.py --yes      # execute
    uv run python scripts/purge_v1.py --yes --flush-redis   # also drop non-enumerable keys
"""

import argparse
import asyncio
import os
import sys

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

V1_TABLES = ("device_events", "branch_ancestor_paths", "hierarchy_nodes", "customers")
# v2 owns these prefixes; never delete them here.
V2_PREFIXES = ("fleet:v1", "chatmem:v1", "evt:seen:v1", "branch_scope")
# Java RedisCacheService layout: <CUSTOMER>:device:*, <CUSTOMER>:node:*, <CUSTOMER>:global:*
JAVA_SEGMENTS = ("device", "node", "global")


def is_java_key(key: str) -> bool:
    if any(key.startswith(p) for p in V2_PREFIXES):
        return False
    parts = key.split(":")
    return len(parts) >= 2 and parts[1] in JAVA_SEGMENTS


async def purge_postgres(url: str, execute: bool) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            present = (
                await conn.execute(
                    text(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                        "AND tablename = ANY(:names)"
                    ),
                    {"names": list(V1_TABLES)},
                )
            ).scalars().all()
            for table in present:
                count = (await conn.execute(text(f'SELECT count(*) FROM "{table}"'))).scalar()
                print(f"  {'DROP' if execute else 'would drop'} {table} ({count} rows)")
            if not execute:
                return
            for table in V1_TABLES:
                if table in present:
                    await conn.execute(text(f'DROP TABLE IF EXISTS "{table}" CASCADE'))
            print("  tables dropped")
        async with engine.connect() as conn:
            left = (
                await conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
                )
            ).scalars().all()
            print(f"  public schema now: {left or '(empty)'}")
    finally:
        await engine.dispose()


async def purge_redis(url: str, execute: bool, flush: bool) -> None:
    import redis.asyncio as aioredis

    client = aioredis.from_url(url, decode_responses=True)
    try:
        print(f"  DBSIZE before: {await client.dbsize()}")
        if flush:
            if execute:
                await client.flushdb()
                print("  FLUSHDB executed (all keys, including non-enumerable ones)")
            else:
                print("  would FLUSHDB (all keys)")
        else:
            targets = [k async for k in client.scan_iter(count=1000) if is_java_key(k)]
            print(f"  {'deleting' if execute else 'would delete'} {len(targets)} java key(s)")
            if execute and targets:
                for i in range(0, len(targets), 200):
                    await client.delete(*targets[i : i + 200])
        print(f"  DBSIZE after: {await client.dbsize()}")
    finally:
        await client.aclose()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="actually execute the purge")
    parser.add_argument(
        "--flush-redis",
        action="store_true",
        help="FLUSHDB instead of targeted delete (clears keys SCAN cannot enumerate)",
    )
    args = parser.parse_args()
    load_dotenv(".env")

    mode = "EXECUTE" if args.yes else "DRY RUN"
    print(f"=== purge v1 ({mode}) ===")
    print("postgres:")
    await purge_postgres(os.environ["DATABASE_URL"], args.yes)
    print("redis:")
    await purge_redis(os.environ["REDIS_URL"], args.yes, args.flush_redis)
    if not args.yes:
        print("\nnothing changed. re-run with --yes to execute.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
