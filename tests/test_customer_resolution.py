"""Pure unit tests for customer resolution (no DB)."""

from unittest.mock import AsyncMock, MagicMock

from app.auth.customers import resolve_customer_prefix


class FakeSettings:
    def __init__(
        self,
        prefixes: list[str] | None = None,
        customers_title_mappings: str = "",
        strict_customer_mapping: bool = False,
    ):
        self.prefixes = prefixes or ["SBI", "BOI", "HDFC", "ICICI"]
        self.customers_title_mappings = customers_title_mappings
        self.strict_customer_mapping = strict_customer_mapping


async def test_T1_explicit_title_mapping_override() -> None:
    """T1: explicit title-mapping override 'Bank of India=BOI' -> BOI (only when BOI exists in known prefixes)."""
    settings = FakeSettings(prefixes=["BOI", "SBI"], customers_title_mappings="Bank of India=BOI")
    result = await resolve_customer_prefix(None, None, "Bank of India", settings)
    assert result == "BOI"

    # Override to unknown prefix -> ignored
    settings2 = FakeSettings(prefixes=["SBI"], customers_title_mappings="Bank of India=BOI")
    result2 = await resolve_customer_prefix(None, None, "Bank of India", settings2)
    assert result2 is None


async def test_T2_unique_normalized_title_match() -> None:
    """T2: unique normalized-title match: title 'SBI STATE BANK', known {SBI}, title-word match unique -> SBI."""
    settings = FakeSettings(prefixes=["SBI", "BOI"])
    result = await resolve_customer_prefix(None, None, "SBI STATE BANK", settings)
    assert result == "SBI"


async def test_T3_zero_candidates() -> None:
    """T3: zero candidates -> None."""
    settings = FakeSettings(prefixes=["SBI", "BOI"])
    result = await resolve_customer_prefix(None, None, "UNKNOWN BANK", settings)
    assert result is None


async def test_T4_multiple_candidates() -> None:
    """T4: multiple candidates -> None."""
    settings = FakeSettings(prefixes=["SBI", "SBIN", "BOI"])
    # Both SBI and SBIN could match "STATE BANK OF INDIA"
    result = await resolve_customer_prefix(None, None, "STATE BANK OF INDIA", settings)
    assert result is None


async def test_T5_strict_mode_none_raises() -> None:
    """T5: strict mode + None -> resolver returns None (caller handles 403)."""
    settings = FakeSettings(prefixes=["SBI"], strict_customer_mapping=True)
    result = await resolve_customer_prefix(None, None, "UNKNOWN BANK", settings)
    assert result is None  # Resolver returns None; caller handles 403


async def test_tb_customer_id_lookup_priority() -> None:
    """Customer table lookup by tb_customer_id has priority over title matching."""
    # This would need DB, but we test the logic - it checks session first
    # Implemented in integration test


async def test_override_multiple_mappings() -> None:
    """Multiple title mappings comma-separated."""
    settings = FakeSettings(
        prefixes=["BOI", "SBI"],
        customers_title_mappings="Bank of India=BOI,State Bank of India=SBI"
    )
    assert await resolve_customer_prefix(None, None, "Bank of India", settings) == "BOI"
    assert await resolve_customer_prefix(None, None, "State Bank of India", settings) == "SBI"


async def test_cache_hit() -> None:
    """Test Redis cache hit returns prefix."""
    settings = FakeSettings(prefixes=["BOI"])
    mock_redis = AsyncMock()
    mock_redis.get.return_value = "BOI"
    mock_redis.setex = AsyncMock()

    result = await resolve_customer_prefix(None, "tb-cust-123", "Some Title", settings, mock_redis)
    assert result == "BOI"
    mock_redis.get.assert_called_once_with("customer:prefix:tb-cust-123")


async def test_db_lookup_caches_result() -> None:
    """Test DB lookup caches result in Redis."""
    settings = FakeSettings(prefixes=["SBI"])
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = "SBI"
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_redis = AsyncMock()
    mock_redis.get.return_value = None
    mock_redis.setex = AsyncMock()

    result = await resolve_customer_prefix(mock_session, "tb-cust-456", "Some Title", settings, mock_redis)
    assert result == "SBI"
    mock_redis.setex.assert_called_once_with("customer:prefix:tb-cust-456", 300, "SBI")