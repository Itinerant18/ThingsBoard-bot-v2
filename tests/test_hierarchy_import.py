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
