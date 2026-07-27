"""Deterministic normalization of ThingsBoard raw device data.

Port of the Java normalization layer; contract: docs/thingsboard-key-map.md.
"""

from app.normalization.snapshot import BranchSnapshot, build_snapshot
from app.normalization.values import NormalizedState

__all__ = ["BranchSnapshot", "NormalizedState", "build_snapshot"]
