from dataclasses import dataclass

import jwt
from fastapi import HTTPException, status

from app.config import Settings


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    customer_id: str | None
    subject: str | None
    claims: dict[str, object]
    scopes: tuple[str, ...] = ()
    region: str | None = None
    prefix: str | None = None
    user_token: str | None = None


def decode_tenant_token(token: str, settings: Settings) -> TenantContext:
    try:
        if settings.require_jwt_verification:
            if not settings.jwt_signing_key:
                raise HTTPException(status_code=503, detail="JWT verification is not configured")
            claims = jwt.decode(token, settings.jwt_signing_key, algorithms=["HS256"])
        else:
            claims = jwt.decode(token, options={"verify_signature": False, "verify_aud": False})
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token"
        ) from exc
    tenant_id = str(claims.get("tenantId") or claims.get("tenant_id") or "")
    customer = claims.get("customerId") or claims.get("customer_id")
    if isinstance(customer, dict):
        customer = customer.get("id")
    if not tenant_id and not customer:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has no tenant claim"
        )
    return TenantContext(
        tenant_id,
        str(customer) if customer else None,
        str(claims.get("sub")) if claims.get("sub") else None,
        claims,
        tuple(str(value) for value in claims.get("scopes", []) if isinstance(value, str))
        if isinstance(claims.get("scopes"), list)
        else (),
        None,  # region — derived from firstName/lastName/sub via extract_region in deps
        None,  # prefix - set by current_tenant dependency
        token,  # user_token - raw token for user-scoped TB calls
    )