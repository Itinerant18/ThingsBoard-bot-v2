"""Computed answers from docs/Telimetry-Attribute-key.md.

Fixtures use shapes sampled from the live fleet on 2026-07-27, including the awkward
ones the doc's pseudo-code does not mention: rock.CAMERAdETAILS is padded with
nulls, cameraTamperCount is an object rather than a number, and VIDEOdETAILS /
zoneInfo are frequently empty.
"""

import json

from app.query import derived

HDD = [
    {"HDDSlot": "Slot 1", "HDDStatus": "OK", "HDDCapacity": "2000.00", "HDDFreeSpace": "500.50"},
    {"HDDSlot": "Slot 2", "HDDStatus": "Error", "HDDCapacity": "1000.00", "HDDFreeSpace": "0.00"},
    {"HDDSlot": "Slot NA", "HDDStatus": "Idle", "HDDCapacity": "0.00", "HDDFreeSpace": "0.00"},
]


class TestHdd:
    def test_total_capacity_sums_string_values(self) -> None:
        assert derived.hdd_total_capacity({"rock.HddINFO": HDD}) == 3000.0

    def test_free_space_sums(self) -> None:
        assert derived.hdd_free_space({"rock.HddINFO": HDD}) == 500.5

    def test_error_count_is_case_insensitive(self) -> None:
        assert derived.hdd_error_count({"rock.HddINFO": HDD}) == 1

    def test_accepts_the_json_string_form_from_redis(self) -> None:
        assert derived.hdd_total_capacity({"rock.HddINFO": json.dumps(HDD)}) == 3000.0

    def test_missing_key_is_none_not_zero(self) -> None:
        """No disks reporting is not the same as a genuine 0 capacity."""
        assert derived.hdd_total_capacity({}) is None
        assert derived.hdd_error_count({}) == 0

    def test_rows_expose_slot_detail(self) -> None:
        rows = derived.hdd_rows({"rock.HddINFO": HDD})
        assert rows[0] == {
            "slot": "Slot 1",
            "status": "OK",
            "capacity": 2000.0,
            "free_space": 500.5,
        }
        # "Slot NA" is kept verbatim — it is what the device reports, and only a bare
        # "NA"/"-"/"" counts as missing. Better to echo the device than to invent a null.
        assert rows[2]["slot"] == "Slot NA"
        assert rows[2]["capacity"] == 0.0  # a real zero, distinct from "not reported"


class TestCameras:
    def test_count_prefers_video_details(self) -> None:
        raw = {"rock.VIDEOdETAILS": [{"cameraName": "a"}, {"cameraName": "b"}]}
        assert derived.camera_count(raw) == 2

    def test_count_falls_back_when_video_details_is_empty(self) -> None:
        """VIDEOdETAILS is [] on many devices while the inventory is populated."""
        raw = {"rock.VIDEOdETAILS": [], "rock.CAMERAdETAILS": [{"id": "1"}, {"id": "2"}]}
        assert derived.camera_count(raw) == 2

    def test_null_padding_is_dropped(self) -> None:
        """The fleet pads CAMERAdETAILS with nulls for unpopulated channels."""
        raw = {"rock.CAMERAdETAILS": [None, None, {"id": "8", "manufacturer": "Dahua"}]}
        assert derived.camera_count(raw) == 1
        rows = derived.camera_rows(raw)
        assert len(rows) == 1
        assert rows[0]["manufacturer"] == "Dahua"

    def test_counts_accept_the_object_form(self) -> None:
        """cameraTamperCount arrives as {} in production despite the name."""
        assert derived.camera_tamper_count({"cameraTamperCount": {}}) == 0
        assert derived.camera_tamper_count({"cameraTamperCount": {"ch1": 1, "ch2": 1}}) == 2
        assert derived.camera_disconnect_count({"cameraDisconnectCount": 3}) == 3


class TestNetwork:
    def test_operator_present_means_on(self) -> None:
        assert derived.network_status({"statusbox_network": "Jio"}) == {
            "status": "On",
            "operator": "Jio",
        }

    def test_blank_means_off_with_dash(self) -> None:
        """Doc: else Status = Off, Operator = "-"."""
        assert derived.network_status({"statusbox_network": ""}) == {
            "status": "Off",
            "operator": "-",
        }
        assert derived.network_status({})["status"] == "Off"

    def test_falls_back_to_the_nested_container_field(self) -> None:
        raw = {"system_status.statusbox_network": "Airtel"}
        assert derived.network_status(raw)["operator"] == "Airtel"

    def test_sos_and_connected_devices(self) -> None:
        assert derived.sos_status({"statusbox_sos_status": "true"}) == "Active"
        assert derived.sos_status({"statusbox_sos_status": "false"}) == "Clear"
        assert derived.sos_status({}) is None
        assert derived.connected_devices({"statusbox_no_of_connected_device": 0}) == 0
        assert derived.connected_devices({}) is None


BAS_RAW = {
    "basSystemIntegration.basMainInfo": {
        "branchName": "BOI-DX5",
        "heartbeat": "online",
        "panelState": "Active",
        "zoneSupported": 24,
        "panelMode": "N/A",
    },
    "basSystemIntegration.basPowerStatus": {"systemVoltage": "13.8", "mainStatus": "N/A"},
    "basSystemIntegration.basAboutDevice": {"panelIp": "10.0.0.5", "model": "N/A"},
}


class TestBas:
    RAW = BAS_RAW

    def test_panel_reads_heartbeat_and_state(self) -> None:
        panel = derived.bas_panel(self.RAW)
        assert panel["heartbeat"] == "online"
        assert panel["panel_state"] == "Active"
        assert panel["panel_mode"] is None  # "N/A" normalises to missing

    def test_power_and_device(self) -> None:
        assert derived.bas_power(self.RAW)["system_voltage"] == "13.8"
        assert derived.bas_power(self.RAW)["mains_status"] is None
        assert derived.bas_device(self.RAW)["panel_ip"] == "10.0.0.5"

    def test_zones_empty_is_not_an_error(self) -> None:
        assert derived.bas_zones({"basSystemIntegration.zoneInfo": []}) == []
        rows = derived.bas_zones(
            {"basSystemIntegration.zoneInfo": [{"zoneName": "Lobby", "areaStates": "Armed"}]}
        )
        assert rows[0]["zone"] == "Lobby"


class TestSummarize:
    def test_empty_reads_as_a_sentence_not_a_blank_table(self) -> None:
        assert derived.summarize("Camera information", []).startswith("No Camera information")

    def test_long_lists_are_truncated(self) -> None:
        rows = [{"channel": str(i)} for i in range(16)]
        text = derived.summarize("Camera information", rows, limit=5)
        assert "(16)" in text
        assert "+11 more" in text
