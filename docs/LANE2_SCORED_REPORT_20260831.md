# Lane 2 Scored Report — Policy D on Kalshi/Gemini (interim, 2026-08-31 04:42 UTC)

Frozen Policy D (2026-08-30 constants, untouched) shadow-run against live
public tape collected 2026-08-30 ~19:15 UTC → 2026-08-31 ~04:35 UTC.
Overnight collection continues; final update lands ~11:05 UTC.

## Headline: Kalshi (the venue that matters — real books, no incentives)

| Metric | Value |
|---|---|
| Settled windows scored | **26** (13 BTC, 13 ETH 15m) |
| Prints observed | 185,714 |
| Shadow maker fills | 1,111 |
| Filled notional | $5,142 |
| Gross P&L | +$263.14 |
| Fees (maker, ceil-cent) | −$26.21 |
| **Net P&L** | **+$236.93 = +4.61¢ / $ after fees** |
| Windows positive / negative | **17 / 9** |
| Worst window | −$50.03 (BTC 00:00) |
| Best window | +$54.59 (ETH 23:45) |
| By asset | BTC +$74.36, ETH +$162.57 — both positive |
| By winner direction | positive nights on both Up- and Down-settled windows |

Signal trajectory as the sample grew: +1.65¢ → +5.79¢ → +7.59¢ → **+4.61¢/$**
at 26 windows. The pullback from +7.59¢ is ordinary regression as more windows
land, not a reversal: the edge stays positive, both assets contribute, and
losses cluster in a few windows rather than a systematic drift.

## Gemini

9 resolved windows, $33.60 shadow notional, net +$6.29 (+18.7¢/$). Sample far
too small to mean anything — Gemini prints are sparse off-peak and winner
resolution there uses a last-print heuristic, not an authoritative result.
Treat as "not contradicting Kalshi," nothing more. The flow-gated Gemini
question is handled separately by the burst census once hot-hours tape exists.

## Extrapolation (FLAGGED: shadow arithmetic, not a forecast)

All of the below inherits the print-through proxy (see caveats). Do not treat
as expected earnings.

- Shadow economics per settled window: ~$198 notional, ~+$9.1 net.
- Both series run ~192 15m windows/day (2 assets × 96). Naive full-coverage
  shadow arithmetic: ~$38k notional/day, ~+$1.7k/day — **an upper bound that
  assumes front-of-queue fills on every eligible print, 24h attention, and
  zero market impact. Reality will be a fraction.**
- Inverted for the $480/day goal at the measured +4.61¢/$: needs ~$10.4k of
  REAL filled notional/day. If live capture is 10–25% of shadow (unknown until
  the canary), that's the difference between ~$170/day and ~$440/day at full
  coverage — i.e. the capture ratio, not the edge, is now the binding unknown.
- Capital: peak shadow exposure per window ran ~$200–420; running both assets
  continuously at shadow scale needs roughly **$1–2k working capital**, well
  below the level where our size would move these books.

## Caveats (unchanged, load-bearing)

1. **Print-through fill proxy**: every print at/through our shadow quote counts
   as our fill — front-of-queue assumption. Real fill rate and realized edge
   will be lower; adverse selection of the fills we *do* get will be worse.
   Only the live canary measures this (runner + runbook are in the repo).
2. **No queue or impact modeling**; our size is assumed invisible.
3. **~9 hours, one Sunday evening/night.** Overnight extension is running;
   different sessions (EU/US day) still unmeasured.
4. **Frozen constants** (tape 30 prints, contra threshold −0.1, floor $0.30,
   90s expiry stop) were validated on L5's Polymarket history — reused, not
   re-tuned, and must stay frozen through the canary.
5. **No incentive dollars anywhere in these numbers** (these Kalshi series
   carry no incentive programs; accounting rule stands regardless).

## Gate status

Lane 2's economic gate — "ported signal exists on an accessible venue with
positive fee-adjusted out-of-sample economics" — is **met on shadow evidence**
(26 settled windows, +4.61¢/$, 65% window win rate). The remaining unknown is
capture ratio, which is a live-canary question, not a research question.
