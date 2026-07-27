"""ThingsBoard-native payload acceptance.

The live rule chain ("Transform Attr Node" in BOI Root(Custom New)) emits
{deviceName, deviceId, data:{currentAttr:{...}}}. If v2 stops accepting that exact
shape, the webhook 422s every message and device_event silently stays empty — so
these pin the real production shape, not a synthetic one.
"""

import pytest

from app.ingest.parse import EventParse, flatten_device_fields
from app.tasks.replay import fold_payload

TENANT = "24d74bb0-2061-11ee-86d5-f58fb189657b"
DEVICE = "bed23640-a821-11f0-b150-2710a8915e1d"

# Verbatim shape produced by the live rule chain.
TB_LIVE = {
    "deviceName": "BOI-LILUAH",
    "deviceId": DEVICE,
    "data": {"currentAttr": {"gatewayStatus": "online", "cpu": "41"}},
}


def test_accepts_live_thingsboard_payload() -> None:
    event = EventParse.from_payload(TB_LIVE, TENANT)
    assert event.device_id == DEVICE
    assert event.tenant_id == TENANT  # supplied by default, TB never sends one
    assert event.event_id.startswith("sha256:")  # deterministic, no id in payload


def test_native_v2_payload_still_works() -> None:
    event = EventParse.from_payload(
        {"tenant_id": "t", "device_id": DEVICE, "event_id": "e1",
         "customer_id": "BOI", "ts": 1700000000000},
        TENANT,
    )
    assert (event.tenant_id, event.event_id, event.customer_id) == ("t", "e1", "BOI")
    assert event.time.year == 2023  # epoch millis parsed, not defaulted to now


def test_camel_case_aliases() -> None:
    event = EventParse.from_payload(
        {"deviceId": DEVICE, "customerId": "BOI", "tbMessageId": "m-1",
         "logType": "ATTRIBUTE_CHANGE"},
        TENANT,
    )
    assert event.customer_id == "BOI"
    assert event.event_id == "m-1"
    assert event.event_type == "ATTRIBUTE_CHANGE"


def test_missing_device_id_rejected() -> None:
    with pytest.raises(ValueError, match="device id"):
        EventParse.from_payload({"data": {"cpu": 1}}, TENANT)


def test_missing_tenant_and_no_default_rejected() -> None:
    with pytest.raises(ValueError, match="tenant id"):
        EventParse.from_payload({"deviceId": DEVICE}, "")


def test_iso_timestamp_accepted() -> None:
    event = EventParse.from_payload(
        {"deviceId": DEVICE, "eventTime": "2026-07-25T02:09:18Z"}, TENANT
    )
    assert event.time.year == 2026 and event.time.month == 7


def test_flatten_unwraps_two_level_container() -> None:
    # data.currentAttr.* must land as flat keys the normalization ladders can read.
    assert flatten_device_fields(TB_LIVE) == {"gatewayStatus": "online", "cpu": "41"}


def test_fold_payload_handles_tb_shape() -> None:
    state: dict = {}
    fold_payload(state, TB_LIVE)
    assert state["gatewayStatus"] == "online"
    assert state["cpu"] == "41"
    # TB envelope fields must not pollute device state
    assert "deviceId" not in state
    assert "deviceName" not in state
