from app.query.answer_support import (
    resolve_battery_status,
    resolve_boolean,
    resolve_from_count,
    resolve_subsystem_alarm,
    resolve_subsystem_fault,
)


def test_resolve_boolean_ladder() -> None:
    assert resolve_boolean({"a": "N/A", "b": "true"}, "a", "b") is True  # skips N/A, walks on
    assert resolve_boolean({"a": "off"}, "a") is False
    assert resolve_boolean({"a": "maybe"}, "a") is None
    assert resolve_boolean({}, "a") is None
    assert resolve_boolean({"a": "   "}, "a") is None  # whitespace-only skipped (Java isBlank)


def test_resolve_from_count() -> None:
    assert resolve_from_count({"c": "3"}, "c") is True
    assert resolve_from_count({"c": "0"}, "c") is False
    assert resolve_from_count({}, "c") is None
    assert resolve_from_count({"c": "3.0"}, "c") is None  # parseInt rejects floats


def test_resolve_battery_status() -> None:
    assert resolve_battery_status({}) == "OK"
    assert resolve_battery_status({"statusbox_battery_low": "true"}) == "Low"
    assert resolve_battery_status({"BATTERY REVERSE": "1"}) == "Reverse"
    assert (
        resolve_battery_status({"statusbox_battery_low": "1", "statusbox_battery_reverse": "1"})
        == "Low & Reverse"
    )


def test_resolve_subsystem_fault_real_keys() -> None:
    assert resolve_subsystem_fault({"intrusion_alarm_system_fault": "true"}, "ias") is True
    assert resolve_subsystem_fault({"fire_alarm_system_fault": "1"}, "fas") is True
    assert resolve_subsystem_fault({"BASfaultCOUNT": "2"}, "bas") is True
    assert resolve_subsystem_fault({"HDD ERROR": "true"}, "cctv") is True
    assert resolve_subsystem_fault({}, "ias") is None
    assert resolve_subsystem_fault({}, "unknown") is None


def test_resolve_subsystem_alarm_count_fallback() -> None:
    # timeLock door alarm falls back to the count key when the ticket flag is absent.
    assert resolve_subsystem_alarm({"time_lock_door_open_count": "1"}, "timeLock") is True
    assert resolve_subsystem_alarm({"ticketStatus_ACS_DOOR_OPEN": "true"}, "accessControl") is True
    assert resolve_subsystem_alarm({}, "bas") is None
