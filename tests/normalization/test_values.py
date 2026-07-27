from app.normalization.values import (
    NormalizedState,
    to_bool,
    to_double,
    to_int,
    to_state,
)


def test_to_state_keywords() -> None:
    for raw in ("online", "ON", "healthy", "active", "true", "1", "yes", "clear", "normal"):
        assert to_state(raw) == NormalizedState.ONLINE
    for raw in ("offline", "off", "inactive", "false", "0", "disconnected"):
        assert to_state(raw) == NormalizedState.OFFLINE
    for raw in ("fault", "alarm", "ERROR", "tamper", "triggered", "critical"):
        assert to_state(raw) == NormalizedState.FAULT
    for raw in ("N/A", "na", "not installed", "-"):
        assert to_state(raw) == NormalizedState.NOT_INSTALLED


def test_to_state_corrupt_and_unknown() -> None:
    assert to_state(None) == NormalizedState.UNKNOWN
    assert to_state("") == NormalizedState.UNKNOWN
    assert to_state("null") == NormalizedState.UNKNOWN
    assert to_state("null1") == NormalizedState.UNKNOWN  # startswith("null")
    assert to_state("not_found") == NormalizedState.UNKNOWN
    assert to_state("not found") == NormalizedState.UNKNOWN
    assert to_state("xyz") == NormalizedState.UNKNOWN


def test_to_state_accepts_non_str() -> None:
    assert to_state(True) == NormalizedState.ONLINE
    assert to_state(1) == NormalizedState.ONLINE
    assert to_state(0) == NormalizedState.OFFLINE


def test_to_bool() -> None:
    assert to_bool("yes") is True
    assert to_bool(1) is True
    assert to_bool("false") is False
    assert to_bool(0) is False
    assert to_bool("xyz") is None
    assert to_bool(None) is None


def test_to_double() -> None:
    assert to_double("57.452") == 57.452
    assert to_double(14) == 14.0
    assert to_double("N/A") is None
    assert to_double("-") is None
    assert to_double("null") is None
    assert to_double("abc") is None


def test_to_int_rejects_float_string() -> None:
    # Java parseInt("3.0") throws -> fallback. Must NOT be int(float("3.0")).
    assert to_int("1800", 0) == 1800
    assert to_int("0", 99) == 0
    assert to_int("3.0", 99) == 99
    assert to_int("N/A", 99) == 99
    assert to_int(None, 99) == 99
