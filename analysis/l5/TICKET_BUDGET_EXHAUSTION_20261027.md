# R=4 campaign: ticket-budget exhaustion, measured burn, and the minimal adjustment

Campaign `camp1.4f1b25fe655d20d53ca8d698` · deployment `77e52ff5` · SHA `d189935a`
Status at 00:47Z: **halt latch CLEAR, 3 windows settled, all three positive, zero residual.**

This document is a proposal. **Nothing has been changed.**

---

## 1. Confirmed settled results (venue-confirmed only)

`resolution_source: VENUE_SETTLEMENT_PX`, `venue_state: MARKET_STATE_EXPIRED` for all three.
No rebate of any kind is inside any figure below.

| window | realized net | matched qty | residual | achieved pair cost | submissions | OPEN tickets |
|---|---|---|---|---|---|---|
| 0000z | **+$0.327000** | 3.66 | 0.00 | 0.9107 | 6 | 4 |
| 0015z | **+$0.402800** | 2.12 | 0.00 | 0.8100 | 4 | 1 |
| 0030z | **+$0.099000** | 1.98 | 0.00 | 0.9500 | 2 | 1 |
| **total** | **+$0.828800** | 7.76 | **0.00** | — | 12 | 6 |

From `L5_CAMPAIGN_ROLLING` at 00:46:29Z, which is authoritative:

- `windows_confirmed: 3`, `windows_with_a_completed_pair: 3`, `completion_rate: 1`
- `contracts_filled: 15.52`, `filled_orders: 8`, `partial_fills: 0`
- `residual_pnl_usd: 0E-27` — **zero stranded inventory in all three windows**
- `actual_fees_usd: 0` — maker-only throughout, 12 maker / 0 taker
- `realized_net_usd: 0.828800`
- `rebate_estimate_usd: 0.0631`, `rebate_credited_usd: 0` — excluded from net

Account all-time realized: −$15.05450 → **−$14.22570**, which is exactly
−15.0545 + 0.8288. The ledger reconciles to the settlement lines with no drift.

**Mean +$0.2763 per settled window; 3 of 3 positive.**

The spread is driven by **achieved pair cost (0.810 → 0.950), not by round count.**
0015z and 0030z both ran a single opening round and produced the best and the
worst window respectively. 0000z is the only window that ran the full four
opening rounds and it landed mid-range. With n=3, rounds-per-window is
unproven in either direction — which is the question the remaining windows
exist to answer, and cannot while OPEN is exhausted.

---

## 2. The exhaustion, and its actual cause

`L5_LIVE_AUTH_MAX_OPEN=12` is **spent, 12/12**, as of 00:31:34Z. Between
00:33 and 00:45 the engine logged **41 `OPEN_BUDGET_EXHAUSTED` refusals** in
0030z: that window wanted further opening rounds and was blocked at one.

### Measured ticket progression

| point | time | OPEN | COMPLETE | REPLACE | total | windows |
|---|---|---|---|---|---|---|
| pre-arm baseline | 23:31:31 | 6 | 5 | 0 | 11 | 0 (`armed: false`) |
| after 0000z | 00:07:44 | 10 | 7 | 0 | 17 | 1 |
| after 0015z | 00:24:05 | 11 | 8 | 2 | 21 | 2 |
| 0030z round 1 | 00:31:34 | **12** | 9 | 2 | 23 | 3 |

Per-window burn, which reconciles **exactly** to each window's `submitted` count:

- 0000z: OPEN +4, COMPLETE +2 = 6 submissions ✓
- 0015z: OPEN +1, COMPLETE +1, REPLACE +2 = 4 submissions ✓
- 0030z: OPEN +1, COMPLETE +1 = 2 submissions ✓

### Two causes, and the second is the real one

1. **11 of the tickets were spent before this campaign ever armed** — 6 OPEN
   and 5 COMPLETE, visible at 23:31:31Z with `armed: false, windows_spent: 0`.
   Class budgets are counted from durable claim-lock files keyed on
   `L5_EXPERIMENT_ID`, **not** on the campaign nonce, and the experiment id was
   not rotated when the new nonce was issued. So of a ceiling of 12, this
   campaign only ever had **6 OPEN tickets for 8 windows.**

2. **The ceiling was sized for a different campaign shape.**
   `analysis/l5/EIGHT_WINDOW_EXPERIMENT_CONFIG.md` sets
   `L5_LIVE_AUTH_MAX_OPEN | 6 | 12 | "8 windows + margin"` — about 1.5
   openings per window, i.e. a one-round-per-window campaign. The mandate
   that approved **R=4** independently reasoned the figure up:

   > `L5_FINAL_MANDATE_FOR_APPROVAL.md` §4: *"approve 4 rounds/window →
   > `L5_LIVE_AUTH_MAX_OPEN=32`"*, with the table *"4 | 8 | **32** | 32"*.

   The activation deployed the pre-R=4 sheet's 12 rather than the mandate's
   32. That is the defect: a configuration mismatch, not a safety failure.

### Tickets are never returned

`src/l5_live_authorization.py:801`:

> *"O_CREAT|O_EXCL and fsynced before the wire. A redeploy, a crash, a
> cancelled order, a rejected order and an ambiguous acknowledgement all
> leave the ticket spent."*

There is no release, refund or unclaim path — confirmed by grep. The claim is
taken immediately before the wire, so a ticket is **not** burned by a decision
refused at an earlier gate, but **is** burned by the 2 cancellations in 0000z
and the 1 rejection in 0015z.

---

## 3. Delay is expensive: a window is spent on arming, not on trading

`reserve_window` (`src/l5_campaign_approval.py:481`) is called once
`authorize_arming` returns a claim — shadow lock off, owner handshake matched,
halt latch clear, preflight passed, book reconciled, deadline holding, scope
digest matching. **Class budgets are not in that list.** So a window with OPEN
exhausted still arms and still spends the window.

This is already happening: `0045z` was claimed at 00:45:00Z with zero OPEN
tickets and will produce nothing.

Windows: 0000z–0145z. Four remain saveable — **0100z, 0115z, 0130z, 0145z** —
and one is lost every 15 minutes. `not_after` is 02:46:48Z, so time is not the
binding constraint; the window ledger is.

---

## 4. Proposed adjustment — ticket counters only

| variable | now | proposed | basis |
|---|---|---|---|
| `L5_LIVE_AUTH_MAX_OPEN` | 12 | **28** | 12 spent + 4 windows × 4 opening rounds. Four is the R=4 design maximum (`L5_TS_ROUND_ALLOCATIONS=4` splits the $12 envelope into 4×$3), and 0000z hit exactly 4. **Stays under the 32 already confirmed in the mandate for R=4 × 8 windows.** |
| `L5_LIVE_AUTH_MAX_COMPLETE` | 18 | **20** | 9 spent + 8 (the 2/window observed) + 2 completion reserve = 19 |
| `L5_LIVE_AUTH_MAX_SUBMISSIONS` | 42 | **60** | 23 spent + 32 (0000z's 6-submission shape × 4 windows, rounded up) + reserve headroom |
| `L5_LIVE_AUTH_MAX_REPLACE` | 24 | **unchanged** | 2 of 24 spent; 0000z at full R=4 used zero |

### Unchanged, explicitly

$12 cash envelope (`L5_MAX_CASH_DEPLOYED_USD`) · $20 total exposure
(`L5_MAX_TOTAL_EXPOSURE_USD`) · 3-contract residual cap
(`PMUS_RESIDUAL_CAP_K=2` → `min(k × clip, 3)`) · $29.30 campaign budget
(`L5_CAMPAIGN_BUDGET_USD`) · $20 cumulative loss halts
(`L5_MAX_CUMULATIVE_LOSS_USD`, `L5_MAX_L5_CUMULATIVE_LOSS_USD`) · $5 daily
(`L5_MAX_L5_DAILY_LOSS_USD`) · `L5_MAX_LIVE_ORDERS=1` · `L5_MAX_WORST_CASE_DEFICIT_USD` ·
`L5_LIVE_AUTH_MAX_WINDOWS=8` · `not_after 02:46:48Z` · maker-only · 0.96 pair
ceiling · 0.20 first-leg floor · the campaign approval document and nonce.

---

## 5. Does raising OPEN force more COMPLETE? Yes — structurally, by design

`completion_capacity_refusal` (`l5_live_authorization.py:895`) runs **before
every OPEN ticket** and refuses unless **both** hold:

1. `COMPLETE remaining ≥ COMPLETION_SUBMISSIONS_PER_OBLIGATION` (= 2)
2. `submissions_spent + 1 + 2 ≤ MAX_SUBMISSIONS`

> *"DO NOT OPEN WHAT YOU CANNOT CLOSE ... a reserve that only one of the two
> bounds respects is not a reserve."*

So raising OPEN alone would simply move the refusal from
`OPEN_BUDGET_EXHAUSTED` to `NO_COMPLETION_CAPACITY_RESERVED`. The flat
backstop branch is **demonstrably** binding at the current ceiling: projected
end-state `submissions_spent` is 23 + 32 = 55, and at `MAX_SUBMISSIONS=42`
openings would start refusing once spent reaches 39. The three variables must
move together, and the reserve itself stays at 2 per obligation — unchanged.

This coupling is a safety property and it is **preserved, not weakened**, at
the proposed numbers.

## 6. Does it create new exposure risk? No — with one honest caveat

A ticket is permission to **attempt one submission**. It is not capital, size,
or exposure. Every economic bound is enforced independently and downstream,
per order and per round, and none of them reads a ticket count:

- `campaign_used = max(0, −cumulative_realized) + cash_committed + pending_obligation`, gated against $29.30
- the $12 cash envelope, split $3 per round at R=4
- the 3-contract absolute residual cap, counting filled inventory **plus** every live order as-if-filled
- $20 total exposure, $20 cumulative loss, $5 daily loss
- one live order (plus one same-round pair slot)
- the 8-window ceiling

More tickets cannot buy more dollars or more contracts. Worst-case committed
cash if all four remaining windows' settlements lagged at once is ~4 × $1.88 ≈
$7.52, well inside the $29.30 budget; in practice each window released its
committed cash on settlement (`committed_released` 1.7172 and 1.8810).

**The caveat, stated plainly:** more tickets means more *attempts*, therefore
more fills, therefore the remaining windows will trade at the approved R=4 rate
instead of stalling at one round. Per-window realized dispersion rises to the
intended level. That is the purpose of the change — but it is a real
behavioural change, not a no-op, and it is the reason the question in §1 can
only be answered after it.

## 7. Restart, authorization, and active orders

- **New authorization: not required.** The approval document, nonce
  `4f1b25fe655d20d53ca8d698`, `not_after 02:46:48Z` and the `PMUS_Q1_*`
  handshake are all unchanged and still valid. Arming re-runs on boot.
- **Restart: required.** Railway delivers variable changes by redeploying, so
  the container restarts. The values are read per call via `os.getenv`, but a
  running process never sees a new env.
- **Window cost of a restart: none.** `reserve_window` is *"IDEMPOTENT PER
  SLUG, so a redeploy or a restart inside a window it has already reserved
  resumes that window instead of spending another: `reserved_now` comes back
  False and the count does not move."*
- **Active orders: this is the one real risk.** On boot the engine drops
  resting order *records* — *"a restart cannot reconstruct a queue position"* —
  while realized inventory is reconciled from the venue and simulated
  inventory carries across. A live resting order at the venue during a restart
  is therefore the hazard to avoid.

### Recommended application moment

Apply when the heartbeat shows **`live_orders: 0` and residual 0**. That state
is reliably present: all three windows ended `residual_qty 0.00`, and
`live_orders: 0` was observed at 00:33:36Z. Verify it on the heartbeat
immediately before applying, and if an order is resting, wait one heartbeat.

Because OPEN is already exhausted, the engine cannot open anything new in the
meantime, so any resting order between now and the change can only be a
COMPLETE or REPLACE on existing inventory.

---

## 8. What I did not propose, and why

**Rotating `L5_EXPERIMENT_ID`** would reset all class budgets at once and is
the mechanism used previously
(`L5_NOKEY_APPROVAL_20261025.md`: *"budget scope key — resets class budgets"*).
I rejected it. It would also discard the durable `acc1.` per-leg attempt
claims, which are the anti-replay protection keyed on (market, round, leg).
Raising the ceilings is purely additive: every existing claim keeps its key and
its meaning. That is strictly the safer of the two.
