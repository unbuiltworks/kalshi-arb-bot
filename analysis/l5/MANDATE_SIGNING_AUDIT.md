# Audit: signing, verification, rotation, expiry, rollback, fail-closed

Requirement 5 of the Phase 2/3 brief. Each claim below was tested by running
the real code, not by reading it.

## Canonical serialization — sound

`canonical_bytes` = `json.dumps(payload, sort_keys=True,
separators=(",", ":"), ensure_ascii=True)`.

| property | result |
|---|---|
| top-level key reordering | identical bytes |
| nested reordering inside `capital` / `promotion` | identical bytes (`sort_keys` is recursive) |
| pretty-printed / rewhitespaced transport | still verifies |
| unicode | `ensure_ascii=True`, so escaping is deterministic |
| floats | unreachable: money must be a decimal **string**, counts must be `int`, and unknown fields refuse |

**Duplicate-key injection is defeated**, and by design rather than luck. The
signature covers a re-serialization of the **parsed** payload, not the raw
bytes. `json.loads` keeps the last of duplicated keys, so injecting
`"seq":999` after a signed `"seq":3` changes the parsed payload and the
signature no longer matches — observed: `MANDATE_SIGNATURE_INVALID`. Signing
raw transport bytes would have been the fragile choice here.

## Key rotation — correct, with one noted gap

- `MANDATE_PUBKEYS` is keyed by `key_id`; retiring a key means **deleting the
  entry**, after which its mandates refuse (`MANDATE_KEY_UNKNOWN`, or
  `MANDATE_NO_TRUSTED_KEY` if the registry empties). Observed.
- Rotation must **remove** the old entry, not merely add a new one: a
  compromised key left in the registry can still sign.
- **Gap:** a key has no independent expiry. It is valid until a code change
  removes it. Mandates expire; keys do not. Acceptable while the registry is
  a reviewed code constant, but it means key retirement requires a deploy.
- Reusing a `key_id` for a different key is warned against in code. The
  failure direction is **conservative**: the old high floor would reject
  legitimate new low-`seq` mandates rather than accept forged ones.

## Expiry — bounded, with a stated trust assumption

`not_before <= now < not_after`, both must be tz-aware, duration ≤
`MAX_MANDATE_S` (30 days). Inverted windows refuse. Naive timestamps refuse
rather than being assumed UTC.

**Trust assumption, stated rather than hidden:** `now` comes from the caller,
which in production is the container clock. A wrong or manipulated clock can
move effective validity. Nothing here detects that; the watchdog documents'
freshness bounds limit the blast radius but do not remove it.

## Rollback — moved off the executor, semantics inverted deliberately

The high-water mark is now published by the **watchdog** and read GET-only
(`HttpSeqFloorStore`). The executor has no writer: `WatchdogSeqWriter` is the
watchdog's, and the HTTP clients expose no `put`/`observe`/`record`.

Because the watchdog records the highest seq it has **observed**, the live
mandate's own seq is already in the floor, so the executor requires
`seq >= floor`, not `>`. Requiring strict inequality would reject the current
mandate every time the watchdog saw it first — which is always.

**Operational consequence worth knowing:** raising the floor is irreversible
by design, so the owner must always move `seq` forward. A high-`seq` mandate
issued and then withdrawn permanently locks out every lower `seq`; recovery
is a new mandate with a higher number, not a rollback.

## Fail-closed — verified at every input

`None` floor → `MANDATE_SEQ_FLOOR_UNKNOWN`. `None` revocation →
`MANDATE_REVOCATION_UNKNOWN`. Absent, stale, future-dated, unparseable,
non-object, missing-`asof_utc`, non-integer-floor, non-string-revocation and
transport-failure all raise `MandateUnavailable` from the stores. A missing
`cryptography` backend makes `_verify_ed25519` return False, so an
environment without the library refuses everything rather than skipping the
check.

Unlike the halt latch, **absence of a watchdog document refuses** rather than
reading as a positive "nothing to report": a missing floor or revocation list
means the watchdog is not publishing, and an executor that cannot find out
whether its mandate was superseded or revoked is not authorized.

`verify` is now **pure** — no file, socket, or store access, asserted through
its AST — so the reporting path cannot consume or advance the owner's
mandate. That was a latent risk in Phase 1 and is now structural.

## One bug found and fixed during Phase 1

`verify` built its four checks as a tuple, evaluating **all four eagerly**, so
`_check_numbers` indexed a block `_check_schema` had already rejected and
raised `KeyError` instead of returning a named refusal. A verifier that can
raise is one whose answer depends on the caller's exception handling, which is
not fail-closed. Now evaluated lazily.
