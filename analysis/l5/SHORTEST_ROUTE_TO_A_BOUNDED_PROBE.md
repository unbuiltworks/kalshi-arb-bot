# From what L5 actually monetized to a bounded executable probe

Builds on `1786440f` (L5 standalone economics) and `6247777f` (PMUS settled
economics). No Model A change, nothing tuned, nothing ranked into a selector.

---

## 1. What L5 actually monetized

**Not trading.** Standalone trading, settlement-reconstructed over 2,216 windows:
net **−$1,050.57 on $728,500 turnover = −14.4 bps**. Every lane, era and
execution-style subset has a 95% CI straddling zero. There is no positive
standalone regime to transfer.

**Incentives.** On the older full-history accounting, incentives ÷ standalone net
= 68,208 ÷ 13,686 ≈ **5.0×**, and the implied incentive rate is **≈117 bps of
turnover**.

So the monetized mechanism was: **run very large two-sided turnover at a pair
basis slightly below par, keep unmatched inventory small, and collect an
incentive of ~100+ bps on the turnover.** The trading leg is the vehicle for
earning the incentive, not the source of profit.

## 2. The incentive environment is not exotic — PMUS already pays it

| | rate |
|---|---|
| L5 implied incentive | ≈117 bps of turnover |
| **PMUS confirmed maker rebate** | **97.59 bps** of maker notional (venue-confirmed, window-level) |

Same order of magnitude. **The gap is entirely on the standalone side.**

| | standalone | + 97.59 bps rebate | viable? |
|---|---|---|---|
| L5 | −14.4 bps of turnover | **+83.2 bps** | yes |
| PMUS today | −3,206.8 bps of capital | −3,109.2 bps | no, by 222× |

## 3. The single controlling variable

`net per matched unit = matched_per_unit + ratio × residual_per_unit`

| | matched $/unit | residual units per matched unit | residual $/unit | net $/matched |
|---|---|---|---|---|
| L5 | +0.0292 | **0.35** | −0.0888 | **−0.0016** |
| PMUS | +0.0206 | **12.00** | −0.1412 | **−1.6738** |

**Pair basis is comparable on both venues (0.9705 vs 0.98).** The entire economic
difference is how much unmatched directional inventory is left standing.

- L5 matched **85%** of what it acquired.
- PMUS matches **14%**.
- PMUS standalone break-even needs **93%** completion (ratio ≤ 0.146).

Completion rate is a function of how long the market stays open after a first
fill. L5's markets settled in **5–15 minutes**. PMUS live sports run **p50 38
minutes, p90 98 minutes**.

## 4. Searching executable venues for L5's environment

Requirement: a **short settlement clock** (minutes, not hours) **plus** a maker
incentive of ~100 bps.

| venue | market class | clock | maker incentive | has L5's environment? |
|---|---|---|---|---|
| **Gemini** | BTC 5m / 15m up-down | **5–15 min** | — | **yes, this is exactly L5's market class** |
| PMUS | table tennis (setka) | ~20–27 min | 97.59 bps confirmed | closest sports analogue |
| PMUS | esports (cs2/lol/dota2) | ~15–45 min | 97.59 bps confirmed | partial |
| PMUS | soccer / NFL / CFB | 100–200 min | 97.59 bps confirmed | no |
| PMUS | crypto short-duration | **does not exist** | — | no |
| International | L5's own venue | 5–15 min | — | not executable |

**PMUS has no crypto or sub-hour market class.** `/v2/sports` lists 21 sports,
none crypto; `btc-updown-5m`, `btc-updown-15m`, `eth-updown-5m`, `crypto`, `btc`
and `bitcoin` all return zero events; the non-sports tabs are politics and
weather. (`/v1/markets?category=` is broken — it ignores the parameter and
returns a stale 2025-11 NFL page — so it is not evidence either way and is not
used here.)

**The venue that runs L5's exact market class is Gemini.** That is a finding
about where the environment exists. It is *not* a proposal to change the Gemini
A+B control, which stays untouched.

## 5. The shortest evidence-supported route

Ordered by evidence required, cheapest first. Each step can falsify the next.

**Step 1 — measure completion rate as a function of settlement clock, on data we
already have.** The controlling variable is completion, and the hypothesis is
that it is driven by clock length. The durable PMUS streams already span table
tennis (~20 min) through NFL (~200 min). If completion does not rise materially
as the clock shortens, the clock hypothesis is wrong and the whole route dies
here, cheaply. **No new collection, no new infrastructure.**

**Step 2 — only if Step 1 holds: establish whether any PMUS class reaches ≥93%
completion.** Short-clock PMUS is ~20–27 min, still 2–4× L5's. If the best PMUS
class tops out well below 93%, PMUS standalone cannot be carried by the 97.59 bps
rebate and PMUS is not the transfer target regardless of policy tuning.

**Step 3 — confirm rebate attributability before relying on it.** The 97.59 bps
is window-level; `/v1/transfers` carries no order id, so no fill-level
attribution exists. A probe whose viability depends on the rebate needs the
rebate to be attributable, or it cannot be measured after the fact.

**Step 4 — only then, a bounded probe**, at the envelope already derived in
`4ec9c896` (sized to the observed distribution, not to throughput).

## 6. What I am explicitly not proposing

- Not repairing Model A against the −32% cohort.
- Not fitting a category selector from the leagues that happened to match.
- Not treating the Gemini finding as licence to touch the A+B control.
- Not using the rebate to rescue negative standalone economics — §2 shows it
  cannot, by 222×.

## 7. The honest summary

L5's profitability was an **incentive-capture** business running a
near-break-even trading vehicle at very high turnover with tight inventory
completion. Reproducing it requires reproducing the *completion rate*, which
requires a *short settlement clock*. The venue we can execute on has the right
incentive and the wrong clock; the venue with the right clock is the one we hold
as a control. That tension — not policy tuning — is the real decision in front
of the programme.
