"""Questions must route to the intents added from docs/Telimetry-Attribute-key.md.

A formatter nobody can reach is dead code, and the keyword extractor is an ordered
if/elif chain — a new branch placed after a broader one never fires. These pin the
routing, including the ordering hazards: "bas panel state" must not be swallowed by
the generic subsystem branch, and "how many cameras" must reach the camera count
rather than the fleet-wide device count.
"""

import pytest

from app.query.extract import KeywordIntentExtractor
from app.query.handlers import METRIC_INTENTS
from app.query.key_profiles import INTENT_KEYS


async def intent_for(question: str) -> str:
    return (await KeywordIntentExtractor().extract(question)).name


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("what is the network status", "network_status"),
        ("which network operator is it on", "network_status"),
        ("sos status of the branch", "sos_status"),
        ("how many connected devices", "connected_devices"),
        ("door status", "door_status"),
        ("cctv storage capacity", "cctv_storage"),
        ("cctv free space", "cctv_storage"),
        ("how many cameras are there", "cctv_camera_count"),
        ("camera information", "cctv_camera_info"),
        ("cctv sd card recording", "cctv_sd_recording"),
        ("camera tamper count", "cctv_tamper_count"),
        ("bas panel state", "bas_panel_info"),
        ("bas heartbeat", "bas_panel_info"),
        ("bas zone information", "bas_zone_info"),
        ("bas power status", "bas_power_status"),
    ],
)
@pytest.mark.asyncio
async def test_question_routes_to_the_expected_intent(question: str, expected: str) -> None:
    assert await intent_for(question) == expected


@pytest.mark.asyncio
async def test_ordering_hazards() -> None:
    """Each of these sits after a broader branch that would otherwise swallow it."""
    # "bas" alone still means the subsystem, not the panel.
    assert await intent_for("bas status") == "subsystem_status"
    # cctv counts must not fall into the fleet-wide global_overview count.
    assert await intent_for("how many cameras") == "cctv_camera_count"
    assert await intent_for("how many devices do i have") == "global_overview"
    # A plain cctv question still lands on cctv_status.
    assert await intent_for("cctv status") == "cctv_status"


def test_every_new_intent_is_answerable_and_has_keys() -> None:
    """An intent the handler cannot serve, or that fetches nothing, is a dead end."""
    added = {
        "network_status",
        "sos_status",
        "connected_devices",
        "door_status",
        "cctv_storage",
        "cctv_camera_count",
        "cctv_camera_info",
        "cctv_sd_recording",
        "cctv_tamper_count",
        "bas_panel_info",
        "bas_power_status",
        "bas_zone_info",
    }
    assert added <= METRIC_INTENTS, added - METRIC_INTENTS
    missing_keys = {name for name in added if not INTENT_KEYS.get(name)}
    assert not missing_keys, f"no key profile, so nothing would be fetched: {missing_keys}"
