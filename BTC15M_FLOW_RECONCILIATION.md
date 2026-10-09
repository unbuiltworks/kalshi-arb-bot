# Reconciliation: BTC15m volume vs the fill opportunities our replay can see

**You were right to challenge it. The "PMUS lacks the opposite-side flow"
conclusion is not supported by this evidence, and I am withdrawing it.**

What the data supports instead is narrower and more awkward: our tape samples
the book roughly every 27 seconds, and at that cadence **99.4% of the
queue-depletion events that constitute fill opportunity are invisible.** The
low modelled fill count measures our sampling rate, not the venue's flow.

Scripts: `analysis/pmus/reconcile/volume_reconcile.py`,
`analysis/pmus/reconcile/queue_dynamics.py`.

---

## 1. The sampling measurement (the decisive one)

12 full BTC15m windows, 1-second ladders, each event counted twice over the
*same* underlying data — once at 1 s, once resampled to our 27 s cadence.
Nothing about the market differs between the two counts; only how often we
look.

| | at 1 s | at 27 s | visible |
|---|---|---|---|
| snapshots per window | 900–925 | 36 | **3.9%** |
| best-bid changes per window | 170–405 | 25–34 | **11.2%** |
| own-level queue decreases | 132–246 | 0–5 | **0.6%** |

Median across the 12 windows. The own-level decrease row is the one that
matters: a resting quote is filled when the queue in front of it is consumed,
and at 27 s we observe **one such event per window out of roughly 175.**

Our replay refuses to credit a fill without evidence, which is correct
behaviour. But it cannot find evidence that the sampling destroyed. A replay
on a 27 s tape is structurally incapable of producing more than a handful of
fills per market regardless of how much flow exists.

## 2. The flow is there, and the queue is small

Same 12 windows, 10,467 seconds of ladder:

- Queue at the own-side touch: **p10 8.5, p50 52, p90 345 shares**.
- **64.3%** of observed seconds have fewer than 100 shares at the touch;
  93.2% fewer than 1,000.
- The touch moves every 2–5 seconds (170–405 best-bid changes per window).
- Median spread is 1 tick in every minute of the window.

A re-quoted 1-share order is not facing a wall. For 14 of the 15 minutes the
queue ahead of it is tens of shares, and the price it rests at is revisited
constantly.

**By minute of the window** (median queue at touch):

| minute | 0 | 1–3 | 4–9 | 10–12 | 13 | 14 |
|---|---|---|---|---|---|---|
| queue | 59 | 38–44 | 30–45 | 52–56 | 154 | **5,988** |

Minute 14 is a different market: the expiry pin. The queue is two orders of
magnitude larger and there is nothing to be done there.

## 3. The PMUS arm, and its limit

Two partial windows is all we hold at fine resolution locally. Both are
informative and neither is sufficient.

**`cpc-btc-updown-15m-2026-09-27-1830z`** — 44 polls, 635 s, **123,914 shares
traded**, 95.5% two-sided:
- queue at the bid: **p10 40, p50 109 shares**
- volume printing at or below the bid: **64,132 shares (51.8% of all volume)**

A 1-share quote with ~109 shares ahead of it, against 64,132 shares of
inferred sell flow over ten minutes, would have filled many times over. This
window alone refutes the flow claim.

**`cpc-btc-updown-15m-2026-09-27-1815z`** — 8,563 shares in 184 s, and the
opposite lesson:
- **0 of 88 polls were two-sided** (bid 0.99, no ask). Our entry rule requires
  a two-sided book, so this window is not an opportunity we decline — it is
  one we never see.
- queue at the bid p50 **12,720 shares**, against 8,563 traded. Here a
  touch-joining quote genuinely would not have filled, at any sampling rate.
  This is the expiry pin again, and it is a real constraint.

**Coverage defects found:** the 1830z recorder run returned **120 errors
against 44 successful polls (73% error rate, all HTTP 404)**, with 21 gaps of
26–29 s. Cumulative volume reconciles exactly in both windows
(`delta_sum_matches_rise: true`), so **no volume is missing** — only the price
and timing of prints inside each interval.

## 4. A hypothesis of mine that the data rejected

I expected the dominant defect to be price mis-attribution.
`pmus_status_report.infer_trades` emits one synthetic trade per poll interval,
assigning the **entire** volume delta to the **last** print's price — which
should both under-credit (volume prints at the bid, last print at the ask) and
over-credit (last print at the bid, whole interval credited to us).

Measured on the PMUS tape at 2 s versus 27 s, attribution was **identical**
(credited share 0.9297 and 0.5176 respectively, unchanged by resampling). The
mechanism is real but it is not what is costing us fills. The cost is event
visibility, not price assignment. I was wrong about which defect dominated.

## 5. Why an active market produces so few modelled fills, ranked

1. **Missing tape — dominant.** 27 s polling sees 0.6% of queue-depletion
   events. This is the whole story for the fill count.
2. **Observation density per market.** The shadow watches ~5 markets per cycle
   out of ~509 live, and the BTC study's own skip reasons were
   `TAPE_TOO_SHORT` on 25 of 114 in-sample and **61 of 115 out-of-sample**
   markets — over half the out-of-sample set had fewer than three
   observations. Those are not illiquid markets; they are unobserved ones.
3. **Queue, in minute 14 only.** Real and insurmountable at the expiry pin
   (median 5,988 shares), irrelevant for minutes 0–13 (30–154).
4. **One-sided books.** Entire windows can be one-sided near expiry and are
   never attempted.
5. **Genuinely insufficient opposing flow — not supported.** 51.8% of
   123,914 shares printed at or below the bid in the one high-volume PMUS
   window we can measure properly.
6. **Wrong price — not supported.** Median spread is 1 tick throughout, so
   there is nowhere else to rest.

## 6. What this does and does not change

**Withdrawn:** that PMUS BTC15m lacks the opposite-side flow for passive
matched-inventory formation. The evidence does not support it.

**Still standing, for a different reason:** no live probe is justified. The
pair economics and completion rates in `PLACEMENT_VERDICT.md` were computed on
the same 27 s tape, so they are *also* undecidable rather than negative. A
NO-GO built on an instrument that cannot see 99.4% of the relevant events is
not a NO-GO about the venue — and an apparently positive result on that tape
would be no more trustworthy. **The honest state is MORE DATA, blocked on
instrumentation, not on economics.**

**Unchanged:** the Polymarket findings. Those rest on a 1-second archive and
on-chain logs, not on our poll. L5's ~40% CTF-primitive dependence, the
confirmed $1.00 atomic par-pair channel, and the 81.7% reached-not-aggressive
split are not affected by any of this.

## 7. What to do, in order

1. **Fix the instrument before re-running any policy comparison.** The
   existing 2-second recorder (`data/pmus_btc15m_tape`, `interval_s: 2.0`,
   with ladders) is the right shape and is already written; it needs to run
   over full windows, and its 73% 404 rate needs diagnosing first. The
   authenticated websocket would be better still. Every placement number we
   have is undecidable until this changes.
2. **Then** re-run P7/P8/P10 on a 2-second tape. Only at that point does the
   pair cost of 0.705–0.79 and the 14–40% completion rate mean anything.
3. **Placement changes the evidence already supports**, to apply when the
   instrument is fixed: rest at the touch and re-quote on every best-bid
   change rather than on a 60 s timer (the touch moves every 2–5 s); exclude
   minute 14 entirely; require a two-sided book, which we already do.
   Residual risk stays bounded by the `MAX_EXTEND` imbalance cap and the
   pair-ceiling budget that are already implemented — none of this proposal
   loosens them.

## 8. What I could not do

The brief asked for at least 10 high-volume **PMUS** BTC15m windows. We hold
two partial ones locally, and the shadow's own 229-market tape lives on the
service volume, which I did not read because that would mean deploying. The
12-window arm above is the **Polymarket** 1-second archive — same instrument
class and 15-minute structure, and the only many-window 1-second
prediction-market book we have, but a different venue, labelled as such
throughout and not used for any PMUS-specific claim.

To close that gap without deploying: run the existing 2-second recorder
locally against ~10 full BTC15m windows (≈2.5 hours of wall clock,
read-only, no credentials, no orders). That is the single measurement that
would make every number in `PLACEMENT_VERDICT.md` decidable.

## Labels

Nothing here is realized profit or a confirmed fill. A decrease in resting
size is `UNKNOWN_DECREASE` — trade or cancellation, indistinguishable without
a trade feed — and is never counted as flow. Queue sizes are public ladder
levels, attributed to no trader. A touch is never a fill.
