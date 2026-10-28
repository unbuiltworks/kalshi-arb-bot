# Campaign 7 — proposed scope and readiness evidence

**Not authorized. Nothing is armed. No automatic arming is requested or
built.** This asks for a decision; it does not act on one.

## Purpose

Campaign 6 was the first positive realized-net campaign: +$1.5908, six
completed pairs, zero campaign residual. One campaign is not repeatability.
Campaign 7 is a **replication under identical limits** — same shape, same
capital, same behavior — to establish whether that result holds, and to
measure opening quality with the instrumentation Campaign 6 made necessary.

Per the owner's direction: repeatability and opening quality, **not** higher
capital. No limit increase is requested. Capital never bound in Campaign 6 —
peak cash headroom was $10.14 of $12.

## Exact scope

| | |
|---|---|
| Series | PMUS BTC 15-minute Up/Down only |
| Windows | **4** consecutive eligible BTC15m windows |
| Max total exposure | **$20** (unchanged) |
| Cash envelope | **$12** (unchanged) |
| Daily realized-loss halt | **$5** (unchanged) |
| Cumulative loss halt | **$20** (unchanged) |
| Worst-case residual deficit | **$3** (unchanged) |
| Simultaneous resting orders | **1** (unchanged) |
| Execution mode | **maker-only**, post-only both legs |
| Allocations / rounds | 4 allocations, 8 rounds per window |
| Ticket budgets | OPEN 40 / COMPLETE 40 / REPLACE 64, backstop 40 |
| Behavioral flags | `L5_COMPLETION_REQUOTE=1` — **preserved, unchanged** |
| New behavioral changes | **none** |
| Arming | **manual, per campaign claim. No automatic arming.** |
| Renewal | none; no automatic repeat after expiry |

Four windows keeps this directly comparable to Campaign 6. A same-shaped
replication is the experiment repeatability actually needs.

## Code delta since d096ef7d

Campaign 6's behavior is preserved. The delta is **accounting and reporting
only** — no execution path, no gate, no pricing, no cancellation logic.

| File | Change | Reaches execution? |
|---|---|---|
| `src/l5_window_accounting.py` | `merge_fill_sources`, `pair_economics`, wired into `window_record` / `campaign_rolling` | No — imports nothing that can reach a venue; exposes no submit, cancel or refuse |
| `src/l5_pmus_live_runner.py` | `_window_fills` helper; passes `fills=` to `window_record` | No — inside `emit_window_accounting`, whose failures are already swallowed and counted |
| `tests/` | 20 new tests | No |
| `analysis/l5/campaign6/`, `analysis/l5/campaign7/` | tape, scripts, docs | No |

`l5_two_sided_probe.py`, `l5_live_execution.py`, `l5_live_validation_gates.py`,
`l5_intent_gate.py` and the cancel mechanism are **untouched**.

**A new SHA is unavoidable** if the counter fix is to be in force, since the
fix must run in the process that produces the counters. The request is to
preserve Campaign 6's *behavior* — which this does — not its hash. If the
owner prefers to re-run on `d096ef7d` exactly, that is viable, with the
consequence that Campaign 7's fill counts will again undercount any raced
fill and must be reconciled from the tape by hand.

## Readiness evidence

### 1. The 12th venue-confirmed fill is in operational counters — **done**

Counters move from 11 fills / 21.67 contracts to **12 / 23.82**, matching the
venue. Root cause: a fill landing between a cancel being sent and the venue
answering produces no positive lifecycle delta. Fixed by merging the venue's
fill-record stream, deduplicated on cumulative quantity.

Evidence: `analysis/l5/campaign6/RECONCILIATION.md` §Verification 1;
`tests/test_l5_race_fill_and_defensive.py` (14 tests),
`tests/test_l5_race_fill_wiring.py` (6 tests), including the real 1630z shape
end to end, partial top-up, idempotence, and both producer key shapes.

### 2. Campaign 6 remains independently reconcilable — **done**

`analysis/l5/campaign6/` holds the raw captures and derived tapes.
`reconcile.py` re-derives six pairs, pair costs 0.99 / 0.87 / 1.05 / 0.55 /
0.85 / 0.90, zero residual in every trading window, net +$1.5908, and exits
0 only if that ties to the cash series. No Railway access required, which
matters because the deploy log rolls off.

Also recorded: 18 `l5f-`-prefixed presubmits exist for a **fifth** window
(`1645z`) that was never armed and never transmitted — no ACK, no lifecycle,
no terminal, no fill, no cash movement. The claim budget refused it
(`windows_spent` 0→1→2→3 of 4, no fifth arming). A presubmit record is not
evidence of a live order, and the tape now marks `transmitted` explicitly.

### 3. Restart timing will not sacrifice an opening window — **procedure fixed**

Measured: arming took **174.1 s** in the window the deploy restarted into,
and **3.3 s / 0.8 s / 2.9 s** in the three windows the process spanned.

Cause: the venue's REST book route 404s persistently for BTC15m (3 of 3 in
both `1545z` and `1600z`); the WS fallback built for that requires
`subscribed_before_open: true`; a process that boots 42 s inside a window
cannot satisfy it for that window. The guard is correct and is not being
relaxed — a late-subscribed socket is a partial view.

**Procedure.** Deploy first. Let the process run across at least one full
window boundary. Before arming, confirm from the live preflight record:

- `book_readable_via_authenticated_ws` → `subscribed_before_open: true`
- verdict `PASSED` on **attempt 1**
- `t_in_window_s < 10`
- `no_unexpected_open_orders: 0`, `executor_starts_disarmed: true`

Arm only then, and only the first authorized window. If any check fails, do
not arm and report the blocker.

### 4. Opening adverse-selection exposure is measured — **done**

`adverse_selection.py` over all six pairs: 2 adverse, 4 favourable, mean
drift **−0.0800**, one pair realised above $1.00.

The $1.05 pair was planned at 0.41 + 0.55 = **0.9600**, exactly at the
opening ceiling with zero headroom, then its DOWN leg was taken while UP ran
0.56 → 0.66 in ~35 s. No gate failed: the ceiling is checked when the plan is
built and has no say over the complement's price when a passive leg is hit.

Two findings, both about openings:

- the only pair to breach $1.00 was the only one planned at the ceiling;
- short completion waits cluster with adverse drift (28 s, 41 s adverse
  against 74–368 s for three of four favourable).

**No opening-rule change is proposed yet.** The candidate — requiring
headroom below 0.96 rather than allowing an opening exactly at it — would
change entry behavior, and one campaign of six pairs is too thin to justify
it. Campaign 7 should measure it again under identical limits; if openings
planned at or within a tick of the ceiling keep producing the breaches, that
is the evidence to act on. Running `adverse_selection.py` on Campaign 7's
tape costs nothing and needs no new instrumentation.

## What is deliberately not changed

- **No hard $1.00 completion ceiling.** Per the owner's ruling,
  risk-reducing completions above $1 remain available where they reduce
  expected loss. The 1600z case took a certain −$0.0970 instead of carrying
  1.94 DOWN against a −$0.7907 downside. They are now reported separately as
  `defensive_loss_usd`, never folded into pair edge.
- **No capital increase.** Not requested; capital did not bind.
- **No opening-strategy change.** See above.
- **No new services, flags or infrastructure.**

## On attribution — stated plainly

**The repricing policy did not cause Campaign 6's profit.** What it
demonstrated is execution behavior: six firings, every one cancelling the
exact order id, refusing to trust the cancel call, re-reading, and only then
replacing; one firing found the order already filled and booked it with its
cost basis rather than losing it or double-ordering. Three replacement chains
ended in a fill.

But every replacement that filled did so *cheaper* than the order it
replaced, while the policy's own target was *dearer* — so the realised gain
came from the market moving between cancel and replace, not from the move the
policy intended. The campaign's edge sits in the favourable drift tail, which
is a property of these windows. Six firings measure mechanism, not causal
profitability, and the counterfactual (whether those orders would have filled
anyway) is not observable.

## Reporting on completion

Per window: submitted, filled, canceled, pairs with pair costs, **pair edge
and defensive loss separately**, residual, confirmed settled net, repricing
attempts against replacement fills, and safety refusals. Plus ending cash,
open orders, outstanding obligations, durable ledger, and a tape under
`analysis/l5/campaign7/` that reconciles without Railway.

## Requested decision

Authorize four BTC15m windows at the limits above, on the accounting-only
delta, with manual arming and no renewal — or direct a different scope.
