# Order-by-order reconstruction: what our own legs actually did

Issue **#209 item 1** / PR #208's next-evidence request, part 1 of 5:
*"reconstruct exact order-by-order first/second-leg behavior from
venue-confirmed live windows, separating maker quotes, submissions, ACKs,
fills, cancels, settlement."*

Source: `analysis/l5/issue208/order_lifecycle_reconstruction.py`, reading
`analysis/l5/residual_exit/preserved/*.ndjson` — 991 tape lines, 7 windows,
**15 own (`l5f-`) orders**, 27 shadow coids excluded. Rows written to
`order_lifecycles_20261024.json`. Nothing here is a modelled fill.

---

## 0. The assertion that makes the rest of it trustworthy

Every window reconciles **penny-exact against `L5_LIVE_SETTLED`, both sides**:

| window | ord | fill | canc | our up/dn qty | settled | our up/dn cost | settled | ok |
|---|--:|--:|--:|---|---|---|---|---|
| 0100z | 3 | 2 | 1 | 1.04 / 1.04 | 1.04 / 1.04 | 0.613600 / 0.384800 | same | ✅ |
| 0115z | 2 | 1 | 1 | 0 / 0.96 | 0 / 0.96 | 0 / 0.230400 | same | ✅ |
| 0130z | 2 | 2 | 0 | 1.27 / 1.27 | 1.27 / 1.27 | 0.368300 / 0.711200 | same | ✅ |
| 0145z | 2 | 1 | 1 | 0 / 1.03 | 0 / 1.03 | 0 / 0.360500 | same | ✅ |
| 0200z | 2 | 2 | 0 | 1.22 / 1.22 | 1.22 / 1.22 | 0.427000 / 0.719800 | same | ✅ |
| 0215z | 2 | 1 | 1 | 1.25 / 0 | 1.25 / 0 | 0.387500 / 0 | same | ✅ |
| 0230z | 2 | 2 | 0 | 1.16 / 1.16 | 1.16 / 1.16 | 0.487200 / 0.591600 | same | ✅ |

`resolution_source` is `VENUE_SETTLEMENT_PX` in all seven. A regression test
pins this; it is the thing that would break first if the reconstruction
drifted.

**It caught a defect in itself while being written.** DOWN cost was computed
from the wire price rather than `1 − wire`, so `up_cost` reconciled and
`dn_cost` came back as the complement (0.655200 against the venue's
0.384800). A DOWN leg is a sell of YES resting on the ask; its own cost is
`1 − wire`. Pinned.

---

## 1. THE FINDING: the hold dominates, not the fill

| window | opening leg | its rest | **hold before the completion was even quoted** | completion | its rest | outcome | realized pair cost |
|---|---|--:|--:|---|--:|---|--:|
| 0100z | DOWN 0.3700 | 73.4 s | **56.8 s** | UP 0.5900 | 0.0 s | FILLED | **0.9600** |
| 0115z | DOWN 0.2400 | 13.5 s | **239.0 s** | UP 0.7000 | 17.6 s | cancelled by us | — |
| 0130z | UP 0.2900 | 14.1 s | **111.0 s** | DOWN 0.5600 | 13.3 s | FILLED | **0.8500** |
| 0145z | DOWN 0.3500 | 5.5 s | **30.0 s** | UP 0.6100 | 13.6 s | cancelled by us | — |
| 0200z | UP 0.3500 | 19.6 s | **10.3 s** | DOWN 0.5900 | 7.3 s | FILLED | **0.9400** |
| 0215z | UP 0.3100 | 19.8 s | **49.8 s** | DOWN 0.6300 | 16.1 s | cancelled by us | — |
| 0230z | UP 0.4200 | 7.3 s | **124.7 s** | DOWN 0.5100 | 0.0 s | FILLED | **0.9300** |

Read the two rightmost timing columns together. **A completion that is
allowed to rest fills in 0.0–13.3 s.** The time that actually costs us the
pair is the **10.3–239.0 s** (median 56.8 s) we spend holding one-sided
exposure *before quoting the other side at all*. In a 900-second window,
0115z spent 239 s — over a quarter of the window — holding a filled DOWN leg
with no UP quote resting.

So "pairs do not complete" is **not** a fill-rate problem on the completion
leg. It decomposes into two different problems:

1. **Latency to quote the second leg** — tens to hundreds of seconds, which
   is scheduling, not the market.
2. **Our own cancels** — all three unpaired windows lost their completion to
   our cancel at 13.6 / 16.1 / 17.6 s, each inside the fill-time
   distribution the same tape produces. Every one of the three residuals
   (0.96 DOWN, 1.03 DOWN, 1.25 UP) is self-inflicted. A test pins that no
   non-fill in this set was the venue's doing.

**4 of 7 windows paired**, at realized pair costs of **0.85, 0.93, 0.94 and
0.96** — every one at or inside the 0.96 ceiling, taken from settlement in
own-price terms. On this evidence the ceiling rejected nothing that was
actually executed (item 4 is a separate question about what it rejected
*before* submission, and is not answered here).

---

## 2. Zero partial fills, ever

**11 fills, 11 complete, every one in a single `cum_qty` step.** No order in
this set filled partially.

That is a hard constraint on item 2's "replacement after partial fill": we
have **no venue-confirmed partial fill to calibrate against**. Any
partial-fill replacement logic is therefore unvalidated by data, and a
replay that models partials is modelling a thing we have never observed. It
should be built to be *safe* under partials rather than tuned for them, and
tested against constructed cases that are labelled as constructed.

---

## 3. Two corrections to the existing evidence base

### 3a. The provenance date is wrong on 11 of the 34 orders

`analysis/l5/issue206/own_order_lifecycles.py` attributes 11 orders to
**"0100z-0200z 2026-11-02"**. Those windows exist only in the 2026-**10-24**
tapes. The cited report, `residual_exit/preserved/
TICK_DISTANCE_AND_FILL_LATENCY.md`, names no date at all — it says "from the
preserved tapes", which are `btc15m_20261024_*.ndjson`, and its companion in
the same directory is `BTC15M_20261024_0115Z_EXIT_ANALYSIS.md`. The
2026-11-02 campaign file that does exist,
`analysis/l5/btc15m_campaign_20261102_reconciliation.json`, covers windows
**2215z–2300z** and holds one `l5f-` coid, so it is not the source.

The orders are real and venue-confirmed either way; the date is off by nine
days and would send anyone looking for the source to the wrong campaign.

### 3b. Four venue-confirmed orders were left out

The tapes hold **15** own orders across 0100z–0230z. The table carries 11,
stopping at 0200z and omitting **0215z and 0230z** — 3 further fills and 1
further cancel, including one of the four completed pairs (0230z, at a
realized pair cost of 0.93). The fill-evidence base undercounts the
available venue-confirmed evidence by 4 orders.

### 3c. The published durations mix two clocks, and are inflated

This one changes a number the code is calibrated against, so it is set out
in full.

The quote record (`L5_FILL_CALIB_PRESUBMIT`) carries its own process clock in
`body.t`, agreeing exactly with its own `body.utc`. The venue's lifecycle
events carry **no clock of their own** — their only timestamp is the log
line's, assigned by the collector. And the collector's lag is **large and
not constant**: measured against the quote body's own clock it runs from
**0.029 s to 9.641 s** across these 15 orders.

So any duration measured from the quote *body* to a lifecycle *line* is
inflated by that lag. Checking the 11 published durations against both
definitions:

| definition | how many of the 11 published values it matches |
|---|--:|
| same-clock (ACK line → terminal line) | 4 |
| mixed-clock (quote body → terminal line) | 5 |
| neither | 2 |

The published set is a **mixture of two different quantities**, plus two
values matching neither. On the Kaplan–Meier estimator over these 15 orders:

| duration definition | KM median fill time |
|---|--:|
| same-clock `rest_s` | **14.1 s** |
| mixed-clock | **18.9 s** |

The previously published median was **17.1 s** — between the two, which is
what a mixture produces.

**What this does and does not change.** `REQUOTE_MIN_REST_S = 14.0` in
`src/l5_two_sided_probe.py` derives from its own fit set's median of 14.5 s,
not from 17.1 s, and the corrected same-clock median here is 14.1 s. So the
floor is **not materially affected** and no code change follows from this. It
is the *evidence base* that needs correcting, and any future floor or timer
fitted to these durations must use the same-clock definition.

**`submit → ACK` latency is not measurable from this source.** There is no
submit-request event, the lifecycle events have no clock of their own, and
the collector lag reaches 9.6 s. `quote_to_ack_s` is reported for
completeness and is explicitly not venue latency. An earlier draft of this
work reported 3.5–9.6 s of "ACK latency" and concluded we were joining the
queue seconds late; that was the collector lag, measured by mixing the two
clocks, and it is withdrawn.

---

## 4. What this does not establish

- Seven windows, one day, one series, one policy's choices. Not a fill curve
  for an arbitrary quote.
- 11 fills is a small sample for any rate, and the KM medians above carry
  collector jitter of up to ~9.6 s on individual observations even in the
  same-clock definition, because both ends are collector timestamps.
- Nothing here is profit. Of the four paired windows the settlement reports
  `matched_pnl` separately from residual, and the three unpaired windows left
  real residual exposure. Confirmed settled realized net is unchanged:
  **−$15.6109** all-time, **−$13.4809** rebate-adjusted.
- The other 19 orders in the existing base (2015z 2026-11-06, 1230z
  2026-11-07) have **no raw tape in the repository** and could not be
  reconstructed. They remain hand-transcribed, and the clock finding above
  means their durations should be treated as the mixed-clock quantity until
  their tapes are recovered from log retention.
