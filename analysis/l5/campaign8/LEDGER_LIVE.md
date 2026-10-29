# Campaign 8 — complete live reconciliation

Campaign `camp1.camp-3cOtW8HnrFbY5u2NAkX1QfvhqUhxsChq`, document fingerprint
`d60871dc…`, deployment `7bf2ad39-6cb6-4028-949b-1b3a1fa066de`.
Armed 02:03:08Z, expired 03:26:43Z (11:26:43 PM EDT Wed Oct 28).
Six windows ran: 0200z, 0215z, 0230z, 0245z, 0300z, 0315z.

Every line is tied to `L5_LIVE_CASH.available_cash_usd`
(`/v1/account/balances`), the authoritative series. Baseline `342.01615` at
02:03:10Z; final `343.21615`, unchanged from 03:24:02Z through 05:00:25Z
(1h36m stable, so fully settled).

**Final realized net: +$1.20000.**

Confirmed three independent ways:
- cash: `343.21615 - 342.01615 = +1.20000`
- heartbeat: `utc_day_realized_usd = "1.200000"`
- the fill ledger below, which reproduces *every* intermediate cash reading
  exactly, with no unexplained residue

## Price convention

The wire is YES/UP. `own(UP) = wire`, `own(DOWN) = 1 - wire`. The
`fill_own_px` field in `L5_FILL_BOOK_EVIDENCE` is **mislabeled** — it carries
the *wire* price, not the own-side price. Established in Campaign 7, unchanged
here. All `own` figures below are derived, not read.

Matched pairs release to cash **on match**, within one 60s balance refresh —
not at window settlement. (The `campaign_budget` label "Settlement releases
exposure; matching does not" describes the gate's conservative *exposure*
accounting, not the cash mechanics.) Only unmatched legs wait for settlement.

## Completed pairs

| pair | win | t(leg1) | t(leg2) | gap | own costs | pair cost | qty | net |
|------|-----|---------|---------|-----|-----------|-----------|-----|-----|
| A | 0200z | 02:03:13 | 02:03:50 | 37s | 0.26 + 0.67 | 0.93 | 1.77 | +0.1239 |
| B | 0200z | 02:04:28 | 02:04:56 | 28s | 0.22 + 0.68 | 0.90 | 1.73 | +0.1730 |
| C | 0200z | 02:05:37 | 02:06:01 | 24s | 0.45 + 0.65 | **1.10** | 2.06 | **-0.2060** |
| D | 0200z | 02:06:35 | 02:08:27 | 112s | 0.38 + 0.48 | 0.86 | 2.17 | +0.3038 |
| E | 0215z | 02:15:52 | 02:16:50 | 58s | 0.41 + 0.56 | 0.97 | 2.12 | +0.0636 |
| F | 0230z | 02:30:52 | 02:31:23 | 31s | 0.37 + 0.60 | 0.97 | 2.18 | +0.0654 |
| G | 0230z | 02:32:05 | 02:33:18 | 73s | 0.41 + 0.60 | **1.01** | 2.12 | **-0.0212** |
| H | 0230z | 02:35:55 | 02:36:29 | 34s | 0.21 + 0.76 | 0.97 | 1.45 | +0.0435 |
| I | 0300z | 03:01:28 | 03:05:20 | 232s | 0.30 + 0.58 | 0.88 | 1.82 | +0.2184 |
| J | 0315z | 03:17:29 | 03:21:22 | 233s | 0.42 + 0.38 | **0.80** | 1.96 | +0.3920 |
| K | 0315z | 03:23:17 | 03:23:58 | 41s | 0.30 + 0.76 | **1.06** | 2.30 | **-0.1380** |

11 pairs, **21.68 matched contracts**, **+$1.0184** = +$0.04697 per matched
contract (quantity-weighted mean pair cost **0.95303**, unweighted 0.9500,
median 0.97).

- 3 of 11 priced **above $1.00** (1.01, 1.06, 1.10): **-$0.3652**
- 8 of 11 at or below $1.00: **+$1.3836**
- 6 of 11 priced above the 0.96 spring-discipline target

All six above-0.96 completions were admitted through `completion_pair_rescue`,
which tests the *cumulative average* `eff = cost_after / matched_after <= 1.00`
rather than the marginal pair. Earlier cheap pairs subsidise admission of later
expensive ones.

## Unmatched legs (settlement coin-flips)

| win | side | qty | own | cost | outcome | net |
|-----|------|-----|-----|------|---------|-----|
| 0215z | DOWN | 1.94 | 0.41 | 0.7954 | **WON** +1.94 | **+1.1446** |
| 0230z | UP | 1.02 | 0.21 | 0.2142 | **LOST** 0 | **-0.2142** |
| 0245z | DOWN | 1.92 | 0.39 | 0.7488 | **LOST** 0 | **-0.7488** |

Stranding in **3 of 6 windows**. $1.7584 of cost at risk returned $1.94 →
**+$0.1816**, which is one draw, not an expectancy.

0300z and 0315z both closed with zero residual.

## Per-window decomposition

| window | matched | residual | net |
|--------|---------|----------|-----|
| 0200z | +0.3947 | 0.0000 | **+0.3947** |
| 0215z | +0.0636 | +1.1446 | **+1.2082** |
| 0230z | +0.0877 | -0.2142 | **-0.1265** |
| 0245z | 0.0000 | -0.7488 | **-0.7488** |
| 0300z | +0.2184 | 0.0000 | **+0.2184** |
| 0315z | +0.2540 | 0.0000 | **+0.2540** |
| **total** | **+1.0184** | **+0.1816** | **+1.2000** |

## The decomposition is the finding

| component | total | mean/window | sd | se | t |
|-----------|-------|-------------|-----|-----|---|
| matched-pair economics | +1.0184 | **+0.1697** | 0.1462 | 0.0597 | **2.844** |
| residual settlement | +0.1816 | +0.0303 | 0.6183 | 0.2524 | **0.120** |
| combined | +1.2000 | +0.2000 | 0.6426 | 0.2623 | **0.762** |

The matched component carries essentially all the expectancy and almost none of
the variance. The residual component contributes **+$0.03/window against a
±$0.62 standard deviation** — it is noise with ~4x the dispersion of the signal
it is attached to.

Consequence: stranding does not mainly *lose* money. It destroys the
signal-to-noise ratio, and therefore our ability to measure, validate, and
scale anything. Combined t = 0.76 after six windows; matched-only t = 2.84
after the same six.

Caveats: n=6, one market, one regime, and the decomposition was chosen after
seeing the data. Treat the matched-only t as suggestive, not established. Its
direction and magnitude agree with the independent 12-window follower baseline
(+$0.1886/window).

## Completion patience vs pair cost

`Pearson r(leg gap, pair cost) = -0.70` across the 11 pairs. The three longest
gaps (112s, 232s, 233s) produced the three cheapest pairs (0.86, 0.88, 0.80);
gaps under 45s produced 0.90, 0.93, 0.97, 0.97, 1.06, 1.10.

Part of this is definitional — we complete when the price is good, so a good
price implies we waited. The load-bearing part is that the patient windows
**still completed**: waiting did not forfeit the pair. Against that, 0245z
waited and never completed at all.

This is the same mechanism as the Campaign 7 dwell-time finding from the other
side: quotes rest a median 8s while fills arrive at a median 11s, giving 17%
quote-time coverage. Pairs completed in a hurry are completed at a bad price;
pairs left to rest are completed cheaply or not at all.

## Safety and operations

Clean throughout, nothing near a limit:

- `armed` went `true` → `false` at expiry; no early stop
- `halt_latch = "CLEAR"`, `halt_why = null`, `would_halt = false` for the
  entire campaign — the $5 daily and $20 cumulative gates never engaged
- peak cash deployed **$22.2436** of the $29.30 budget ($7.0564 unused)
- `journal_entries = 32`, `journal_errors = 0`
- max unmatched contracts reached 3 of the 5 cap
- 24 orders filled across 25 fill events (one order filled in two parts)
- no unresolved orders and no open positions at expiry
- `all_time_realized_usd` moved from -10.5112 to **-9.5912**

The 40→72 submission backstop **never bound**. `submissions_spent` read 22 at
02:50Z with four windows complete; no submission-budget refusal occurred. The
post-expiry reading of 142 is **not** this campaign's count — `campaign_id`
goes `null` at expiry and the counter loses its namespace, falling back to the
all-time durable-claim total. Same class of reporting artefact as the Campaign 7
`MAX_LIVE_ORDERS` miscount; do not read a post-expiry `submissions_spent` as a
campaign figure.

## Evidence gap

Railway's decision-log retention rolled off the in-campaign
`L5_LIVE_DECISION` tape before it was captured — only records from 03:25:47Z
onward survive, of which just two fall inside the campaign. **Campaign 8 has no
recoverable decision/refusal classification.** The economics are complete
because cash and fills were captured while the campaign ran; the decision tape
was not.

Operational rule for Campaign 9: capture the decision tape *during* the
campaign, per window, not after it ends.
