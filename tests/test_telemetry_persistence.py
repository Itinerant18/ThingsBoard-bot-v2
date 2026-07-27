"""Every device key must reach the device_telemetry hypertable.

Before this, nothing pulled from ThingsBoard was persisted: live sync fetched all
128 devices every 60s and wrote only to Redis with a 15-minute TTL, and device_event
covered just 19 devices because only the rule-chain webhook fed it.
"""

import json
from datetime import UTC, datetime

import pytest

from app.ingest.telemetry import _split_value, changed_fields, write_telemetry


class TestSplitValue:
    def test_numbers_populate_both_columns(self) -> None:
        assert _split_value(14.0) == (14.0, "14.0")
        assert _split_value(3) == (3.0, "3")

    def test_numeric_strings_are_recognised(self) -> None:
        """Redis returns everything as text; "220.0" must still aggregate."""
        assert _split_value("220.0") == (220.0, "220.0")

    def test_booleans_are_not_silently_ones_and_zeros_only(self) -> None:
        """bool is an int subclass — without an explicit branch True becomes 1.0 with
        no readable text form."""
        assert _split_value(True) == (1.0, "true")
        assert _split_value(False) == (0.0, "false")

    def test_containers_are_stored_as_json_text(self) -> None:
        num, text = _split_value({"powerStatus": "Off"})
        assert num is None
        assert json.loads(text) == {"powerStatus": "Off"}

    def test_lists_survive_whole(self) -> None:
        num, text = _split_value([{"HDDSlot": "1"}])
        assert num is None
        assert json.loads(text) == [{"HDDSlot": "1"}]

    def test_plain_text_has_no_numeric_form(self) -> None:
        assert _split_value("Offline") == (None, "Offline")

    def test_none_is_nothing(self) -> None:
        assert _split_value(None) == (None, None)


class TestChangeDetection:
    def test_no_baseline_writes_everything(self) -> None:
        fields = {"battery_voltage": 14.0, "gateway_sts": "on"}
        assert changed_fields(fields, None) == fields

    def test_unchanged_keys_are_skipped(self) -> None:
        prev = {"battery_voltage": "14.0", "gateway_sts": "on"}
        assert changed_fields({"battery_voltage": 14.0, "gateway_sts": "on"}, prev) == {}

    def test_typed_vs_string_baseline_is_not_a_false_change(self) -> None:
        """The baseline comes from Redis (all strings) while the fetch is typed. A raw
        comparison would mark every key changed on every cycle and defeat the point."""
        assert changed_fields({"n": 1.0, "b": True}, {"n": "1.0", "b": "true"}) == {}

    def test_real_changes_are_detected(self) -> None:
        changed = changed_fields({"battery_voltage": 12.5}, {"battery_voltage": "14.0"})
        assert changed == {"battery_voltage": 12.5}

    def test_new_key_counts_as_changed(self) -> None:
        assert changed_fields({"a": 1, "b": 2}, {"a": "1"}) == {"b": 2}


class _Session:
    """Captures the rows a write would insert."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def execute(self, stmt):
        compiled = stmt.compile()
        params = compiled.params
        self.rows.append(params)

        class _Result:
            rowcount = 1

        return _Result()

    async def commit(self) -> None:
        pass


@pytest.mark.asyncio
async def test_write_skips_bookkeeping_and_nulls() -> None:
    session = _Session()
    written = await write_telemetry(
        session,  # type: ignore[arg-type]
        "dev-1",
        {
            "battery_voltage": 14.0,
            "device_id": "dev-1",  # bookkeeping, not device data
            "branch_name": "BOI-X",
            "node_id": "n1",
            "empty": None,  # a null observation carries nothing
        },
        customer_id="BOI",
        observed_at=datetime(2026, 7, 27, tzinfo=UTC),
    )
    assert written == 1
    keys = {v for k, v in session.rows[0].items() if k.startswith("key")}
    assert keys == {"battery_voltage"}


@pytest.mark.asyncio
async def test_empty_input_writes_nothing() -> None:
    session = _Session()
    assert await write_telemetry(session, "dev-1", {}) == 0  # type: ignore[arg-type]
    assert session.rows == []
