"""Time windows parsed from questions, and inherited across follow-ups.

Phase 2 of conversational memory. Phase 1 remembered WHAT was asked (the intent);
this remembers WHEN, and — critically — gives it a consumer: device_telemetry had
been accumulating millions of rows that nothing ever read.

Parsing is deliberately a small explicit vocabulary. A wrong window silently answers
about the wrong period, which is far harder to notice than a refusal, so anything
unrecognised must yield None and leave latest-value behaviour intact.
"""

import pytest

from app.query.extract import KeywordIntentExtractor
from app.query.memory import ChatContext
from app.query.timeframe import MAX_WINDOW_HOURS, asks_for_history, parse_window


class TestParsing:
    @pytest.mark.parametrize(
        ("text", "hours"),
        [
            ("battery voltage last week", 24 * 7),
            ("battery voltage last 24 hours", 24),
            ("ac voltage yesterday", 24),
            ("system current last month", 24 * 30),
            ("battery voltage last 3 days", 72),
            ("battery voltage past 12 hours", 12),
            ("battery voltage last 2 weeks", 24 * 14),
            ("battery voltage last year", 24 * 365),
        ],
    )
    def test_recognised_windows(self, text: str, hours: int) -> None:
        window = parse_window(text)
        assert window is not None
        assert window.hours == hours

    def test_longest_phrase_wins(self) -> None:
        """"last 24 hours" must not be shadowed by the shorter "last day"."""
        window = parse_window("battery voltage last 24 hours")
        assert window is not None and window.hours == 24

    @pytest.mark.parametrize(
        "text",
        ["battery voltage of liluah", "cctv status", "how many devices", "gateway status now"],
    )
    def test_no_window_means_latest_value(self, text: str) -> None:
        assert parse_window(text) is None

    def test_absurd_window_is_capped(self) -> None:
        """A question must not be able to demand a scan of the whole hypertable."""
        window = parse_window("battery voltage last 999 months")
        assert window is not None
        assert window.hours == MAX_WINDOW_HOURS

    def test_singular_label_reads_naturally(self) -> None:
        window = parse_window("battery voltage last 1 day")
        assert window is not None and window.label == "the last 1 day"

    def test_asks_for_history_catches_verbs_without_a_period(self) -> None:
        assert asks_for_history("what is the battery voltage trend") is True
        assert asks_for_history("average battery voltage") is True
        assert asks_for_history("battery voltage of liluah") is False


class TestWindowMemory:
    @pytest.mark.asyncio
    async def test_window_is_extracted_onto_the_intent(self) -> None:
        got = await KeywordIntentExtractor().extract("battery voltage last week")
        assert got.name == "battery_voltage"
        assert got.window is not None and got.window.hours == 24 * 7

    @pytest.mark.asyncio
    async def test_fragment_inherits_the_remembered_window(self) -> None:
        """"...last week" then "and Howrah?" must stay on the same week rather than
        silently reverting to the latest value."""
        context = ChatContext(intent="battery_voltage", window_hours=168, window_label="the last week")
        got = await KeywordIntentExtractor().extract("and howrah?", context)
        assert got.name == "battery_voltage"
        assert got.window is not None and got.window.hours == 168

    @pytest.mark.asyncio
    async def test_a_new_window_overrides_the_remembered_one(self) -> None:
        context = ChatContext(intent="battery_voltage", window_hours=168, window_label="the last week")
        got = await KeywordIntentExtractor().extract("and yesterday?", context)
        assert got.window is not None and got.window.hours == 24

    @pytest.mark.asyncio
    async def test_self_contained_question_does_not_inherit_a_window(self) -> None:
        """Asking a fresh question with no period means NOW, even if the previous turn
        was about last week — inheriting there would answer the wrong period."""
        context = ChatContext(intent="battery_voltage", window_hours=168, window_label="the last week")
        got = await KeywordIntentExtractor().extract("cctv status of liluah", context)
        assert got.name == "cctv_status"
        assert got.window is None
