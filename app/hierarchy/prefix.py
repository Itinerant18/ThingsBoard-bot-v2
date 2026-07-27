import re


def derive_prefix(device_name: str, full_path: str | None, known_prefixes: set[str]) -> str | None:
    known = {item.upper() for item in known_prefixes}
    candidate = device_name.split("-", 1)[0].upper()
    if candidate in known:
        return candidate
    root = re.split(r"(?:→|->|/)", full_path or "", maxsplit=1)[0].strip()
    first = root.split(maxsplit=1)[0].upper() if root else ""
    return first if first in known else None
