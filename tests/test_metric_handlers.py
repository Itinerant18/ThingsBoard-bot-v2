import json
import uuid
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from app.auth.jwt import TenantContext
from app.hierarchy.scope import ScopedBranches
from app.normalization import build_snapshot
from app.query.contracts import ExtractedIntent, RequestContext
from app.query.handlers import (
    DeviceInventory,
    GlobalOverview,
    MetricHandler,
    _format_metric,
    _load_raw,
)


class FakeClient:
    def __init__(self, attrs: dict[str, Any] | None = None, telem: Any = None, exc: Exception | None = None) -> None:
        self._attrs = attrs or {}
        self._telem = telem if telem is not None else {}
        self._exc = exc
        self.attr_calls = 0
        self.telemetry_keys: str | None = None
        self.closed = False

    async def attributes(self, device_id: str, scope: str) -> Any:
        self.attr_calls += 1
        return self._attrs.get(scope, [])

    async def telemetry(self, device_id: str, keys: str | None = None) -> Any:
        self.telemetry_keys = keys
        if self._exc is not None:
            raise self._exc
        return self._telem

    async def close(self) -> None:
        self.closed = True


def make_ctx(prefix: str | None = "BOI", token: str | None = "user-token") -> RequestContext:
    tenant = TenantContext(
        tenant_id="tt",
        customer_id="cust",
        subject="s",
        claims={},
        scopes=(),
        region=None,
        prefix=prefix,
        user_token=token,
    )
    tb = SimpleNamespace(settings=None)
    return RequestContext(tenant=tenant, db=SimpleNamespace(), redis=SimpleNamespace(), tb=tb)  # type: ignore[arg-type]


def make_handler(scoped: ScopedBranches, client: FakeClient) -> MetricHandler:
    async def scope_fn(ctx: RequestContext) -> ScopedBranches:
        return scoped

    def factory(settings: Any, token: str) -> FakeClient:
        return client

    return MetricHandler(scope_fn=scope_fn, client_factory=factory)


def gateway_intent(device_id: str | None) -> ExtractedIntent:
    return ExtractedIntent(name="gateway_status", device_id=device_id)


# --- security gates ----------------------------------------------------------


async def test_missing_device_id_asks_for_a_branch_and_offers_fleet_answers() -> None:
    """The old reply demanded a device UUID, which no operator types, and
    dead-ended: 77 real questions were fleet-wide and had a fleet answer
    available, so the reply now names that route instead."""
    handler = make_handler(ScopedBranches([], []), FakeClient())
    answer = await handler.handle(gateway_intent(None), make_ctx())
    assert "needs a branch" in answer.text
    assert "fleet-wide" in answer.text


async def test_a_scraped_word_is_not_echoed_back_as_a_rejected_id() -> None:
    """The extractor scrapes device_id from a word after "device"/"asset", so
    "What NVR models are deployed?" arrived here as device_id="models" and was
    answered "'models' is not a valid device id." — a word from the user's own
    sentence handed back as a bad identifier, on 13 real questions. A non-UUID
    never came from the caller naming a device, so it is ignored."""
    handler = make_handler(ScopedBranches([], []), FakeClient())
    answer = await handler.handle(gateway_intent("models"), make_ctx())
    assert "not a valid device id" not in answer.text
    assert "models" not in answer.text
    assert "needs a branch" in answer.text


async def test_no_prefix_denied() -> None:
    handler = make_handler(ScopedBranches([], []), FakeClient())
    answer = await handler.handle(gateway_intent(str(uuid.uuid4())), make_ctx(prefix=None))
    assert "not mapped to a customer" in answer.text


async def test_empty_scope_says_not_imported() -> None:
    client = FakeClient()
    handler = make_handler(ScopedBranches([], []), client)
    answer = await handler.handle(gateway_intent(str(uuid.uuid4())), make_ctx())
    assert "No branches are imported" in answer.text
    assert client.attr_calls == 0  # denied before any TB fetch


async def test_device_out_of_scope_denied_before_fetch() -> None:
    client = FakeClient()
    scoped = ScopedBranches(["b"], [str(uuid.uuid4())])  # some other device
    handler = make_handler(scoped, client)
    answer = await handler.handle(gateway_intent(str(uuid.uuid4())), make_ctx())
    assert "not in your authorized scope" in answer.text
    assert client.attr_calls == 0


async def test_in_scope_device_answers_and_closes_client() -> None:
    device = str(uuid.uuid4())
    client = FakeClient(attrs={"SERVER_SCOPE": [{"key": "active", "value": "true"}]})
    handler = make_handler(ScopedBranches(["b"], [device]), client)
    answer = await handler.handle(gateway_intent(device), make_ctx())
    assert "Gateway is ONLINE" in answer.text
    assert client.closed is True


async def test_fetch_requests_ladder_keys() -> None:
    # The telemetry request must include the answer-layer ladder keys, not just the
    # intent profile — else telemetry-typed fault/count keys silently under-import.
    device = str(uuid.uuid4())
    client = FakeClient()
    handler = make_handler(ScopedBranches(["b"], [device]), client)
    await handler.handle(gateway_intent(device), make_ctx())
    assert client.telemetry_keys is not None
    requested = client.telemetry_keys.split(",")
    assert "intrusion_alarm_system_fault" in requested  # a ladder key
    assert "BASfaultCOUNT" in requested


async def test_cctv_intent_fetches_vendor_keys() -> None:
    # cctv_* intents have no key_profile; the vendor JSON keys must still be requested.
    device = str(uuid.uuid4())
    client = FakeClient()
    handler = make_handler(ScopedBranches(["b"], [device]), client)
    await handler.handle(ExtractedIntent(name="cctv_recording_info", device_id=device), make_ctx())
    assert client.telemetry_keys is not None
    requested = client.telemetry_keys.split(",")
    assert "Hikvision_NVR_CameraRecInfo" in requested
    assert "rock_HddINFO" in requested


async def test_tb_error_answers_gracefully_and_closes_client() -> None:
    # A ThingsBoard failure must never escape as a 500 through the chat pipeline.
    device = str(uuid.uuid4())
    client = FakeClient(exc=RuntimeError("tb down"))
    handler = make_handler(ScopedBranches(["b"], [device]), client)
    answer = await handler.handle(gateway_intent(device), make_ctx())
    assert "could not reach ThingsBoard" in answer.text
    assert answer.structured["error"] == "thingsboard_unavailable"
    assert client.closed is True  # finally: close() still ran


async def test_tb_auth_error_tells_user_token_expired() -> None:
    device = str(uuid.uuid4())

    class _Resp:
        status_code = 401

    exc = RuntimeError("401")
    exc.response = _Resp()  # type: ignore[attr-defined]
    client = FakeClient(exc=exc)
    handler = make_handler(ScopedBranches(["b"], [device]), client)
    answer = await handler.handle(gateway_intent(device), make_ctx())
    assert "may have expired" in answer.text
    assert answer.structured["error"] == "thingsboard_auth"
    assert client.closed is True


# --- fleet handlers are scope-only (no service-client leak) ------------------


def _scoped_fn(scoped: ScopedBranches) -> Any:
    async def scope_fn(ctx: RequestContext) -> ScopedBranches:
        return scoped

    return scope_fn


async def test_global_overview_counts_only_scoped_devices() -> None:
    handler = GlobalOverview(scope_fn=_scoped_fn(ScopedBranches(["b1", "b2"], ["d1", "d2"])))
    answer = await handler.handle(ExtractedIntent(name="global_overview"), make_ctx())
    assert "2 device" in answer.text
    assert answer.structured["device_count"] == 2


async def test_global_overview_no_prefix_denied() -> None:
    handler = GlobalOverview(scope_fn=_scoped_fn(ScopedBranches(["b1"], ["d1"])))
    answer = await handler.handle(ExtractedIntent(name="global_overview"), make_ctx(prefix=None))
    assert "not mapped to a customer" in answer.text


async def test_device_inventory_lists_only_scoped_names() -> None:
    handler = DeviceInventory(scope_fn=_scoped_fn(ScopedBranches(["BOI-A", "BOI-B"], ["d1", "d2"])))
    answer = await handler.handle(ExtractedIntent(name="device_inventory"), make_ctx())
    assert "BOI-A" in answer.text and "BOI-B" in answer.text
    assert "2 branch device" in answer.text


async def test_device_inventory_answers_current_region() -> None:
    handler = DeviceInventory(scope_fn=_scoped_fn(ScopedBranches(["BOI-A"], ["d1"])))
    ctx = make_ctx()
    ctx.tenant = replace(ctx.tenant, region="FGMO EAST")
    answer = await handler.handle(
        ExtractedIntent(name="device_inventory", raw_question="Which region is currently active?"),
        ctx,
    )
    assert answer.text == "One region is active in your current scope: FGMO EAST."


async def test_device_inventory_answers_map_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.query import handlers

    async def fake_states(redis, customer, device_ids):
        return {
            "d1": {
                "branchName": "BOI-TARAKESHWAR",
                "lat1": "22.88",
                "lon1": "88.01",
                "active": "true",
            }
        }

    monkeypatch.setattr(handlers, "load_fleet_states", fake_states)
    handler = DeviceInventory(
        scope_fn=_scoped_fn(ScopedBranches(["BOI-TARAKESHWAR"], ["d1"]))
    )
    answer = await handler.handle(
        ExtractedIntent(name="device_inventory", raw_question="Which branch is visible on the map?"),
        make_ctx(),
    )
    assert "BOI-TARAKESHWAR (ONLINE)" in answer.text
    assert answer.structured["map_markers"][0]["latitude"] == 22.88


async def test_metric_handler_default_reaches_branch_scope() -> None:
    # Real defaults (no injection): _default_scope -> branch_scope(ctx.db, ...). The stub
    # ctx has no real session, so it must ERROR reaching the DB, not silently allow.
    handler = MetricHandler()
    with pytest.raises(Exception):  # noqa: B017 — proving the gate is reached, not bypassed
        await handler.handle(gateway_intent(str(uuid.uuid4())), make_ctx())


# --- _load_raw ---------------------------------------------------------------


async def test_load_raw_merges_telemetry_over_attributes() -> None:
    device = str(uuid.uuid4())
    client = FakeClient(
        attrs={"SERVER_SCOPE": [{"key": "cctv_sts", "value": "ONLINE"}, {"key": "shared", "value": "attr"}]},
        telem={"shared": [{"ts": 2, "value": "telem"}], "battery_status_battery_voltage": [{"ts": 1, "value": "40"}]},
    )
    raw = await _load_raw(client, device, ["shared"])
    assert raw["cctv_sts"] == "ONLINE"
    assert raw["battery_status_battery_voltage"] == "40"
    assert raw["shared"] == "telem"  # telemetry wins on key collision


# --- formatters (pure) -------------------------------------------------------


def _fmt(name: str, raw: dict[str, Any], subsystem: str | None = None) -> str:
    intent = ExtractedIntent(name=name, subsystem=subsystem)
    return _format_metric(intent, build_snapshot(raw), "dev").text


def test_format_battery_voltage() -> None:
    assert "48.5" in _fmt("battery_voltage", {"battery_status_battery_voltage": "48.5"})
    assert "No battery voltage" in _fmt("battery_voltage", {})


def test_format_cctv_status() -> None:
    text = _fmt(
        "cctv_status",
        {"cctv_sts": "ONLINE", "CAMERAdETAILS": [{"cameraStatus": "online"}, {"cameraStatus": "offline"}]},
    )
    assert "ONLINE" in text
    assert "1/2" in text


def test_format_battery_low_uses_real_keys() -> None:
    # snapshot.power.battery_low reads only "BATTERY LOW"; the answer now resolves the
    # real statusbox ladder, so a device reporting statusbox_battery_low is caught.
    assert "Low" in _fmt("battery_low_status", {"statusbox_battery_low": "true"})
    assert "OK" in _fmt("battery_low_status", {})


def test_format_subsystem_named() -> None:
    assert _fmt("subsystem_status", {"fas_sts": "FAULT"}, subsystem="fas") == "FAS is FAULT."


def test_format_subsystem_enriched_with_real_fault_key() -> None:
    text = _fmt(
        "subsystem_status",
        {"ias_sts": "ONLINE", "intrusion_alarm_system_fault": "true"},
        subsystem="ias",
    )
    assert "IAS is ONLINE" in text
    assert "fault YES" in text


def test_format_subsystem_summary() -> None:
    text = _fmt("subsystem_status", {"cctv_sts": "ONLINE"})
    assert "CCTV: ONLINE" in text


def test_format_device_hardware() -> None:
    assert "CPU 50.0" in _fmt("device_hardware", {"cpu": "50"})


def test_format_cctv_device_info() -> None:
    text = _fmt("cctv_device_info", {"Hikvision_NVR_model": "DS-7608NI", "rock_NoOfHDDSlots": "2"})
    assert "Vendor: Hikvision" in text
    assert "Model: DS-7608NI" in text
    assert "HDD Slots: 2" in text
    assert "not available" in _fmt("cctv_device_info", {})


def test_format_cctv_recording_info() -> None:
    raw = {
        "VIDEOdETAILS": json.dumps(
            [{"channel": "1", "total_recording_days": 95}, {"channel": "2", "total_recording_days": 0}]
        )
    }
    text = _fmt("cctv_recording_info", raw)
    assert "2 camera(s)" in text
    assert "1 compliant" in text
    assert "0 days" in text


def test_format_cctv_hdd_error_uses_real_keys() -> None:
    assert "ACTIVE" in _fmt("cctv_hdd_error_status", {"ticketStatus_HDD_ERROR": "true"})
    assert "NORMAL" in _fmt("cctv_hdd_error_status", {"hddStatus": "HEALTHY"})
    assert "N/A" in _fmt("cctv_hdd_error_status", {})
