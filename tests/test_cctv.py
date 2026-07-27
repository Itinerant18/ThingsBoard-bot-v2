import json

from app.query.cctv import device_info, hdd_info, nvr_vendor, parse_recordings, recording_summary


def test_nvr_vendor_from_brand_then_model_prefix() -> None:
    assert nvr_vendor({"nvr_brand": "Hikvision"}, "whatever") == "Hikvision"
    assert nvr_vendor({}, "DS-7608NI") == "Hikvision"
    assert nvr_vendor({}, "iDS-9016") == "Hikvision"
    assert nvr_vendor({}, "XVR5108HS") == "Dahua"
    assert nvr_vendor({}, "CP-UNR-104") == "CPPLUS"
    assert nvr_vendor({}, "UNKNOWN123") is None
    assert nvr_vendor({}, None) is None


def test_device_info_collects_present_fields() -> None:
    raw = {
        "Hikvision_NVR_model": "DS-7608NI",
        "rock_NoOfHDDSlots": "2",
        "rock_capacity": "4",
        "Hikvision_NVR_Resolutions": "1080p",
    }
    info = device_info(raw)
    assert info == {
        "vendor": "Hikvision",
        "model": "DS-7608NI",
        "hdd_slots": 2,
        "storage_tb": 4.0,
        "resolution": "1080p",
    }
    assert device_info({}) == {}


def test_hdd_info_reads_both_vendor_schemas() -> None:
    # Dahua schema (HDDSlot/HDDCapacity/HDDFreeSpace) must not all show N/A.
    raw = {
        "rock_HddINFO": json.dumps(
            [{"HDDSlot": "1", "HDDStatus": "OK", "HDDCapacity": "2", "HDDFreeSpace": "1.5"}]
        )
    }
    slots = hdd_info(raw)
    assert slots == [{"slot": "1", "status": "OK", "capacity_tb": "2", "free_tb": "1.5"}]
    assert hdd_info({}) == []


def test_parse_recordings_dedups_channel_and_reads_both_day_fields() -> None:
    raw = {
        "Hikvision_NVR_CameraRecInfo": json.dumps([{"channel": "1", "total_duration": 30}]),
        # Same channel 1 reported again with more days -> keep the max.
        "Dahua_NVR_CameraRecInfo": json.dumps(
            [{"channel_no": "1", "total_recording_days": 95}, {"channel_no": "2", "total_recording_days": 0}]
        ),
    }
    cams = parse_recordings(raw)
    assert cams == {"1": 95, "2": 0}


def test_recording_summary_compliance() -> None:
    raw = {
        "VIDEOdETAILS": json.dumps(
            [
                {"channel": "1", "total_recording_days": 95},  # compliant
                {"channel": "2", "total_recording_days": 10},  # non-compliant
                {"channel": "3", "total_recording_days": 0},   # zero
            ]
        )
    }
    rec = recording_summary(raw, retention_days=90)
    assert rec["available"] is True
    assert rec["total"] == 3
    assert rec["compliant"] == 1
    assert rec["non_compliant"] == 2
    assert rec["zero"] == 1
    assert rec["zero_channels"] == ["3"]
    assert rec["min_days"] == 0
    assert rec["max_days"] == 95
    assert recording_summary({})["available"] is False
