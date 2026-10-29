# ACTIVATION — settlement-pending concurrency repair + cohort 3

OWNER 2026-09-17 P0 **"FIX SETTLED-PENDING FULLY-MATCHED CONCURRENCY DEADLOCK"**.

## The failure this repairs

Market A reached **YES 0.34 / NO 0.34, residual exactly 0**, every order
terminal, venue traded quantity **0.68 = ledger 0.68**. The **scheduler**
released it — correctly, there was no work left. The **mandate** did not,
because it saw fills. The campaign selected market B, admission refused it
`max_concurrent_markets_exhausted`, the session HALTed, and the campaign ended
**24 minutes before A actually resolved** — so the first matched pair this
engine ever held resolved with no auditor running.

Two counters held different opinions about one market, and the tie-break was a
stop.

## The invariant

Economic/**risk exposure** is now separate from settlement/**accounting
obligation**. A market may leave the **concurrency** set — and only that set —
while awaiting settlement, when **every** clause holds:

| clause | source |
|---|---|
| no live or unresolved order | mandate ledger, re-derived |
| reconciliation attestation exists | written by the session holding the transport |
| attestation asserts venue/ledger agreement | `venue_vs_ledger.agrees is True` |
| attestation asserts `may_assert_flat` | no open orders, no contradictions |
| attested quantities still current | re-derived from fill rows at read time |
| YES == NO > 0, residual exactly zero | mandate ledger, re-derived |

It **keeps**: the durable ledger, its place in `max_total_markets`, its risk-set
charge against the daily stop, its settlement watch, and its un-realized status.
It **releases**: the acquisition slot, and nothing else.

## No limit moved

| | |
|---|---|
| concurrency | **1** (unchanged) |
| total markets | **10** (unchanged) |
| per market | **$5.00** (unchanged) |
| campaign | **$15.00** (unchanged) |
| residual cap | **3.00** (unchanged) |
| daily stop | **−$6.00** (unchanged) |
| sizing / k | **$0.10 / $0.125 / 0.00619** (unchanged) |

A test pins that a pending-settlement market still exhausts
`max_total_markets` — releasing the slot must never become unlimited market
scanning.

## Fail-closed, proven by mutation

Residual ≠ 0 · unmatched inventory · unresolved order · missing terminal
evidence · venue/ledger disagreement · unreadable ledger · stale attestation ·
unknown exposure — **each keeps the slot and still refuses B**. Disabling the
release predicate fails 6 of the 26 tests including the reproduction, so they
are not passing vacuously.

The attestation is **evidence, never a permit**: a fill arriving after it
invalidates it rather than inheriting its permission — including when the later
fills are themselves matched and residual returns to zero.

## Position carried into this cohort

`aec-itfme-artkuk-egople-2026-09-17` — matched **0.34 / 0.34**, residual
**0.00**, cost basis 0.92, `venue_vs_ledger` agrees, `may_assert_flat` true,
`still_open []`. Venue-resolved (public record) but **never audited**:
`PENDING_SETTLEMENT`, `realized_confirmed: false`, **no realized figure**.
The redeployed probe's settlement auditor is what records it. Nothing about it
is rewritten and nothing is called realized.

## Not changed, deliberately

- **The stochastic selector.** Untouched.
- **The matched-pair ceiling gate.** Investigated separately per instruction;
  no semantic change. See `MATCHED_PAIR_CEILING_FINDING_2026-09-17.md`.
- **The residual-cap overshoot.** Still filed, still unrepaired.

## Verification

lineage `ACCEPTANCE: True` after re-pin · targeted 138 + 26 · full suite
**5094 passed, 0 failed** · runtime `action_selection == stochastic` re-verified
after deploy.
