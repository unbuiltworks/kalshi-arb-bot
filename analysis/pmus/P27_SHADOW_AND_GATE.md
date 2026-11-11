# PMUS BTC15m — fee repair, frozen-L5 shadow, selector, economics, and the gate

Read-only throughout. No PMUS order was placed or prepared, no credentials are
held in this container or were requested, and **Gemini B2 is untouched and
still frozen** (`spec_sha bfe7ba23…`, `armed False`). The Kalshi lane and its
evidence are preserved.

---

## P0 — execution correctness

### The taker coefficient was stale, and it was stale in the dangerous direction

PMUS republished its fee schedule **effective 12 AM ET, Thursday 2026-09-17**:

```
Fee = Θ × C × p × (1 − p)      taker Θ = 0.0695      maker Θ = −0.0125
"Maker rebate is applied at the point of trade."        docs.polymarket.us/fees
```

`pmus_q1_executor` used `Θ = 0.06`, 15.8% low. That is not cosmetic: the
fee-safe `complement_bound` is *derived* from the taker fee, so understating it
**loosens a risk bound**.

`src/pmus_fees.py` is now the single authoritative schedule. It is
**effective-dated rather than simply updated** — analyses of pre-17 fills are
correct at 0.06, and silently re-rating them would corrupt evidence that was
right when it was recorded. `taker_theta(when)` returns 0.06 before
2026-09-17T04:00Z and 0.0695 from it.

**Provenance is stronger than a headline number.** PMUS publishes a 99-row
table of what a 100-lot taker pays and a 100-lot maker receives at every price
from $0.01 to $0.99. **All 99 rows reproduce exactly** from Θ = 0.0695 / 0.0125
with banker's rounding to the cent — which pins the *rounding rule* as well as
the coefficients. The captured table is the test fixture
(`analysis/pmus/pmus_published_fee_schedule.json`, 112 assertions).

### Audit of the other PMUS fee assumptions

| assumption | status |
|---|---|
| maker rebate Θ = 0.0125 | **current** — unchanged, and confirmed by all 99 rows |
| maker rebate timing | **current** — credited at the point of trade |
| banker's rounding to the cent | **current** — confirmed by the table |
| price domain $0.01–$0.99 | **current** |
| taker rebate | **not ours** — tiers start at $250,000 prior-calendar-month taker volume. Written down in `TAKER_REBATE_TIERS`, returns 0 for this account, counted at $0 everywhere |
| `pmus_q1_executor.TICK = 0.005` | **STALE FOR CRYPTO** — see the gate below. Flagged, not patched |
| `MIN_PRICE/MAX_PRICE = 0.20/0.80` | **sports parameter** — see below |

Two tests caught real consequences of the repair, which is what tests are for:
the venue-adapter test was itself asserting the stale `0.0150`, and the
spring-image test found that a module the image `COPY`s would no longer import.
Both fixed. The R1 probe's pinned copy set moves **38 → 39** for the same
reason — `src/pmus_fees.py` travels with `pmus_q1_executor.py` or the image
carries an unresolvable import — and that pin change is **disclosed in the
assertion**, not absorbed.

### The entry band was NOT widened

`0.20–0.80` remains exactly as it is. The shadow **records it as binding** on
every decision it blocks and refuses to act, with the reason stamped
`entry_band`. It is treated as an experiment parameter requiring evidence, and
the evidence it would need is in P4 below.

### Crypto settlement provenance

`PMUSAdapter(PMUS_PRODUCT_CRYPTO_UPDOWN)` now reports the real semantics —
literal clock, CF Benchmarks BRTI, the 60-price average rule, the ≥ comparison
— plus the two measurement limits (30 s cached book, no public trade tape) and
the incentive program carried at $0. The sports default is byte-identical.

---

## P1 — the continuous frozen-L5 shadow

`src/pmus_btc15m_l5_shadow.py` runs the **same** frozen `persistence-v2` model
and the **same** `state_key` as the Kalshi lane. Nothing is tuned, and nothing
was tuned against these windows. The clock is **literal TTE** from the window's
own expiry — the fidelity the PMUS sports lane never had, where 635/635
decisions collapsed into one TTE bucket.

Every decision captures: `t_utc_epoch`, `tte_s`, `book` (both sides, sizes,
spread, snapshot age), `native_l5_action`/`relation`, `side`, `desired_price`,
`size`, `inventory`, `imbalance`, `matched_inventory`, `residual`,
`effective_pair_cost`, `fee_economics`, `maker_economics`, and a `reason` for
the action or the HOLD.

**Two tracks, never summed.**

- **Track A — decisions only.** Inventory held at zero, **no fill assumption
  anywhere**. Assumption-free, and the primary track.
- **Track B — assumed-fill counterfactual.** Inventory cannot evolve without
  fills, so questions like "does it EXTEND repeatedly" are unanswerable at zero
  inventory. Track B advances inventory under exactly one named assumption,
  stamped on every row it produces:
  `ASSUMPTION_VOLUME_AT_TOUCH` — a quote resting at the touch is assumed filled
  for one unit when `sharesTraded` strictly increased while it was live.

  Every Track B row carries `fill_class: ASSUMED_FILL_SHADOW` and
  `fill_evidence: "none"`. **A fill is never inferred from a cached book later
  moving through our price**, which is a different and weaker thing.

One replay correctness fix: the walk now stops at the **last snapshot**, not at
the window's clock. Walking past the end of the tape manufactured a wall of
`stale_book` holds that described the clock, not the venue.

---

## P2 — the public-book limitation

**Yes, our existing authenticated infrastructure can consume a fresher feed
without placing orders.** `src/pmus_ws_evidence_recorder.py` already:

- connects to `wss://api.polymarket.us/v1/ws/markets`;
- subscribes to **`SUB_MARKET_DATA = 1`** *and* **`SUB_TRADE = 3`** — the live
  book and **the real trade tape the public gateway withholds**;
- is **structurally read-only**: `OUTBOUND_FRAME_KEYS = {subscribe,
  unsubscribe, heartbeat}`, enforced by `guard_outbound` at send time *and* by
  structural tests. No order endpoint is reachable from the module.

What it lacked was a universe that rolls. A static `PMUS_WS_MARKETS` list goes
stale every fifteen minutes, because each window is its own market. **`PMUS_WS_SERIES`**
now resolves the trading window through the *same* `series_slugs()` the public
tape uses, so there is one definition of "which window is trading now" and
rediscovery rolls it:

```
PMUS_WS_SERIES=btc-updown-15m
  → ['cpc-btc-updown-15m-2026-09-23-1400z']   why=series:btc-updown-15m
  → subscribe frames: subscription_type 1 (market data) and 3 (trades)
```

That feed distinguishes all four things asked for: **actual prints** (trade
subscription), **book movement** (market-data subscription), **hypothetical
queue interaction** (book deltas at our price level over time), and
**maker/taker side where the venue reports it**.

**It needs `PMUS_API_KEY_ID` and `PMUS_API_SECRET_KEY`. This container holds
neither, and I did not request them.** Until it runs somewhere that has them,
the fill question below stays unanswered.

---

## P3 — the active-window selector

A venue selector, not a directional signal: it never chooses a side.

**Signal A — the previous window's outcome.** Volume alternates
(`.V.V.V.V`, transition rate **0.76** against 0.50 for an independent coin).
The previous window is settled *before* the next one opens, so this uses no
future information and has **zero detection latency**.

| | value |
|---|---|
| windows evaluated | 88 |
| TP / FP / FN / TN | 34 / 20 / 1 / 33 |
| **recall** | **0.971** — one active window missed out of 35 |
| **precision** | **0.630** against a base rate of **0.398** |
| accuracy | 0.761 |
| false-positive rate | 0.377 |
| **false-negative rate** | **0.029** |
| detection latency | **0 s** |

Skipping the windows it calls quiet would avoid 33 of 54 dead windows at the
cost of one active window in 88.

**Signal B — within-window onset.** First `sharesTraded` growth at 2.4 s and
31.2 s in the two windows with usable coverage; first two-sided book at 0.0 s in
both. **These are upper bounds only**: the gateway's ~30 s cache cannot resolve
an onset faster than itself. n is far too small to report a latency
distribution, and the collector is still running.

---

## P4 — L5 behavioural compatibility

4 windows replayed, 41 decision points, Track B.

| question | PMUS BTC15m (shadow) | L5 BTC15m historical |
|---|---|---|
| Does the controller **OPEN**? | **YES** — 4 opens | — |
| Does it **EXTEND** repeatedly? | **YES** — 5 extends, longest run **4** | P(next = extend) = **0.66** |
| P(extend) observed | **0.417** | 0.66 |
| Does opposite inventory become **obtainable**? | **YES under Track B's assumption** — 3 completes | — |
| Can inventory **cross/overshoot zero**? | **NO — 0 of 4 windows** | **84.3%** of episodes FLIP through zero |
| Repeated repair opportunities | one window produced OPEN→EXTEND→COMPLETE cycles | 6.9 residual episodes/window |
| How long do they persist? | the whole window — volume was flat-to-rising across thirds | — |
| Desired price **near the executable book** | **100%** of quotes were at the touch | — |
| **$0.01 tick** materially altered the action | **0 of 41** (0.0%) | — |
| **Existing CAP/risk controls bind** | **entry band bound 23 of 41 (56.1%)**; one-sided book 6 | — |

Three readings that matter, and one of them is negative.

1. **The controller expresses its full vocabulary here.** OPEN, EXTEND and
   COMPLETE all fire, with an EXTEND run of 4 — the incremental-inventory
   behaviour the model was learned from.
2. **The $0.01 tick never changed a decision.** The coarser tick was the main
   mechanical worry in the transfer analysis, and on this evidence it costs
   nothing: every desired price sat exactly at the touch and none would have
   crossed.
3. **Inventory never crossed zero — 0 of 4 windows, against L5's 84.3%.** That
   is the single largest behavioural divergence found, and it is not yet
   interpretable: with the entry band blocking 56% of decisions, the controller
   is being stopped before it can oscillate. Whether PMUS BTC15m supports the
   oscillation is **UNKNOWN**, not answered.

---

## P5 — economics at q = 1

Six lines, never merged. Track B's 12 assumed fills across 4 windows.

| line | value | label |
|---|---|---|
| 1. matched-pair economics | 3 pairs, cost **$2.9414**, payout $3.0000, **P&L +$0.0586**, avg pair cost **0.9805** | SHADOW — these pairs were never bought |
| 2. directional residual | 6 units, cost basis $1.5086 | **NOT MARKED** — its value is a settlement outcome, and L5's own residual P&L is **−$144,065** over 5,546 windows |
| 3. maker rebate | **$0.0279** accrual | ACCRUAL ONLY — excluded from pre-rebate P&L and from strategy selection |
| 4. liquidity reward | **$0.00** | eligibility and share of score not established |
| 5. taker fees | **$0.00** | every action is a maker join; nothing crosses |
| 6. modelled net | **+$0.0586** pre-rebate, **+$0.0865** with the accrual | **MODELLED** — and incomplete, because line 2 is deliberately unmarked |

**Capacity is not derived**, and **no market share is assumed**. Deriving
capacity needs a fill assumption with evidence behind it; the only one
available is Track B's, which has none. That is what the authenticated trade
tape would supply.

One number deserves attention on its own: the average matched-pair cost is
**0.9805**. Positive, but **well above the ≤0.96 spring discipline** that
governs this programme's pair completion. At 0.9805 a pair earns 1.95 cents
before the residual, and L5's own residual line is negative.

---

## ADVANCEMENT GATE — **NOT PASSED. No live manifest is produced.**

| # | condition | verdict |
|---|---|---|
| 1 | recurring active windows identifiable prospectively | **PASS** — zero-latency selector, recall 0.971, precision 0.630 vs 0.398 base rate, n = 88, no future information |
| 2 | genuine two-sided interaction supports L5 recycling | **PARTIAL** — 100% two-sided and back-loaded volume in the strongest window, but 6 one-sided holds in another, and only 2 windows have usable coverage |
| 3 | opposite-side acquisition **plausible rather than merely theoretical** | **FAIL** — Track B *assumed* it. There is no trade tape on the public gateway, so not one fill, queue position or taker side has been observed. This is precisely the "merely theoretical" the condition excludes |
| 4 | accounting / fee / rebate handling correct | **PASS** — one effective-dated schedule, all 99 published rows reproduced, rebate segregated as an accrual, liquidity reward at $0, taker rebate at $0 |
| 5 | authoritative reconciliation available | **NOT ESTABLISHED HERE** — `/v1/incentives/earnings` and `/v1/portfolio/activities` exist and `pmus_incentive_audit.py` already reads them, but this container has no credentials, so no reconciliation has been demonstrated |
| 6 | no unresolved execution-path defect | **FAIL** — two, both named below |

### The two unresolved execution-path defects

1. **`pmus_q1_executor.TICK = 0.005`.** `complement_bound` rounds the
   complement price onto the **0.005 grid**, but PMUS crypto's
   `orderPriceMinTickSize` is **0.01**. A complement at 0.475 is not a price
   this venue accepts. Flagged, not patched — it is an execution parameter and
   it needs the same review the band needs.
2. **The `0.20–0.80` entry band binds 56.1% of decisions.** It is a sports
   parameter applied to a market whose observed median mid was 0.155–0.225. It
   is deliberately **not widened**; until it is reviewed on its own evidence,
   more than half the controller's decisions are refused and the behavioural
   picture in P4 stays distorted.

### What would clear the gate, in order

1. **Run the authenticated recorder with `PMUS_WS_SERIES=btc-updown-15m`** on a
   host that holds the PMUS keys. That single step supplies real prints, taker
   sides and a live book — and it is what turns condition 3 from an assumption
   into evidence, condition 2 from partial into measured, and makes a fill
   model possible at all. It places no orders.
2. **Review the entry band and the tick as crypto experiment parameters**, on
   the evidence above, separately from this work.
3. **Re-run the shadow against the WS tape** and re-ask P4's zero-crossing
   question without the band suppressing 56% of decisions.
4. **Demonstrate reconciliation** from the authenticated endpoints.

Only then is a bounded q=1 manifest worth writing. I have not written one, and
no authorization is requested.

### Unchanged

- **Gemini B2** — frozen, `spec_sha bfe7ba23…`, $10.16 ceiling, awaiting only
  explicit owner approval.
- **Kalshi** — evidence preserved, collector unchanged.
- **The PMUS collector keeps running**, writing raw provenance to
  `data/pmus_btc15m/`.
