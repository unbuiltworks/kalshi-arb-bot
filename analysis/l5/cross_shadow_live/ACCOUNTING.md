# Ledger D: is the deterioration real losses, delayed accounting, or both?

**Both — and they are separable, so the answer is not a hedge.** Read-only
investigation, 2026-09-15. No PMUS change, no order placed or cancelled, the
−$30.00 daily stop untouched, unconfirmed rows still excluded.

## 1. The economics really are getting worse

Computed on **venue-confirmed markets only**, so it cannot be an artifact of the
accounting gap. The 32 confirmed settlements in chronological halves:

| | n | sum | mean | median | winners |
|---|---|---|---|---|---|
| first half | 16 | −11.1971 | −0.6998 | −0.9570 | 4 / 16 |
| second half | 16 | −36.5345 | −2.2834 | −1.7280 | **1 / 16** |

Mean loss per market **more than tripled**; winners fell from 4-in-16 to
1-in-16. This is real economic deterioration in the part of the book we can see.

## 2. "Delayed accounting" is the wrong label — the gap is terminal

The natural assumption is that `SETTLED_REALIZED_UNCONFIRMED` is a queue that
drains. It is not.

**Observed state transitions across the whole ledger:**

| from | to | count |
|---|---|---|
| PENDING | CONFIRMED | 29 |
| PENDING | UNCONFIRMED | 7 |
| **UNCONFIRMED** | **CONFIRMED** | **0** |

**No market has ever gone from unconfirmed to confirmed.** `honwon-yanhou` has
been unconfirmed for **29.4 hours**. Once a market lands there it stays. All
eight carry the identical cause — *"a trade lacks the venue's realizedPnl"* —
so this is one mechanism, not a variety of coincidences.

## 3. How much is invisible

| | n | ledger_qty | realized |
|---|---|---|---|
| confirmed | 32 | 306.26 | **−47.7316** |
| unconfirmed | 8 | 133.18 | — (excluded) |

**30.3 % of traded quantity sits in a terminal unaccounted state.** The
−$47.7316 is computed over a shrinking share of what the probe actually traded,
and the unconfirmed share is *rising*: 0 % through most of 09-14, then 33–50 %
late on 09-14, then **100 % at 09-15T08 and 09-15T09**.

They stay excluded. No estimate is substituted for a missing `realizedPnl`.

## 4. nicpar-gabpen is a known alarm class, not a mystery

I flagged this for hours as "left the risk set without a venue-confirmed
settlement, cause unknown". The ledger already contains the answer, and it is an
instrumented alarm the system raised itself:

```
kind: risk_set_alarm   alarm: held_absent_while_risk_bearing
slug: aec-itfme-nicpar-gabpen-2026-09-14   at 2026-09-14T12:36:54Z
was_scheduled: true   still_risk_bearing: true
terminal_settlement_recorded: false
action: "none taken; read-only alarm by owner decision"
```

The alarm's own reading: *"left the scheduler's held set with no settlement row.
Its full downside is STILL charged against the daily stop, so this is an
accounting/observability signal, not an uncovered-risk one."*

**A second instance exists** — `noalui-eneper-2026-09-14`, 2026-09-14T15:27:45Z,
same alarm class. So this is a recurring mechanism with n = 2, not a one-off.

**Correcting my own earlier framing:** I said nicpar-gabpen "left the risk set".
At the time of the alarm it was still `risk_bearing: true` and had left the
*scheduled/held* set — a different thing. It has since left `risk_bearing` too,
without ever recording a settlement.

**What is distinctive about it:** it holds **1** settlement row, where
comparable pending markets hold 10–26 (`tatmar-taytow` 26, `blaell-hirmor` 18,
`kanpet-zijcui` 10). It was checked once, at 2026-09-14T11:53:58Z, and never
re-polled. Its last session ended `SPRING_MATCHED`, `flat_verified: true`,
residual 0.00 — fully matched and flat.

That is consistent with flat/matched state being treated as "done" for polling
purposes, which is exactly the distinction the `risk_set` label says must not be
made: *"A market leaves the risk set only on a venue-confirmed settlement row."*
**Consistent with, not established** — confirming the mechanism needs the
scheduler's own code path, and this investigation is read-only.

**It is not in either bucket.** nicpar-gabpen is PENDING, so it is neither in
the −47.7316 nor in the 8 excluded. It is simply unaccounted.

Four other pending markets have not been re-polled in over 30 h
(`andfen-ryaseg` 33.8 h, `sea-ath` 35.8 h, `cribuc-biaand` 34.4 h,
`kanpet-zijcui` 30.8 h).

## 5. Daily stop

Unchanged at **−$30.00**. Not reset, widened, postponed or bypassed. Latest
authoritative `risk_set` row: `daily_realized −22.6851`, `combined_loss
−23.0226` — **77 % consumed**. Today's path: −12.10 (05:09) → −14.13 (05:18) →
−21.43 (07:25) → −23.02 (10:15).

Note the direction of the accounting error: markets that drop out of
`risk_bearing` without settling stop being charged, so the stop may now
*under*-count exposure relative to its own definition.

## 6. Rejection bursts — separate investigation, no change proposed

Distinct `order_id` with `rejected_at_non_crossing_price`, never log rows:

| window | distinct order_ids |
|---|---|
| earlier | 3 |
| 02:20 → 05:28 | 6 |
| 05:25 → 07:28 | 8 |
| 07:25 → 10:50 | 11 |

They arrive in **bursts of three at a single instant** (07:39:29, 09:09:11,
10:16:42), consistent with the established stale-book race. The diagnosis is
**not** re-derived and **no retry or aggression change is implemented or
proposed**. The book-age pattern still holds: per-market `age_at_probe` p50 runs
6.5–13.4 s with contemporaneous(≤5 s) at 21–38 %.
