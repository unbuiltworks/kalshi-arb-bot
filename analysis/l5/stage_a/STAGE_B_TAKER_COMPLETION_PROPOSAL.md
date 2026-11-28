# Stage B proposal — bounded taker completion

**Status: awaiting explicit owner authorization. Nothing is armed, the policy
flag is default-off, and `L5_TS_COMPLETION_CROSS` remains unset.**

## 1. What the evidence established

Verified against the Stage A campaign's own timestamped records (deployment
`9b43b312`, commit `0304fbe8`), not modelled.

**The 2145z opportunity was genuinely executable, not merely attractive.**

| check | finding |
|---|---|
| duration | both-positive from **21:48:23 to beyond 21:54:55** — ~6.5 min, ~30 consecutive decision ticks |
| depth | displayed depth at full size usually **400–1500** contracts against a **2**-contract need (200×–750×) |
| depth failures | only **2 of 45** samples were depth-starved (21:51:36 at 1.00, 21:52:15 at 0.69) |
| latency | immaterial: an opportunity persisting ~390 s absorbs seconds of submit latency |
| fees | `taker_fee_usd 0.02` on 2 contracts at 0.10 — reproduced independently as `ceil(0.0695 × 2 × 0.10 × 0.90)` |
| order constraints | on-tick, above min-qty, inside cash headroom and the worst-case ceiling — all already enforced |
| settlement | 2145z resolved DOWN; our 2 DOWN @ 0.45 redeemed $2.00 for +$1.10, which ties to the ledger |

**The engine's own arithmetic is conservative, and I verified it rather than
trusting it.** Independently recomputing `net_settlement` reproduces
`-0.9400 / -0.0500 / +0.8400` exactly. `worst_before` charges a taker fee
($0.04) on a DOWN leg that actually filled as a **maker for $0.00**, so the
true status quo was **-$0.90** and the true full-fill outcome **+$0.88**. The
engine reports -$0.94 → +$0.84. Both figures are understated by the same
$0.04, so `worst_improvement_usd 1.78` is exact and the gate is strictly
conservative. No correction is needed.

**Partial fills cannot flip the sign.** The venue offers
`TIME_IN_FORCE_IMMEDIATE_OR_CANCEL` but **no fill-or-kill**, so partials are
real. Worst-case P&L is **monotonically non-decreasing in fill quantity** at
every complement price (0.10/0.30/0.50/0.54/0.60 all verified), provided the
cross never exceeds the deficit. A half fill of the 2145z cross is -$0.05
against -$0.94 for doing nothing: strictly better, merely short of +$0.84.
A partial is a **shortfall against the modelled gain, never a new loss.**

## 2. Independent opportunities, deduplicated

The 45 `L5_TS_COMPLETION` rows in 2145z are **one** opportunity against one
stranded leg, not 45. Deduplicating on `(market, held side, held qty, held
basis)` gives **four** independent opportunities — one per stranded leg — of
which **two** were real:

| window | stranded inventory | viable? | best `worst_after` | actual settled |
|---|---|---|---|---|
| 2115z | DOWN 1.74 @ 0.237 | **no** — best **-0.0400**, never positive | — | -$0.2756 |
| 2130z | UP 2.05 @ 0.46 | **yes** — 22 of 49 samples | **+$0.3915** | -$0.9430 |
| 2145z | DOWN 2.00 @ 0.45 | **yes** — sustained ~6.5 min | **+$0.9500** | +$1.1000 |
| 2200z | DOWN 1.85 @ 0.33 | **no** — 0 of 58, complement pinned ~0.99 | — | -$0.6105 |

Two findings matter more than the headline:

- **2200z's -$0.610 was not avoidable by crossing.** It resolved UP, so our
  DOWN leg was the loser and every complement was ~0.99. The policy
  correctly does nothing. Same for 2115z.
- **2145z's +$1.10 was better than the +$0.84 the policy would have taken.**
  The policy would have *given up* ~$0.15–$0.26 there — trading a coin flip
  for certainty. That is the trade on offer, and it is the right one only
  because the same unhedged position was -$0.94 the other way, which is
  exactly what 2130z and 2200z were.

## 3. Modelled campaign effect, and why the margin floor exists

Replaying the policy over the campaign's own books:

| firing rule | campaign total | vs actual -$0.7291 |
|---|---|---|
| first both-positive tick | -$0.6421 | **+$0.0870** |
| `worst_after >= +$0.25` | -$0.3371 | **+$0.3920** |
| best observed tick | +$0.4554 | +$1.1845 (hindsight, not implementable) |

Firing on positivity alone is **nearly worthless** — it spends the window's
one cross on a tick that buys almost nothing. The best-tick row is hindsight.
So the policy fires on a **margin above zero**, defaulting to **+$0.25**,
which is the floor at which both real opportunities still qualify (2130z
+0.2890, 2145z +0.2600) and the marginal ticks do not.

**Even at its best this is a modelled +$0.39 on a -$0.73 campaign.** It does
not by itself make the strategy profitable. It removes one specific,
repeatable way of losing money.

## 4. What is implemented (default-off)

`src/l5_taker_completion_policy.py` — a pure authorization layer. It prices,
sizes and submits nothing; it answers whether a cross that already passed
every existing gate may be acted on. Gates, each with an auditable refusal:

1. `POLICY_DISABLED` — **the default.** Opt-in via `L5_TAKER_COMPLETION_POLICY=YES`, an explicit string compare so a stray `0`/`false` cannot enable it.
2. `OWNER_AUTHORIZATION_ABSENT` — `L5_TS_COMPLETION_CROSS` still separately required.
3. `HALTED` / `HALT_STATE_UNKNOWN` — reads the same halt the money path reads; unknown fails closed.
4. `WORST_OUTCOME_NOT_POSITIVE` — the gate `completion_decision` deliberately leaves to its caller, and what 2115z (-0.0400) fails.
5. `MAKER_NOT_ATTEMPTED` / `MAKER_PATIENCE_NOT_SPENT` — maker-first, default 90 s, measured from the **submit ledger** not a flag.
6. `CROSS_EXCEEDS_DEFICIT` — the invariant the partial-fill safety rests on.
7. `CONSERVATIVE_*` — the book is **walked** for VWAP (slippage) with every rung cut to a **0.25 haircut**; positivity must survive at the quantity that conservatively clears.
8. `MARGIN_BELOW_FLOOR` — default +$0.25.
9. `OUTLAY_ABOVE_CAP` / `WINDOW_OUTLAY_EXHAUSTED` — $1.50 per cross, $3.00 per window, charged on the conservative quantity and VWAP.

Every existing control is untouched and still binds: cash headroom, the
worst-case deficit ceiling, account exposure, the campaign budget, the daily
settled-loss halt, tick/min-qty legality, "improvement must exceed its own
fee", and the independent watchdog.

**Maker-first is read from `state["submitted"]`, not stamped at the
trigger.** A flag set where the completion is *decided* would have claimed
the maker route was tried on windows where nothing was ever posted — the
completion path was approved and then refused `WOULD_CROSS` **41 times** in
this campaign. Only a submit row proves an order was sent.

### The scheduling defect (item 5), fixed

`starts_utc` is now an optional field of the approval scope, and
`authorize_window` refuses `WINDOW_BEFORE_AUTHORIZED_START` for any window
whose **own start** precedes it — judged on the window's start, not on `now`,
because trading a window that opened early is trading an unapproved window.
An unlocatable slug under a start bound fails closed
(`WINDOW_START_UNREADABLE`). Absent `starts_utc` keeps the previous
behaviour, so no standing approval is retroactively refused.

Had this been in force on 2026-10-30, 2115z would have been refused and the
campaign's four windows would have been the four that were approved.

## 5. Validation status

- **63 new regression tests**, all passing, covering every refusal above, the
  monotonicity invariant across five complement prices, slippage turning an
  authorized cross into a refusal, the empty-vs-unreadable book distinction,
  and all four windows' recorded states.
- **Recorded-evidence replay** (`analysis/l5/stage_a/replay_taker_policy.py`):
  authorizes 2145z @ 21:51:49 ($0.22 outlay, +$0.84) and 2130z @ 21:37:53
  ($0.6755, +$0.3915); refuses 2115z, 2200z, both marginal first ticks, the
  depth-starved sample, and every gate-off case.
- **Baseline parity** against the full suite.
- **Out-of-sample shadow is NOT yet done.** The policy is wired to evaluate
  on every completion tick with the flag off, so it records what it *would*
  have authorized without acting. That data does not exist yet.

## 6. What I am asking for

**Not authorization to cross.** Authorization to run the policy in
**shadow** — flag on, `L5_TS_COMPLETION_CROSS` still unset — so it logs
`taker_policy` verdicts against live books out of sample. That moves no
money and submits nothing.

Then, if the shadow record shows the policy firing on genuine opportunities
and staying silent otherwise, a bounded live proposal with these parameters:

| parameter | proposed |
|---|---|
| venue / series | PMUS BTC15m only |
| windows | 4, each with an explicit `starts_utc` |
| concurrency | one market |
| crossing | completion only — **never** an opening leg |
| per-cross outlay cap | $1.50 |
| per-window outlay cap | $3.00 |
| margin floor | +$0.25 conservative `worst_after` |
| maker-first wait | 90 s, from a posted quote |
| depth haircut | 0.25 |
| aggregate exposure | $20 |
| per-market worst-case deficit | $1.50 |
| daily settled-loss halt | $5 |

**Expected execution economics, stated as modelled and bounded:** on the four
Stage A windows the policy fires **twice**, spends **$0.90** of additional
outlay in total, and improves the campaign by a modelled **+$0.39**. Worst
case if every cross fills fully and every window then settles against us is
bounded by the existing $1.50 per-market deficit and $5 daily halt — the
crosses cannot breach them because the outlay is charged through the same
cash and exposure gates. The realistic downside is not a loss but a
**shortfall**: a partial fill delivers less than the modelled gain while
still beating inaction.

**Do not read +$0.39 as a path to $100/window.** It is not. It removes one
repeatable loss; the opening policy's adverse selection, documented in
`FIRST_LEG_TO_COMPLEMENT.md`, is untouched by this change and remains the
larger problem.

## 7. What must not be concluded

- Nothing here is realized. Every figure is modelled on recorded books,
  assuming an IOC fills at displayed-minus-haircut depth.
- 2 of 4 stranded legs had no exit at all. A completion policy is not a
  solution to stranding; it is a partial mitigation.
- The only profitable Stage A window was profitable by directional luck, and
  this policy would have reduced that window's P&L. That is the intended
  trade and it must not be sold as an improvement to that window.
