"""Fleet-wide CCTV recording and inventory, against the real NVR payload shape.

The `rock` container is what a live BOI NVR actually publishes: a single JSON object
that flatten.expand_containers addresses by dotted path. The fixture keeps that shape
— including the Hikvision HDD field spellings (HDDSlots/HDDcapacity/HDDfreeSpace,
which differ from Dahua's HDDSlot/HDDCapacity/HDDFreeSpace) — because reading the
wrong spelling is precisely how this data went unparsed.
"""

import json

import pytest

from app.normalization.flatten import expand_containers
from app.query import cctv
from app.query.cctv_fleet import aggregate_cctv, format_cctv_fleet
from app.query.extract import KeywordIntentExtractor

HDD = [
    {"HDDSlots": "1", "HDDStatus": "ok", "HDDcapacity": "5.46", "HDDfreeSpace": "1.20"},
    {"HDDSlots": "2", "HDDStatus": "ok", "HDDcapacity": "5.46", "HDDfreeSpace": "3.43"},
]


def _rock(
    channels: dict[str, int], *, model: str = "DS-7716NXI-K4", cameras: int | None = None
) -> dict:
    """One NVR. `cameras` defaults to one CAMERAdETAILS entry per channel, which is what
    a live NVR reports; pass it explicitly only for the empty-VIDEOdETAILS case."""
    installed = len(channels) if cameras is None else cameras
    return {
        "model": model,
        "manufacturer": "Hikvision",
        "NoOfHDDSlots": 4,
        "capacity": 21.84,
        "HddINFO": HDD,
        "VIDEOdETAILS": [
            {"channel_no": ch, "channel_name": f"Camera {ch}", "total_duration": days}
            for ch, days in channels.items()
        ],
        "CAMERAdETAILS": [{"Channel Name": f"Camera {i + 1}"} for i in range(installed)],
    }


def _branch(name: str, rock: dict, *, as_json: bool = False) -> dict:
    # Redis hands every value back as a string; the live fetch hands back dicts. Both
    # shapes reach this code, so the fixture exercises both.
    return expand_containers(
        {"branch_name": name, "rock": json.dumps(rock) if as_json else rock}
    )


FLEET = {
    "d1": _branch("BALLYBAZAR", _rock({"1": 491, "2": 156, "3": 0, "4": 0})),
    "d2": _branch("DOBSON", _rock({"1": 239, "2": 208}), as_json=True),
    "d3": _branch("LILUAH", _rock({"1": 45, "2": 0}, model="DH-XVR5108")),
    # A branch in scope whose NVR published nothing: must not read as zero cameras.
    "d4": expand_containers({"branch_name": "MALDATOWN"}),
    # Cameras installed but VIDEOdETAILS empty — common in the fleet, and the reason
    # camera_count falls back to CAMERAdETAILS.
    "d5": _branch("HOWRAH", _rock({}, model="DS-7608NI", cameras=6)),
}


def answer(question: str) -> str:
    return format_cctv_fleet(aggregate_cctv(FLEET), question)


def test_parsers_read_the_dotted_container_not_an_underscore_name() -> None:
    """rock_VIDEOdETAILS matches nothing in the fleet; rock.VIDEOdETAILS is the key."""
    summary = cctv.recording_summary(FLEET["d1"])
    assert summary["available"] is True
    assert summary["total"] == 4
    assert summary["max_days"] == 491
    assert cctv.device_info(FLEET["d1"])["model"] == "DS-7716NXI-K4"
    assert len(cctv.hdd_info(FLEET["d1"])) == 2


def test_hikvision_hdd_field_spelling_is_read() -> None:
    slots = cctv.hdd_info(FLEET["d1"])
    assert slots[0]["capacity_tb"] == "5.46"
    assert slots[0]["slot"] == "1"


def test_totals_exclude_branches_that_published_no_nvr_data() -> None:
    fleet = aggregate_cctv(FLEET)
    assert len(fleet.reporting) == 4
    assert [b.branch for b in fleet.silent] == ["MALDATOWN"]
    # 4 + 2 + 2 recording channels, plus HOWRAH's 6 installed cameras with no rows.
    assert fleet.total_channels == 14
    assert fleet.recording == 5
    assert fleet.not_recording == 9


def test_a_silent_branch_is_disclosed_not_hidden() -> None:
    reply = answer("What is the current recording status across all branches?")
    assert "1 scoped branch(es) returned no NVR data" in reply


def test_recording_health_percentage() -> None:
    reply = answer("What is the current recording health percentage?")
    assert "35.7%" in reply  # 5 of 14
    assert "90-day retention" in reply


def test_branch_with_the_most_cameras_not_recording() -> None:
    reply = answer("Which branch has the most cameras currently not recording?")
    # HOWRAH reports 6 installed cameras and no recording rows at all.
    assert reply.startswith("HOWRAH")
    assert "6 of 6" in reply


def test_recording_failures_list_every_offending_branch() -> None:
    reply = answer("Are there any recording failures right now?")
    assert "9 of 14 channels have no recorded footage" in reply
    assert "HOWRAH" in reply and "BALLYBAZAR" in reply and "LILUAH" in reply
    assert "DOBSON" not in reply


def test_storage_consumption_sums_installed_and_free_capacity() -> None:
    reply = answer("What is the current storage consumption for recordings?")
    assert "87.36 TB" in reply  # 21.84 x 4 NVRs that answered
    assert "18.52 TB free" in reply  # (1.20 + 3.43) x 4


def test_inventory_lists_models_and_camera_count() -> None:
    reply = answer("What CCTV camera models are currently deployed?")
    assert "14 cameras are configured" in reply  # 4 + 2 + 2 + 6
    assert "Hikvision DS-7716NXI-K4 x2" in reply
    assert "DH-XVR5108" in reply


def test_no_data_in_scope_says_so() -> None:
    reply = format_cctv_fleet(aggregate_cctv({"d9": expand_containers({})}), "recording status")
    assert "No CCTV recording data is currently available" in reply


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question",
    [
        "What is the current recording status across all branches?",
        "Which branch has the most cameras currently not recording?",
        "Are there any recording failures right now?",
        "What is the current recording health percentage?",
        "Which cameras have recording gaps right now?",
        "What is the current storage consumption for recordings?",
        "How many CCTV cameras are currently deployed across all branches?",
        "What is the current inventory status of CCTV devices?",
        "What CCTV camera models are currently deployed?",
    ],
)
async def test_fleet_shaped_cctv_questions_do_not_land_on_a_single_branch_handler(
    question: str,
) -> None:
    assert (await KeywordIntentExtractor().extract(question)).name == "cctv_fleet"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("cctv hdd info of liluah", "cctv_hdd_info"),
        ("cctv storage of liluah", "cctv_storage"),
        ("what is the cctv status of liluah", "cctv_status"),
    ],
)
async def test_single_branch_cctv_questions_still_route_per_device(
    question: str, expected: str
) -> None:
    assert (await KeywordIntentExtractor().extract(question)).name == expected


def test_a_corrupt_capacity_cannot_swamp_the_fleet_total() -> None:
    """Production printed "2987145560790.61 TB of installed recording capacity".
    One NVR reporting a corrupt figure dominated the sum, and "consumed" was then
    derived from that same corrupt total and printed to two decimal places."""
    broken = _rock({"1": 90}, model="DS-BROKEN")
    broken["capacity"] = 2987145560768.0
    fleet = aggregate_cctv({**FLEET, "d9": _branch("CORRUPTED", broken)})

    assert fleet.storage_tb < 500  # the four credible NVRs only
    assert [b.branch for b in fleet.implausible_storage] == ["CORRUPTED"]

    reply = format_cctv_fleet(fleet, "What is the current storage consumption?")
    assert "2987145560790" not in reply
    assert "implausible capacity" in reply
    assert "CORRUPTED" in reply


def test_every_credible_capacity_rejected_says_so_rather_than_printing_zero() -> None:
    broken = _rock({"1": 90})
    broken["capacity"] = 9e12
    fleet = aggregate_cctv({"d1": _branch("ONLY", broken)})
    reply = format_cctv_fleet(fleet, "storage consumption")
    assert "credible recording capacity" in reply.lower()
    assert "implausible" in reply.lower()
