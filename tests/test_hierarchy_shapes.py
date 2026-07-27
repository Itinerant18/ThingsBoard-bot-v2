"""Every customer has its own hierarchy shape — nothing may assume a fixed depth.

Fixtures below are REAL chains reconstructed from the production hierarchy_node
table (2026-07-27), not invented paths. Eighteen distinct shapes exist; these cover
every structural class:

    SEPL    depth 2   HO -> BRANCH
    BOI     depth 2   HO -> BRANCH                  (device hanging straight off HO)
    PNB     depth 3   HO -> ZO -> BRANCH
    CANARA  depth 3   HO -> HO -> BRANCH            (head office nested under HO)
    BOB     depth 4   HO -> ZO -> RO -> BRANCH      (only customer using RO)
    BOI     depth 4   HO -> NBG -> NBG -> BRANCH    (repeated container type)
    BOI     depth 5   HO -> ZO -> NBG -> ZO -> BRANCH

Leaves do NOT sit at a uniform level: BOI's are spread across levels 2-5, CANARA's
2-4. So `node_level` is a recorded fact, never a predicate — leaf-ness comes from
`is_leaf` and ancestry from the branch_ancestor_path closure table, both of which
are depth-agnostic. These tests fail if anyone reintroduces a depth or type-per-level
assumption that fits BOI and silently breaks SEPL, PNB or CANARA.

Display names are verbatim production values, so they also pin the naming reality:
"ZO(Kolkata)" carries parentheses, "NBG-3" a hyphen, and "BAS" / "SBI Branch1" /
"LOHARDAGA CC" carry no type prefix at all and rely on the ZO fallback.
"""

from itertools import pairwise

from app.hierarchy.parser import parse_device_path

# (prefix, device, full_path, expected node types) — all straight from production.
SHAPES = [
    ("SEPL", "SEPL-DX8", "SEPL Head Office → SEPL-DX8", ["HO", "BRANCH"]),
    ("BOI", "BOI-BAHALDA", "BANK OF INDIA → BOI-BAHALDA", ["HO", "BRANCH"]),
    ("PNB", "Jarvis2", "PNB Head Office → PNB PATNA → Jarvis2", ["HO", "ZO", "BRANCH"]),
    (
        "CANARA",
        "CANARA-HALDI2",
        "CANARA BANK → HO (Canara Bank) → CANARA-HALDI2",
        ["HO", "HO", "BRANCH"],
    ),
    ("SBI", "SBI-TimeLock", "STATE BANK OF INDIA → SBI Branch1 → SBI-TimeLock", ["HO", "ZO", "BRANCH"]),
    (
        "BOB",
        "BOB-APC-ROAD",
        "BANK OF BARODA → ZO(Kolkata) → RO(KMR) → BOB-APC-ROAD",
        ["HO", "ZO", "RO", "BRANCH"],
    ),
    (
        "BOI",
        "BOI-DX7",
        "BANK OF INDIA → NBG EAST → NBG SEPLE → BOI-DX7",
        ["HO", "NBG", "NBG", "BRANCH"],
    ),
    (
        "DEXTER",
        "DEXTER-RANCHI",
        "DEXTER RANCHI CUS → NBG-3 → ZO-5 → DEXTER-RANCHI",
        ["HO", "NBG", "ZO", "BRANCH"],
    ),
    (
        "BOI",
        "BOI-LOHARDAGA-CC",
        "BANK OF INDIA → LOHARDAGA CC → NBG-3 → ZO-5 → BOI-LOHARDAGA-CC",
        ["HO", "ZO", "NBG", "ZO", "BRANCH"],
    ),
]


def test_each_customer_shape_parses_to_its_own_depth() -> None:
    for prefix, device, path, expected_types in SHAPES:
        nodes = parse_device_path(prefix, device, f"dev-{prefix}", path)
        assert [n.node_type for n in nodes] == expected_types, prefix
        # Depth follows the path; it is never forced to a customer-independent number.
        assert [n.node_level for n in nodes] == list(range(1, len(expected_types) + 1)), prefix


def test_depths_genuinely_differ_across_customers() -> None:
    """Guards the fixtures themselves: if these ever collapse to one depth, the
    suite would still pass while testing nothing."""
    depths = {len(types) for _, _, _, types in SHAPES}
    assert depths == {2, 3, 4, 5}


def test_exactly_one_leaf_per_path_regardless_of_depth() -> None:
    for prefix, device, path, _ in SHAPES:
        nodes = parse_device_path(prefix, device, f"dev-{prefix}", path)
        leaves = [n for n in nodes if n.is_leaf]
        assert len(leaves) == 1, prefix
        assert leaves[0] is nodes[-1], prefix
        # The device id hangs off the leaf only — never off a container node.
        assert [n.tb_device_id for n in nodes] == [None] * (len(nodes) - 1) + [f"dev-{prefix}"]


def test_parent_chain_is_contiguous_at_any_depth() -> None:
    """Closure-table ancestry is built from parent_id, so the chain must not break
    on a 2-level customer any more than on a 5-level one."""
    for prefix, device, path, _ in SHAPES:
        nodes = parse_device_path(prefix, device, f"dev-{prefix}", path)
        assert nodes[0].parent_id is None, prefix
        for parent, child in pairwise(nodes):
            assert child.parent_id == parent.node_id, prefix


def test_container_node_ids_are_customer_scoped() -> None:
    """Two customers sharing a container name (both have an "NBG EAST") must not
    collapse onto one node_id — that would merge their fleets."""
    boi = parse_device_path("BOI", "BOI-DX5", "d1", "BANK OF INDIA → NBG EAST → BOI-DX5")
    sdf = parse_device_path(
        "SDF", "SDF-RASP5", "d2", "SDF Head Office → NBG EAST → ZO GUWAHATI → SDF-RASP5"
    )
    assert boi[1].node_id != sdf[1].node_id
    assert boi[1].node_id.startswith("BOI:") and sdf[1].node_id.startswith("SDF:")
