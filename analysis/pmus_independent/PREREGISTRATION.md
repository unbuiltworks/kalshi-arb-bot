# PMUS BTC15m — independent mechanistic hypotheses: PREREGISTRATION

Written 2026-10-05 ~03:20Z, **before any per-second row from the data below was
read by this lane**. Branch `claude/pmus-btc15m-independent-hypotheses`, based on
`ae80e9d` (`claude/l5-pmus-economic-results-7c116e`). Exploratory research. No
order is placed, previewed or prepared. No Railway operation of any kind.

## Data, and what is never touched

| set | windows (by venue window start, UTC) | role |
|---|---|---|
| **2026-10-01 (any hour)** | — | **RESERVED HOLDOUT. Never decompressed, reduced, derived or read.** Raw parts whose name is `pmus_ws_20261001_*` are skipped by name inside the workflow before any byte is opened; the skip count is logged. |
| **D — discovery** | `[2026-10-02 00:00Z, 2026-10-04 00:00Z)` | rules may be inspected and understood here. Disclosed: the prior lane's 178-window OOS sample and 43-window reversal holdout lie inside D; they generated *other* hypotheses (pair completion, residual exit, reversal), not these. |
| **C — confirmation** | `[2026-10-04 00:00Z, end of archive)` | evaluated **once**, by the analysis script as committed before C is opened. No rule, bucket or threshold below may change after C is read. Any later change is labelled `POST-C` and has no confirmatory standing. |
| fresh tape after this archive | future | the real out-of-sample set for any survivor |

Source: raw recorder parts already copied out by run `37253257157`
(`raw-tape-archive-*` artifacts), reduced with the existing calibrated pipeline
unchanged: `pmus_oos_reduce_part.py` → `pmus_oos_derive_rows.py` (recall 1.000 vs
development) → `pmus_state_table.py` (causal per-second state; BRTI 1s from the
venue gateway; `priceToBeat` withheld until ws+15s; book = latest update ≤ t;
label attached last).

## Executability conventions (all rules)

- One UP book. UP buy price = `up_ask` (qty `up_ask_qty`). DOWN buy price =
  `down_px` = 1 − UP bid (qty `down_qty` = UP bid qty).
- **Exit before settlement uses only the BUY path**: an UP lot is closed by
  buying DOWN at `down_px`, a DOWN lot by buying UP at `up_ask`. The pair pays
  $1 at settlement, so exit P&L = 1 − entry − complement, minus both taker fees.
  This is economically identical to selling at the bid and needs no SELL path.
- Taker fee = 0.0695·p·(1−p) per contract at 4 dp (`pmus_fees.taker_fee`,
  cents=False), on **every** leg. Maker fills are never assumed; rebates = 0.
- Freshness: a decision second is usable only if `book_age_s ≤ 2.0`. An exit
  scheduled for t+h executes at the first fresh second ≥ t+h; if none exists
  before the window's last row, the lot is held to settlement (counted).
- Unit = 1 contract. **One trade per window per rule** (the first qualifying
  second), so observations are window-independent. Trigger counts across all
  seconds are reported separately as opportunity frequency only.
- `Phi_up(t) = Φ(z_t)`: the parameter-free driftless-random-walk probability that
  UP settles, from the state table's own `z`. Not fitted. `Phi_dn = 1 − Phi_up`.
- `mid = (up_ask + (1 − down_px)) / 2` in UP terms.

## Hypotheses (frozen)

Every variant below is reported, including losers. Exits are fixed, never chosen
per path.

**H0 baselines.** Buy UP at first fresh second ≤ 450 s remaining; same for DOWN.
Settle.

**H1 (family D/H — BTC distance vs price).** Descriptive: at checkpoints
840/600/450/300/180/120/90/60/30 s remaining, calibration of executable ask vs
realized win, by ask bucket, with window-level Wilson CIs; calibration of Φ(z)
itself. Rules **H1-600, H1-300, H1-120**: at the first fresh second ≤ T, buy the
side with `Phi_side − ask_side − fee ≥ 0.05` (if both, larger gap). Settle.

**H2 (family B — overreaction).** Seconds with 120 ≤ remaining ≤ 870.
`dmid30 = mid_t − mid_{t−30}`, `dPhi30 = Phi_up_t − Phi_up_{t−30}`.
- **H2-UNEXPL**: |dmid30| ≥ 0.10 and |dPhi30| < 0.03 → buy the side that got
  cheaper. Exit variants: **+60 s** and **settle**.
- **H2-ALL**: |dmid30| ≥ 0.10 (any cause) → buy the side that got cheaper. +60 s / settle.

**H3 (family C — momentum).** Same trigger window.
- **H3-ALL**: |dmid30| ≥ 0.10 → buy the side that got more expensive. +60 s / settle.
- **H3-EXPL**: |dmid30| ≥ 0.10 and dPhi30 same sign with |dPhi30| ≥ 0.05 → buy the
  side that got more expensive. +60 s / settle.

**H4 (family G — fast liquidity shock).** 120 ≤ remaining ≤ 870. A side's ask
falls ≥ 0.05 within 3 s (ask_t ≤ ask_{t−3} − 0.05) while that side's Φ changed
by < 0.02 over the same 3 s → buy that side at ask_t. Exit **+30 s** / settle.

**H5 (family F — touch imbalance).** 120 ≤ remaining ≤ 870, spread == 0.01.
`imb = (bid_qty − ask_qty)/(bid_qty + ask_qty)` (UP terms). imb ≥ 0.9 → buy UP;
imb ≤ −0.9 → buy DOWN. Exit **+30 s** / settle.

**H6 (family E — wide spread).** Descriptive only (no maker fill model exists that
is not an assumption): frequency of spread ≥ 0.04 with 120 ≤ remaining ≤ 870;
mean change of each side's ask over the next 10 s; realized calibration of mid in
those states. No P&L claim.

**H7 (family I/H — favorite in the un-capped band).** First fresh second with
remaining ≤ T where the favorite's ask ∈ [0.70, 0.95]; buy favorite, settle.
Variants **H7-300** and **H7-120** (120 is before the 60 s averaging window
opens). Mirror **H7U-300/H7U-120**: buy the underdog when its ask ∈ [0.05, 0.30]
at the same timing (complement, reported for completeness; underdog work proper
belongs to another lane).

**H8 (family L — regime).** Every rule above split, descriptively only, by US
cash hours (13:30–20:00Z) vs other, and by σ1s at entry above/below the D-median
σ1s. No regime is used to select anything.

Family J (two-sided sequential inventory) and K-as-completion are **not re-run**:
`PATH_ECONOMICS_RESULT.md`, `ADDENDUM_LAG_AND_CAPTURE.md` and
`SETTLEMENT_LOCK_DIAGNOSTIC.md` already reject them causally on 178 windows; they
are cited, not rediscovered. BRTI→PMUS latency is likewise closed there.

## Statistics reported for every rule

eligible windows; trades; wins/losses; mean executable entry; gross; fees; net;
net/trade; window bootstrap 95% CI (10,000 resamples, seed 7); t; max drawdown;
max consecutive losses; opportunity frequency (trades per eligible window and
qualifying seconds per window); $/96-window day at 1 unit; median and p10 touch
qty at entry (capacity); held-to-settle fall-back count for timed exits.

## Promotion criteria (fixed now)

~30 rule-variants are tested, so a nominal 5% test will produce ~1–2 false
positives. Therefore:

1. **D**: net/trade > 0 **and** t ≥ 2.5 (≈ Bonferroni-aware, not exact).
2. **C**: same sign, net/trade > 0, evaluated once.
3. Economic: net/trade ≥ 1.0 c *and* plausible capacity at the touch.

Passing 1–3 makes a rule a **candidate for fresh-tape validation**, not a
strategy. Anything failing is recorded as rejected with its numbers.
