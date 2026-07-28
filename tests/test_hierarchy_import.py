from app.hierarchy.parser import parse_device_path
from app.hierarchy.prefix import derive_prefix


def test_shared_hierarchy_path_has_expected_nodes() -> None:
    path = "BOI Head Office → ZO Kolkata → RO Howrah → BOI-MALDATOWN"
    nodes = parse_device_path("BOI", "BOI-MALDATOWN", "device-id", path)
    assert [node.node_type for node in nodes] == ["HO", "ZO", "RO", "BRANCH"]
    assert nodes[-1].node_id == "BOI-MALDATOWN"
    assert nodes[-1].tb_device_id == "device-id"


def test_missing_full_path_falls_back_to_head_office() -> None:
    nodes = parse_device_path("SBI", "SBI-BRANCH", "device-id", None)
    assert [(node.node_type, node.node_id) for node in nodes] == [
        ("HO", "SBI_HO"),
        ("BRANCH", "SBI-BRANCH"),
    ]


def test_prefix_requires_known_prefix() -> None:
    assert derive_prefix("BOI-MALDATOWN", None, {"BOI"}) == "BOI"
    assert derive_prefix("UNKNOWN-BRANCH", "UNKNOWN Head Office → UNKNOWN-BRANCH", {"BOI"}) is None


def test_space_separated_device_names_resolve_to_a_prefix() -> None:
    """Every SBI device in the fleet is named with a space, not a hyphen.

    Splitting on "-" alone returned None for all nine of them, and a device with no
    prefix joins no hierarchy and no scope — so it is invisible to every question
    rather than merely mis-filed. Names taken from the live fleet.
    """
    known = {"BOI", "SBI", "CANARA"}
    assert derive_prefix("SBI PARIHAR", "PARIHAR → PARIHAR → ZO Muzaffapur", known) == "SBI"
    assert derive_prefix("SBI LHO PATNA", "STATE BANK OF INDIA → PATNA", known) == "SBI"
    assert derive_prefix("SBI DHARBHANGA MCC", None, known) == "SBI"
    # The hyphen form must keep working unchanged.
    assert derive_prefix("BOI-LOHARDAGA-CC", "LOHARDAGA CC → NBG-3", known) == "BOI"


def test_a_malformed_path_root_is_matched_on_any_word_not_only_the_first() -> None:
    known = {"BOI", "SBI"}
    # First-word-only matching turned this root into "ZO" and gave up.
    assert derive_prefix("gateway-42", "ZO BOI KOLKATA → BRANCH X", known) == "BOI"


def test_an_ambiguous_root_stays_unresolved_rather_than_guessing() -> None:
    known = {"BOI", "SBI"}
    assert derive_prefix("gateway-42", "BOI SBI SHARED → BRANCH X", known) is None
