"""Pure unit tests for regional scope filtering (no DB - operate on in-memory node lists)."""

from dataclasses import dataclass

from app.hierarchy.scope import RegionalScope, filter_branches


@dataclass(frozen=True)
class HierarchyNode:
    node_id: str
    customer_id: str
    parent_id: str | None
    node_type: str
    node_level: int
    display_name: str
    is_leaf: bool
    tb_device_id: str | None


def build_boi_hierarchy() -> tuple[list[HierarchyNode], list[HierarchyNode], dict[str, set[str]]]:
    """
    BOI hierarchy = Bank hierarchy:
    HO -> [ZO KOLKATA -> RO HOWRAH -> b1, b2 ; ZO DELHI -> b3]
    closure rows included. Branches b1..b3 are leaf nodes.
    Returns: (all_nodes, leaf_nodes, ancestor_paths)
    """
    nodes: list[HierarchyNode] = []
    # HO
    nodes.append(HierarchyNode("BOI_HO", "BOI", None, "HO", 1, "BOI Head Office", False, None))
    # ZO KOLKATA
    nodes.append(HierarchyNode("BOI:ZO KOLKATA", "BOI", "BOI_HO", "ZO", 2, "ZO Kolkata", False, None))
    # RO HOWRAH
    nodes.append(HierarchyNode("BOI:RO HOWRAH", "BOI", "BOI:ZO KOLKATA", "RO", 3, "RO Howrah", False, None))
    # Branch b1
    nodes.append(HierarchyNode("BOI-MALDATOWN", "BOI", "BOI:RO HOWRAH", "BRANCH", 4, "BOI-MALDATOWN", True, "dev-1"))
    # Branch b2
    nodes.append(HierarchyNode("BOI-PARKST", "BOI", "BOI:RO HOWRAH", "BRANCH", 4, "BOI-PARKST", True, "dev-2"))
    # ZO DELHI
    nodes.append(HierarchyNode("BOI:ZO DELHI", "BOI", "BOI_HO", "ZO", 2, "ZO Delhi", False, None))
    # Branch b3
    nodes.append(HierarchyNode("BOI-DWARKA", "BOI", "BOI:ZO DELHI", "BRANCH", 3, "BOI-DWARKA", True, "dev-3"))

    # Build ancestor_paths (node_id -> set of ancestor_ids)
    parent_map = {n.node_id: n.parent_id for n in nodes}
    ancestor_paths: dict[str, set[str]] = {}
    for node in nodes:
        ancestors: set[str] = set()
        current = node.node_id
        while current is not None:
            ancestors.add(current)
            current = parent_map.get(current)
        ancestor_paths[node.node_id] = ancestors

    leaf_nodes = [n for n in nodes if n.is_leaf]
    return nodes, leaf_nodes, ancestor_paths


class TestRegionalScope:
    """Tests for regional scope filtering."""

    def test_T1_explicit_region_zo_kolkata(self) -> None:
        """T1: token with explicit region 'ZO KOLKATA' -> exactly {b1,b2}."""
        all_nodes, leaf_nodes, ancestor_paths = build_boi_hierarchy()
        scope = RegionalScope(name="ZO KOLKATA", explicit=True)
        result = filter_branches(scope, leaf_nodes, all_nodes, ancestor_paths)
        branch_ids = {n.node_id for n in result}
        assert branch_ids == {"BOI-MALDATOWN", "BOI-PARKST"}

    def test_T2_explicit_region_ro_howrah(self) -> None:
        """T2: explicit region 'RO HOWRAH' -> {b1,b2} (same subtree via RO node)."""
        all_nodes, leaf_nodes, ancestor_paths = build_boi_hierarchy()
        scope = RegionalScope(name="RO HOWRAH", explicit=True)
        result = filter_branches(scope, leaf_nodes, all_nodes, ancestor_paths)
        branch_ids = {n.node_id for n in result}
        assert branch_ids == {"BOI-MALDATOWN", "BOI-PARKST"}

    def test_T3_explicit_region_nonexistent_fail_closed(self) -> None:
        """T3: explicit region 'ZO PATNA' (no such node) -> EMPTY list (fail closed)."""
        all_nodes, leaf_nodes, ancestor_paths = build_boi_hierarchy()
        scope = RegionalScope(name="ZO PATNA", explicit=True)
        result = filter_branches(scope, leaf_nodes, all_nodes, ancestor_paths)
        assert result == []

    def test_T4_guessed_region_unresolvable_unfiltered(self) -> None:
        """T4: guessed region (from email 'zo.patna@boi.in') that resolves to nothing -> UNFILTERED full list."""
        all_nodes, leaf_nodes, ancestor_paths = build_boi_hierarchy()
        scope = RegionalScope(name="ZO PATNA", explicit=False)
        result = filter_branches(scope, leaf_nodes, all_nodes, ancestor_paths)
        branch_ids = {n.node_id for n in result}
        assert branch_ids == {"BOI-MALDATOWN", "BOI-PARKST", "BOI-DWARKA"}

    def test_T5_no_region_info_unfiltered(self) -> None:
        """T5: token with no region info at all -> unfiltered."""
        all_nodes, leaf_nodes, ancestor_paths = build_boi_hierarchy()
        scope = RegionalScope(name=None, explicit=False)
        result = filter_branches(scope, leaf_nodes, all_nodes, ancestor_paths)
        branch_ids = {n.node_id for n in result}
        assert branch_ids == {"BOI-MALDATOWN", "BOI-PARKST", "BOI-DWARKA"}

    def test_T6_region_name_normalized_case_punctuation(self) -> None:
        """T6: region name matching is normalized (case/punct-insensitive: 'zo-kolkata' matches 'ZO Kolkata')."""
        all_nodes, leaf_nodes, ancestor_paths = build_boi_hierarchy()
        # Test various normalizations
        for name in ["zo-kolkata", "ZO KOLKATA", "zo kolkata", "Zo-Kolkata", "ZO-KOLKATA"]:
            scope = RegionalScope(name=name, explicit=True)
            result = filter_branches(scope, leaf_nodes, all_nodes, ancestor_paths)
            branch_ids = {n.node_id for n in result}
            assert branch_ids == {"BOI-MALDATOWN", "BOI-PARKST"}, f"Failed for name: {name}"

    def test_T7_non_leaf_nodes_never_returned(self) -> None:
        """T7: non-leaf nodes never appear in the returned branch list."""
        all_nodes, leaf_nodes, ancestor_paths = build_boi_hierarchy()
        scope = RegionalScope(name="ZO KOLKATA", explicit=True)
        result = filter_branches(scope, leaf_nodes, all_nodes, ancestor_paths)
        for node in result:
            assert node.is_leaf is True

    def test_explicit_region_zo_delhi(self) -> None:
        """Test explicit region ZO DELHI -> b3."""
        all_nodes, leaf_nodes, ancestor_paths = build_boi_hierarchy()
        scope = RegionalScope(name="ZO DELHI", explicit=True)
        result = filter_branches(scope, leaf_nodes, all_nodes, ancestor_paths)
        branch_ids = {n.node_id for n in result}
        assert branch_ids == {"BOI-DWARKA"}

    def test_explicit_region_ho_returns_all_branches(self) -> None:
        """Explicit region HO returns all leaf nodes (entire bank)."""
        all_nodes, leaf_nodes, ancestor_paths = build_boi_hierarchy()
        scope = RegionalScope(name="BOI HEAD OFFICE", explicit=True)
        result = filter_branches(scope, leaf_nodes, all_nodes, ancestor_paths)
        branch_ids = {n.node_id for n in result}
        assert branch_ids == {"BOI-MALDATOWN", "BOI-PARKST", "BOI-DWARKA"}

class TestScopedBranchesWiring:
    """Regression: the REAL extracted scope must reach branch_scope — an explicit
    region degraded to guessed would fail OPEN.

    branch_scope now sits behind app.auth.scope_resolver, which every caller
    (deps.scoped_branches, chat handlers, the branch-name gate) goes through, so the
    patch target moved there.
    """

    async def test_explicit_scope_survives_dependency_wiring(self, monkeypatch) -> None:
        from app import deps
        from app.auth import scope_resolver
        from app.auth.jwt import TenantContext
        from app.config import Settings
        from app.hierarchy.scope import ScopedBranches

        captured: dict[str, object] = {}

        async def fake_branch_scope(session, prefix, scope, redis):
            captured["scope"] = scope
            # Parallel by construction, as branch_scope emits them: leaf "b1" owns
            # device "d1", leaf "b2" owns "d2". ThingsBoard authorizes only d1, so
            # b2 must disappear from the names as well as d2 from the devices.
            return ScopedBranches(
                branch_node_ids=["b1", "b2"], tb_device_ids=["d1", "d2"]
            )

        async def fake_acl(settings, token, redis):
            return frozenset({"d1"})  # ThingsBoard authorizes only d1

        monkeypatch.setattr(scope_resolver, "branch_scope", fake_branch_scope)
        monkeypatch.setattr(scope_resolver, "authorized_device_ids", fake_acl)
        tenant = TenantContext(
            tenant_id="t",
            customer_id="c",
            subject="user@boi.in",
            claims={"firstName": "ZO PATNA"},
            prefix="BOI",
            user_token="tok",
        )
        result = await deps.scoped_branches(
            tenant,
            db=None,  # type: ignore[arg-type]
            redis=None,  # type: ignore[arg-type]
            settings=Settings(database_url="postgresql+asyncpg://unused/unused"),
        )
        scope = captured["scope"]
        assert scope.name == "ZO PATNA"
        assert scope.explicit is True, "explicit flag lost in wiring => fail-closed broken"
        # And the ThingsBoard ceiling is applied on the way out.
        assert result.tb_device_ids == ["d1"], "TB ACL not intersected in the deps path"
