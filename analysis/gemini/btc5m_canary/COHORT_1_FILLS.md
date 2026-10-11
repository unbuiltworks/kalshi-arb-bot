# BTC5m EXECUTION CANARY -- cohort 1, five live fills

AUTHORIZATION: OWNER-2026-10-05-GEMINI-BTC5M-CANARY-1.
Run 37335223517, job 111848183357, SHA 76f976bd6dc34854fb1a16e9bdf892a916c8b0d4.
Live step 15:45:02Z -> 16:05:23Z, exited ATTEMPT_CAP_REACHED.

THIS IS EXECUTION/MECHANICS EVIDENCE ONLY. The side rule is UP always, which
expresses no view, and no BTC5m directional edge is established. Nothing here
may be read as strategy evidence or as expectancy.

## The five attempts

| # | window | T+ | bid/size | ask/size | limit | ack | latency | fill | role | fee |
|---|--------|----|----------|----------|-------|-----|---------|------|------|-----|
| 1 | …1550 | 15.4 | 0.50/234 | 0.72/25 | 0.72 | RESTING | 493.8 ms | 1 @ 0.72 | MAKER | 0 |
| 2 | …1555 | 15.3 | 0.37/405 | 0.67/25 | 0.67 | FILLED | 183.1 ms | 1 @ 0.67 | taker | 0.02 |
| 3 | …1600 | 15.6 | 0.32/25 | 0.60/400 | 0.60 | FILLED | 123.8 ms | 1 @ 0.60 | taker | 0.02 |
| 4 | …1605 | 15.5 | 0.37/405 | 0.64/416 | 0.64 | FILLED | 148.9 ms | 1 @ 0.64 | taker | 0.02 |
| 5 | …1610 | 15.6 | 0.45/181.75 | 0.74/25 | 0.74 | FILLED | 152.3 ms | 1 @ 0.74 | taker | 0.02 |

All five reached TERMINAL_FILLED on the venue's own order record, q=1, no
partials. Acquisition 0.72+0.67+0.60+0.64+0.74 = 3.37; fees 0.08; committed
3.4460 against the authorized 4.5315 cohort cap, headroom 1.0855. No HALT.

## What the venue taught us

1. THE LADDER-PRICED LIMIT CROSSES. Four of five filled instantly at exactly
   the displayed ask. CI15M-1's contract-priced limits rested and were
   cancelled; pricing from the executable ladder is what changed, and it is
   now confirmed live rather than inferred from the 372-instant tape.
2. ONE FILLED AS A MAKER instead. Attempt 1's 0.72 limit arrived to a moved
   ask, RESTED, and later filled passively at its own price -- `aggressor:
   false`, `maker: "maker"`, fee 0. So a marketable limit at the real ask is
   not a guaranteed cross; it is a cross OR a rest at a price we already
   accepted. Nothing was paid above the cap either way.
3. THE PUBLISHED FEE SCHEDULE RECONCILES against our own fills for the first
   time. Taker 0.07*q*p*(1-p) predicts 0.0155 / 0.0168 / 0.0161 / 0.0135,
   charged 0.02 each at cent rounding; maker 0.0175*0.72*0.28 = 0.0035,
   charged 0. Previously this schedule was published-but-unreconciled.
4. ACK LATENCY 123.8-493.8 ms, median 152.3 ms.

## Two defects to fix, neither in the trading rule

* `authenticated_fills` returns ok:false "unattributable venue fill(s)" on all
  five, classifying our own fills as `foreign`, even though each row's
  `order_id` matches the order we placed exactly. Attribution is by
  clientOrderId and it is not matching. The fills ARE ours and provable by
  order_id, but the attributor says otherwise, and an attributor that cannot
  recognise our own fill cannot be trusted to deny someone else's.
* `discover()` does not carry `strike.value` through, so `strike` is null on
  every window row. Settlement cannot be audited from this artifact alone.

## NOT YET ESTABLISHED

SETTLED and CASH_CONFIRMED. The run ended at the attempt cap before the last
windows expired, and it never polled settlement. Under the repository's
accounting lifecycle these five positions are FILLED and nothing more. There
is no realized P&L here, and none may be reported until the venue's own
settlement and cashflow records are read.

## Preregistered exclusion

These five windows are PROBE-CONTAMINATED and are excluded from all strategy
analysis: GEMI-BTC05M2610051550-UP, …1555-UP, …1600-UP, …1605-UP, …1610-UP.
