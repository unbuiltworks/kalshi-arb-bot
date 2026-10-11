# Fresh L5-class observation: 1¢ tail buys in a 5-minute BTC market

**OWNER-supplied, 2026-09-13. Preserved as an observation. No strategy change.**

## What was observed

A Polymarket profile (screenshot, `polymarket.com`) showing:

- **Profit/Loss, 1D: +$3,027.06**, labelled "Past Day"
- Activity: repeated **`Buy Down 1¢`** on `Bitcoin Up or Down — September 13,
  7:30AM–…`, in **10.0-share ($0.10)** and **15.0-share ($0.15)** clips,
  timestamped "5h ago"

## What it establishes, and what it does not

**Establishes:** a live trader is placing many small same-side buys at the
**1¢ tail** of a **5-minute** binary, i.e. an extremely short window with
extremely high per-unit convexity (1¢ → $1 is 100×).

**Does not establish** that those trades produced the $3,027.06. The two
visible clips total **$0.25 of cost**; a perfect 100× on both is **$25**. The
headline figure is three orders of magnitude larger, so it is driven by
something not visible in the two rows shown. Recorded as an open question, not
as a result.

**Venue caveat, load-bearing:** the screenshot is `polymarket.com`. Our engine
trades `polymarket.us`. A keyless slate read returned **zero** crypto markets,
and across our full live corpus — 103 markets, 15,422 trades — PMUS has served
only sports (`aec-`/`asc-`/`atc-`/`tsc-`). **"Bitcoin Up or Down" does not
exist on our venue.** Any transfer of this behaviour is cross-venue.

## Why it is worth keeping: it is a SIZE and FREQUENCY datapoint

This is the dimension the owner flagged — target behaviour and scale, not a
signal to trade. Against our own live numbers:

| | observed here | our live R5FT |
|---|---|---|
| clip size | 10–15 shares | **1.33–2.12 contracts** |
| entry price | 1¢ | 24¢–59¢ |
| window | 5 minutes | hours to days |
| per-clip cost | $0.10–$0.15 | ~$1.00 (capped) |

And against canonical L5, which our frozen artifact puts at **p50 = 10.0
shares**, p25 4.54–5.0, p75 20.0–28.78:

**The observed 10- and 15-share clips sit exactly on L5's own p50–p75 size
band.** Our engine is not sized there — it executes 1.33–2.12 contracts
because of the declared safety override (`owner size cap $30.00/market + venue
minimum`), which every decision row labels as *"declared safety override, not
L5 behavior"*.

So this observation is consistent with the standing thesis rather than a
challenge to it: **L5-scale behaviour is 10-ish share clips, and we are
currently an order of magnitude below it by deliberate choice.** That is the
capital/capacity gap the scaling path has to close, measured, after the
corrected engine proves clean settled cycles.

## What would make this actionable

Not the screenshot. The account's **public history**, harvested through the
pipeline that reverse-engineered L5 in the first place: hit rate at 1¢,
realised expectancy, drawdown, and whether the 1¢ price is genuinely
mispriced or correctly priced as a longshot. That needs the **profile handle
or wallet address**, which the screenshot does not show.

If the interest is short-window crypto tails specifically, two lanes already
exist to test it without touching PMUS or the canary: `kalshi-l5-shadow`
(15m crypto) and `gemini-l5-shadow`.

## Status

**OBSERVATION ONLY.** No strategy change, no sizing change, no gate change, no
model change. Filed against the standing anti-drift rule: this does not
redefine the program, it supplies a scale target for the capacity work that
follows clean post-cutover cycles.
