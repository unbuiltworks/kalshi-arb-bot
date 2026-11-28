# Venue-confirmed settled evidence, reconciled 2026-11-07 04:00Z

Every figure below is a `L5_LIVE_SETTLED` record with
`resolution_source: VENUE_SETTLEMENT_PX` and `venue_state:
MARKET_STATE_EXPIRED`. This is **settled realized money**, not a model, not
a shadow decision, and not a counterfactual.

| window | matched qty | matched pair | **matched P&L** | residual | residual cost | **residual P&L** | settlement P&L |
|---|---|---|---|---|---|---|---|
| 2026-11-05 2315z | 0 | — | $0.0000 | 1 UP | 1.0000 | $0.0000 | $0.0000 |
| 2026-11-05 2330z | 1 | **1.0000** | **$0.0000** | 0 | — | $0.0000 | $0.0000 |
| 2026-11-06 0300z | 1 | 0.9700 | +$0.0300 | 1 UP | 0.6200 | **−$0.6200** | −$0.5900 |
| 2026-11-06 0315z | 0 | — | $0.0000 | 1 UP | 0.3800 | **−$0.3800** | −$0.3800 |
| 2026-11-06 2015z | 4 | 0.9875 | +$0.0501 | 0.03 UP | 0.0115 | −$0.0115 | **+$0.0386** |
| 2026-11-07 0100z | 0 | — | $0.0000 | 1 DOWN | 0.4900 | **−$0.4900** | −$0.4900 |
| | | | **+$0.0801** | | | **−$1.5015** | **−$1.4214** |

`realized_today_after` on the last record: **−15.20090000**. No settlement
has been recorded since 2026-11-07T01:15:56Z. **Confirmed all-time settled
realized net: −$15.2009, reconciled and unchanged.**

## What the settled tape proves

**1. The asymmetry is real, and it is 18.7×.** Matched pairs earned
**+$0.0801** in total across six settled windows. Unmatched residuals lost
**−$1.5015**. That is the same asymmetry the queue-depletion work measured
on recorded books (+$0.05 a pair against −$0.19 to −$0.25 a residual) — but
here it is settled money rather than a model, and the ratio is worse.

**2. A zero-edge completion really happens.** 2330z completed one pair at a
pair cost of **exactly 1.0000** for **$0.0000**. It spent the cash, the
order slot and the window's cycle to lock nothing. That is precisely the
trade PR #200's ceiling now refuses, observed in settlement.

**3. 2015z is the only profitable window, and it is profitable for the
right reason.** Four matched pairs at a 0.9875 pair, residual only 0.03
contracts. **Matching, not direction, produced the +$0.0386.**

**4. Residual size dominates the loss.** 0300z lost −$0.62 on one unmatched
UP leg bought at 0.62 — more than every matched gain in the table combined.

## What it does not prove

- **Nothing here demonstrates a positive edge.** Six windows, net
  −$1.4214, all-time −$15.2009.
- Fees and maker rebates are separate lines and are **not** in
  `settlement_pnl`; no rebate has been confirmed credited, and a zero
  commission is not a rebate.
- The sample is six windows over three days under two different
  configurations.

## The economic case for the controller stack, stated against this table

PR #200 prevents the 2330z outcome (completing at a 1.0000 pair for
$0.0000). PR #202 prevents cancelling a profitable resting completion that
cannot be re-placed. PR #204 makes the residual an explicit priced decision
instead of a silent carry — and the residual column is where **−$1.5015 of
the −$1.4214** came from.

**None of that turns the table positive on its own.** The residual losses
are realized adverse selection, and the only action that caps them is a
crossing one, which is disabled by owner order. With it off, the controller
can preserve good pairs and refuse bad ones; it cannot stop a residual from
settling against us.
