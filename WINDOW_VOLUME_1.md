# BTC5m window volume — executed, from the tape

**Scope.** Every BTC5m window this programme has captured: the three shadow
soaks (12 windows) and the live CONTINUOUS windows (7 that ran). Two C8
dispatches that were refused before the latch never selected a window and are
excluded.

**Method.** `GET /v1/trades/{symbol}`, PUBLIC and unauthenticated. **The tape
carries `amount`**, so contracts and dollar notional are *measured*. **And it
serves expired contracts** — C8B's tape returned its complete six-print life
ten hours after settlement — so every window can be read in full, including
the live windows that recorded no tape at the time.

**Displayed depth appears in its own column and is added to nothing.**

> **SNAPSHOT AT 19 WINDOWS.** Every table below is the first pass. Soak #4
> later added 7 windows; the totals become **26 windows · 154 prints ·
> 3,686.14 contracts · $2,093.71**, the 100+ bucket's share of notional goes
> 77.7% → **78.7%**, and TTE 30–180 goes 87.6% → **88.5%**. The per-TTE and
> attribution shares moved more than that, and
> `LARGE_PRINT_FORENSIC_1.md` §7 records by how much. This document is kept
> at 19 windows as the record of what was known when the question was first
> answered.

Reproduce: `python3 -m src.gemini_execution.volume_report` →
`analysis/gemini/window_volume.json`. Code: `window_volume.py` · 16 tests.

---

## 0. Two structural facts that were wrong in everything I reported before

**(a) Prints can precede the contract's own `effectiveDate` — but not
always, and I over-stated this first time.** Across these 19 windows the first
print lands at TTE **375.9–378.2 s**, median 377.0 — a 2.3-second band across
19 independent contracts, which is an event rather than chance. The contract
object's `effectiveDate` really is exactly 300 s before expiry, so in these
windows trading began about 77 seconds before it.

> **CORRECTED after soak #4.** I wrote that "a BTC5m contract's tradeable life
> is ~377 seconds, not 300" and stated it as structural. It is not. The 7
> windows captured 11:35–12:05Z have their first print at TTE **268.7, 216.3,
> 204.2, 197.4, 171.4, 160.8 and 39.3** — none before the effective window at
> all. So the split is **19 windows with a pre-effective opening burst, 7
> without**, and the tradeable life is not a constant. What is constant is
> `effectiveDate` at exactly 300 s. Rates below use the measured first-print
> span per window, which remains right; the generalisation was wrong.

**(b) 55 of 125 prints — 44% — land before TTE 300**, and **60 of 125 (48%)
land before our entry band can even open at TTE 280.** They are small: those
60 prints are only **$122.71 of $1,543.69 (7.95%)**.

## 1. The table, ranked by executed activity

| WINDOW | PRINTS | SHARES | $ NOTIONAL | PR/MIN | DEAD GAP | TOUCH DEPTH | OUR FILL? |
|---|---|---|---|---|---|---|---|
| …190140 (C8B) | 6 | 735.69 | **573.08** | 0.96 | 165.1 s | — | **YES** |
| …190605 | 12 | 791.79 | **395.93** | 1.91 | 130.8 s | 99.5 | no |
| …190430 | 10 | 693.98 | **354.71** | 1.59 | 138.3 s | 10.0 | no |
| …190310 | 17 | 141.79 | 38.08 | 2.70 | 71.4 s | 10.0 | no |
| …181730 (C4) | 10 | 53.21 | 32.63 | 1.59 | 124.9 s | — | **YES** |
| …190440 | 9 | 62.21 | 27.93 | 1.43 | 240.9 s | 10.0 | no |
| …190555 | 7 | 26.93 | 21.28 | 1.11 | 129.1 s | 188.0 | no |
| …190305 | 6 | 27.12 | 19.54 | 0.96 | 199.8 s | 221.5 | no |
| …190445 | 6 | 27.48 | 18.87 | 0.96 | 156.0 s | 192.0 | no |
| …181620 (C3) | 6 | 23.42 | 14.15 | 0.95 | 284.1 s | — | no |
| …190255 | 4 | 21.07 | 11.70 | 0.64 | 178.6 s | **750.0** | no |
| …182215 (C6) | 5 | 24.57 | 8.98 | 0.80 | 193.8 s | — | **YES** |
| …190300 | 4 | 13.70 | 6.83 | 0.64 | 298.8 s | 10.0 | no |
| …190610 | 3 | 9.33 | 6.60 | 0.48 | 227.4 s | 205.0 | no |
| …182110 (C5) | 4 | 10.63 | 4.01 | 0.64 | 210.5 s | — | no |
| …181435 (C2) | 8 | 30.66 | 3.02 | 1.27 | 249.6 s | — | no |
| …190600 | 3 | 5.89 | 3.00 | 0.48 | 221.6 s | 10.0 | no |
| …190435 | 3 | 11.96 | 2.56 | 0.48 | 229.3 s | 10.0 | no |
| …182330 (C7) | 2 | 1.56 | **0.77** | 0.32 | 375.6 s | — | no |

**Totals: 19 windows · 125 prints · 2,712.99 contracts · $1,543.69.**

Per-window extras (full detail in the JSON): active seconds with a print,
entry-region prints, quoting-region prints, YES/NO demand split, median and
max print size, largest print with its TTE, and our own fills matched **by
tid** — never by price, because someone else trading at our price is not our
fill.

**C5 shows `no` in the fill column, and that is the column being strict
rather than the fill being absent.** Our C5 fill *is* on the tape — `0.01 @
0.54, buy` — but the OUR FILL column matches by `tid`, which needs the venue's
own fill timestamp from the window's terminal read, and C5's terminal read
happened *before* the fill (it landed 57 s after the window stopped). The fill
is confirmed in `reconcile_slot_20260918T214547Z_c5s0.json` and is counted in
the fill census; only this column misses it.

**That row also taught the tape's price convention.** Our order was a passive
**NO buy at 0.46**; it appears on the tape as an aggressor **YES buy at
0.54**, because selling NO to us *is* buying YES. The tape is priced in YES
and records the aggressor's side. The overlay study's fill test had used the
YES rule for NO rungs until this row was checked against it.

## 2. Displayed depth does not predict volume, and is close to inverted

- The **deepest** book we recorded (**750** at the touch) traded **$11.70**.
- The two busiest windows by notional had touch depths of **10.0** and 99.5.
- Rank correlation of touch depth against realised flow across 12 soak
  windows: **+0.02**. Spread: −0.06. Probability band: +0.18.

This is the same lesson C8B taught at cycle resolution: it joined a bid
showing **483** contracts, and one cycle later that level showed **0.01** —
ours, alone. **Resting size is not liquidity here.**

## 3. Where the money actually is

| TTE bucket | prints | contracts | $ notional | share | median px |
|---|---|---|---|---|---|
| 370–380 (the open) | 43 | 51.74 | 25.41 | 1.6% | 0.50 |
| 300–370 | 12 | 120 | 65.90 | 4.3% | 0.60 |
| 280–300 | 5 | 50 | 31.40 | 2.0% | 0.64 |
| **240–280 — our entry band** | 11 | 67.33 | 38.61 | **2.5%** | 0.61 |
| 180–240 | 13 | 95.76 | 30.20 | 2.0% | 0.31 |
| **120–180** | 21 | 673.49 | 323.96 | **21.0%** | 0.43 |
| **60–120** | 13 | 405.01 | 245.67 | **15.9%** | 0.75 |
| **30–60** | 7 | 1,249.66 | **782.54** | **50.7%** | 0.44 |
| 0–30 | 0 | 0 | 0 | 0% | — |

**87.6% of all notional trades inside TTE 30–180.** The single busiest bucket,
**TTE 30–60, carries 50.7%** — and our envelope's `withdraw_all_tte_s` is
**30**, with `no_new_open_extend_tte_s` at **60**. We stop adding liquidity at
the start of the busiest sixth of the contract's life and withdraw entirely at
its end.

There is also a reliable **opening event**: 43 prints across all 19 windows,
two per window, at exactly 0.48 or 0.50, median size 1 contract, one buy and
one sell. It looks like an opening cross. It is worth **$25.41 in total** —
structurally interesting, economically nothing.

## 4. Can the venue confirm the tape independently?

**No.** The contract object publishes `prices`, `strike`, `resolutionSide`,
`settlementValue` and 20 other fields, and **no volume or open-interest field
at all**. `/v1/symbols/details/{symbol}` returns tick size and status, no
volume. `/v1/ticker/{symbol}` and `/v2/ticker/{symbol}` are **404** for
prediction symbols. `/v1/tradevolume` is *our* maker volume, not the market's.

So the tape is the only executed-volume source, and there is **no independent
reconciliation available**. What can be said: the tape's own internal
consistency is good — our C8B fill appears on it exactly once, at the right
millisecond, price and size, with a `tid` matching the venue's own fill
record, and no window returned a truncated tape.

## 5. The answer

> **Are these markets genuinely low-volume, or are we simply failing to
> observe/measure some of their volume?**

**Both, and mostly the second.** They are thin, not dead: **$1,543.69 across
19 five-minute windows, a median of $14.15 and a mean of $81.25 per window**,
with single prints as large as **714 contracts ($564)**. What made them look
dead was our measurement:

1. **The soak counted prints, not size.** A print count treats a 0.34-contract
   trade and a 714-contract trade identically. On a notional basis the busiest
   window is 740× the quietest; on a print count it is 8×.
2. **The soak stopped watching too early.** It runs 22 cycles at 8 s from TTE
   ~280 and stops around TTE ~104 — before the buckets carrying **66.6%** of
   all notional.
3. **The denominator was wrong.** Rates were computed over a 300-second life
   that is really ~377 seconds.
4. **Displayed depth was doing work it cannot do.** It correlates +0.02 with
   flow.

**But the venue is not the whole story either.** $1,420.98 of the $1,543.69 —
**92%** — traded inside TTE 30–280, where our orders *could* have been
resting. We were present for the money and did not interact with it. Why that
is, is the overlay study's question, not this report's.

**What this does not license.** It is not an argument for moving the entry
band, changing `withdraw_all_tte_s`, or quoting into the last 30 seconds. The
TTE 30–60 bucket trades at a median price of 0.44 with the outcome nearly
determined, and the envelope's late-window bounds exist to avoid exactly that.
The tension is real and it is the owner's to resolve, not mine.
