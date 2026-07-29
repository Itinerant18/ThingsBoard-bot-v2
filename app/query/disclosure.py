"""What this assistant must never reveal, in one place.

Policy set by the product owner on 2026-07-29: never show or reveal any password,
credential or tenant identifier, and never expose one tenant's people to another.

Two distinct rules live here because they fail differently:

  * A CREDENTIAL request must be REFUSED, not deflected. Measured on the live
    build, "What device passwords are stored in S-Vault?" was answered with
    "You have 98 device(s) in your authorized scope: 14 online, 84 offline." That
    is not a refusal — it is a non-sequitur that leaves the operator unsure whether
    the bot would have answered had it known. A refusal has to be unmistakable.

  * An ACTOR outside the caller's customer must be masked. The audit trail
    correctly shows work done ON the caller's devices, but the person who did it
    may belong to the integrator or another tenant. "romen.halder@seple.in
    TIMESERIES_DELETED on BOI-LILUAH" tells a bank operator that a named outsider
    deleted their data — the ACTION is theirs to see, the outsider's identity is
    not. Masking keeps the accountability without the directory listing.

Kept separate from `unavailable_telemetry`: "I do not hold that" and "I will not
tell you that" are different statements, and collapsing them would let a future
change quietly turn a refusal into a lookup.
"""

import re

# Anything that is or leads to a secret. Deliberately broad — a false positive
# costs one refused question, a false negative discloses a credential.
_CREDENTIAL_RE = re.compile(
    r"\bpasswords?\b|\bpasswd\b|\bpassphrase\b|\bcredentials?\b|\bsecrets?\b"
    r"|\bapi[- ]?keys?\b|\baccess tokens?\b|\bbearer\b|\bprivate keys?\b"
    r"|\bcertificates?\b|\bpem\b|\bssh keys?\b|\blogin details?\b"
    r"|\bs-?vault (?:contents?|configs?|configurations?)\b",
    re.IGNORECASE,
)

REFUSAL = (
    "I will not disclose passwords, credentials, keys or tenant identifiers — not "
    "for your own account and not for any device. That is a fixed limit, not a "
    "gap in my data, so rephrasing will not change it. I can tell you whether a "
    "device is reachable, healthy or alarming without touching its credentials."
)


def asks_for_credentials(question: str) -> bool:
    return bool(_CREDENTIAL_RE.search(question))


def mask_actor(display: str, *, in_scope: bool) -> str:
    """An audit actor's name, masked when they are outside the caller's customer.

    The action stays visible — it happened to the caller's device and they are
    entitled to know — but the outsider's address is replaced by their role. Local
    part is dropped entirely rather than partially obscured, since a partial mask
    of a small integrator team is barely a mask at all.
    """
    if in_scope or "@" not in display:
        return display
    domain = display.rsplit("@", 1)[1].strip().lower()
    return f"a user outside your organisation ({domain})"
