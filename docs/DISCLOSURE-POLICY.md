# Disclosure policy

Set by the product owner, 2026-07-29. Enforced by `app/query/disclosure.py` and
`tests/test_disclosure.py`. A change that makes those tests fail is a change that
discloses something this policy forbids.

## The rule

**Never show or reveal any password, credential, key or tenant identifier, and
never expose one tenant's people to another.**

## What that means in practice

| Asked for | Response | Why |
| --- | --- | --- |
| Passwords, credentials, API keys, tokens, private keys, certificates, SSH keys, login details | **Refuse** | A secret. Never disclosed, for any caller, at any authority level. |
| What S-Vault *holds* — contents, files, configurations, entries | **Refuse** | Asking what a secret store contains is asking for its contents. Refused rather than declined so the answer does not change the day S-Vault is integrated. |
| S-Vault capacity, uptime, disk usage, bandwidth, instance online/offline | **Decline honestly** | Not secrets. Ordinary operational metrics this build has no integration for. Becomes answerable if that integration is ever built. |
| An audit actor from another tenant | **Show the action, mask the person** | The work happened to the caller's device and they are entitled to know. The other tenant's staff directory is not theirs to read. |
| The caller's own device and alarm ids | **Show** | Their own devices, already scoped, and the widget needs them to link. |
| Tenant id, other customers' ids | **Never emitted** | Verified absent from all 769 live answers as of 2026-07-29. |

## Why refuse and decline are kept apart

"I do not hold that" is a statement about today's integrations. "I will not tell
you that" is a statement about policy. Collapsing them would let a future change
quietly turn a refusal into a lookup — wire up S-Vault, and every "what is stored
in the vault" question silently starts answering.

The refusal wording also states the limit is fixed rather than a data gap, so it
does not invite the operator to rephrase and try again.

## Ordering matters

The credential check runs **before every other routing rule, including
conversational-fragment inheritance**. Nothing can capture a credential request
first, and no previous turn can supply an intent that turns it into a lookup.
`tests/test_disclosure.py::test_a_credential_request_cannot_inherit_a_previous_intent`
pins this.

## Masking

An outside actor is reduced to their organisation, not partially obscured:

    romen.halder@seple.in  ->  a user outside your organisation (seple.in)

The local part is dropped entirely. A partial mask of a small integrator team is
barely a mask. The domain stays because accountability needs a party — a bank
operator seeing telemetry deleted on their device should know which organisation
did it, even if not which individual. Tenant admins see the full name.

## Open decisions, NOT settled by this policy

1. **Should S-Vault be integrated at all?** If yes, the contents rule above must
   be enforced at the integration boundary too, not only in the router — a future
   handler with vault access must never enumerate secrets regardless of phrasing.
2. **Vendor deletions on customer devices.** Masking hides *who*; it does not
   address that an outsider deleted a bank's telemetry three times in one week and
   the bank cannot act on it. Visibility, alerting, or prevention is a separate
   decision.
3. **Branch master data** (address, manager, phone, pincode) is declined because
   ThingsBoard does not hold it. If a source is added, revisit whether contact
   details for staff should be answerable at all.
