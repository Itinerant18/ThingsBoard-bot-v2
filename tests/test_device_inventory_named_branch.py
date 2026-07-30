"""A named branch must be answered about, not drowned in the inventory.

Production, 2026-07-30 head-office audit:

    Q: Is there a NASIK branch currently active in the system?
    A: You have 104 branch device(s) in scope: BOI-GANERA, BOI-AMALNER, ...

NASIK was in that list. The gate had already resolved it and the orchestrator had
already put it on the intent; DeviceInventory ignored both.
"""

from types import SimpleNamespace

import pytest

from app.hierarchy.scope import ScopedBranches
from app.query.contracts import ExtractedIntent
from app.query.handlers import DeviceInventory

SCOPED = ScopedBranches(
    branch_node_ids=["BOI-NASIK", "BOI-GANERA"], tb_device_ids=["dev-nasik", "dev-ganera"]
)


def _handler():
    async def scope_fn(ctx):
        return SCOPED

    return DeviceInventory(scope_fn)


def _ctx():
    return SimpleNamespace(tenant=SimpleNamespace(prefix="BOI", region=None), db=None, redis=None)


@pytest.mark.asyncio
async def test_named_branch_is_answered_about() -> None:
    intent = ExtractedIntent(
        name="device_inventory",
        device_id="dev-nasik",
        node_name="BOI-NASIK",
        raw_question="Is there a NASIK branch currently active in the system?",
    )
    answer = await _handler().handle(intent, _ctx())  # type: ignore[arg-type]

    assert "BOI-NASIK" in answer.text
    assert answer.structured["in_scope"] is True
    assert "BOI-GANERA" not in answer.text, "still dumping the inventory"


@pytest.mark.asyncio
async def test_unresolved_question_still_lists_the_inventory() -> None:
    intent = ExtractedIntent(name="device_inventory", raw_question="what devices do I have")
    answer = await _handler().handle(intent, _ctx())  # type: ignore[arg-type]

    assert "2 branch device(s) in scope" in answer.text


@pytest.mark.asyncio
async def test_a_branch_outside_scope_does_not_get_a_yes() -> None:
    # device_id resolved but not in the ACL-filtered set: must not claim it is in scope.
    intent = ExtractedIntent(
        name="device_inventory",
        device_id="dev-bas",
        node_name="BOI-BAS",
        raw_question="Is there a BAS branch active?",
    )
    answer = await _handler().handle(intent, _ctx())  # type: ignore[arg-type]

    assert "BOI-BAS" not in answer.text
