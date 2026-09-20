# Rebate-first economics — the objective changes, and so does the machine

**Owner directive, 2026-09-20: "Losing is fine. I want rebates if we can't win."**
The objective is now **confirmed rewards minus realized drag**, not trading P&L.
That is exactly the engine L5 ran: standalone −0.144% of spend, incentives ≈ +936
bps, net positive. It is not permission to relabel a bad trade — `CLAUDE.md`'s rule
stands: **no reward is assumed until it is credited and source-accounted.**

---

## The structural point that makes this different from everything we have tried

Kalshi liquidity rewards are paid for **resting**, scored on size and proximity to a
reference price. Drag is only paid when an order actually **fills**.

L5 *wanted* fills — it was recycling inventory, and its ladder existed to maximize
them. A reward farmer wants to **qualify and not fill**. The two terms are driven by
different variables, so "lose on trading, win on rebates" is structurally reachable
here in a way it never was for the L5 ladder on PMUS.

The whole question is the curve between them: rest closer → score more, fill more,
bleed more.

---

## Measured inputs — no estimate anywhere

| quantity | value | provenance |
|---|---|---|
| reward pool, per market per 15 min | **$20.00** | `/incentive_programs`, `period_reward` 200000 centi-cents |
| live now | `KXCRYPTOLEAD15M-26SEP201715-{BTC,ETH,SOL,HYPE}` | queried 2026-09-20T21:0xZ, ends 21:15Z, rolling |
| target size, both sides | 1,000 contracts | program rows; full-book depth clears it 3,354–259,338 |
| drag, **incentivised** series | **−0.8% to −13.8%** per turnover $ | `kalshi_reward_economics_chain.py` LINK 5 |
| drag, L5's own habitat | −0.037% per turnover $ | same |
| drag, our PMUS lane | −6.458% per turnover $ | same |
| credited to us on Kalshi | **$0.00** | zero orders ever placed on this venue |

---

## The only number that decides it: what share of the pool must we win to break even?

Share of **one** market's 15-minute pool needed to cover the drag from that much
turnover. Above 100% the pool cannot cover it at any share, so the row is dead
regardless of queue position.

| turnover / 15 min | drag @0.8% | drag @6.5% | drag @13.8% |
|---|---|---|---|
| $10 | 0.4% | 3.2% | 6.9% |
| $25 | 1.0% | 8.1% | 17.2% |
| $50 | 2.0% | 16.2% | 34.5% |
| $100 | 4.0% | 32.5% | 69.0% |
| $250 | 10.0% | 81.2% | **impossible** |
| $500 | 20.0% | **impossible** | **impossible** |
| $1000 | 40.0% | **impossible** | **impossible** |

**The strategy is viable only in the low-turnover, tight-leg corner.** At the worst
measured drag, $100 of turnover per window needs 69% of the pool — implausible
against 120–516 contracts of competing band depth. At the best, $250 needs 10%,
which is not obviously out of reach.

---

## What is NOT known, and why no net is produced

1. **Kalshi's scoring function.** `discount_factor_bps = 5000` is in every program
   row and we do not know what it multiplies. Our share of the pool is unmeasured.
2. **We hold no tape for `KXCRYPTOLEAD15M`**, the series that actually pays. The
   693,112-print habitat measurement (pooled I = 0.0212, floor 0.0010, excess
   +0.0202, implied r/m floor 0.043) was on **`KXBTC15M`/`KXETH15M`, whose programs
   were WITHDRAWN 2026-05-12**. Those markets pay nothing. The measurement is real
   and the habitat is genuinely four times better balanced than L5's — but it is on
   the *unsubsidised* series and must never be quoted as the incentivised one.
3. **Fee schedule uncertified** for these series.

A net that sums a measured negative against an unmeasured positive is not a net.
`NET_SHADOW_ECONOMICS` stays `None`; `P0_VERDICT` stays `UNDECIDED`.

---

## What closes it, in order

1. **Read-only, no authorization needed.** Point the existing `policy_d_port`
   collector at `KXCRYPTOLEAD15M` and measure, on the series that actually pays:
   drag and fill rate **as a function of resting distance from mid**. That produces
   the left-hand column of the table above from real data instead of a range, and it
   tells us whether the low-drag corner exists at all.
2. **Still read-only.** Measure competing band depth over time at several proximity
   bands, so "our share" has an empirical envelope even without the scoring function.
3. **Needs explicit authorization.** The only experiment that closes the reward side
   is resting a small amount of qualifying liquidity and **reading the credited
   reward**. Until a credit lands, every reward number is modelled, and modelled
   rewards are never netted against measured drag.

Step 1 is the next action. No orders, no probe, no arming until 1 and 2 are done and
the owner authorizes 3.
