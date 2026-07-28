import re


def derive_prefix(device_name: str, full_path: str | None, known_prefixes: set[str]) -> str | None:
    """Which configured customer a device belongs to, or None if it cannot be told.

    None is not harmless: a device with no prefix joins no hierarchy and no scope, so
    it becomes invisible to every question. Measured against the live fleet, splitting
    the device name on "-" alone lost all nine SBI devices, which are named with a
    SPACE ("SBI PARIHAR", "SBI LHO PATNA") rather than a hyphen like "BOI-MALDATOWN".
    Both separators are in production use, so both are accepted.
    """
    known = {item.upper() for item in known_prefixes}
    candidate = re.split(r"[-\s]", device_name.strip(), maxsplit=1)[0].upper()
    if candidate in known:
        return candidate
    root = re.split(r"(?:→|->|/)", full_path or "", maxsplit=1)[0].strip().upper()
    if not root:
        return None
    # Match any WORD of the path root, not only its first. A root reached here is
    # usually malformed (the bank segment missing), and taking word one turned
    # "STATE BANK OF INDIA" into "STATE". Ambiguity resolves to None, never a guess.
    words = root.split()
    matches = {prefix for prefix in known if prefix in words}
    if len(matches) == 1:
        return matches.pop()
    return None
