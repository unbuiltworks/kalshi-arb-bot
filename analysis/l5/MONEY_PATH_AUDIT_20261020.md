# End-to-end money-path audit, BTC15m two-sided accumulation

2026-10-20. Running commit at audit time `abb2c3b0` (shadow-locked, kept
running throughout; nothing was deployed to perform this audit).

Traced locally, stage by stage, against the deployed configuration. The
deployed configuration is not assumed -- every value below is either read
back out of a production record or derived from observed production
behaviour, because reading the service's variables would mean reading the
owner's arm token.

| value | how it was established |
|---|---|
| `L5_TS_DEPTH_TICKS=2` | the plan in the 0330z record prices UP at `touch - 0.02` |
| `L5_TS_CANCEL_MARGIN_S=300` | `TOO_LATE_TO_WITHDRAW` appears at all, which is impossible at the 180s default because `PAST_ENTRY_CUTOFF` fires first |
| `L5_TS_COMPLETION_CROSS` unset | `L5_TS_COMPLETION {"authorized_to_cross": false}` |
| `max_live_orders=1`, `$5/$5`, `$4.65`, `NO_RECYCLING` | the `L5_LIVE_GATES` and heartbeat records |

## The chain, and what each stage actually does

1. **Authenticated book.** `AuthBook.snapshot` returns None unless connected
   AND two-sided. The executor holds the MIRROR (`ts_book`) and snapshots per
   decision, so no restart or reconnect can reuse an old frame. Disconnect,
   stale and one-sided all fail closed. **OK** (fixed `bd8ef135`).
2. **Market/window selection.** The runner rolls to the current slug every
   15 minutes; `L5_TS_MARKET=cpc-btc-updown-15m-*` matches the family, so the
   engine no longer goes inert on the roll. `slug_start` gives the window
   start; `cancel_cutoff_from_start` exists so no caller can pass a start
   where an end is wanted. **OK**
3. **Eligibility.** `may_enter` / `may_complete`, chosen by what the action
   IS rather than by a clock. See the timing table below. **OK, but the
   configured completion cutoff is dead** -- see C1.
4. **Pricing and sizing.** `build_orders` rests `depth_ticks` behind each
   touch, sizes from the cash envelope, and reports `fee_floor`. **Priced
   correctly; not capped** -- see B3.
5. **Authorization.** Shadow lock -> `armed()` dual key -> HALT latch ->
   per-(market, round, leg) claim, fsynced before the wire. The claim is
   taken only when the transport is armed, so shadow can never spend it.
   **OK**
6. **Risk gates.** `probe_precheck` gates the WHOLE pair as if both legs
   were already resting, so leg one cannot spend leg two's headroom; then
   `limits.check` on the single leg. **One gate was void** -- see B1.
7. **Submission.** `record_submit` persists the live order BEFORE
   `transport.submit`, and the money fields are compared byte-for-byte
   against what the gates saw. **OK**
8. **Fill reconciliation.** `poll_orders` re-reads every live order; the ACK
   is treated as a receipt, not a final state. A completing fill pops the
   row; a partial keeps it with its remainder. **OK**
9. **Inventory.** Positions, leg cost, max clip and residual all keyed per
   market. **OK**
10. **Cancellation / replacement.** Per-order TTL (120s) and the terminal,
    `min(submit + ttl, terminal)`, cancelled by exact venue id and proved by
    re-read. Replacement is bounded by an attempt budget and permitted only
    over a leg holding nothing with nothing live. **Replacement was
    unreachable** -- see B2.
11. **Settlement.** `settle` pays matched pairs exactly $1.00 a share, scores
    residual only on the known outcome, and keeps taker fees and maker
    rebates as separate lines. **OK**
12. **Realized net.** Four populations, never collapsed; a None is UNKNOWN
    and never zero. `verified_l5_realized` is `+$0.0276`; account-wide
    carries `-$84.62` of unattributable history that does NOT block live
    authorization. **OK, and the only trustworthy figure is the verified L5
    line.**

## Timing, as deployed

With the 300s margin the terminal is `T+600`, which is EARLIER than the
configured 720s completion cutoff:

```
T+0      entry opens (no placement floor configured)
T+480    completion phase begins (terminal - 120s)
T+540    entry closes -- on TOO_LATE_TO_WITHDRAW, not on the 600s cutoff
T+600    TERMINAL. nothing may rest past it; completion also ends here
T+720    the configured completion cutoff: UNREACHABLE
T+900    window close
```

Each order lives at most 120s from its own submission.

# Blockers, grouped by impact

## A. Would have made a live window unsafe (both now FIXED in this batch)

**B1 -- the one-live-order bound was void after any partial fill.**
`still_fillable_order_count` is the count `max_live_orders` binds on. It
skipped any row whose state string contained a terminal hint, and
`"PARTIALLY_FILLED"` CONTAINS `"FILLED"` -- the exact trap `order_progress`
documents and guards against thirty lines earlier. So a partially filled
order still resting with a remainder was not counted, and a second order
could go out on top of it. Every maker fill of a 3.32-contract leg is a
candidate. Cash and exposure gates were unaffected (they use the number), so
money stayed bounded; what broke was the ORDER-COUNT bound that the owner's
authorization and the watchdog's one-resting-order posture both rest on.
*Fixed: the number decides, as it already does elsewhere. No row left in
`live_orders` is terminal -- `terminate_order` and a completing fill both pop
it -- so the state string cannot be consulted here at all.*

**B2 -- a leg cancelled unfilled was never re-placed, and the engine moved
to the opposite side.** `probe_legs_claimed` asked only about attempt ZERO,
so the first submission marked a leg spent for the rest of the round whatever
became of it. Two consequences: the bounded replacement budget
(`L5_ACC_MAX_ATTEMPTS`) was unreachable, and after a TTL cancellation the
chooser skipped to the complement -- so a round could end holding ONE
unhedged leg on whichever side happened to fill. That is precisely the state
a both-or-neither pair exists to prevent.
*Fixed: a claimed leg is spent only when no further placement is permitted,
which is what `acc_leg_attempt` already decides -- and it refuses whenever
the leg holds filled quantity or an order is live, so a FILLED leg still can
never be re-placed. The crash-after-the-wire race stays covered because
`record_submit` persists the live order before `transport.submit`.*

## B. Would have wasted the window, the authorization and the handshake

**B3 -- the complement price is carried from planning time.** The plan is
frozen per round, rightly: re-planning each tick produced legs of different
sizes, which is not a matched pair. But with `max_live_orders=1` the
complement CANNOT rest beside the first leg -- it is refused
`MAX_LIVE_ORDERS` until the first leg leaves the book -- so it is ALWAYS
placed later, against a book that has moved, at its planning-time price.
Observed live in 0330z: the plan was frozen against a 0.42/0.56 touch and the
book went to 0.91/0.92, leaving the UP leg 51 cents below the touch
(unfillable) and the DOWN complement's wire price 45 cents BELOW the bid --
post-only, a venue rejects that for crossing, so it never rests and the
filled leg is left unhedged.

**B4 -- a stale round is re-proposed rather than retired.** Same window: the
same unreachable plan was proposed 32 times. The remedy needs no new
machinery -- 8 rounds are authorized and only round 0 is used -- it needs a
rule that abandons a round whose plan the market has left and opens a new one
at the new touch. This is NOT repricing a resting order, which is chasing.

**B5 -- the stranded-inventory backstop is switched off.**
`maybe_ts_completion` re-derives an executable taker price from the live book
in the last two minutes and crosses only when that strictly improves the
worse settlement outcome. `authorized_to_cross: false` in production, so it
is a logged proposal and nothing more. With B3 and B4 outstanding, this is
the only thing that would rescue a stranded leg.

## C. Configuration incoherence, not unsafe

**C1 -- `COMPLETE_CUTOFF_S=720` is unreachable** under the 300s margin: the
terminal at `T+600` always fires first. Entry likewise ends on
`TOO_LATE_TO_WITHDRAW` at `T+540`, not on the 600s entry cutoff. The
intended "completing stays available later than opening" asymmetry is only
60 seconds wide in practice, not 120.

**C2 -- `L5_ACC_MAX_ROUNDS=8` cannot be funded.** Under `NO_RECYCLING` a
completed pair releases nothing until settlement, and one full-size pair
already costs `$4.648` of the `$4.65` gate. So exactly ONE round per window
is fundable at full size. Smaller legs would fund two or three, but per-ORDER
fee rounding to the whole cent turns the net negative past about three
rounds. The honest ceiling at $5 is 1 full-size pair, or 2-3 small ones.

**C3 -- the pair cost has no cap in this module.** `MIN_GROSS_EDGE` is
`0.01`, so a pair at `0.99` passes, and `fee_floor` calls it profitable only
because it judges POST-ONLY execution: `net_if_taker` is negative there and
`both_positive_if_taker` is False, and neither is gated on. What protects us
is one environment variable -- at `depth_ticks=2` the pair is at most
`1 - spread - 0.04`. At 1 tick a one-cent book gives `0.97`; at 0 it gives
`0.99`. The operating model forbids assuming an uncredited rebate, so the
guard should be a cap, not a setting.

## D. Structural, not fixable by code

**D1 -- shadow cannot validate anything after a fill.** The claim is taken
only when armed, so under the shadow lock no leg is claimed, the round never
advances, and complements, second rounds, replacement attempts, inventory and
settlement cannot be exercised in production shadow at all. That is the
safety design. The end-to-end suite added in this batch is the substitute,
and it is why it had to drive the real executor rather than mock it.

# The smallest batch to a credible bounded live validation

Done in this batch (local only, not yet deployed):

1. **B1** -- the partial-fill order count. One line; restores the one-live-order bound.
2. **B2** -- replacement reachability. `probe_legs_claimed` consults the attempt axis.
3. **Testability** -- `InventoryStore(now_fn=...)`, defaulting to the previous
   expression exactly, so the per-order TTL can be exercised against the same
   clock the decision used. Production behaviour is unchanged.
4. **`tests/test_l5_e2e_window.py`** -- one scripted window, runner-to-executor,
   22 tests, production configuration, deterministic, no network, no threads,
   no live orders.

Still required before asking for a live window:

5. **B4** -- retire a round whose plan the market has left; open a new one at
   the new touch. Bounded by the existing round budget.
6. **B3** -- re-derive the complement from the book at the moment it is
   placed, keeping the frozen QUANTITY (which is what makes the pair matched)
   and refusing if the fresh price no longer clears the fee floor.
7. **C3** -- gate on `both_positive_if_taker`, or cap the pair at the 0.96
   spring target, so safety stops depending on one environment variable.

5-7 are one coherent change to the same decision path and belong in ONE
batch with their own end-to-end tests. B5 (`L5_TS_COMPLETION_CROSS`) is a
configuration decision for the owner, not a code change, and is only worth
turning on once 5-7 land.

# What to measure, once live

Only these: executable pair opportunities per window; the realistic
completion rate (observed n=2, which supports nothing); stranded-inventory
loss per incomplete window; capital efficiency against the $5 that is
actually tied up until settlement; and settlement-only realized net. Pair
economics at the current book are about **+$0.18 to +$0.22 per completed
pair** against a **-$1.70 to -$1.85** stranded single leg, so the whole
question is the completion rate, and nothing else we can measure matters
until it has a real denominator.
