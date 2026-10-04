# Momentum + pullback exit: REJECTED out of sample. 805 windows, 24× the sample.

**Verdict: all three preregistered candidates are negative at every size, in every
duration, on every slice except the original 33 BTC 15m windows. The capacity curve
is monotonically decreasing, so no size maximizes executable dollars per window —
the argmax is the smallest size tested, which is the curve saying "don't trade."**

Do **not** proceed to realtime shadow. Do **not** define a bounded SELL live probe
for this candidate. `sell_path.py` stays disarmed.

## What changed: the sample, and nothing else

`run_window` is imported **unmodified** from `scripts/momentum_pullback_backtest.py`.
Same ask-ladder entry, same bid-ladder exit, same pullback-measured-on-the-bid,
same exit-before-extend ordering, same 60s cadence. The only edits to that file were
purely additive instrumentation (slippage, bid depth, unsellable exits, NO_TRADE
counts) and a `coin=None` load option; **the ten original arms reproduce identically**
— verified field-by-field against the pre-instrumentation run before anything was
expanded.

| file | windows | coins | sha256 |
|---|---|---|---|
| `updown_5m.parquet` | 471 | 8 | `166822b2…ae118d11` |
| `updown_15m.parquet` | 265 | 8 | `d8619150…2256bd0be959` |
| `updown_4h.parquet` | 69 | 8 | (recorded in the runner) |

805 windows total. The original result used **BTC 15m alone — 33 windows, one coin
of eight, one duration of three.**

Candidates fixed before the run, as preregistered: **C1** momentum/no-base 3¢,
**C2** momentum/no-base 5¢, **C3** liquidate-all 5¢. No fourth arm, no threshold
search, two-sided base not mandatory (C1/C2 carry none).

## The capacity curve (non-BTC out-of-sample, net $/window)

| size | 5m | 15m | 4h |
|---|---|---|---|
| $2 | −0.78 | −2.13 | −26.15 |
| $5 | −2.03 | −5.37 | −76.67 |
| $7.50 | −2.83 | −8.10 | −127.15 |
| $10 | −3.69 | −10.95 | −184.18 |
| $12.50 | −4.55 | −13.88 | −237.27 |
| $15 | −5.44 | −16.87 | −294.18 |
| $20 | −7.33 | −23.11 | −415.55 |
| $25 | −9.38 | −29.74 | −556.58 |

(C2; C1 and C3 differ by cents at 15m/5m and are the same shape.) **There is no
interior optimum.** Loss grows faster than linearly in size — exit slippage against
the bid ladder rises from 0.12% of touch at $2 to 2.0% at $25 on 5m, and 0.63% → 11.4%
on 4h. `largest_size_with_measured_depth` is `None` at every duration: even $2 leaves
shares unsold at some exits. Per the standing instruction, nothing is extrapolated
toward $5/window — the measured curve runs the other way.

## Why the original result happened: it was one coin, at one duration

C2 at $2/extension, per coin:

**15m — 2 of 8 coins positive**

| coin | n | net/win | after 2% fees | win % | extension *realized* | settlement | max DD |
|---|---|---|---|---|---|---|---|
| btc | 33 | **+0.84** | +0.32 | 72.7 | +10.84 | +16.76 | −7.52 |
| xrp | 31 | +0.12 | −0.40 | 41.9 | −41.91 | +45.63 | −23.42 |
| eth | 31 | −0.17 | −0.67 | 54.8 | −16.16 | +10.96 | −17.41 |
| bnb | 27 | −0.88 | −1.40 | 37.0 | −31.77 | +7.90 | −27.63 |
| sol | 34 | −1.25 | −1.75 | 29.4 | −44.16 | +1.74 | −42.42 |
| doge | 42 | −1.59 | −2.07 | 33.3 | −68.25 | +1.61 | −66.64 |
| hype | 32 | −1.79 | −2.29 | 28.1 | −72.73 | +15.48 | −58.80 |
| zec | 35 | **−8.63** | −8.89 | **0.0** | −299.15 | −2.83 | −301.98 |

**5m — 1 of 8 positive at $2 (eth, by +0.0016), 0 of 8 at $10. BTC itself is
negative at 5m: −0.15/window, 46.8% win rate.**

So the effect is narrower than "BTC works": it is *BTC at the 15-minute duration in
this particular 33-window tape*. The same coin at 5m loses. Raising size to $10 at
15m leaves **1 of 8** coins positive.

Two mechanisms, both visible in the table:

1. **The exit itself loses money.** `extension_realized` — the only line the pullback
   rule actually produces — is **negative in 15 of 16 coin/duration cells**. The one
   exception is eth 5m at +3.69 total, i.e. noise. A 3–5¢ retreat from the post-entry
   high is not a signal; it is the ordinary oscillation of a book whose spread and
   tick size are of that order, so the rule systematically sells into a dip and buys
   the next tick higher.
2. **What carried BTC 15m was settlement, not the exit.** +16.76 settlement vs
   +10.84 realized. That is the residual directional leg winning 72.7% of 33 windows
   — a 33-sample coin-flip streak, not an edge. `CLAUDE.md` is explicit that residual
   directional exposure is not assumed to carry skill, and this is what that rule is
   for.

Win rate tracks flip frequency inversely, which is the same arithmetic that killed
the earlier FAVORITE/FLIP rule: zec flips constantly and loses every single one of
35 windows.

## Fees make it worse, not marginal

The 2% sensitivity is additive on gross bought, and gross bought scales with size, so
the fee column diverges from the gross column as size grows. At no (duration, size,
candidate, slice) combination outside original-BTC-15m does the fee-adjusted number
reach zero. **The candidate does not need a better fee estimate to be rejected.**

## Instrumentation findings worth keeping

- **Exits that could sell nothing at all**: 10 at 15m, 5–7 at 5m, 0 at 4h. This is
  the venue's documented no-live-sell-quote state appearing in a historical tape. It
  never occurred in the original 33 windows (`unsold_shares = 0` throughout), which
  is precisely why that sample could not test the exit.
- **Partial exits** rise from 4 at $2 to 34 at $25 (15m) — the pullback rule cannot
  be executed in full at commercial size even when it is executable at all.
- **Entry slippage is negligible at $2 and material by $25**; exit slippage is the
  dominant term throughout, as expected for a strategy whose P&L is the sell.

## What this does and does not close

**Closed:** momentum + pullback exit as a candidate for capital, in all three
preregistered forms, at every size on the dense $2–$25 grid. The 33-window positive
was a single-coin single-duration artifact and is now explained rather than merely
doubted.

**Not closed, and not touched by this:** the frozen .96 Gemini control (untouched,
fingerprint `5c113fab…6329`), and the L5 replication program, which is a different
question on different data. The SELL capability work stands on its own — the executor
path is real, documented and disarmed, and it is what any *future* exit-bearing
strategy would use. It simply has nothing to execute yet.

Accounting label: **historical replay on third-party public data.** No order was
sent. Nothing here is realized profit.
