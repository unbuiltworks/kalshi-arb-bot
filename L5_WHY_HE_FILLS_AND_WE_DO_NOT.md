# Why L5 gets fills and we do not — same venue, same series, same books

The owner's correction is the premise and it is correct: **PMUS execution is proven**, by L5's
own transactions. My "transferable" framing was wrong — the question is mechanical, not
cross-venue. This answers it from the full corpus (5,502 windows / 677,597 fills) and arrives
at **one correction that is already written, already pre-validated, and off by default.**

## What the data can and cannot see

Every corpus row is an **execution**. A fill time is an **upper bound** on submit time, never
the submit time. Queue position is never published and is never claimed here.

| | |
|---|---|
| OBSERVED | fill timestamp, outcome, price, size, usdcSize, window |
| INFERRED | that an order rested at that price at or before the fill |
| UNKNOWABLE | submit time, queue position, orders cancelled or replaced unfilled, every order that never filled |

One signature survives that limit: **two fills on one side, in one second, at two prices cannot
come from one order.** That is direct evidence of a resting ladder, and it is in 92.6% of windows.

## The measured gap

| | L5 (5,502 windows) | us (7 venue-confirmed BTC15m windows) | gap |
|---|---|---|---|
| fills per window | median **108** | **2.0** (14 fills / 7 windows) | **54x** |
| first fill, seconds into window | median **16** (48.5% within 15s) | median **105** (min 96) | 6.6x later |
| distinct price levels per side | median **23**, p90 40 | **1** | 23x |
| price span of one side's fills, per window | median **0.550**; 82.6% span >= 0.40 | one price | — |
| seconds between fills | median **2** | — | — |
| contracts per fill | median 13.1, **p10 2.0** | 1.1–1.6 | **comparable at his p10** |
| windows with a swept multi-level ladder | **92.6%** | n/a | — |
| share of window spend in first 60s | median **4.6%** | — | he does **not** front-load |

**He is not early-and-done, and he is not a static ladder.** He is early (median 16s), he spends
steadily across the whole 900 s (median 4.6% in the first minute), and his fills on one side span
**55 cents** of price. That is **continuous two-sided presence that follows price**. We place one
order at one price and stop.

## Why that is the mechanism, not a correlate

Measured on live venue books by the running service (501 samples, authenticated feed):

- at the touch, contracts queued **ahead** of us are a median **4,758x** our need
- **52.3% of instants have no bid at all** — nothing to cross

So a quote at the touch cannot win a queue race, and a **static** complement quote is betting on
one draw from a distribution that is empty half the time. A quote that **follows price** takes
many draws instead of one. L5's 55-cent span is that, measured.

This is also the venue-confirmed separator: across our 7 windows we realized **−$2.1448 on
$4.5790 of acquisition (−46.8%)**, and **2 of 7 lost exactly 100% of acquisition cost** — first
leg filled, complement never arrived, residual settled worthless. Our one window with **three**
fills (1330z) is the one that completed, with the best positive realized, **+$0.2130**. n=1, so
suggestive only.

## Minimum capital — far less than his bankroll

| | p10 | median | p90 |
|---|---|---|---|
| peak **gross outlay** (capital you must hold) | $330.83 | **$898.15** | $2,125.36 |
| peak **net cash at risk** (what you can lose) | $47.33 | **$130.24** | $214.25 |
| **1 contract at each level he used, both sides** | **$10.64** | **$22.31** | $37.30 |

**The minimum-shape clone needs about $22, not $898.** Our $5 cap buys 11.4 contracts at his
median fill price of 0.44 — roughly **5 levels per side against his 23**. So $5 runs a narrowed
version of the shape, not the full width; the full width needs ~$25–40.

## THE CORRECTION — one variable, already written, already pre-validated

Our engine **already** cancels and re-quotes when the touch moves off a resting order. **COMPLETE
is excluded from it.** In the tape: **36 OPEN and 35 EXTEND orders exited via
`repriced:touch_moved`, against 0 of 868 COMPLETEs.**

`src/pmus_spring_l5_session.py`:

```python
COMPLETE_REQUOTE_ENV = "PMUS_SPRING_COMPLETE_REQUOTE"
DEFAULT_COMPLETE_REQUOTE = False

def requote_relations() -> tuple:
    """OPEN and EXTEND always; COMPLETE only when explicitly armed."""
    return (("open", "extend", "complete") if complete_requote_enabled()
            else ("open", "extend"))
```

**The correction is to arm `PMUS_SPRING_COMPLETE_REQUOTE`.** No code change. It makes the
complement follow price for the rest of the window instead of resting at a dead price — which is
precisely the behaviour the corpus shows produces L5's fills.

### Evidence already on file

Pre-registered bar `L5_COMPLETE_REQUOTE_OFFLINE_BAR_v1` sha `699cc8205fd23383`, frozen **before**
the replay ran — **OFFLINE_EVIDENCE_STRONG, all six checks PASS**:

| conversion | completions | net |
|---|---|---|
| 100% | 15 | **+$16.2153** |
| 50% | 7 | **+$8.1076** |
| 25% | 3 | **+$4.0538** |

Net is positive at every rung, including 25%. The bar was deliberately set on **money, not fill
count**, because **66.7% of these re-quotes are above par** (median pair 1.01, worst 1.19) — they
lock small losses, and still beat a residual measured at **−$0.331449 per unit**.

### Why it costs no capital and no risk headroom

A re-quote is a **cancel-and-replace of an order that already exists**. It adds no order and no
dollar. `max_live_orders` stays 1, `max_cash_deployed_usd` stays $5, the 0.96 cap, inventory
limits, maker-only, post-only, the daily loss stop and the frozen model sha all stay as they are.
`pending_obligation()` already counts every resting order at full worst case, so the dollar cap
governs regardless.

### What it needs, and what I am not doing

Both the gate and the A/B design state it explicitly: **"Authorises designing a live A/B: True.
Authorises shipping: False."** `DEFAULT_COMPLETE_REQUOTE = False` and the comment says it "ships
dark and is enabled only by an explicit owner decision, per arm." **I have not armed it.**

`L5_COMPLETE_REQUOTE_AB_v1` is already designed for this: arms assigned by **deterministic hash of
the market slug** (never per session — a residual can span a restart and would belong to neither
arm; never by clock — that confounds the arm with the slate), endpoint **realized dollars per
capital-dollar-hour, treat minus control**, and every `session_end` row carries `requote_arm` so
the frozen extension gate counts control-arm sessions only and keeps its sha.

**Two things to settle before any live run, stated rather than buried:**

1. It was left off for a reason: `L5_COMPLETION_EXTENSION_GATE_v1` is frozen and accumulating, and
   two changes to one surface make neither attributable. The A/B halves its accrual rate
   (~20 remaining firings move from ~5 forward hours to ~10).
2. The A/B design's bounds (~$2.50/action, $7.50/market) are the **spring session's**, not the
   currently deployed $0.80 opening notional and $5 caps. They need reconciling to the deployed
   values before arming, downward, not upward.

Live validation also needs a fresh campaign authorization, which the Oct 25 one is not — it
expired at 20:30Z and I have not re-armed it.

## What this retires

- **The ceiling is not the answer.** Verified by execution, not by argument: at the median
  one-cent spread the 0.96 cap forces the quote **2 ticks** behind each touch and a 0.99 cap
  forces **1** — and at spreads of 2c or wider the cap makes **no difference at all**. It is worth
  exactly one tick on one-cent books, against a 54x fill gap.
- **Entry timing is not the answer.** Already falsified on venue-confirmed data; and 90 seconds of
  a 900-second window cannot explain 54x when he does not front-load.
