# Campaign 6 — reconciliation and readiness evidence

Deployed SHA `d096ef7d`. Four authorized BTC15m windows on 2026-10-28:
`1545z`, `1600z`, `1615z`, `1630z`. Limits as authorized: $20 exposure,
$12 cash envelope, $5 daily / $20 cumulative loss halts, $3 residual
budget, one resting order, maker-only, `L5_COMPLETION_REQUOTE=1` as the
single behavioral change.

Everything below is re-derivable from this directory with no Railway
access. `raw/` holds the exact captures; `extract_tape.py` turns them into
the tapes; `reconcile.py` and `adverse_selection.py` read only the tapes.

```
python3 -I analysis/l5/campaign6/extract_tape.py
python3 -I analysis/l5/campaign6/reconcile.py          # exits 0 iff cash agrees
python3 -I analysis/l5/campaign6/adverse_selection.py
```

## Settled result

| Window | Submitted | Filled | Pairs | Pair costs | Residual | Net |
|---|---|---|---|---|---|---|
| 1545z | 0 | 0 | 0 | — | 0 | $0.0000 |
| 1600z | 8 | 6 | 3 | 0.99, 0.87, **1.05** | 0 | +$0.1818 |
| 1615z | 5 | 2 | 1 | 0.55 | 0 | +$0.9045 |
| 1630z | 6 | 4 | 2 | 0.85, 0.90 | 0 | +$0.5045 |
| **Total** | **19** | **12** | **6** | — | **0** | **+$1.5908** |

Cash $339.75245 → $341.34325. `reconcile.py` ties the six pairs to that
delta exactly and exits 0.

**Decomposed as the owner directed:**

| | |
|---|---|
| pair edge (pairs ≤ $1.00) | **+$1.6878** |
| defensive loss (pairs > $1.00) | **−$0.0970** |
| realized net | **+$1.5908** |
| residual / stranded loss | **$0.00** |
| actual fees | **$0.0000** |
| rebate estimate / credited | $0.1048 / **$0** |

The venue's `matched_pnl` line reports one number and folds the defensive
loss into it. `pair_economics` in `l5_window_accounting.py` now separates
them in the live report.

## Verification 1 — the 12th fill is in the counters

**Was:** 11 fills / 21.67 contracts. **Is:** 12 / 23.82.

`l5f-4fad3a63e74ca7685254363a` (1630z DOWN 2.15 at wire 0.4900) filled in
the microseconds between the requote's cancel being sent and the venue
answering. Its only `L5_LIVE_LIFECYCLE` row read `cum 0, delta 0`; the fill
appeared as `L5_FILL_CALIB_FILL` and in the cancel's own `filled[]`.
`fill_events()` counts positive lifecycle deltas, so it scored zero, and
`order_outcomes` read `qty_filled` as zero with it.

Money was never wrong — the pair matched, residual was zero, cash moved —
but the counters a campaign is judged repeatable by were.

Fixed in the reporting layer only, so execution is untouched:

- `merge_fill_sources(lifecycle, fills)` merges the venue's fill-record
  stream, deduplicated on cumulative quantity. Idempotent; omitting `fills`
  reproduces the old counts exactly.
- `_window_fills(runner, market)` in the runner reads the same
  `FillCalibration.orders` fold `_window_submits` already uses, so the two
  cannot disagree about window membership, and never raises into reporting.

Tests: `tests/test_l5_race_fill_and_defensive.py` (14),
`tests/test_l5_race_fill_wiring.py` (6) — partial top-up, both producer key
shapes, idempotence, junk rows, broken recorder, shadow-id exclusion, and
the real 1630z shape end to end.

## Verification 2 — the six pairs and zero residual stand independently

`reconcile.py` re-derives from the tape: 6 pairs, pair costs 0.99 / 0.87 /
1.05 / 0.55 / 0.85 / 0.90, residual UP=0 DOWN=0 in all three trading
windows, net +$1.5908, and `cash agrees with pairs YES`.

Own vs wire is applied throughout: a matched pair redeems exactly $1.00, so
pair cost is the sum of OWN prices, and the venue quotes DOWN as
`1 - own`.

**A labelling trap found while building the tape.** 37 `l5f-`-prefixed
`L5_FILL_CALIB_PRESUBMIT` records exist, but only 19 were transmitted. The
other 18 are priced candidates in `1645z` — a **fifth** window. None was
sent: no ACK, no lifecycle row, no terminal, no fill, no cash movement, and
`1645z` never appears in the inventory ledger. `tape_arming.jsonl` shows
why — four armings with `windows_spent` 0→1→2→3 of budget 4, and no fifth.
The claim budget refused it structurally.

A PRESUBMIT with a live-looking coid is therefore **not** evidence of a
live order. `extract_tape.py` marks each row `transmitted` on whether the
venue ever answered it.

## Verification 3 — restart timing

| Window | Opened | Armed | Latency |
|---|---|---|---|
| 1545z | 15:45:00 | 15:47:54 | **174.1 s** |
| 1600z | 16:00:00 | 16:00:03 | 3.3 s |
| 1615z | 16:15:00 | 16:15:00 | 0.8 s |
| 1630z | 16:30:00 | 16:30:02 | 2.9 s |

Root cause of the 174 s, in order:

1. The venue's **REST** book route 404s persistently for BTC15m — 3 of 3
   attempts failed in `1545z` *and* in `1600z`.
2. `_ws_book_substitute` exists for exactly that, but requires
   `subscribed_before_open is True`.
3. The Campaign 6 deploy restarted the process at 15:45:42, **42 s after
   `1545z` opened**. A process that boots inside a window cannot have
   subscribed before that window opened, so the fallback was correctly
   refused and preflight could only pass if REST happened to answer. It
   did, once, on attempt 7.

`1600z` passed on attempt 1 in 3.29 s with the same three REST 404s,
because the socket had subscribed before 16:00:00.

The guard is correct and must not be relaxed: a late-subscribed socket
really is a partial view. The fault was restarting into a live window.

**Procedure for Campaign 7.** Deploy, then let the process run across at
least one full window boundary before arming. Before arming, confirm from
the live preflight record that `subscribed_before_open: true`, the verdict
is PASSED on attempt 1, and `t_in_window_s < 10`. Arm only then. Observed
baseline when the process spans the boundary is 0.8–3.3 s.

## Verification 4 — opening adverse-selection exposure

`adverse_selection.py`, over all six pairs. For each, the complement price
available when the opening leg was submitted against the complement price
actually paid. Positive drift is adverse.

| Window | Open | Own | Comp @open | Comp paid | Realised | Drift | Wait |
|---|---|---|---|---|---|---|---|
| 1600z | DOWN | 0.36 | 0.59 | 0.63 | 0.99 | **+0.0400** | 41 s |
| 1600z | DOWN | 0.45 | 0.50 | 0.42 | 0.87 | −0.0800 | 74 s |
| 1600z | DOWN | 0.41 | 0.55 | 0.64 | **1.05** | **+0.0900** | 28 s |
| 1615z | DOWN | 0.46 | 0.48 | 0.09 | 0.55 | −0.3900 | 338 s |
| 1630z | DOWN | 0.40 | 0.55 | 0.45 | 0.85 | −0.1000 | 368 s |
| 1630z | UP | 0.39 | 0.55 | 0.51 | 0.90 | −0.0400 | 27 s |

2 adverse, 4 favourable, mean drift **−0.0800**, one pair realised above
$1.00.

**The $1.05 pair.** Planned 0.41 + 0.55 = **0.9600** — exactly at the
opening ceiling, with zero headroom. The DOWN leg then rested while UP ran
0.56 → 0.66 in ~35 s, and it was taken *because* the complement was running
away. Realised 1.05. No gate failed: the 0.96 ceiling is evaluated when the
plan is built, and has no say over what the complement costs by the time a
passive leg is hit.

Two structural observations, both opening-quality rather than completion:

- **Openings placed at the ceiling have no buffer.** The only pair that
  breached $1.00 was the one planned at exactly 0.9600. The adverse drifts
  (+0.04, +0.09) are ordinary in size; what made one of them cross was
  starting with nothing in hand.
- **Short completion waits cluster with adverse drift** (28 s and 41 s for
  the two adverse; 74–368 s for three of four favourable). A complement
  that fills quickly is one the market came to, which is the same event as
  the pair getting dearer.

**What this does not show.** The repricing policy did not cause Campaign
6's profit. It fired six times, all cancel-confirmed, and three replacement
chains ended in a fill — demonstrated execution behavior. But every
replacement that filled did so *cheaper* than the order it replaced while
the policy's own target was *dearer*, so the gain came from the market
moving between cancel and replace. The favourable drift tail above is where
the edge came from, and it is a property of the market in these windows,
not an effect attributable to repricing. Six firings measure mechanism, not
causal profitability.
