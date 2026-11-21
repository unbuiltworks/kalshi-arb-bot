# Probe 06 — final accounting, from the venue

Market `cpc-btc-updown-15m-2026-09-25-1700z`, session
`l5probe-20260925-btc15m-06`, account `d51b44a3fa83`, preflight PASSED.
Deployment `ae9bf4e1-1219-40a0-a2be-75d01772440a`, commit `49e6271`.
Container up 16:59:50Z, ten seconds before the pinned window opened.

## Identity

52 distinct L5 actions admitted, 6 distinct refused
`MARKET_IDENTITY_MISMATCH`, **0 foreign tokens admitted**. The binding is
the venue's own record:

    slug         btc-updown-15m-1790355600
    conditionId  0x91b910577386ac9dd4325b7342dc0e8d45ecf333551fc4dfff8446ee6138d1bd
    window       1790355600 .. 1790356500   (17:00:00Z .. 17:15:00Z)
    tokens       4040...7183 (UP)   5902...0651 (DOWN)

Every one of the 52 action keys carries one of those two tokens. Three
distinct foreign tokens were rejected and are named in `rejected_tokens`.
Counts are per distinct action: identity is judged after de-duplication and
refused keys are remembered, so a foreign action counts once however often
the feed re-serves it.

## The six executions

| order | L5 action | our side | desired | executable | limit | avgPx | comm | signed cash | inventory after |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| CQ91P90Y4YCD | UP 0.54 x27 | LONG | 1.22006326 | 1.22 | 0.5400 | 0.5400 | 0.0200 | −0.658800 | up 1.22 |
| CQ92QQ8VRYBQ | UP 0.84 x10 | LONG | 0.45187528 | 0.45 | 0.8400 | 0.8400 | 0.0000 | −0.378000 | up 1.67 |
| CQ92KPB9TYC3 | DOWN 0.14 x1.162785 | SHORT | 0.05254338 | 0.05 | 0.1400 | 0.8600 | 0.0000 | +0.043000 | up 1.67 dn 0.05 |
| CQ93KVNFWYCC | UP 0.90 x50 | LONG | 2.25937641 | 2.25 | 0.8800 | 0.8800 | 0.0200 | −1.980000 | up 3.92 dn 0.05 |
| CQ943BT7WYC3 | UP 0.87 x50 | LONG | 2.25937641 | 2.25 | 0.8700 | 0.8700 | 0.0200 | −1.957500 | up 6.17 dn 0.05 |
| CQ955ANTWYAQ | DOWN 0.13 x0.93103 | SHORT | 0.04207094 | 0.04 | 0.1100 | 0.8900 | 0.0000 | +0.035600 | up 6.17 dn 0.09 |

All six `ORDER_STATE_FILLED`, `leavesQuantity 0`, no partials. Quantity
floored down every time; largest quantization delta `0.00937641`. The two
SHORT rows are `ORDER_SIDE_SELL` / `OUTCOME_SIDE_NO`: our YES-convention
limit 0.14 and 0.11 reach the venue as NO prices 0.86 and 0.89, and they
CREDIT rather than debit.

## Cash identity

    execution    -4.895700
    commission   -0.060000
    settlement   +6.080000     (settlementPx 1.0000; 6.17 long, 0.09 short)
    ------------------------
    net          +1.124300

    balance      61.90495 -> 63.02925   delta +1.12430

**Closes exactly.** Realized P&L **+$1.12430**.

`committed_usd` finished at 5.0529 -- above the $5 cap -- because it counts
a short's credit as cash out. It no longer gates anything.

## Risk

Peak `risk_exposure_usd` over every prefix in venue time order:

    after CQ91P90Y4YCD   0.678800
    after CQ92QQ8VRYBQ   1.056800
    after CQ92KPB9TYC3   1.013800
    after CQ93KVNFWYCC   3.013800
    after CQ943BT7WYC3   4.991300   <- peak
    after CQ955ANTWYAQ   4.955700

**Peak $4.9913 against a $5 cap, never breached** -- and the next two
eligible actions were refused `MAX_USD_PER_MARKET`. The gate bound at the
boundary, with real inventory, which is what observe-only could never show.

## L5 fidelity, 52 actions

| | actions | L5 UP | L5 DOWN |
|---|---:|---:|---:|
| executed | 6 | 4 | 2 |
| STRICT_NOT_EVALUABLE | 43 | 14 | 29 |
| MAX_LIVE_ORDERS | 1 | 0 | 1 |
| MAX_USD_PER_MARKET | 2 | 2 | 0 |
| **total** | **52** | **19** | **33** |

**All six executions were TAKER. All 43 STRICT refusals were MAKER.** The
partition is exact and it is the same one Probe 05 showed: we follow L5 only
where we must cross, and the frozen rule refuses every action he rests
inside the spread because we cannot prove such an order would have filled.

Price fidelity on the six: four match L5 to the tick (0.54, 0.84, 0.87, and
0.14 against 0.140000086). Two are 0.02 inside him -- 0.88 against his 0.90,
and 0.11 against his 0.13 -- which is our own book's touch at decision time,
not a translation error; the book was 0.006-0.775s old on every action.

Size: L5's 27, 10, 1.16, 50, 50, 0.93 became 1.22, 0.45, 0.05, 2.25, 2.25,
0.04 after lambda and flooring.

## Reconciliation

Close-out failed CLOSED on `UnrecognizedVenueShape: "position row lacks
side"`, capturing the first populated position row's real field list. No
side field exists in it. The GET-only recovery run
(`b44fdb49-2299-4c54-95df-9f96a3117d39`) then reconciled from the venue:
uncertainty cleared, live orders empty, positions empty, exposure released.
Nothing was hand-edited and nothing mutated.
