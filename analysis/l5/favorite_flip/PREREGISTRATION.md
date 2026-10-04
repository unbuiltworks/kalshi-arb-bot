# FAVORITE → EXTEND → FLIP → SWITCH → REPEAT — preregistration

Written and committed BEFORE any result was computed. Everything below is
fixed in advance: the market-state source, the definitions, the train/held-out
boundary, the baselines, and what counts as the hypothesis surviving.

No live orders. No modification of the frozen `.96` control. The frozen
`trade_imbalance >= 0.75 @2s` diagnostic stays out of this lane entirely.

## 0. The defect this lane exists to fix

Every earlier side model here took the market price from **L5's own fill
prices** (`analysis/l5/next_side/features.py`: `p_up = px if side == "Up"
else 1 - px`). That is endogenous. "L5 buys the favorite" scored on that
series would be partly true by construction, because the series is built from
the side he chose.

So the primary price series for this lane is **exogenous**: the market-wide
print tape from `data-api.polymarket.com/trades?market=<conditionId>`, with
**L5's own prints dropped by `proxyWallet`**. Both are retained —
`p_up_ex` (counterparty flow only) and `p_up_all` — so the size of the
endogeneity is measured, not assumed.

Limit stated up front: prints are not a book. There is no bid, no ask and no
depth in this source, so the favorite here is a **last-traded** favorite. A
book-based favorite is available only on the Zenodo 1-second L2 overlap
(2026-08-21..24, 12 L5 BTC15m windows, 3,166 fills), which is used as a
smaller validation tier, never as the primary sample.

## 1. Unit of observation

One **transaction**, not one leg. `next_side/features.aggregate_transactions`
established that every multi-leg transaction in this history is single-sided
(6,396 of them, 19.7% of fills), so counting legs inflates same-side
persistence by construction (71.50% raw vs 68.24% per transaction). This lane
reuses that aggregation unchanged.

Scope: **BTC15m only**, 527 windows, 63,569 legs. 5m and ETH are excluded for
the reason `SWITCH_TRIGGER.md` already established — they carry too few
switches to be evidence in either direction, and pooling manufactures
confidence.

## 2. State immediately preceding each action

As-of the last exogenous print strictly BEFORE the action's timestamp. A
print at the same second as the action is excluded, because same-second
ordering is not recoverable from this source and including it risks reading
L5's own effect on the tape back in as his input.

Fields (all from prints 1..k with t_k < t_action):

- `p_up_ex` — last exogenous print mapped to the Up outcome's price;
- `favorite` — the outcome with price > tau (tau fixed per run, see 5);
- `fav_px`, `dist_from_50 = |p_up_ex - 0.50|`;
- `flip_since_prev_action` — favorite differs from the favorite as-of the
  previous action;
- `last_flip_t`, `flip_latency_s = t_action - last_flip_t`;
- `mom_1s/5s/15s/30s` — `p_up_ex(t) - p_up_ex(t - h)`, emitted ONLY when a
  genuine exogenous print exists in that lookback; otherwise `null`. A
  carried-forward price is not momentum and is never substituted for one.
- `bought_favorite`;
- `extended_residual` / `reduced_residual` — sign of the change in
  `|signed residual|` caused by this action;
- `consec_fav_buys`, `consec_fav_qty`;
- `up_qty`, `dn_qty`, `signed_imbalance`, `abs_imbalance` before the action.

Momentum nullability is a first-class result, not a nuisance: if the tape is
too sparse to define 1-second momentum for most actions, that is reported and
the 1s column is not used, exactly as the BTC-descriptor coverage failure was
reported in `REGIME_READING_STATUS.md` rather than filled in.

## 3. Train / held-out boundary — fixed now

**Primary:** chronological, at a window boundary, 70% of windows by window
start to TRAIN and the last 30% to HELD-OUT. This reproduces the convention
of `next_side/NEXT_SIDE_ANSWER.md` (358/154). No window spans both.

**Secondary (preregistered, not a fallback chosen later):** within-era split
of the August windows only, windows assigned by a hash of `window_id` so the
assignment cannot be steered. This exists because the primary split is also
an era change (Mar-Apr -> Aug) and prior art already showed a fitted model
transferring 4 points WORSE than persistence across that boundary. Reporting
both separates "no signal" from "no regime transfer"; neither is allowed to
be presented as the result alone.

Every threshold, every tau, every momentum horizon choice is made on TRAIN.
The held-out windows are scored once per model.

## 4. Models compared (item 6), all preregistered

1. persistence — repeat previous side;
2. inventory rebalance — buy the under-held side;
3. current favorite;
4. favorite + momentum;
5. aggregate inventory model;
6. favorite/momentum + aggregate inventory.

Scored on L5's actual next side. Baseline to beat is **persistence**, which
is already 0.7039 OOS. Size, price and timing are scored separately and only
where the state supports it.

**Survival criterion, fixed now:** FAVORITE/FLIP survives only if a
favorite-bearing model beats persistence on held-out windows by a margin that
exceeds the window-level bootstrap spread of that margin. Matching
persistence to three decimals — which is what the 24-feature logit did
(+0.0003) — is a failure, not a partial success.

## 5. The 50c question

tau is NOT assumed to be 0.50. On TRAIN only: estimate P(buy Up) against
`p_up_ex` on a fine grid and test a discontinuity at 0.50 against a
continuous response, and separately fit tau over a grid. Whatever tau TRAIN
selects is then used unchanged on held-out. The held-out sample is never
swept.

## 6. Economics gate

The shadow controller of item 7 is built ONLY if the behavioral test above
survives on held-out windows. If it does not survive, the economics are not
run, and that is reported as the result.

When it is built: mechanism first, no threshold sweep — favorite -> add,
favorite strengthens -> bounded extend, leadership flips -> switch, repeat,
under aggregate inventory/residual/capital limits, on the clean authenticated
PMUS tape. Pair economics measured at the PORTFOLIO level; the `.96`
immediate-complement requirement is not imposed and is also not altered.
Reported separately: matched inventory P&L, directional residual P&L, fees,
maker rebates, cleanup, max residual, capital employed, net/window, capacity.
The $3/window test and the $5 stretch are evaluated, not optimized toward.

## 7. What this lane may not conclude

- It may not promote a controller.
- It may not claim realized profit of any kind; labels stay
  `historical replay` / `shadow`.
- It may not reuse the held-out windows after seeing their score.
