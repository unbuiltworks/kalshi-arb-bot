# Gemini BTC5m — MAKER TOXICITY SEPARABILITY (Lane 3)

**Label** `GEMINI_MAKER_TOXICITY_SEPARABILITY_LANE3_V1`
**Script** `scripts/gemini_maker_toxicity.py` (sha256 `a0eba3fc0fdbb5b2e0bfb51b784a063ddb52c88eff9c85e7677b14e6f1d416d1`)
**JSON** `analysis/gemini/updown/MAKER_TOXICITY.json`
**Status** RESEARCH ONLY. No live orders, no venue writes, no credentials. Authorizes nothing and opens no venue surface.

## VERDICT: SEPARATION_OF_DEGREE_ONLY__NO_NON_TOXIC_STATE — MAKER HYPOTHESIS **KILLED**

10698 evidenced hypothetical fills across 46 distinct windows. 93.7% of fills are toxic. Some pre-fill features DO order states by how badly the fill turns out -- but EVERY bin of EVERY feature has an adverse-selection interval entirely BELOW zero. The least-toxic bin found anywhere is touch_depth=LOW at -0.10965 USD per contract (CI [-0.16047, -0.0852]), still strictly negative. Pre-fill information here ranks states from bad to worse and never identifies one worth supplying liquidity into, so toxic-versus-non-toxic is not a separation that exists to be made. The features that order the loss are additionally confounded with the settlement direction of the few windows behind each bin (each bin's UP=yes share is reported), which is exactly the base-rate trap, so even the ordering is weaker than it looks.

**Recommended action.** KILL the BTC5m maker hypothesis. No surviving variant is proposed and no cancellation threshold is swept: a cancel rule cannot help, because there is no non-toxic state for it to stay resting in.

Fill scarcity is **not** the binding constraint here. That was the open
possibility going in — the frozen study found 0-for-7 — and it is now
answered: on a 1 Hz candidate grid with the same strict evidence standard the
tape supplies **10,698 evidenced hypothetical fills across
46 distinct windows**. The question was answerable, and the
answer is negative.

## The question this answers

Conditional on information available **strictly before** the hypothetical fill,
is the distribution of post-fill outcomes different for toxic versus non-toxic
maker states? The premise under test was that Gemini BTC5m's extraordinary
median touch spread (~$0.24–0.29) might be payable without inheriting all of
the adverse selection.

It is not. The spread is not a gift; it is the market's price for information
we do not have. The spread's own width is in fact the single strongest
*predictor of worse* outcomes (see `spread` below): wide-spread states are
precisely the states where the fill goes against us hardest.

**This is not a cancellation-threshold sweep.** No cancel rule is proposed,
swept or tuned anywhere in the script. A cancel rule could not help in any
case, for the reason the verdict gives: there is no non-toxic state for a
quote to remain resting in.

## What was reused, unchanged, from the frozen studies

`scripts/gemini_btc5m_maker_study.py` (`267ef1d15944feb49c5e25c897e61064e446f9d5a5e4ce7d7a33ac965f763a63`)
and `scripts/gemini_btc5m_state_aware_maker.py` (`513eff6e…`)
were **read, not modified**. Copied in substance:

- fill-evidence standard (TRADE_THROUGH only, aggressor side from buyer_is_maker, cumulative size must clear the queue ahead; NO touch=fill)
- placements 25/50/75% through the spread, snapped away from aggression
- conservative queue (behind all better-or-equal displayed size, plus our own)
- fee model, with NO rebate assumed or credited
- unsettled windows excluded from the denominator

Specifically on fill evidence — the standard an earlier version of this
analysis got wrong — a resting quote is credited with a fill **only** when a
later print lands at or through its price **and** `buyer_is_maker` puts the
aggressor on the side that could actually have taken it (True = the buyer
rested and a seller crossed, so the print can hit a resting **bid** and not a
resting ask) **and** cumulative qualifying size exceeds the queue ahead of us
plus our own contract. **No touch = fill.** A print whose side cannot be
determined is not evidence. The frozen study's weaker `BOOK_EVOLUTION` tier is
deliberately **not** used: a separability test on outcomes must not be fed
fills that were never evidenced.

## What is new, and why the fill count is so much larger

1 Hz p_snapshot books, minimum 1.0s apart -- deliberately far denser than the frozen study's eight EARLY_FROM_T0 checkpoints, so that fill scarcity is measured at its most generous

a hypothetical quote is left resting from its state until the window's expiry and is never cancelled or repriced. This is the MOST GENEROUS fill assumption available and is why the fill count far exceeds the frozen study's 0-for-7: that study quoted only at eight EARLY_FROM_T0 checkpoints, whereas this grid is 1 Hz. The evidence standard itself is unchanged. Note also that an inside-spread placement has little or no displayed size ahead of it, so the conservative queue is usually just our own contract -- the fills are cheap to earn and still lose money.

Median time to fill: **86.69s**.

## External BTC price

NOT AVAILABLE and therefore OMITTED. The lead/lag feed probe rejected all four candidate venues, so no external spot or futures series exists for this tape. The only reference used is the venue's own authoritative index_price (raw.I == LRRR_XBT).

## Sample

| | |
|---|---|
| Tapes | 7 files (union of the 14\*, 17\* and 19\* tapes) |
| Tape rows | 200,382 |
| BTC05M trade prints | 374 |
| `index_price` points (LRRR_XBT) | 32,058 |
| BTC05M markets seen | 80 |
| excluded — sealed OOS (outcomes never read) | 6 |
| excluded — probe-contaminated | 10 |
| excluded — unsettled (**not** counted as unfilled orders) | 2 |
| **windows used** | **62** |
| quotable states | 8,494 |
| hypothetical quotes placed | 25,464 |
| **evidenced hypothetical fills** | **10,698** (42.0123%) |
| distinct windows with a fill | 46 |
| fills by placement | {'p25': 3468, 'p50': 3579, 'p75': 3651} |

Sealed OOS tickers remain unread: BTC05M2610051845, BTC05M2610051850, BTC05M2610051855, BTC05M2610051900, BTC05M2610051905, BTC05M2610051910.

## Unconditional post-fill outcome

| | |
|---|---|
| Adverse selection given fill (settlement payout − price − fee) | **-0.2380 USD/contract** |
| Window-level bootstrap CI95 | **[-0.2809, -0.1925]** |
| Fills / distinct windows behind it | 10,698 / 46 |
| Share of fills that are toxic (AS < 0) | **93.7%** |

No rebate or liquidity reward is assumed, modelled or credited anywhere. Both
the maker rate (0.0175·q·p·(1−p)) and the taker rate (0.07·q·p·(1−p)) are
**fees**.

## Per-feature separability

Bins are **predeclared terciles of the pre-fill feature over all placed
states** — not over the fills, and not chosen by looking at outcomes. Heavily
tied features (aggressor flow, in a venue printing a handful of trades per
window) fall back to a predeclared sign split (<0 / =0 / >0). Every interval
is a **window-level bootstrap with windows resampled whole**, because 1 Hz
observations inside one five-minute window are not independent trials. `w=` is
the distinct-window count behind the cell; `UP=yes` is the settlement-direction
share, the base-rate confound.

| feature | separates? | best−worst | CI95 (window bootstrap) | | bins |
|---|---|---|---|---|---|
| `aggr_imbalance_30s` | no | +0.0191 | [-0.1033, +0.1101] | spans 0 | LOW (< 0): fr 42.5%, n=1249, w=22, AS -0.243 CI [-0.324, -0.135], UP=yes 0.5677<br>MID ([0, 1e-12)): fr 42.3%, n=8676, w=46, AS -0.236 CI [-0.284, -0.188], UP=yes 0.3706<br>HIGH (>= 1e-12): fr 38.7%, n=773, w=15, AS -0.255 CI [-0.329, -0.133], UP=yes 0.5304 |
| `aggr_imbalance_60s` | no | +0.0171 | [-0.0779, +0.1023] | spans 0 | LOW (< 0): fr 42.1%, n=2035, w=23, AS -0.249 CI [-0.314, -0.164], UP=yes 0.5214<br>MID ([0, 1e-12)): fr 42.9%, n=7406, w=46, AS -0.233 CI [-0.288, -0.178], UP=yes 0.3642<br>HIGH (>= 1e-12): fr 37.6%, n=1257, w=17, AS -0.250 CI [-0.310, -0.169], UP=yes 0.4582 |
| `aggr_imbalance_signed_frac_30s` | no | +0.0115 | [-0.1297, +0.1462] | spans 0 | LOW (< 0): fr 42.5%, n=1249, w=22, AS -0.243 CI [-0.324, -0.131], UP=yes 0.5677<br>MID: no fills<br>HIGH (>= 1e-12): fr 38.7%, n=773, w=15, AS -0.255 CI [-0.328, -0.139], UP=yes 0.5304 |
| `aggr_imbalance_signed_frac_60s` | no | +0.0010 | [-0.1041, +0.1007] | spans 0 | LOW (< 0): fr 42.1%, n=2035, w=23, AS -0.249 CI [-0.314, -0.167], UP=yes 0.5214<br>MID: no fills<br>HIGH (>= 1e-12): fr 37.6%, n=1257, w=17, AS -0.250 CI [-0.310, -0.166], UP=yes 0.4582 |
| `aggr_volume_30s` | not testable | degenerate or too-sparse feature; no distinct tercile edges exist | | | | |
| `aggr_volume_60s` | not testable | degenerate or too-sparse feature; no distinct tercile edges exist | | | | |
| `lrrr_abs_delta_15s` | no | +0.0434 | [-0.0414, +0.1196] | spans 0 | LOW (< 2.6827): fr 40.1%, n=3398, w=39, AS -0.254 CI [-0.318, -0.187], UP=yes 0.4023<br>MID ([2.6827, 6.7163)): fr 40.7%, n=3447, w=44, AS -0.251 CI [-0.302, -0.192], UP=yes 0.4221<br>HIGH (>= 6.7163): fr 45.3%, n=3841, w=38, AS -0.211 CI [-0.256, -0.165], UP=yes 0.3936 |
| `lrrr_delta_15s` | no | +0.0666 | [-0.0074, +0.1341] | spans 0 | LOW (< -3.6993): fr 45.4%, n=3853, w=33, AS -0.203 CI [-0.252, -0.151], UP=yes 0.143<br>MID ([-3.6993, 1.5642)): fr 40.5%, n=3431, w=38, AS -0.245 CI [-0.311, -0.170], UP=yes 0.3623<br>HIGH (>= 1.5642): fr 40.1%, n=3402, w=40, AS -0.270 CI [-0.316, -0.213], UP=yes 0.7466 |
| `lrrr_delta_30s` | YES (degree only) | +0.0822 | [+0.0088, +0.1551] | excludes 0 | LOW (< -7.3155): fr 45.2%, n=3825, w=29, AS -0.188 CI [-0.239, -0.129], UP=yes 0.1294<br>MID ([-7.3155, 2.754)): fr 42.6%, n=3609, w=39, AS -0.261 CI [-0.324, -0.184], UP=yes 0.3738<br>HIGH (>= 2.754): fr 38.4%, n=3252, w=39, AS -0.270 CI [-0.312, -0.217], UP=yes 0.7657 |
| `lrrr_delta_5s` | no | +0.0629 | [-0.0098, +0.1289] | spans 0 | LOW (< -1.2333): fr 45.4%, n=3846, w=33, AS -0.206 CI [-0.254, -0.151], UP=yes 0.1422<br>MID ([-1.2333, 0.5712)): fr 38.4%, n=3261, w=40, AS -0.242 CI [-0.310, -0.161], UP=yes 0.3677<br>HIGH (>= 0.5712): fr 42.2%, n=3582, w=41, AS -0.269 CI [-0.310, -0.218], UP=yes 0.7225 |
| `mid_accel_15s` | no | +0.0606 | [-0.0218, +0.1410] | spans 0 | LOW (< -0.025): fr 42.4%, n=3320, w=44, AS -0.264 CI [-0.306, -0.214], UP=yes 0.4292<br>MID ([-0.025, 0.035)): fr 43.6%, n=3421, w=43, AS -0.203 CI [-0.275, -0.136], UP=yes 0.3347<br>HIGH (>= 0.035): fr 42.8%, n=3670, w=43, AS -0.251 CI [-0.297, -0.202], UP=yes 0.4417 |
| `mid_accel_5s` | no | +0.0268 | [-0.0513, +0.1015] | spans 0 | LOW (< -0.005): fr 41.0%, n=3315, w=46, AS -0.248 CI [-0.290, -0.205], UP=yes 0.4377<br>MID ([-0.005, 0.01)): fr 43.9%, n=3490, w=42, AS -0.221 CI [-0.288, -0.156], UP=yes 0.3143<br>HIGH (>= 0.01): fr 42.4%, n=3654, w=46, AS -0.244 CI [-0.287, -0.197], UP=yes 0.4488 |
| `mid_delta_30s` | no | +0.0800 | [-0.0121, +0.1678] | spans 0 | LOW (< -0.055): fr 43.3%, n=3486, w=34, AS -0.223 CI [-0.279, -0.150], UP=yes 0.1902<br>MID ([-0.055, 0.025)): fr 40.2%, n=3382, w=39, AS -0.205 CI [-0.281, -0.133], UP=yes 0.2182<br>HIGH (>= 0.025): fr 44.9%, n=3707, w=35, AS -0.285 CI [-0.329, -0.228], UP=yes 0.7758 |
| `mid_delta_5s` | no | +0.0240 | [-0.0426, +0.0937] | spans 0 | LOW (< -0.005): fr 42.0%, n=2948, w=45, AS -0.223 CI [-0.260, -0.173], UP=yes 0.213<br>MID ([-0.005, 0)): fr 39.2%, n=899, w=39, AS -0.224 CI [-0.284, -0.175], UP=yes 0.2191<br>HIGH (>= 0): fr 42.7%, n=6690, w=46, AS -0.247 CI [-0.296, -0.196], UP=yes 0.5081 |
| `spread` | YES (degree only) | +0.1531 | [+0.0863, +0.2014] | excludes 0 | LOW (< 0.25): fr 40.3%, n=3148, w=33, AS -0.158 CI [-0.209, -0.127], UP=yes 0.1849<br>MID ([0.25, 0.3)): fr 40.2%, n=3041, w=42, AS -0.212 CI [-0.285, -0.120], UP=yes 0.4561<br>HIGH (>= 0.3): fr 44.7%, n=4509, w=46, AS -0.311 CI [-0.345, -0.265], UP=yes 0.5245 |
| `spread_delta_10s` | no | +0.0280 | [-0.0422, +0.0983] | spans 0 | LOW (< -0.02): fr 44.2%, n=2962, w=44, AS -0.222 CI [-0.270, -0.170], UP=yes 0.4099<br>MID ([-0.02, 0)): fr 41.9%, n=2291, w=41, AS -0.231 CI [-0.277, -0.189], UP=yes 0.3422<br>HIGH (>= 0): fr 41.6%, n=5257, w=44, AS -0.250 CI [-0.299, -0.198], UP=yes 0.424 |
| `touch_depth` | YES (degree only) | +0.1588 | [+0.0823, +0.2129] | excludes 0 | LOW (< 50): fr 38.1%, n=1611, w=28, AS -0.110 CI [-0.160, -0.085], UP=yes 0.1006<br>MID ([50, 293.62)): fr 43.6%, n=5554, w=45, AS -0.256 CI [-0.306, -0.200], UP=yes 0.3932<br>HIGH (>= 293.62): fr 41.6%, n=3533, w=46, AS -0.268 CI [-0.317, -0.208], UP=yes 0.5627 |
| `touch_depth_delta_10s` | no | +0.0391 | [-0.0325, +0.0992] | spans 0 | LOW (< -24): fr 42.0%, n=3439, w=45, AS -0.241 CI [-0.292, -0.185], UP=yes 0.4292<br>MID ([-24, 0)): fr 39.3%, n=774, w=33, AS -0.202 CI [-0.257, -0.162], UP=yes 0.2545<br>HIGH (>= 0): fr 43.0%, n=6297, w=44, AS -0.241 CI [-0.286, -0.195], UP=yes 0.4056 |
| `tte_since_available_s` | YES (degree only) | +0.0978 | [+0.0298, +0.1568] | excludes 0 | LOW (< 75.74): fr 48.3%, n=4099, w=41, AS -0.234 CI [-0.315, -0.137], UP=yes 0.5414<br>MID ([75.74, 159.59)): fr 43.1%, n=3656, w=32, AS -0.284 CI [-0.327, -0.238], UP=yes 0.4398<br>HIGH (>= 159.59): fr 34.7%, n=2943, w=27, AS -0.186 CI [-0.237, -0.144], UP=yes 0.1723 |
| `tte_to_expiry_s` | YES (degree only) | +0.0978 | [+0.0289, +0.1575] | excludes 0 | LOW (< 140.41): fr 34.7%, n=2943, w=27, AS -0.186 CI [-0.235, -0.143], UP=yes 0.1723<br>MID ([140.41, 224.26)): fr 43.1%, n=3653, w=32, AS -0.284 CI [-0.326, -0.236], UP=yes 0.4402<br>HIGH (>= 224.26): fr 48.3%, n=4102, w=41, AS -0.235 CI [-0.312, -0.135], UP=yes 0.541 |

### How to read that table

Five features — `spread`, `touch_depth`, `tte_to_expiry_s` /
`tte_since_available_s` (the same clock, two directions) and `lrrr_delta_30s` —
have best-minus-worst separations whose window-level CI excludes zero. **None
of them separates toxic from non-toxic.** They order states from bad to worse.
The least-toxic bin found anywhere in the whole feature set is:

`touch_depth` = LOW (< 50): **-0.1096 USD/contract**,
CI95 [-0.1605, -0.0852], 1,611 fills across
28 windows.

Still strictly negative, with its whole interval below zero. Count of bins
anywhere whose adverse-selection interval reaches zero: **0**.

### Base-rate trap, named explicitly

a settlement-direction base rate over few windows can imitate skill, so each bin also reports the UP=yes share of its fill windows

The confound is visible and it is severe. The "best" bins of the separating
features are the bins whose fill windows settled one particular way:
`touch_depth` LOW has an UP=yes share of 0.1006,
`lrrr_delta_30s` LOW is 0.13 against HIGH's 0.77. Underdog-side fills in
windows that settled against the underdog lose the premium; fills in windows
that settled for it do not. Across only 46
distinct fill windows, that is largely a settlement-direction base rate wearing
a feature's clothes, not pre-fill skill at avoiding adverse selection. **Even
the ordering is therefore weaker than its CI makes it look** — which only
strengthens the kill.

### The features that are flat

Everything a maker would hope to use is flat: book acceleration at both
horizons (`mid_accel_5s`, `mid_accel_15s`), short-horizon LRRR movement
(5s, 15s), spread and depth *changes*, and recent signed aggressor flow at 30s
and 60s. Aggressor flow is the one a maker most wants, and the venue prints too
few trades for it to carry information — `aggr_volume_30s` and
`aggr_volume_60s` are not even testable, being zero in more than two thirds of
states.

## Mechanism

The underdog-side maker fill is adversely selected by construction on this
venue. We are filled *because* the reference moved away from the side we are
buying; the print that fills us is the print that is repricing the market. An
inside-spread placement has little or no displayed size ahead of it, so fills
are cheap to earn — 42.0123% of placements fill — and
93.7% of them lose. Being easy to fill is the problem,
not the solution.

## What this closes, and what it does not

- **Closed.** Pre-fill conditioning cannot make BTC5m maker liquidity supply
  pay. Lane 3 is a negative result and the BTC5m maker hypothesis is **killed**.
  Do not sweep cancellation thresholds; do not re-run this policy on more of
  the same tape.
- **Not touched.** Sealed OOS windows remain unread. The frozen studies are
  unmodified. This authorizes nothing, changes no cohort or recorder, and opens
  no venue surface.
- **Not claimed.** Nothing here is realized P&L. Every number is
  *historical replay* on hypothetical quotes under the labelling lifecycle:
  historical replay → shadow → live probe → submitted → filled → settled →
  realized. These are at the first stage and go no further.
