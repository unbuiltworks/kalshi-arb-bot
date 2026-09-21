# EP013 — the CAP4-LIVE-2 BTC15m q=1 window: it never reached its own quoting window

**Authorization:** `OWNER-2026-09-21-GEMINI-BTC15M-Q1-CAP4-LIVE-2` — SPENT, DISARMED.
**Run:** [35653449506](https://github.com/unbuiltworks/kalshi-arb-bot/actions/runs/35653449506)
on `84cc6c0`, started 20:55:11Z, finished 21:09:32Z, conclusion success.
**Readiness:** [35651839289](https://github.com/unbuiltworks/kalshi-arb-bot/actions/runs/35651839289)
PASS, manifest `69e458ad…`.
**Cohort** `COHORT-2026-09-21-BTC15M-CAP4-2`, **0 of 12 consumed**; predecessor
`…CAP4-1` verified intact (5/12, sha `01c52cc8…` recorded == current).

```
spec 7fda21b1…  executable dd20a63b…  envelope be8b544b…  bundle aafbc36d…
spec_sha_check ok=True        mutation_boundary ENABLED      armed=True
symbol GEMI-BTC15M2609212115-UP   entered tte 874.0s   band [840, 880]
terms ACCEPTED (hasAcceptedLatest true, v1/v1, blocks_orders false)
preflight frozen_comparison AGREES, 0 discrepancies
```

## Outcome in one line

**Zero orders. Zero fills. Zero mutations. Zero cashflow of any kind.**
`place_acks_observed: []`, `mutations_observed: []`, `wire: []`,
`order_history_raw.verdict = NO_ORDERS_ON_RECORD (total_rows 0)`,
`orders_live_after_stop: 0`, budget 0/12.

## What ultimately stopped the window

A **single CROSSED book read at tte 332.4 s**, 32.4 seconds before the frozen
surface's quoting window would have opened.

```
cycle 54  21:09:19  tte 340.7  RUNNING      yes 0.01/0.06  no 0.94/0.99  pair 0.95
cycle 55  21:09:27  tte 332.4  WITHDRAWING  UNREADABLE — "CROSSED book: a bid at
                                            or above its own ask"
                                            refused yes bid 0.12 / ask 0.05
                                                    no  bid 0.95 / ask 0.88
```

The chain, verbatim from the frozen code:

1. `continuous_probe.state_from_venue` — `crossed(b)` is true, so it returns
   `state_id: "unreadable"`, `inventory_known: False`.
   *"An inconsistent or unreadable book yields inventory_known=False, which
   STOPS the probe rather than quoting into something we cannot read."*
2. `continuous_probe.plan` — `if state.get("inventory_known") is False:` returns
   `{"stop": "inventory could not be established from authoritative reads;
   unknown state holds, it never assumes flat", "actions": [], "holds": []}`.
3. `ws_continuous.Driver.decide` — `if phase == WINDOW_WITHDRAWN or turn.get("stop")`
   → `self.state = WITHDRAWING`, terminal-withdrawal plan.
4. `run_window` — `if self.state == WITHDRAWING: self.stop(STOPPED, "window
   withdrawn; one contract only, no continuation into the next")`.

**This is a legitimate frozen-controller terminal condition.** It is the
book-readability refusal, deliberately frozen, exercised on a real venue tick.
It is emphatically **not** the stale-durable-reservation stop the owner's
instruction told the run not to accept — the durable ledger was `CLEAR` with
`committed_usd "0"` on all 55 reconciles and never charged anything.

The refused numbers were internally consistent under the complement identity
(`no_bid = 1 − yes_ask`, `no_ask = 1 − yes_bid`, both deltas 0.00) — the venue
published one coherent but **crossed** contract, a transient. The cycle before
and the collapse trend (yes 0.01/0.06 for the preceding seven cycles) make a
momentary publication artifact the natural reading. The refusal is still
correct: an unreadable book is never quoted into.

## Why there were no orders in the preceding 54 cycles

Not a defect, and not a risk stop. The frozen surface carries
`TTE_policy.quote_between_tte_s = [0, 300]` (`contract_cohort.py:65`, an
OURS_ADAPTED parameter inherited from the 5-minute family). The window entered
at tte 874 and died at tte 332: **it was never once inside its own quoting
window.**

Replayed against this run's own recorded book (cycle 1's prices, q=1):

```
tte 861.0  ->  0 rungs
tte 340.7  ->  0 rungs
tte 299.0  ->  2 rungs
tte 250.0  ->  2 rungs
```

Every cycle 1–54 recorded `actions: []`, `holds: []`, `classification: []`,
`plan {keep 0, place 0, cancel 0, mutations 0}`, `budget_left 12`. The surface
withheld; no bound, no envelope and no risk gate was ever consulted on a rung.

CAP4-LIVE-1 survived to tte ≈ 291 and placed its first pair there. This window
lost the race by 32 seconds.

## The EP012 fix in production — what was and was not proven

**Proven:** the per-cycle authoritative reconcile ran **55/55 times**, every one
`verdict CLEAR`, `committed_usd "0"`, `released []`, `foreign []`,
`coherent True`, `resting 0`, `degraded False`. Pre-latch recovery also read
`committed_usd "0"`. No phantom charge was ever created — which is the class of
error EP012 fixed, and it did not recur across 55 reads.

**Not proven:** the *release* path. With zero orders there was no fill and no
cancel, so "a filled or cancelled order releases its reservation instead of
being charged alongside the same cash in filled spend" was **not exercised
live** in this window. It remains proven only by `tests/test_outstanding_release.py`
(10 tests) and by EP012's reconstruction. Stating otherwise would be a claim
the evidence does not carry.

The new `_refresh` UNREADABLE→`DO_NOT_TRADE` guard also never fired: the
outstanding read was readable on all 55 cycles.

## Economics — kept separate, and all of them zero

| account | value | basis |
|---|---|---|
| matched-pair economics | **n/a** | no pair; `matched_qty 0`, `effective_matched_pair_cost null` |
| directional residual P&L | **$0.00** | `yes_qty 0`, `no_qty 0`, `directional_residual 0` |
| spread capture | **$0.00** | no resting order ever existed |
| fees | **$0.00** | no fill |
| maker rebates | **$0.00** | no maker fill; nothing credited, nothing assumed |
| liquidity rewards | **$0.00** | none claimed, none credited |
| settlement cashflow | **$0.00** | nothing held into settlement |
| reconstructed P&L | **$0.00** | from the run's own fills: there are none |
| **cash-confirmed REALIZED** | **$0.00** | no attributable cash movement to confirm |

Contract settlement, public read at 21:19Z (evidence only — we held nothing):

```
GEMI-BTC15M2609212115-UP   status settled   marketState closed
resolutionSide  no         resolvedAt 2026-09-21T21:15:07.815Z
settlementValue 86594.68128205127
```

The UP contract resolved NO. Had the window quoted at tte ≤ 300 into a
0.01/0.06 YES market it would have been quoting the losing side cheaply; that
is a counterfactual, not a finding.

## Terminal state — verified

```
withdrawal  TERMINAL_WITHDRAWAL, rounds 1, cancelled [], failed [],
            remaining [], cleanup_consumed 0, verdict CLEANUP_CONFIRMED
state       STOPPED_FLAT_ORDERS       orders_live_after_stop 0
after       open_orders [], fills [], trades [], orders_seen [],
            order_history NO_ORDERS_ON_RECORD
position    /v1/prediction-markets/positions 200, 0 rows for this instrument,
            our_signed 0, venue_signed 0, verdict AGREE, tradeable true
```

Disarmed immediately as the grant required, regardless of outcome: all five
constants `None`/sentinel, `GEMINI_CANARY_AUTH` back to `NO-AUTHORIZATION-IN-FORCE`.
No second window.

## Two reporting defects found, neither affecting money or behaviour

1. **`stop_why` is generic.** Any `turn["stop"]` — a risk gate, a deadman, an
   unreadable book — lands in the same `WITHDRAWING` branch and is overwritten
   by `"window withdrawn; one contract only, no continuation into the next"`.
   The actual cause survives only in `decisions[-1].why`. A reader of the
   summary alone would conclude the contract rolled. It did not.
2. **`position_gate.n_rows_for_symbol` is mislabeled.** It is `len(pos["rows"])`
   — every row on the account (2) — not rows for this instrument (0). The
   reconciliation itself filters correctly and returned AGREE.

Both are evidence-layer naming. Neither is changed here: this window was
authorized with "no parameter changes", and these are proposals, not edits.

## The one thing this window says about the programme

The 840–880 entry band buys ~9 minutes of deliberate silence under a frozen
`[0, 300]` quoting window, and during that silence **a single transient venue
tick can terminate the whole window before the controller is ever allowed to
speak.** A crossed book is one cycle of unreadability; treating it as a
one-cycle HOLD rather than a terminal stop would have cost nothing and bought
the window its 300 seconds. That is a candidate change with a clear test, and
it needs its own authorization — it is not made here.

Nothing in this packet is REALIZED. Nothing was submitted, filled or settled on
our account in this window.
