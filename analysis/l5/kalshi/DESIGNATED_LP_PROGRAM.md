# Kalshi Designated Liquidity Provider Program — informational, non-executable

Source: [Liquidity Provider Program](https://help.kalshi.com/en/articles/15410219-liquidity-provider-program).
Recorded 2026-10-01. **No contact was made, no application was filed, and no
account status was changed.**

This is a separate program from the ordinary Liquidity Incentive Program whose
`/incentive_programs` endpoint the census reads. The two should not be
conflated in any economics we produce.

## What it is

Kalshi designates "Incentivized Series" and "Incentive Periods". A member who
has **executed a Market Maker Agreement with Kalshi** may become a Designated
Liquidity Provider for a series; meeting the Incentive Period Requirements
earns the Incentive Period Reward. Selection is by **periodic auction**, in
which interested parties state "the minimum amount you're willing to receive
as an Incentive Period Reward" — i.e. a reverse auction that competes the
subsidy down.

Ordinary retail users are not listed as eligible. The documentation names
roughly 40+ active incentivized series across crypto, commodities, sports and
financial indices.

## The entry relevant to us

| Series | Termination | Provider count |
|---|---|---|
| **Crypto 15min markets** | **12/31/26** | **2** |
| BTC Perpetual (KXBTCPERP) | 12/31/26 | 6 |
| ETH Perpetual (KXETHPERP) | 12/31/26 | 6 |
| WTI crude oil | 12/31/26 | 2 |

**We must not assume our account qualifies, and these payments are excluded
from our executable economics entirely.** Any later eligibility question is a
business decision requiring explicit approval, not a research step.

## Does it explain what we observed?

Three observations from the census and the live book, and what the DLP program
does and does not account for.

### 1. Absence of ordinary KXBTC15M liquidity incentives — CONSISTENT, not proven

KXBTC15M has 9,781 programs in the census dump and **0 active, 0 upcoming**;
KXETH15M 8,299 and **0, 0**. The dump shows these series *were* running
ordinary programs historically and are not now.

A designated-provider arrangement covering Crypto 15min through 12/31/26 is a
coherent explanation: the venue pays two contracted makers directly instead of
subsidising an open pool. It is **INFERRED**, not confirmed — the public
endpoint does not state why a series stopped, and a general wind-down of
incentive spending is an equally available explanation. Kalshi has separately
filed with the CFTC to end its Volume Incentive Program.

### 2. Existing depth and competition — CONSISTENT, and directly observed

The single open KXBTC15M market at the time of writing
(`KXBTC15M-26OCT012000-00`):

- best bid 0.4600, best ask 0.4700 — **a one-tick spread**;
- **302,562 contracts resting on YES, 310,720 on NO**;
- the book is populated to the 100-level depth limit on both sides.

That is a continuously two-sided, tick-tight, six-figure-depth book in a
15-minute contract carrying **zero** open liquidity subsidy. Depth of that
shape without an open pool is what a contracted obligation looks like. Two
providers meeting an agreed requirement is a sufficient explanation for it.

### 3. Is ordinary-user L5-style provision structurally disadvantaged there?

**Yes, and on two independent grounds — this one is OBSERVED, not inferred.**

- **No compensation exists to earn.** Zero active ordinary programs on
  KXBTC15M/KXETH15M means the paid-liquidity term that made L5 profitable
  (+$23,047.40 against −$6,338.70 of trading, per the frozen benchmark) is
  identically zero on these series. This holds whatever the reason.
- **No spread to capture and no queue to win.** A one-tick spread is the
  minimum the venue permits, so there is no width for a maker to take. Joining
  302k contracts of resting depth at the touch puts a q1 order at the back of
  a queue it cannot advance in, against counterparties under a standing
  obligation to quote.

An ordinary maker on KXBTC15M is therefore competing on price with
participants who are paid separately for being there, for a spread that is
already at the venue minimum, with no incentive income of its own. That is
structural, not a matter of execution quality — better latency or smarter
placement does not create a revenue line that does not exist.
