# L5 — Direct BTC15m Replication: P0 Closed, P1 Measured, Profit Verdict

**Date:** 2026-10-13 · **Branch:** `research/l5-opportunity-discovery-20261013` · follows `46049391`
**Mode:** read-only. No code, no deployments, no orders, no arming, no new infrastructure, no other venues. Existing safety controls untouched. No ledger evidence modified or deleted. Remains disarmed.

---

# Verdict first

**The engine's risk limits retain 0.45% of L5's actual action stream.** Under the current configuration, even *perfect* behavioural fidelity yields a ceiling of **~$8.85/day** — a **54× shortfall** against $480/day. The binding constraint is not strategy, fidelity, or any defect. It is scale.

And **P0 is now closed: 0030z is retirement residue.** Stale inventory survived retirement. An age-bounded escalation **is** required.

---

# P0 — 0030z closed

## Evidence, from the service's own records

At **2026-10-12T02:47:32Z** — 2h17m after the 0030z window closed:

```
retired_markets : ["cpc-btc-updown-15m-2026-10-12-0030z"]          <- IS retired
drift           : "...0030z|DOWN": {"ours":"2.0000","venue":"0"}
gross_before    : 0030z {up 0, dn 2.0000, matched 0, residual -2.0000, net -2.0000}
venue_net       : {}
committed_usd   : {"aec-nfl-bal-atl-2026-10-11":"0.8800"}          <- NO 0030z entry
cash_deployed   : [ aec-nfl-bal-atl only ]                          <- NO 0030z entry
realized_attributed : "1.6800"
```

And at **2026-10-13T17:29Z**, **41 hours later**, the same `…0030z|DOWN ours 2 / venue 0` is still present.

## Determination

| Question | Answer |
|---|---|
| Was settlement booked? | **Yes.** The market is in `retired_markets`, and `retire_settled_market` is the only path that sets it — it books redemption into `realized` and releases the caps. `committed_usd` and `cash_deployed` carry **no** 0030z entry, confirming the release happened. |
| Does stale inventory remain after retirement? | **Yes.** A `|DOWN` position of 2.0000 shares survives in the gross book. Retirement released the caps but did not pop the position key. |
| Is this the documented failure class? | **Yes.** `l5_live_execution.py:1874-1882` records 2115z re-emitting `L5_LIVE_SETTLED` 40+ times *"because retirement popped only the `|UP` and `|DOWN` keys while the market's real inventory sat under `|ORDER_SIDE_SELL`."* Same class, different residue. |
| Is an age-bounded escalation required? | **Yes.** The residue has stood **41+ hours** with nothing escalating it. |
| Did venue silence prove settlement? | **No, and I did not rely on it.** Settlement is evidenced by `retired_markets` plus the cap release, not by `venue_net: {}`. |

## A second finding the owner should see: the agreement test hides matched residue

In the *same* drift record, `aec-nfl-bal-atl-2026-10-11` shows **both** legs stale — `|UP ours 2 / venue 0` **and** `|DOWN ours 2.0000 / venue 0` — yet `gross_net_agrees` for it is **`true`**, because its `matched 2, residual 0.0000, net 0.0000` equals the venue's `0`.

The test is `gross[m]["net"] == net.get(m, D(0))`. **A stale *matched pair* nets to zero and therefore passes.** Only *unmatched* residue is ever visible. So 0030z was detected purely because it happened to be one-sided; nfl-bal-atl carried the same defect invisibly.

This strengthens the escalation case: the right trigger is **any position key surviving in a market that appears in `retired_markets`**, which catches both, rather than a net-equality mismatch, which catches only the one-sided case. That is a narrower and strictly better predicate than the staleness bound I proposed in `46049391`.

**No code written.** Per your standing instruction and the reasons in `46049391` (no replication session running to coordinate with; `l5-pmus-live` deploys from that branch, so a push would likely redeploy a live executor).

---

# P1 — The actual L5 BTC15m sequential comparison

Source: `l5-ops-data`, `data/l5/ops/activity_2026100*.jsonl` — L5's own chronological fills. **814 BTC15m events, 96,312 BUY actions.** This is L5's real behaviour, not a fitted model, and not ATP tennis.

## L5's actual per-event action sequence

| Dimension | L5 actual | Engine policy | Gap |
|---|---|---|---|
| Capital deployed / event | **$652** median (p90 $1,950, max $8,407) | `max_usd_per_market $5` | **130×** |
| Fills / event | **75** median (p90 237, max 1,164) | `q1_size 1`, `max_live_orders 8` | ~75× |
| Size / fill | **14.96** shares median | 1 contract | ~15× |
| Distinct price levels / event | **37** median (max 91) | engine extends 1.56% of actions | ~37× |
| Side switches / event | **19** median (max 221) | — | — |
| Concurrent markets | **~95 of 96** events/day | `max_concurrent_markets 1` | ~95× |
| Fill price distribution | p5 **0.04** / median 0.44 / p95 **0.92** | band **0.20–0.80** | **38.2% out of band** |
| Trading span / event | 13.2 min | full window | comparable |

**Side/size/price/timing/inventory/extensions/complements, in one sentence:** L5 buys both outcomes in ~15-share clips across ~37 distinct price levels, switching sides ~19 times inside a 13-minute window, deploying ~$652, spanning prices from 0.04 to 0.92 — then redeems at settlement. Mean realised **+$12.02/event**, median +$0.30, 50.2% of events positive (measured earlier, 42-day tape).

**The engine's expressible behaviour:** 1 contract, 1 market at a time, $5 cap, inside 0.20–0.80, extending 1.56% of the time.

These are not the same strategy at different sizes. **L5's edge is produced by the breadth of the price ladder and the number of clips; the engine cannot represent either.**

## Holdout

The per-event structure above is stable across the October sub-period used here; the 42-day aggregate economics (+$12.02/event mean, 73.8% positive days) were measured earlier on the full tape. **No future information enters any entry figure** — every statistic is computed from fills at or before their own timestamps, and no entry decision is conditioned on a later price.

---

# P2 — A / B / C on the frozen BTC15m observations

## C — Existing policy, with the rejection ledger quantified

I could not add a shadow ledger to the engine without writing code, so I computed the rejection directly from L5's action stream against the live limits:

```
individual fills with notional > $5 cap : 45,115 of 96,312 = 46.8%
median fill notional                    : $4.40   (p90 $22.75)
fills reachable per event before the $5/market cap binds : median 0, mean 0.54
TOTAL retained by the $5 cap            : 439 of 96,312 = 0.46%   (REJECTED 99.54%)
band 0.20-0.80 alone rejects            : 38.2% of all actions
COMBINED cap AND band retains           : 435 of 96,312 = 0.45%
```

**The median event cannot take even its first L5-sized fill**: median fill notional $4.40 against a $5 cap that must also cover the complement. **The existing limits retain 0.45% of L5's actions.**

## A vs B — and why B is not implementable on this evidence

| | **A — existing policy** | **B — complement priceability + depth at entry, strict entry-time info** |
|---|---|---|
| Retained opportunities | 0.45% of L5 actions (435 of 96,312) | **Not strictly evaluable** |
| Achievable fills | ≤ A by construction | ≤ A |
| Pair economics | L5 reference: mean pair cost $0.9967 (venue-attested, 11.98 shares) | unknown |
| Residual exposure | the one settled instance cost −38.0¢/share (n=1) | lower by construction |
| Fee-adjusted result | ceiling ~$8.85/day (below) | ≤ A |

**B cannot be evaluated under your own constraint.** Strict entry-time evaluation needs the order book **as it stood at each L5 entry** in the September–October BTC15m windows. The L5 tape contains *fills only — no book*. The only PMUS BTC15m book data in the system is `pmus-ws-evidence-recorder`, which holds **one slug from 2026-10-13** (verified in `46049391`). There is no overlap with the L5 history.

So B would require either future information or a fabricated book. **I am reporting it as not evaluable rather than approximating it** — and per your instruction, B is not implemented, and certainly not on the basis that it eliminates trades.

## The ceiling, which is what actually decides this

```
L5 measured edge per event : +$12.02 on $652 deployed = 1.844% of capital per event
At $5/market, 1 concurrent market, 96 windows/day:
    $5 x 1.844% x 96 = $8.85/day
Target $480/day -> 54x shortfall
```

This assumes **perfect** replication of L5's edge *rate* at the capped scale. It is an upper bound, and it is 54× short. Separately, 38.2% of L5's fills lie outside the 0.20–0.80 band and are unreachable **at any capital level**.

---

# Profit-focused verdict

**Biggest specific discrepancy:** the **$5 per-market cap against L5's $652 median deployment — 130×** — which, combined with the 0.20–0.80 band, retains **0.45%** of L5's actions and leaves the median event unable to take even its first L5-sized fill.

**Estimated economic consequence:** the configuration caps achievable profit at **~$8.85/day** even with perfect fidelity. Every fidelity improvement available — closing the extension gap, better complement timing, the withdrawn taker exception — operates inside that ceiling and cannot move it.

**Smallest correction justified by the evidence:** **none to the strategy.** The evidence does not support a code change; it shows the engine is operating at 1/130th scale in a narrower price band than the behaviour it replicates. The only correction the evidence justifies is the **P0 escalation predicate** — flag any position key surviving in a `retired_markets` market — which is a safety fix, not a profit fix, and which I have specified rather than written.

**What this means for the objective:** $480/day is unreachable from this configuration by any amount of replication fidelity. Reaching it requires a ~54× capital-and-concurrency increase, which requires exactly what you have withheld: owner authorization plus verified positive net expectancy. And verified expectancy does not exist — the largest settled outcome on record remains **−$1.9912**, against an entire venue-attested paired record of 11.98 shares at a $0.9967 mean pair cost.

**The honest ordering, then:** the question is no longer "how do we replicate L5 better." It is **"is there evidence that this edge survives at 1/130th scale?"** — because that is the only scale currently authorized. The R4 campaign is the only evidence at that scale, and it returned −$1.9912 on n=1 settled residual and +$0.0082 on n=1 settled pair. **That is the measurement that matters, and n=2 cannot answer it.**

Remaining disarmed. Owner authorization and verified positive net expectancy still required before any live trading.
