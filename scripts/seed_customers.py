"""Populate the `customer` table (tb_customer_id -> prefix) from an extraction dump.

current_tenant resolves a caller's prefix from their JWT customerId via this table
(authoritative) before falling back to title matching. Without these rows every chat
answer degrades to "your token is not mapped to a customer".

Prefix derivation reuses app.hierarchy.prefix.derive_prefix, so the mapping always
agrees with what /api/v1/admin/import wrote into hierarchy_node.

Usage:
    uv run python scripts/seed_customers.py [--extract data/tb_extract_full.json]
"""

import argparse
import asyncio
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db.models import Customer
from app.hierarchy.prefix import derive_prefix


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--extract", default="data/tb_extract_full.json")
    args = parser.parse_args()
    load_dotenv(".env")

    settings = get_settings()
    records = json.loads(Path(args.extract).read_text(encoding="utf-8"))
    known_prefixes = set(settings.prefixes)

    # A TB customer can host several devices; take the prefix the majority derive to.
    votes: dict[str, Counter[str]] = defaultdict(Counter)
    titles: dict[str, str] = {}
    for record in records:
        tb_customer_id = record.get("customerId")
        if not tb_customer_id:
            continue
        full_path = str(
            record["telemetry"].get("full_path")
            or record["serverAttributes"].get("full_path")
            or ""
        )
        prefix = derive_prefix(record.get("name") or "", full_path, known_prefixes)
        if prefix:
            votes[tb_customer_id][prefix] += 1
            titles.setdefault(tb_customer_id, record.get("name") or tb_customer_id)

    engine = create_async_engine(settings.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    added = updated = 0
    async with sessions() as session:
        for tb_customer_id, counts in votes.items():
            prefix = counts.most_common(1)[0][0]
            existing = (
                await session.execute(
                    select(Customer).where(Customer.tb_customer_id == tb_customer_id)
                )
            ).scalar_one_or_none()
            if existing:
                if existing.prefix != prefix:
                    existing.prefix = prefix
                    updated += 1
            else:
                session.add(
                    Customer(
                        tb_customer_id=tb_customer_id,
                        title=titles.get(tb_customer_id, tb_customer_id),
                        prefix=prefix,
                    )
                )
                added += 1
        await session.commit()
        rows = (await session.execute(select(Customer.prefix))).scalars().all()
    await engine.dispose()

    print(f"customers added={added} updated={updated} total={len(rows)}")
    print("prefix distribution:", dict(Counter(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
