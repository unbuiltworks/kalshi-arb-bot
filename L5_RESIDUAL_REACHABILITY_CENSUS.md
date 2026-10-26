# Complement reachability across 6 windows: the queue finding holds, the
# "no bids half the time" finding does not

**Read-only. Venue books via the authenticated feed, production `l5-pmus-live`
deployment `482cb9e3` (built from `497b1152`), 2026-10-26 02:45–04:30Z.**
`src/l5_residual_reachability_census.py`, 6 tests. Nothing was armed, submitted,
cancelled or deployed. No capital, no limit change, no new service.

This is the extension `L5_COMPLETION_IS_A_QUEUE_PROBLEM.md` asked for in its own
"honest limits": *"One window. The obvious extension is the same measurement
across many windows, which needs only the log stream already running."*

## 1. Corpus

| | |
|---|---|
| windows | **6** (0245z, 0315z, 0330z, 0345z, 0400z, 0415z) |
| arm samples | 1,756 |
| **distinct book instants** | **827** |
| shadow arms sampling each book | 6 |

**The method correction.** Six controllers sample the same book, so a raw sample
count multiple-counts each instant. The prior result quoted "501 samples" from
539 s at a 5 s sampler — about 108 distinct instants seen ~5 times each. The
percentages barely move; the effective n moves by nearly 5x, and n is what a
confidence claim rests on. Every rate below is therefore given per book instant
as well as per arm sample.

## 2. The finding that did NOT generalize

| | single window (2315z, prior) | 6 windows, per book instant |
|---|---|---|
| nothing to cross on the wire | **52.3%** | **7.5%** |
| taker cross impossible | **52.3%** | **7.5%** |

Per window: 0.0%, 0.0%, 1.3%, 4.7%, 6.5%, **17.9%** — the range does not come
near 52.3%, and the worst window is still three times better than the prior
figure. Per arm sample it is 13.7%, so even the inflated reading does not
reproduce it.

**So "a taker cross is impossible half the time" is not a corpus fact.** On this
evidence a complement is crossable at roughly **92.5%** of observed instants.
The prior number came from the one window whose held side had collapsed to ~1c
with an empty bid book — the stranded window itself. Measuring reachability on
the window that stranded selects for unreachability.

This retires "the complement is simply not there" as the explanation, and it
removes the standing objection to taker completion being *available*. It says
nothing about whether taking it is **profitable** — in 1945z, the one
venue-confirmed strand with a full lifecycle, crossing would have locked a
certain ~42c/unit loss, and that remains the only real-money test we have.

## 3. The finding that DID hold, about 7x smaller

`queue_ahead_multiple_of_need` — contracts resting ahead of us at the join
price, as a multiple of what we need:

| | prior (1 window) | 6 windows (arm samples, n=1,714) |
|---|---|---|
| p10 | 626x | **66.7x** |
| **p50** | **4,758x** | **648.6x** |
| p90 | 40,865x | 9,434.7x |
| min | 14x | **0.1x** |
| max | 42,464x | 41,023.5x |
| >= 1x our need | 100.0% | **99.7%** |
| >= 100x | 97.2% | 86.4% |
| >= 1000x | 82.2% | 38.9% |

Per book instant the median is 594.9x (n=808). Per-window medians: 410.8, 439.5,
563.8, 616.0, 739.2, 1,268.7 — **never below 410x in any window.**

**The direction is robust and the magnitude was overstated.** Joining the touch
still puts us behind far more than we need essentially always, but "median
4,758x" should be read as ~600x, and the 82.2% above 1000x is really 38.9%.

**A genuinely new detail: 0.3% of samples have less queue ahead than we need**
(min 0.1x; two of six windows dip below 1x). The prior "not one sample in 501"
is not true across windows. It is a thin set — roughly 5 instants in 1,714 — but
it is the only place a maker completion is reachable without crossing, so it is
worth knowing it is not empty.

## 4. What this does and does not change

- **Does not rescue the cap.** `queue_ahead >= 1x` at 99.7% means a permission to
  pay more still does not buy queue position. `L5_COMPLETE_REQUOTE_VERDICT`
  (0 of 15) and the 0.96→0.99 result stand.
- **Does not establish a taker policy.** Availability is not profitability, and
  the one real-money strand says crossing would have been worse.
- **Does sharpen the ask on `max_live_orders`.** If the complement is crossable
  92.5% of the time and the maker queue is ~600x deep, then resting early
  through the window — which needs more than one slot in 93.9% of windows — is
  the mechanism that remains untested, not the cap and not the ceiling.

## 5. Honest limits

- **Six consecutive windows on one night**, 02:45–04:30Z on 2026-10-26, from one
  deployment. Better than one window; not a corpus across regimes, sessions or
  volatility states. It cannot be read as a stable rate.
- `queue_ahead_bound` is an **upper** bound; the venue publishes no per-order
  queue position. The true queue may be smaller, which would weaken the queue
  finding further, not strengthen it.
- These are **shadow** residual samples, so our own first-leg price is absent
  (`why: "no_first_leg_price"`) and pair costs are not computed. The bid-presence
  and queue figures do not depend on it.
- A displayed quote is not an executable opportunity. Nothing here is a fill, a
  realized number, or a profitability claim in either direction.
