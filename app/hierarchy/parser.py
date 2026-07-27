import re
from dataclasses import dataclass

NODE_TYPES = {"HO", "FGMO", "LHO", "ZO", "RO", "RBO", "CO", "NBG"}


def normalize(value: str) -> str:
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", value.upper()).split())


def split_full_path(full_path: str | None, prefix: str, branch_name: str) -> list[str]:
    # Production ThingsBoard full_path uses the unicode arrow "→"; "->" and "/" are fallbacks.
    parts = [part.strip() for part in re.split(r"(?:→|->|/)", full_path or "") if part.strip()]
    if not parts:
        return [f"{prefix} Head Office", branch_name]
    first = normalize(parts[0])
    if not any(marker in first for marker in ("BANK", "HO", "HEAD OFFICE", normalize(prefix))):
        parts.insert(0, f"{prefix} Head Office")
    return parts


def node_type(segment: str, is_leaf: bool) -> str:
    if is_leaf:
        return "BRANCH"
    normalized = normalize(segment)
    lead = normalized.split(maxsplit=1)[0] if normalized else ""
    if lead in NODE_TYPES:
        return lead
    return "NBG" if "NBG" in normalized else "ZO"


@dataclass(frozen=True)
class ParsedNode:
    node_id: str
    customer_id: str
    parent_id: str | None
    node_type: str
    node_level: int
    display_name: str
    is_leaf: bool
    tb_device_id: str | None


def parse_device_path(
    prefix: str, device_name: str, tb_device_id: str, full_path: str | None
) -> list[ParsedNode]:
    segments = split_full_path(full_path, prefix, device_name)
    # The ThingsBoard path may contain a stale branch label; device name is authoritative.
    segments[-1] = device_name
    nodes: list[ParsedNode] = []
    parent_id: str | None = None
    for index, segment in enumerate(segments):
        leaf = index == len(segments) - 1
        # Leaf wins over root: a single-segment path is the branch itself, never the HO node.
        if leaf:
            kind = "BRANCH"
            identifier = device_name
        elif index == 0:
            kind = "HO"  # root is always the head office, whatever the segment says
            identifier = f"{prefix}_HO"
        else:
            kind = node_type(segment, leaf)
            identifier = f"{prefix}:{normalize(segment)}"
        nodes.append(
            ParsedNode(
                identifier,
                prefix,
                parent_id,
                kind,
                index + 1,
                segment,
                leaf,
                tb_device_id if leaf else None,
            )
        )
        parent_id = identifier
    return nodes
