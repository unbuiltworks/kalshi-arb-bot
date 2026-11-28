# How L5 gets his pair cost, and what our controller should copy

Evidence: L5's **venue-confirmed** BTC15m fills and redemptions for the 118
windows settled between 2026-11-09 12:50Z and 2026-11-10 19:30Z
(`data/l5/recent/btc15m_stream_20261110.json`, Polymarket Data API v2).
Reproduce: `PYTHONPATH=. python3 analysis/l5/l5_stream_replay.py`. Tests:
`tests/test_l5_stream_replay.py`.

## What he actually did

| | |
|---|---|
| spent / redeemed | $56,027.65 / $56,855.36 |
| **realized trading net** | **+$827.71** (+1.48% of spend), +$7.01 a window [-1.90, +16.20] |
| per window (median) | 48 fills, 941 contracts, $421 |
| matched | 88% of contracts, aggregate pair cost 0.924 |
| matched P&L / residual P&L | **+$3,772 / -$2,944** |
| residual on the winning side | 27% of windows |
| worst window | -$119.95 |
| rebates credited in the period (separate) | maker $182.97, taker $48.11 |

The window in the owner's screenshot (19:30-19:45Z) settled after this file
was cut: 87 fills, $492.84 spent, $371.06 redeemed, **-$121.78**.

## How the low pair cost arises - and why it is not the target

"Aggregate pair cost" is average UP cost plus average DOWN cost over
**everything** bought. L5 keeps buying the side that is falling (54% of his
volume is bought on a down-tick of its own side; 54% adds to the side he is
already long), so that side's average is dragged down by 10-25 cent
contracts. Those contracts are the residual, and the residual loses.

- Pair cost does not depend on how far the price travelled (correlation 0.00).
- His **balancing** fills are not cheap: the price paid plus the average
  cost of the side he was long has a median of 0.968, and 44% of that
  volume is above $1.00.
- By role, per contract: balancing fills **+4.2c**, adds to the heavy side
  **-1.7c**, first fills -8.1c. By price: 0.60-0.80 **+4.9c**, 0.20 and
  under **-1.4c**. None is separately distinguishable from zero.

So the 0.925 is partly an accounting effect of cheap losing inventory. The
money is in the balancing fills; the leak is in adding to the heavy side.

## What the controller should copy

Each of his fills is an offer; the production `decide` says, on our own
running inventory, whether it would be quoting that side at his price.

| all 118 windows | contracts / window | net / contract | net / window | matched / residual $ | peak unmatched | worst window |
|---|---|---|---|---|---|---|
| L5 as traded | 1,057 | +0.0066 [-0.0017, +0.0154] | +7.01 [-1.90, +16.20] | +3,772 / -2,944 | 178 | -119.95 |
| controller, current profile | 0 | - | 0 | - | 0 | 0 |
| **controller, BALANCE** | 345 | **+0.0052 [+0.0008, +0.0095]** | +1.80 [+0.27, +3.38] | +375 / -163 | 30 | -26.62 |
| BALANCE, no 0.30 floor | 349 | +0.0046 [+0.0003, +0.0087] | +1.62 | +380 / -189 | 30 | -26.62 |
| BALANCE, hedging stops at 600 s | 230 | +0.0039 [-0.0027, +0.0103] | +0.90 | +162 / -56 | 30 | -27.45 |
| BALANCE, hedge capped at a 1.00 pair | 113 | +0.0029 [-0.0126, +0.0166] | +0.33 | +641 / -602 | 28 | -20.27 |
| BALANCE, hedge capped at a 0.96 pair | 89 | +0.0053 [-0.0139, +0.0218] | +0.47 | +655 / -599 | 28 | -20.27 |
| BALANCE without balance-first | 611 | +0.0128 [+0.0050, +0.0208] | +7.82 [+3.10, +12.51] | +1,045 / -123 | 123 | -68.41 |

BALANCE (`src/l5_portfolio_controller.BALANCE`): both sides at the touch on
the book's own mid; while anything is unmatched only the deficient side is
quoted and the held side's resting openings are withdrawn; no new exposure
under 30 cents; a hedge is never capped by pair cost and may be placed
until T+840 s; new exposure stops at T+480 s.

By half (70 / 48 windows): +0.0044 [-0.0014, +0.0103] and +0.0060 [-0.0002,
+0.0121]. Positive in both, clear of zero only over all 118.

What it changes against L5 as traded: the residual loss falls from $2,944
to $163, the worst window from -$119.95 to -$26.62, peak unmatched from 178
contracts to one clip. It also takes a third of the volume and earns a
quarter of the dollars: it buys certainty, not profit.

What the evidence says **not** to do: cap a hedge by pair cost (it strands
the residual it was meant to remove), or stop hedging at 600 s.

Dropping balance-first keeps the same time structure and earns more per
contract at four times the unmatched inventory. It is the next step once
there is live evidence, not the first.

## The assumption, and what our own venue says

This takes his fills as available to us. On PMUS's own book (380 windows of
production book samples) with conservative trade-through fills at the
touch, the same cap-and-floor rule is still **negative**: -1.0c a contract
[-1.7, -0.4], pair cost above $1.00, because a bid that is only filled when
the market trades through it pairs at a loss. L5's matched pairs cost 0.979
under this rule; that difference is fill quality, not the rule. Only our
own venue fills can say which of the two we get.
