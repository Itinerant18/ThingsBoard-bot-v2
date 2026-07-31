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
    # dexter_config is a device attribute carrying a modem_parameter block with
    # user_name, password, client_id and access_token alongside the harmless
    # brand/branch. Nothing reads it today, so asking for it was safe BY ACCIDENT —
    # it fell through to a device count. The panel-brand answer added below reads
    # that attribute, so from now on the raw object is one bug away from an answer.
    # Refuse the container by name; the brand question is answered without it.
    r"|\bdexter[_ ]?config\b|\bmodem[_ ]?param(?:eter)?s?\b|\bmodem (?:user|login)\b",
    re.IGNORECASE,
)

# Asking what a secret store HOLDS is asking for its contents, however politely it
# is phrased. This is refused rather than declined on purpose: "I do not hold that"
# is a statement about today's integrations and would quietly become a lookup the
# day S-Vault is wired up. "I will not" survives that change.
# Capacity, uptime and bandwidth are NOT contents — those stay ordinary questions
# that this build simply cannot answer yet.
_VAULT_CONTENTS_RE = re.compile(
    r"\b(?:stored|store|kept|holds?|contents?|files?|configs?|configurations?"
    r"|entries|records)\b[^?]{0,30}\b(?:s-?)?vault\b"
    r"|\b(?:s-?)?vault\b[^?]{0,30}\b(?:contents?|files?|configs?|configurations?"
    r"|stored|holds?|entries|records)\b",
    re.IGNORECASE,
)

REFUSAL = (
    "I will not disclose passwords, credentials, keys or tenant identifiers — not "
    "for your own account and not for any device. That is a fixed limit, not a "
    "gap in my data, so rephrasing will not change it. I can tell you whether a "
    "device is reachable, healthy or alarming without touching its credentials."
)


def asks_for_credentials(question: str) -> bool:
    return bool(_CREDENTIAL_RE.search(question) or _VAULT_CONTENTS_RE.search(question))


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
