# 5m market-state coverage — what it records and how long it must run

`src/l5_5m_coverage/recorder.py` · READ ONLY · no order path, no decision, no
state. Built, tested, **not deployed** — deploying needs authorization.

## Why a recorder and not a 5m switch classifier

BTC15m: OOS AUC 0.674 on 446 test rows / 196 switches — usable.
ETH15m 5 test switches, ETH5m 4, BTC5m 1 — all below the pre-fixed ≥40-row and
≥30-per-class threshold. Fitting a 5m rule on that borrows BTC15m behaviour
under a 5m label. This records the states that would let 5m answer for itself.

**States where L5 does nothing are the point.** A switch model needs CONTINUE
examples; a recorder that wakes on a fill produces a dataset with no negatives.

## Per sample, every field the spec named

both outcome prices (`up_bid/up_ask/down_bid/down_ask`) · `spread` · depth at
touch · `ret_5s/15s/30s/60s` · `tte_s` · L5 inventory (`inv_up/inv_dn`) ·
`matched_qty` · `residual_abs` **and** `residual_relative` ·
`attainable_pair_cost` · `delta_attainable_pair_cost` · flow
(`flow_buy_qty/flow_sell_qty`) · `l5_acted`.

Every unavailable component is `None` beside its own `_reason`. Never a
fabricated 0 — a 0 spread reads as a locked market, a 0 depth as an empty one,
and a 0 pair cost as the cheapest pair obtainable.

Capture runs `T − 120 s` through settlement, at 1 Hz.

## The Gemini defect cannot recur here, by construction

That collector lost 82 s of every window because discovery and subscription
were fused to one 45 s REST tick, and the 600 s session period was an exact
multiple of the 300 s window period, so the loss phase-locked instead of
averaging out. Here windows are **enumerated from the clock** — 5m windows sit
on fixed 300 s epochs, so there is nothing to discover — the roll runs every
loop iteration, and `first_sample_lead_s` is in the per-window digest so a
regression shows up there rather than being reconstructed later.

## How long it must run

Test fraction 0.30, the chronological split this program uses.

| cohort | windows/day | switch% | switch/day | continue/day | days → 30 OOS SWITCH | days → 30 OOS CONTINUE |
|---|---|---|---|---|---|---|
| BTC5m | 48.7 | 68.2% | 33.2 | 15.5 | 3.0 | **6.4** |
| ETH5m | 152.0 | 55.3% | 84.1 | 67.9 | 1.2 | **1.5** |

**≈1 week of capture clears both cohorts**, BTC5m CONTINUE binding at 6.4 days.

Two honest caveats. These are **window** counts; a switch model is fitted on
decision states and a 7-fill window contributes several, so this is a floor on
the evidence, not a ceiling — but ≥30 was stated in windows, so windows are what
is reported. And the rates are L5's **March–August** behaviour; if his current
5m activity has thinned, the recorder collects proportionally less and the
schedule stretches. That is measurable on day one and should be checked then
rather than assumed now.
