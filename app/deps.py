from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.customers import resolve_customer_prefix
from app.auth.jwt import TenantContext, decode_tenant_token
from app.clients.thingsboard import UserAwareThingsBoardClient
from app.config import Settings, get_settings
from app.hierarchy.scope import ScopedBranches, branch_scope, extract_region


def settings_dep() -> Settings:
    return get_settings()


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


def get_redis(request: Request) -> Redis:
    redis: Redis = request.app.state.redis
    return redis


async def current_tenant(
    settings: Annotated[Settings, Depends(settings_dep)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    authorization: Annotated[str | None, Header()] = None,
    x_tb_token: Annotated[str | None, Header()] = None,
) -> TenantContext:
    token = x_tb_token or (authorization.removeprefix("Bearer ") if authorization else None)
    if not token:
        raise HTTPException(status_code=401, detail="Bearer token required")

    tenant = decode_tenant_token(token, settings)

    # Resolve customer prefix
    customer_title_claim = tenant.claims.get("customerTitle")
    customer_title: str | None = customer_title_claim if isinstance(customer_title_claim, str) else None
    prefix = await resolve_customer_prefix(db, tenant.customer_id, customer_title, settings, redis)

    if prefix is None and settings.strict_customer_mapping:
        raise HTTPException(status_code=403, detail="Customer not mapped to known prefix")

    # Build new TenantContext with prefix; region name comes from the claims
    # (firstName/lastName explicit, else guessed from the email local part).
    return TenantContext(
        tenant_id=tenant.tenant_id,
        customer_id=tenant.customer_id,
        subject=tenant.subject,
        claims=tenant.claims,
        scopes=tenant.scopes,
        region=extract_region(tenant.claims).name,
        prefix=prefix,
        user_token=tenant.user_token,
    )


async def scoped_branches(
    tenant: Annotated[TenantContext, Depends(current_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> ScopedBranches:
    """Get scoped branches for the current tenant's region.

    SECURITY: the scope MUST come from extract_region(claims) so the explicit
    flag survives — an explicit-but-unresolvable region fails CLOSED (empty),
    a guessed one fails open. Degrading explicit to guessed here would turn a
    scope denial into full-inventory access.
    """
    if not tenant.prefix:
        return ScopedBranches(branch_node_ids=[], tb_device_ids=[])

    return await branch_scope(db, tenant.prefix, extract_region(tenant.claims), redis)


async def user_tb_client(
    tenant: Annotated[TenantContext, Depends(current_tenant)],
    settings: Annotated[Settings, Depends(settings_dep)],
) -> AsyncIterator[UserAwareThingsBoardClient]:
    """User-scoped ThingsBoard client - uses caller's token. Closed after the request."""
    if not tenant.user_token:
        raise HTTPException(status_code=401, detail="User token required for TB access")
    client = UserAwareThingsBoardClient(settings, tenant.user_token)
    try:
        yield client
    finally:
        await client.close()