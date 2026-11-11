# GEMINI CONTINUOUS WINDOW #1 — FAIL CLOSED, NOTHING SUBMITTED

**Authorization:** OWNER, 2026-09-18, ONE live BTC5m continuous-inventory window
**Named artifact:** `executable_sha 55467cca…9e73`, `envelope_sha b3bff654…aa17`
**Decision:** DID NOT TRADE. No venue mutation. No latch consumed. Nothing armed.
**Recorded:** 2026-09-18T13:0xZ

The authorization's own instruction: *"If any material assumption differs from
the reviewed READY artifact, FAIL CLOSED and submit nothing."* One does.

---

## The venue side was entirely green

Read-only preflight, run
[35347362693](https://github.com/unbuiltworks/kalshi-arb-bot/actions/runs/35347362693)
on `3e923c15`, success, 12:57Z. Evidence commit `c1dbf5b7`.

| gate | measured |
|---|---|
| instrument metadata readable | `true` |
| contract tradeable | `GEMI-BTC05M2609181300-UP`, `active` / `open` |
| quantityMinimum / Increment | `0.01` / `0.01` — matches the frozen envelope |
| priceMinimum / Increment | `0.01` / `0.01` — matches |
| terms accepted | `true`, v1 of 1, `blocks_orders: false` |
| request transport | accepted |
| order payload valid | `true` |
| terminal resolution readable | `true` |
| VENUE_WRITE_PATH | `PROVEN` |
| account reads | `/v1/balances` 200, `/v1/transfers` 200 (9 rows), `/v1/notionalvolume` 200 |
| starting exposure | `n_legs: 0`, `position_at_expiry: 0` — **flat** |

**The only blocking gate was `armed`.** Steps 3, 4 and 8 of the authorization's
preamble all passed. Steps 1, 2, 5–7 and 9 were never reached.

---

## Why it could not be armed: the authorized artifact is UNARMABLE

Step 1 of the authorization is *"create the new explicit owner authorization
bound to the exact executable SHA and envelope SHA above."* Doing that is what
exposed the defect.

`executable_sha()` hashed the **whole file, including the three authorization
constants**. So:

```
reviewed file (constants = None)          -> 55467cca…9e73   (what was authorized)
same file with the constants bound        -> 7052c0b1…bdcb
the driver's own rule: AUTHORIZED_SHA == executable_sha()
                       55467cca…        != 7052c0b1…        -> REFUSED
```

This is not "a constant that still needs setting". Satisfying the rule requires
a 64-hex string **X** such that embedding X in the file yields `sha256 == X` —
a **self-referential preimage of SHA-256**. No commit can produce it.

I reported that artifact as "unarmed by construction". It was **unarmable** by
construction — a stronger property than I intended, and a defect, not a
feature. It went unnoticed because every test asserted the driver *refuses*,
and refusing is exactly what a broken arm check does.

---

## The fix, and why it is not a loophole

The hash now excludes the authorization block and **nothing else** — the
standard detached-signature shape, where the signature field is excluded from
the payload it signs. Measured, not asserted:

```
installing a real authorization  -> executable_sha() UNCHANGED   (no-op)
one-character logic change
  (budget_left() < 1  ->  < 0)   -> executable_sha() CHANGED     (invalidates)
```

Exactly three lines may normalize. If the pattern matches more or fewer, the
hash is **refused** rather than computed, because a normalization that silently
covered more than intended would be a way to hide code from review. Both
directions are pinned by test.

```
NEW executable_sha   2793c4cda4577debb00b3c3a04120138d8f78c8b38f057497105024a4cdc8d5d
    envelope_sha     b3bff654b55a6792a72b5ea7ac39157a3a1d4183b15cc4ada4bdc961bd77aa17   (unchanged)
```

The envelope hash is unchanged because **no bound moved**. Action size 0.01,
12 mutation slots, $0.15 exposure, 0.04 residual, 0.04 imbalance, 0.10 YES,
0.10 NO, MOC maker-only, one BTC5m contract — all exactly as reviewed.

---

## Safety state

- **No venue mutation.** Nothing submitted, amended or cancelled.
- **No latch consumed.** The only latch on disk remains
  `latch_OWNER-2026-09-17-GEMINI-CANARY-1.json`, state `REJECTED` — canary #1,
  a different authorization.
- **Nothing armed.** `REQUIRED_AUTH_ID`, `AUTHORIZED_SHA` and
  `AUTHORIZED_ENVELOPE` are all still `None`; `authorized()` is `False`.
- **This authorization is unspent**, and names a SHA that no longer exists in a
  runnable form.

Because there was no first mutation, there is no ambiguity to resolve.

---

## What a re-authorization needs

Only one field changes. The envelope, the controller, the transport and every
bound are identical to what was already reviewed:

```
executable_sha   2793c4cda4577debb00b3c3a04120138d8f78c8b38f057497105024a4cdc8d5d
envelope_sha     b3bff654b55a6792a72b5ea7ac39157a3a1d4183b15cc4ada4bdc961bd77aa17
```

The diff to review since the authorized artifact is small and bounded: the
`executable_body()` / `executable_sha()` pair, plus six tests pinning that
installing an authorization is hash-neutral while a logic change is not.

**6134 passed, 27 skipped, 1 xfailed, 0 failed.**
