# Two-sided maker replication — verified PMUS economics, executor gaps,
# and a bounded validation

**Scope change accepted:** the Dec 2025–Mar 2026 two-sided maker behavior
is the replication candidate. The August 97–99¢ favorite strategy is
retired from active development. The old losing straddle campaign is not
restored and nothing here assumes 0x8dxd's economics transfer to PMUS.

**Production disarmed. No executor file or parameter changed by this
work. No new service. Probability model non-voting. Every capital/loss
ceiling and the independent watchdog unchanged.**

---

## 1. PMUS BTC5m fee treatment — verified, and it is tiered

From `docs.polymarket.us/fees` and the live market object, read
2026-11-17. **This corrects an earlier claim of mine that PMUS pays no
rebate on BTC5m:** maker rebates use "the same tier coefficient in every
market, **including Table Tennis and crypto**". The `/v1/incentives`
pools I checked before are a *separate* liquidity-reward programme, and
their absence on BTC5m does not remove the standing maker rebate.

But the rebate is **tiered**, and the thresholds decide the question:

| maker rebate tier | qualifying maker volume | coefficient |
|---|---|---|
| **Tier 1** | **below $10,000,000** | **NO MAKER REBATE** |
| Tier 2 | $10M – $50M | −0.0035 (~5% of taker fee) |
| Tier 3 | $50M – $200M | −0.0070 (~10%) |
| Tier 4 | $200M+ | −0.0104 (~15%) |

| taker fee tier | trailing 30-day taker volume | coefficient θ |
|---|---|---|
| **Tier 1** | **below $10,000** | **0.095** |
| Tier 2 | $10k – $50k | 0.085 |
| Tier 3 | $50k – $250k | 0.075 |
| Tier 4 | $250k+ | 0.0695 |

Taker rebate: Tier 1 (below $250,000) → **none**.

### Where our account actually sits

**Tier 1 on all three.** Confirmed, not assumed: the live market object
returns `feeCoefficient: 0.095`, which is exactly the Tier-1 taker
coefficient. Our trailing volumes are far below every threshold.

Three consequences, and the first is the important one:

1. **A maker pays no fee, and at Tier 1 receives no rebate — so
   maker-only execution is exactly fee-neutral for us.** That is the
   single most favourable fact in this document: the strategy's
   economics rest purely on pair cost, with no fee drag.
2. **The maker rebate is structurally unavailable at our size.** Tier 2
   needs **$10,000,000** of maker volume in 30 days. At $4.50/window ×
   288 windows × 30 days we would do **$38,880** — **257× short**. This
   is not "unconfirmed"; it is unreachable under our ceilings, and no
   configuration changes that.
3. **Taker execution is punitive for us**: θ = 0.095 is the worst tier,
   costing `0.095·(1−p)` per dollar of notional — 532 bps at p=0.44.
   Taker stays disabled.

Also confirmed: tick `0.01`, `minimumTradeQty 0.01`, the fee coefficient
is **fixed when the order is inserted** and kept if it fills later, no
fee on cancel/expire/reject, and fees round to the cent with banker's
rounding (small trades can round to $0.00).

**Our code uses `taker_theta = 0.0695`, which is the Tier-4 value.** For
our account the correct figure is **0.095**. Every BTC5m EV number this
program has produced understates taker cost by 37%. Unchanged pending
your approval, recorded as a defect.

## 2. STRADDLE executor — smallest change per capability

| capability | status | smallest change |
|---|---|---|
| **two-sided maker quoting near executable prices** | **already supported** | **config only.** `L5_BTC5M_STRADDLE_PRICE` / `_RUNGS` are bounded `(0, 0.50)` *exclusive*, which already admits 0.40–0.49. No code change. **But see the EV-gate caveat below — this is not safely config-only.** |
| **repeated entries / re-quote after fills** | **blocked by one line** | `l5_btc5m_controller.py`: `if inv[side][0] > 0: continue` — "a side that already holds inventory is finished". Replace the boolean with a bounded per-side clip budget. |
| **inventory-aware quote adjustment** | **not supported** | The price is fixed by deliberate design: "a fixed limit cannot drift, so if both fill the pair cost is exactly 2× that price — no adverse selection on the PRICE at all". Making it inventory-aware trades that property away and is genuinely new logic. |
| **controlled unmatched exposure** | **partly** | `_exposure()` already computes `unmatched_qty`, `matched_cost_per_pair` and `worst_case_settlement_loss_usd`. There is **no imbalance ceiling** — today imbalance is implicitly capped at one clip *by the one-fill-per-side rule*. Removing that rule therefore **requires** adding an explicit bound in the same change. |
| **exact-order cancellation and reconciliation** | **already supported** | Cancel by exact venue id at `t=270`, the ledger, the ownership registry and the STOP latch all exist. No change. |

### The EV gate is the real obstacle to "just reconfigure it"

`l5_straddle_ev.entry_ev` requires `p_pair_natural`, `p_complete` and
`p_lone_win` as **inputs with no defaults** — by design: "a guessed rate
here would manufacture the edge the gate exists to test for." The values
in use were measured **at 0.08 rungs** (lone-leg win 9.81% against an 8%
price, over 503 shadow windows; opposite side reaching 0.08 in 19 of
245).

**Those probabilities are price-conditional and are wrong at mid price.**
Moving the rungs to 0.42 without re-measuring them either refuses
(`P_PAIR_NATURAL_NOT_SET`) or, worse, admits a rung on inputs measured
for a different trade. So the config change needs a measurement first,
and that measurement is read-only.

## 3. Our executable prices and queue position vs 0x8dxd

Measured live, read-only, 2026-11-17 (`/v1/markets/{slug}/book` and
`/bbo`; sampler still running):

- **Touch spread is 2¢** (median; p10 1¢). So a maker joining **both**
  touches gets a pair at **0.98** — about +2¢. 0x8dxd realised a median
  pair cost of **0.9342**, i.e. ~6.6¢. **They were not capturing the
  touch spread**; they were buying each side only on dips across a
  0.310-wide band over 106–207 fills. Our `COMPLETION_PAIR_COST_MAX` of
  0.90 is unreachable at the touch — it would need a 10¢ spread.
- **Queue ahead of a $2.00 clip at mid-price levels: 360–1,675
  contracts**, i.e. **67× to 394× our own size** (0.40: 537 ahead,
  107×; 0.44: 362 ahead, 80×; 0.47: 1,675 ahead, 394×).

**A price touch therefore fills nothing of ours.** For our clip to fill,
the market must trade *through* the level and consume the entire queue
ahead. This is the gap the instruction warned about, and it is the
difference between us and 0x8dxd: their first fill was at **t+7s
median**, i.e. they were in the queue before it built, with ~$1,000
clips and ~93% maker fills. Our measurements are mid-window; queue depth
in the first seconds is thinner and is **not yet measured** — that is
the single most valuable thing the read-only phase below will establish.

## 4. Conservative scaled model, under the unchanged $2 clip / $10 limits

As a Tier-1 maker: **fee = 0, rebate = 0**, so no fee term.

`EV per share = p_both·(1 − 2X) + (1 − p_both)·(pw − X)`

`p_both` is a **lower bound** measured from 0x8dxd's own fills across
2,734 March windows: the fraction of windows in which a BUY was observed
at or below X on **both** outcomes, so a symmetric resting pair at X
would have had both legs trade at or through it.

| X | pair cost | p_both (lower bd) | EV/share at fair odds | break-even `pw` | adverse-selection tolerance |
|---|---|---|---|---|---|
| 0.35 | 0.70 | 24.3% | +$0.0729 | 0.2537 | 9.6 pp |
| **0.40** | **0.80** | **37.2%** | **+$0.0744** | **0.2815** | **11.8 pp** |
| **0.42** | **0.84** | **42.6%** | **+$0.0682** | **0.3013** | **11.9 pp** |
| 0.44 | 0.88 | 48.4% | +$0.0581 | 0.3274 | 11.3 pp |
| 0.47 | 0.94 | 59.0% | +$0.0354 | 0.3837 | 8.6 pp |
| 0.49 | 0.98 | 66.2% | +$0.0132 | 0.4508 | 3.9 pp |

The optimum is around **X = 0.40–0.42**, which tolerates a leg filling at
X winning **11.8 pp less often than its price implies** before the policy
breaks even. For contrast the 97–99¢ policy tolerated about **1.2 pp** —
that difference in robustness is the whole argument for the switch.

At X = 0.40, $2.00 buys 5.00 contracts a side; a completed pair costs
$4.00 and pays $5.00. EV/window under the table's assumptions is
**+$0.372**. Worst case in a window is the whole outlay on one leg,
**$2.00**, and the $5 per-market / $10 aggregate ceilings bind exactly as
before.

**I am deliberately not extrapolating that to a daily figure.** Three
gates sit between this table and any realized dollar, and all three are
currently unmeasured for us:

1. **`p_both` is from the international venue and from a trader with
   queue priority**, not from our venue and not from our queue. Section 3
   shows 67–394× our size resting ahead of us at these very levels.
2. **`pw` at mid prices is unmeasured.** The table's "fair odds" column
   assumes a leg filling at X wins with probability X. Adverse selection
   means it should win *less* — the tolerance column is exactly how much
   less the policy survives.
3. **Settlement and queue are not the same question.** Fill quality
   decides whether the EV is ever collected.

## 5. The three highest-impact gaps, and the bounded validation

**Gap 1 — the one-fill-per-side lockout** (`if inv[side][0] > 0:
continue`). It caps the strategy at 2 fills per window against 0x8dxd's
106–207, and it is the only thing standing between the current executor
and repeated two-sided entries. It cannot be removed alone: it is also
the current de-facto imbalance bound, so it must be replaced by a
per-side clip budget **plus** an explicit unmatched-exposure ceiling in
one change.

**Gap 2 — the rungs sit where nothing trades, and the EV gate cannot
honestly move them.** The price bound already admits 0.40–0.49, so the
rung move is config; but `p_pair_natural`, `p_complete` and `p_lone_win`
were measured at 0.08 and are price-conditional. Moving the rungs
without re-measuring them either refuses or manufactures the edge the
gate exists to test.

**Gap 3 — queue position is unmodelled.** The executor prices a rung's
EV on the assumption it fills if the price reaches it. Measured: 67–394×
our clip rests ahead at mid-price levels, and at 0.97 we were at the
front of the queue in 3 of 120 samples. Until fill probability is
measured rather than assumed, every EV figure is an upper bound.

### Proposed validation — read-only first, and it needs no approval

**Phase A (zero money, no orders, no new service).** Extend the existing
read-only book sampler across ~200 consecutive BTC5m windows to measure,
on **our** venue:

- `p_both(X)` for X in 0.35–0.49 — from the actual price path, not from
  another trader's fills;
- queue depth at mid-price levels **in the first 30 seconds**, which is
  where 0x8dxd entered and where our straddle already rests;
- the realized excursion per window, which is what `p_pair_natural`
  actually is.

That produces the three probabilities the EV gate needs, measured at the
prices we would use, and it answers Gap 3's cheap half. It is a
collector run on existing infrastructure.

**Phase B (bounded live, and I am not asking for it yet).** Only once
Phase A shows a positive measured EV at a specific X:

- one market, BTC5m only, **maker-only, taker disabled**;
- two-sided at the Phase-A-selected X, **$2.25 per side** (existing
  `STRADDLE_CLIP_MAX_USD`), worst case $4.50/window;
- **20 consecutive windows**, automatic disarm after, no auto-continue;
- unchanged $5 per-market / $10 aggregate / $5 daily-loss ceilings,
  unchanged watchdog, STOP latch and exact-id cancellation;
- the measurement it buys is **actual maker fill quality**: of the
  windows where the price reached our rung, in how many did we fill, and
  what did the filled legs settle at. That is the one number no amount
  of read-only work can produce.

**Phase B needs your separate explicit approval and I am not requesting
it in this document.** Phase A is read-only and I can start it on your
word.
