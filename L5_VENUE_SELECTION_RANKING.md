# Venue/instrument ranking for L5-style inventory + maker execution

Read-only. No new service, no deploy, no orders, no risk-limit change. The
production system stays in shadow; watchdog, reconciliation and hard limits
untouched. The $90 fill-horizon probe stays **unarmed**.

The decisive quantity from the accepted negative result is the one this search
is organised around:

> a maker's **capture** (half the spread + *credited* rebate) against its
> **measured adverse selection**. On PMUS crypto 15m that is **$0.0081 vs
> $0.0827 — 10.2× underwater.**

So the question is not "which venue has volume" but **"where is capture not an
order of magnitude below adverse selection?"**

---

## 1. Existing evidence, inventoried

| venue / instrument | spread | fees / incentives | measured fill + adverse | verdict |
|---|---|---|---|---|
| **PMUS crypto 15m** (`cpc-btc-updown-15m`) | median **0.0100** | taker θ 0.0695, maker rebate θ 0.0125 credited; no per-order fee | P(fill@30s) **0.4325** (965 real orders); adverse **−$0.0827/filled contract**, 91.63 % adverse, **98 % of fills by sweep** (956 fills, 475 windows) | **DEAD.** Capture $0.0081 vs adverse $0.0827 |
| **PMUS sports** (props, totals, spreads) | median 0.0100, **p75 0.0400, p90 0.2900** | same schedule; **liquidity programs credited $0.00** | no venue-confirmed adverse measurement; shadow shows residual→cap, matched 0 | **ranked #1, but see §3** |
| **Kalshi crypto 15m** (`KXBTC15M`) | **0.0100** with size both sides | `quadratic`, multiplier 1; only 2 of 274 crypto series charge maker fees, neither a 15M lane | no collector, no service, no deploy config | same instrument class as a measured-dead lane |
| **Gemini crypto 5m/15m** | measured in existing shadow | known and modelled | shadow running, probe DISARMED | same instrument class; settles off Kaiko, not BRTI |

### The liquidity-reward thesis, tested and dead

PMUS publishes **46 active sports liquidity programs with $242,125 of pools**,
and `econ_shared_12.json` models **$30.64/day** for 12 resting contracts across
them — rewards paid for *resting*, not for *filling*, which would make adverse
selection second-order. That is the most attractive mechanism in the evidence
base, and it is **not available to this account**:

- `our_traded_slugs_programs.json`: **239 slugs queried, `with_programs: 0`**
- `PMUS_INCENTIVE_BREAKEVEN.md`: 4 earnings rows, **$0.22 total, every row status
  `SKIPPED`**; **0 credit rows** in the full activity walk;
  `our_markets_in_programs = 0` and `published_programs_covering_our_markets = 0`
  in **all five independent audits**
- verdict already on record: *"Every advertised reward beyond the maker rebate
  has credited this account **$0.00** and is worth **$0.00** here."*

The $30.64/day also rests on `our_share_of_side ≈ 0.96`, which that artefact's
own README flags as sampled **at the wrong time** — a Thursday afternoon, for
pools that pay during Saturday/Sunday live play. So it is an ex-ante model whose
key input is known-wrong in the optimistic direction, against a realized credit
of **$0.00**. **Not a candidate.**

### What the credited maker rebate is actually worth

Measured, not published: **25 credited prints, $0.42 total**, min $0.01 / max
$0.02 per print, so a complete pair collects **at most $0.04**. The rounding is
a **step, not a slope** — a raw credit of 0.002819 rounds to **$0.00**. Small
clips earn nothing at all.

---

## 2. A correction to my own analysis, made in this session

My first pass ranked sports markets by `half_spread × touch_depth` and produced
a "$150 one-shot capture" for a 30-cent-spread corner prop. **That was the
degenerate metric this repository had already identified and warned about.**
From `src/pmus_fill_model.py`'s own docstring:

> "`marginal_pair_cost` came out EXACTLY equal to `1 − spread` on 400 of 501
> live rows (79.8 %) … **a market with a 0.69-wide book scored a 0.31 'good
> completion' — an unfillable quote dressed as an edge.**"

I verified the identity holds here: **`maker_pair_cost == 1 − spread` for 32 of
32 rows.** A wide book mechanically produces a large "margin" because both
quantities are the same number. Any ranking built on it is circular, and mine
was. Withdrawn.

---

## 3. What the data says once the metric is fixed

Ranking by **capture × *contemporaneous* flow** instead of capture × depth
inverts the picture:

| group (2026-09-10 slate, n=32) | median flow |
|---|---|
| **wide** (spread ≥ 0.28), n=4 | **$0.074 / min** |
| **tight** (spread ≤ 0.011), n=22 | **$1,682 / min** |

**Spread and flow are inversely related by ~22,700×.** Wide spread in PMUS
sports is a *symptom of nobody trading*, not an opportunity. The five markets
whose half-spread exceeds measured adverse selection are exactly the five with
essentially zero flow — the best of them, at $150 of modelled "capture", has
flow of **$0.058/min**.

Only one cell carries both, and it is the ranked candidate below.

---

## 4. Ranked candidate

### #1 — PMUS sports TOTALS with mid-width spreads and live flow

`tsc-ucl-psv-sha-2026-09-10-2pt5` — **spread 0.090, flow $86,124/min**
(sibling `…-3pt5`: spread 0.120, flow $2,802/min).

| | |
|---|---|
| half-spread capture | **+$0.0450** |
| credited maker rebate (measured, ≤$0.02/print) | +$0.0100 at best |
| adverse selection **if** crypto's measured figure transfers | **−$0.0827** |
| **net on that assumption** | **−$0.0277** |
| **required adverse selection to break even** | **< $0.0550** — i.e. **≥ 33 % below** the crypto-measured value |

**This is the only candidate in the entire evidence base whose single unknown is
a measurable quantity rather than a missing edge.** Everything else is either
measured negative or measured $0.00.

**Why it is plausible that sports adverse selection is lower**, stated as a
hypothesis and not a result: the crypto figure's mechanism is that **98 % of
fills arrive as sweeps** on a 15-minute binary whose value is driven by a
continuously-priced external asset. A soccer totals market has no equivalent
continuous external reference and far less short-horizon informed flow. That
argues for lower adverse selection. It does **not** establish it, and the
opposite is also arguable — a goal is a discrete 30-cent information event, and
whoever trades first through a resting order after one is strictly informed.

### Not ranked, and why

- **PMUS crypto 15m** — measured −$0.0746/contract. No positive quantity exists.
- **PMUS sports wide-spread props** — capture is real, flow is ~zero; capture × P(fill) ≈ 0.
- **PMUS liquidity programs** — credited **$0.00** across five audits.
- **Kalshi / Gemini crypto 15m** — 1-cent spread, same instrument class as a measured-dead lane, and Kalshi has no collector or service at all. Building one to re-test a measured-negative structure is the anti-drift rule's own example of what not to do.

---

## 5. Capacity, against the $480/day target

| net per contract | contracts/day for $480 | notional/day at $0.50 |
|---|---|---|
| $0.0050 | 96,000 | $48,000 |
| $0.0100 | 48,000 | $24,000 |
| $0.0200 | 24,000 | $12,000 |
| $0.0450 | 10,667 | $5,333 |
| $0.1000 | 4,800 | $2,400 |

Even in the **best imaginable** case for candidate #1 — full half-spread capture
with adverse selection at zero, which no measurement supports — $480/day needs
**10,667 contracts/day**. Against a current authorized envelope of $5 aggregate
exposure and one resting order, that is a gap of three to four orders of
magnitude in throughput, independent of whether the per-contract edge exists.
**The target is not reachable by this mechanism at this scale even if the
remaining unknown resolves favourably.** That is worth saying plainly now rather
than after another measurement cycle.

---

## 6. Biggest remaining blocker, and the next action

**Blocker: adverse selection on PMUS sports is unmeasured, and it is the single
quantity that decides candidate #1.** Capture there is $0.045–0.055; the only
figure we have for adverse selection is $0.0827 from a structurally different
instrument. Without a sports-specific measurement the candidate can be neither
adopted nor rejected, and no amount of controller work substitutes for it.

**Next action — read-only, no new service, no money:** measure it from the
`pmus-sports-forward-collector` that is **already running** with a 50 GB volume
at `/state/pmus-sports`, tracking **1,120 markets** with 239 carrying data. The
measurement is the adverse-selection proxy `ARB_EDGE_CENSUS_2026-08.md` already
defines: for each trade print that would have reached a resting maker at the
touch, record the subsequent mid move against that side. That needs book
time-series at the print, which this collector holds.

**One caution for whoever runs it:** this collector's own
`theoretical_dollars_per_capital_hour` (0.0975–0.195) is **not** evidence for
candidate #1. Its own label says *"fills are 'possible' upper bounds from later
inferred trades; maker identity unobservable; nothing realized"* — it is the
same upper-bound class as the metric withdrawn in §2. And its shadow decisions
currently show `residual_cap:3>=3` with `matched: 0` on most candidates, i.e.
the same residual-accumulation pathology measured on crypto, where `matched 0`
windows ran **−$0.3148 each**.
