# BTC5m favorite accumulation — diagnosis and expected net economics

Date: 2026-11-12. Service `l5-btc5m-live` (project `arb-ev-wallet-shadow`).
Deployed SHA at the time of writing: `ee5f4e8027cedfe5cb6301711e215f3397a42ee4`
(branch `integration/l5-safe-release-candidate-20261109`, deployment
`69acea6a-c051-4c05-baf5-7dd3ea64c755`, 2026-11-12 08:05Z, SUCCESS).

**Nothing in this document is realized money.** No order of this strategy
has ever filled. The economics below are a replay of the real controller
over recorded public books, bounded by two fill rules, and the headline is
not statistically distinguishable from zero.

---

## A. CURRENT RUNNING STATE

| | |
|---|---|
| service | `l5-btc5m-live`, restart policy NEVER, one approved window then idle |
| deployed SHA | `ee5f4e80` |
| mode | armed transport in the last three windows; **zero fills, ever** |
| live orders | none |
| controller entry rule before this change | `EDGE_GATED` |
| aggregate exposure ceiling | **$0.45** (unchanged by this work) |

## B. WHY THE OVERNIGHT CAMPAIGN PRODUCED ONE ORDER

From the service's own `L5_BTC5M_START` / `L5_BTC5M_SESSION_REPORT` /
`L5_BTC5M_STEP` records, 2026-11-12:

| window | preflight | armed | submitted | filled | what stopped it |
|---|---|---|---|---|---|
| 03:45z | FAILED | no | 0 | 0 | `cryptography` / `websockets` absent from the image; every authenticated call raised `ModuleNotFoundError`. 3 decisions recorded as `would_submit`, nothing sent |
| 05:00z | PASSED | yes | **1** | 0 | the one order of the campaign; cancelled at the close, never filled. Most steps had `bid: null`, i.e. a one-sided book |
| 06:30z | PASSED | yes | 0 | 0 | **every step `EV_BELOW_MIN`** with `room_usd` ≈ $0.41 of the $0.45 cap unused |
| 08:00z | PASSED | yes | 0 | 0 | same |

Five causes, in order of how much throughput each cost:

1. **The edge gate was arithmetically unreachable** (primary). An entry was
   admitted only when `p_side − price − 0.06 ≥ 0.01`. The entry rested
   `depth_ticks = 2` under the favorite's bid and `p_side` came from the
   book mid, so on a one-cent book the test reduces to
   `half_spread + 0.02 ≥ 0.07`, i.e. the entry had to sit about **seven
   cents** under the bid. A 5-minute PMUS book does not offer that. The
   06:30z record shows it exactly: entry priced 0.68, qty 0.14, room
   $0.4080, refused.
2. **One window per deployment.** The runner trades the single window named
   in `L5_BTC5M_MARKET` and then idles for ever ("never chooses another
   window"). The "overnight campaign" was therefore four windows of which
   three were armed — not a night of 5-minute windows.
3. **One entry at a time.** `ENTRY_ALREADY_RESTING` vetoed a second entry
   even with $0.35 of the cap free, so there was no accumulation available
   even if the gate had passed.
4. **Resting two ticks under the bid.** Behind the entire visible queue in
   a 300-second window: the rare fill is the maximally adversely selected
   one.
5. **The eligible band was judged on the touch, not on the price paid.** A
   touch of 0.52 paid 0.50 and was admitted; a touch of 0.91 paid 0.89 and
   was refused. The contract speaks about the price paid.

A sixth, hidden in the executor rather than the controller, was found by
the new acceptance tests: a second identical clip hashed to the **same**
client order id and was refused `DUPLICATE_INTENT`. Accumulation could not
have worked without fixing the order identity (see `coid(..., slot=)`).

## C. WHAT CHANGED

`src/l5_btc5m_controller.py` gains a second entry rule, `ACCUMULATE`,
selected by `L5_BTC5M_ENTRY_MODE`. Unset — the shipped default — keeps
`EDGE_GATED` byte-for-byte, and the 506 pre-existing BTC5m tests pass
unchanged. In `ACCUMULATE`:

* the entry joins the favorite's own bid (`accumulate_depth_ticks = 0`);
* eligibility is `$0.51 ≤ price ≤ $0.90` **inclusive, on the price paid**;
* there is **no edge gate**; `ev_per_share` is still computed and reported
  on every decision as evidence and blocks nothing;
* up to `max_resting_entries` clips rest at once
  (`L5_BTC5M_MAX_RESTING_ENTRIES`, default 2, hard-bounded at 8);
* a resting entry is requoted on **any** tick of drift from the bid;
* `hedge_enabled = False` and `reserve_mode = "NONE"`: no completing order,
  no pair formation, no L5 replication, and the whole $0.45 is available to
  entries rather than part of it being reserved for a completion;
* adding to a side stops the moment it is no longer the favorite, and
  anything resting on the other side is cancelled.

Unchanged and still binding in both modes: the $0.45 aggregate ceiling, the
account-wide cap, the shared halts, the watchdog halt record, startup
reconciliation, the arm token, and the per-order authorization claim.
Maker-only; taker remains disabled.

## D. EXPECTED NET ECONOMICS

**Method.** The real `l5_btc5m_controller.decide` in `ACCUMULATE` mode,
$0.45 cap, $0.10 clip, stepped every 5 s to the 270 s entry deadline, over
36 recorded PMUS BTC5m windows (09:00Z–11:55Z, 2026-11-12, 4,976 public
quotes from service `l5-btc5m-public-recorder`); 34 windows resolved from
the venue's own terminal quote. Script: `replay.py`; output:
`replay_output_20261112.txt`; tape: `pmus_btc5m_quotes_20261112.jsonl`.

**Fills are bounded, not estimated.** The tape carries quotes only — no
prints, no depth — so a maker fill cannot be observed. Two rules, reported
side by side and never averaged:

* **ADVERSE** — filled when the side's own bid falls *below* our price. We
  are filled exactly as what we just bought gets cheaper.
* **FAVOURED** — ADVERSE plus filled when the bid *stays* at our price for
  10 s. Assumes front of queue; an explicit upper bound.

**Fees.** Maker commission is **zero**, read back from the venue
(`commissionNotionalTotalCollected` = 0.0000 on every order of the
overnight campaign), not assumed. Carried through the arithmetic anyway.

### Net by entry-price bucket (ADVERSE rule)

| price paid | fills | shares | paid $ | win rate | breakeven | adv. sel./share | net $ |
|---|---|---|---|---|---|---|---|
| 0.51–0.54 | 18 | 3.34 | 1.7507 | 0.278 | 0.524 | **−0.2457** | **−0.8207** |
| 0.55–0.59 | 25 | 4.24 | 2.4146 | 0.802 | 0.569 | +0.2324 | +0.9854 |
| 0.60–0.64 | 19 | 2.98 | 1.8338 | 0.577 | 0.615 | −0.0382 | −0.1138 |
| 0.65–0.69 | 20 | 2.85 | 1.9298 | 0.800 | 0.677 | +0.1229 | +0.3502 |
| 0.70–0.74 | 11 | 1.51 | 1.0743 | 0.907 | 0.711 | +0.1958 | +0.2957 |
| 0.75–0.79 | 10 | 1.24 | 0.9529 | 0.806 | 0.768 | +0.0380 | +0.0471 |
| 0.80–0.84 | 17 | 2.02 | 1.6452 | 0.941 | 0.814 | +0.1261 | +0.2548 |
| 0.85–0.89 | 6 | 0.66 | 0.5786 | 1.000 | 0.877 | +0.1233 | +0.0814 |
| 0.90 | 1 | 0.11 | 0.0990 | 1.000 | 0.900 | +0.1000 | +0.0110 |
| **all** | **127** | **18.95** | **12.2789** | **0.706** | **0.648** | **+0.0576** | **+1.0911** |

FAVOURED gives +$0.7917 over the same windows (+$0.0417/share). The truth
is between the two and nearer ADVERSE.

### Throughput and capital

| | ADVERSE |
|---|---|
| entries placed | 288 over 34 windows (**8.5 per window**, against 1 order in 4 armed windows live) |
| fill rate | 44.1% of placements |
| capital filled per window | mean $0.361, max $0.395 of the $0.45 cap |
| binding constraint | `NO_EXPOSURE_ROOM` on 712 of ~1,770 decisions — **the cap, not the opportunity** |
| other refusals | `OUT_OF_BAND` 419, `BOOK_UNUSABLE` 263, `BOOK_STALE` 83, `MAX_RESTING_ENTRIES` 5 |

### The honest uncertainty

| quantity | estimate | s.e. | t | 95% CI |
|---|---|---|---|---|
| net per window | **+$0.0321** | 0.0319 | **1.01** | **[−$0.031, +$0.095]** |
| net per filled share | +$0.0672 | 0.0369 | 1.82 | [−$0.005, +$0.140] |

**Profitability is not established.** The per-window mean is one standard
error from zero. Two further reasons not to read the headline as an edge:

1. **The sample is directionally biased.** 24 of the 34 windows settled
   DOWN. BTC fell through this 3-hour block, and "the favorite keeps
   winning" flatters a trending tape. A mean-reverting block would do the
   opposite to the same policy.
2. **The band-floor sweep is non-monotone**, which is the signature of
   noise, not structure: net/window is +0.0321 at a $0.51 floor, +0.0766 at
   $0.55, **+0.0052** at $0.60, +0.0301 at $0.65, +0.0339 at $0.70,
   +0.0183 at $0.75. Nothing here supports choosing a floor, and picking
   $0.55 because it prints best on 34 windows would be fitting noise.

What the sample *does* support, because it is a large effect on the full
set rather than a bucket difference:

* **The $0.06 adverse-selection penalty the old gate charged is not
  visible.** Measured over all 127 fills, the favorite bought at the bid
  won *more* often than its price implied (+$0.058/share, ADVERSE). The
  parameter that blocked every order overnight was borrowed from a
  15-minute proxy and this 5-minute evidence does not reproduce it. It is
  right that it no longer gates entry; it is reported instead.
* **The bucket adjacent to the coin flip is the worst one** (0.51–0.54:
  win rate 0.278 against 0.524 paid, −$0.82 of a +$1.09 total, on 18
  fills). Mechanistically that is what one expects — near $0.50 the
  "favorite" carries almost no information and the fill is pure adverse
  selection — but 18 fills do not settle it.

Clip size: $0.15–$0.22 replayed slightly better than $0.10 (+$0.053 and
+$0.056 per window against +$0.032), consistent with fewer, larger clips
wasting less of a $0.45 cap on requote churn. Also inside the noise.

## E. BOTTLENECK

Not order count any more — it is **evidence**. 34 windows of one
contiguous, directionally biased 3-hour block cannot separate this policy
from zero. The next measurement that changes a GO/BLOCKED decision is a
**read-only shadow of `ACCUMULATE` over several hundred windows spanning
both trending and mean-reverting blocks**, recording every decision, its
price bucket, and the settled outcome. The recorder service that produced
this tape already runs; the shadow needs no new money path.

A second, operational bottleneck is a hard limit this work did not touch:
`L5_LIVE_AUTH_MAX_OPEN` on `l5-btc5m-live` caps OPEN-class submissions per
campaign. Its value is not readable from here. Accumulation needs roughly
6–10 OPEN submissions per window; if that variable is set for the old
one-entry pair strategy it will throttle accumulation regardless of the
controller. **It is an owner-set authorization control and was deliberately
left alone.**

## F. WHAT IS NOT CLAIMED

* No fill, no settlement, no realized P&L for this strategy — ever.
* The replay's fills are bounds from a quote tape, not observed fills.
* Queue position is unobservable from public data and is not modelled.
* The band floor, the clip size and the resting limit are **not** tuned
  here; the shipped defaults are the owner's contract values.
* `analysis/l5/btc5m_cap_policy_replay.py`, cited in the controller's
  `completion_reserve` docstring as the source of the 42-window cap-policy
  measurement, is **not present in the repository**. That citation is
  unverifiable from this tree.
