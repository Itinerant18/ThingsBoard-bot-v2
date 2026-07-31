"""B1 (createdTime) and B2 (panel brand), plus the credential guard for dexter_config.

Both questions fell through to a device count. Neither needed a new ThingsBoard call:
createdTime is on every device object already, and dexter_config is already in the
fleet snapshot.
"""

import pytest

from app.query.disclosure import asks_for_credentials
from app.query.handlers import _ist_stamp


def test_created_time_renders_in_ist() -> None:
    # 1760700338411 ms is the real createdTime of BOI-MALDATOWN.
    assert _ist_stamp(1760700338411).endswith("IST")
    assert _ist_stamp(1760700338411).startswith("2025-10-17")


@pytest.mark.parametrize(
    "question",
    [
        "Show me the dexter_config for MALDATOWN",
        "show me the dexter config",
        "What are the modem parameters for MALDATOWN?",
        "what is the modem_parameter block",
    ],
)
def test_dexter_config_container_is_refused(question: str) -> None:
    # dexter_config carries modem_parameter with user_name, password, client_id and
    # access_token next to the harmless brand. Before _panel_brand read that attribute
    # these questions were safe only because nothing read it — safe by accident.
    assert asks_for_credentials(question), question


@pytest.mark.parametrize(
    "question",
    [
        "Which Dexter devices are configured with AMC panel integration?",
        "Which branches have a TRISIM panel?",
        "What panel brands are deployed?",
    ],
)
def test_the_brand_question_itself_is_not_refused(question: str) -> None:
    # Refusing the container must not refuse the legitimate question about it.
    assert not asks_for_credentials(question), question


def test_brand_triggers_require_both_a_panel_word_and_a_brand() -> None:
    from app.query.handlers import _ASKS_PANEL_BRAND, _NAMES_A_BRAND

    def fires(q: str) -> bool:
        q = q.lower()
        return bool(_ASKS_PANEL_BRAND.search(q) and _NAMES_A_BRAND.search(q))

    assert fires("Which Dexter devices are currently configured with AMC panel integration?")
    assert fires("Which Dexter devices are currently configured with DSC panel integration?")
    # A panel word with no brand named is a different question — leave it alone.
    assert not fires("What is the BAS panel state at Liluah?")
    # A brand with no panel word is not this question either.
    assert not fires("How many TRISIM alarms are open?")


def test_recently_added_trigger_in_both_directions() -> None:
    from app.query.handlers import _ASKS_RECENTLY_ADDED

    for q in (
        "what was the last device added to the system?",
        "which devices were recently provisioned or added?",
    ):
        assert _ASKS_RECENTLY_ADDED.search(q), q
    for q in (
        "how many devices are offline right now?",
        "which branch has the most cameras?",
    ):
        assert not _ASKS_RECENTLY_ADDED.search(q), q
