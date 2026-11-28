# The Grok inventory policy against actual PMUS execution economics

Audit of `analysis/l5/grok_inventory_policy_replay.py` against what the
venue has actually paid us. Read-only. **The replay cannot be re-run: its
input is gone** — no `penalty.json` and no `tape/` directory survive under
`data/`. So this is a code-and-evidence audit, not a re-measurement, and
any figure the replay once produced should be treated as unreproducible
until the tape is regenerated.

Three defects, each in the direction that flatters the policy, and each
contradicted by a number the venue has given us.

## 1. It credits a maker rebate the account has never received

```python
MREB = D("0.0125")
...
st = "HELD"; fees -= fee(MREB, QTY, opx)      # entry leg
matched += QTY;  fees -= fee(MREB, QTY, cpx)  # completing leg
```

Every maker fill is credited `0.0125·C·p·(1−p)`, and a completed pair is
credited twice.

**Against the venue's own audit (2026-11-18T14:08Z):
`5_rebates_credited_usd` = `0`**, over 8,963 submitted orders and 1,736.87
venue-confirmed filled contracts. Not a small rebate — **none**.

At p=0.5 and QTY=2 that is 0.625 ¢/contract of invented income on a
strategy whose measured pair margin is a few cents, and it is credited on
*both* legs of every pair. The program rule is explicit: do not assume a
rebate until it is credited and source-accounted.

## 2. The fill model is "touch reached ⇒ filled", with no queue at all

```python
if st == "RESTING" and cross(side, bid, ask) <= opx:
    st = "HELD"
```

An order is treated as filled the instant the crossing price touches its
limit. There is no queue position, no size resting ahead of us, no partial
fill, and no requirement that a trade actually printed at our price.

**This cannot reproduce our measured fill behaviour.** On 148
authenticated BTC15m settlements the pair rate is **p_pair = 0.3716** —
62.8% of entries never got their complement filled. A model that fills
whenever the touch is reached would pair nearly everything. The gap
between 37% and ~100% is precisely the queue behaviour the replay omits.

The same optimism applies to the completion leg, so the policy's
"completion logic preserved untouched" is preserved *inside an optimistic
fill model*, which is not the same as preserved against the venue.

## 3. The outcome is inferred from the closing book, on a filtered sample

```python
win = "UP" if bid >= D("0.60") else ("DOWN" if ask <= D("0.40") else None)
if win is None: continue          # window DISCARDED
```

Two problems compounding:

- **Settlement is proxied by the final BBO.** Strands are then scored
  `red = QTY if side == win else 0` against that proxy.
- **Windows whose closing book is ambiguous are dropped from the
  sample.** The windows hardest to call are removed, and those are
  exactly the windows where a strand is most likely to be worthless.

Neither is necessary any more. The venue serves the real answer:
`/v1/market/slug/{slug}` returns `outcomePrices`, verified this session
against our own settled ledger for five separate windows with exact
agreement. A book proxy on a filtered sample should be replaced by the
venue's own settlement on the full sample.

## 4. It models no replenishment, which is one of the three things asked about

The simulation is a single state machine per window —
`FLAT → RESTING → HELD → MATCHED`, with `break` on `MATCHED`. **At most
one entry and one pair per window.** The live engine replenishes after
fills, which is the behaviour whose economics were the question. The
replay is silent on it, so it offers no evidence either way about
maker replenishment.

## What this means for the probability correction

The late-variance correction (`d2ea06c64`) is real and now empirically
calibrated on 20 hours of true BRTI, but it addresses **one term in the
entry decision**. It does not touch any of the four items above. On the
measured BTC15m economics the binding problem is that 62.8% of entries
strand, and break-even needs p_pair to rise 6.9 points — a fill-rate and
selection problem, not a probability-precision problem.

So: **improved calibration alone does not solve the strategy**, and
nothing here should be read as claiming it does.

## What would make the Grok policy decidable

1. Regenerate the tape, or re-point the replay at book data that still
   exists.
2. Set `MREB = 0` unless and until a rebate is credited and attributed.
3. Replace the touch-reached fill with a queue-aware one, or report fills
   as an upper bound and say so in the headline.
4. Take the outcome from `outcomePrices` for every window, and stop
   discarding the ambiguous ones.

Items 2-4 are small edits to an offline script. Item 1 is the data
dependency, and it is the same gap the late-window work hit: we retain no
5-minute book with depth.
