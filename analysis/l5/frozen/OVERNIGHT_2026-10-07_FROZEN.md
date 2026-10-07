# FROZEN — overnight results, 2026-10-07

**These results are frozen as evidence. Do not tune them, do not adjust
them to agree with a later result, and cite this file rather than
re-deriving them.**

Frozen at branch head `5ff055f6873224b485cbf36e22aaf322818525cf`.

A local git tag `overnight-2026-10-07-frozen` carries the same manifest,
but **it is not on the remote**: this container's git proxy returns
HTTP 403 for tag refs while branch pushes succeed. So this committed file
is the authoritative freeze, not the tag.

| lane | frozen result |
|---|---|
| **S3** | **INSUFFICIENT.** `markets_COMPLETE` 0 of 25 required. Two instrument defects: the complement side was never observed (`complement_never_observed: true`, all 5 markets, causing the gate breach) and COMPLETE is unreachable by construction (spans 537–787 s vs the 840 s criterion). S3's substantive question is UNANSWERED, not answered negatively. |
| **Lane 3** | L5 did **not** abandon the subsidy posture — **AUDITED AND UPHELD**, with corrections. The $34,822.79 is **CREDITED, $0.00 modeled** (a bare sum of the venue's own `usdcSize` over `type=MAKER_REBATE` rows, 82/82 carrying it, each with its own on-chain txHash). All three stream totals and the whole 37-day table reproduced **to the cent** by an independent loader. **But three claims were wrong:** `MAKER_REBATE` is NOT the larger stream (the totals span different windows — on `REWARD`'s own 6 paying days REWARD was **$8,229.66 vs $6,261.74**, 1.31× larger, and larger per day and per notional); the surviving stream is **DECAYING** ($1,043.62/day and 63.8 bps in early September → **$385.74/day and 42.7 bps in October**, a 63% fall in rebate/day against a 38% fall in notional); and the claim that a rebate *is* what makes above-$0.96 completion rational does not hold. See `analysis/l5/post_august/REBATE_SOURCE_AUDIT.md`. |
| **Lane 4** | `STAGE-6-S6P` fingerprint `32360e4c0441a8d7296794bd098e47ae962dabbc3fa406f98327dab40b2d96d8`, frozen, tested, **DISARMED**. The 1.00-share candidate is **NOT authorized and NOT to be tuned.** |
| **Lane 5** | **INCONCLUSIVE / DO-NOT-AUTHORIZE.** 95.58% of evaluable states UNKNOWABLE (book-only archive: no trade prints, no sequence numbers). Within the biased proxy subset, 67.72% matched against the 78.73% break-even. |
| **Collector** | 69,840 two-token book rows, 2,163 activity rows, 70 tokens / 35 markets, `single_token_markets: []`, `http_429: 0`, 197.1 RPM measured against a 240 cap. One closed `BOOK_ONLY_ACTIVITY_UNAVAILABLE` interval, 03:33:10Z → 04:17:51.286Z. |
| **A6** | **FILLED / settlement unresolved / NOT REALIZED.** |

## Boundaries that held

Zero live orders. $0.00 at risk. No owner value set. No order-capable image
deployed. XR2W, its spec and its fingerprint untouched. No evidence
deleted. No parameter tuned or fitted against a result.

## Known caveats carried forward, not resolved here

- `derived/activity_windows.json` **understates** the BOOK_ONLY outage by
  29 min 52 s: the OPEN edge could not be written before the code that
  writes it shipped at 04:03. Exclude the TRUE bound above.
- The **frozen-SPEC mutation bug**: the inherited `_apply` shared mutable
  state with `DIFF`, so a frozen `SPEC` could be mutated in place with the
  fingerprint assertion still passing. S6P deep-copies; prior specs are
  under audit in `analysis/l5/audit/FROZEN_SPEC_MUTATION_AUDIT.md`.
- `qty = 1.00` is an observability choice (`fitted: False`), not economics,
  not a venue constraint, and not L5's sizing. It sits below the rebate
  floor.
- The websocket endpoint is **unproven**; the hostname first written did not
  exist, and it cannot be settled from this container.
