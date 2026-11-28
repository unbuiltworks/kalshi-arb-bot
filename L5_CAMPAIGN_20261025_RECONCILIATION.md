# L5 — eight-window live campaign, final reconciliation (2026-10-25 00:30Z–02:30Z)

Experiment: the fresh no-key campaign authorized 2026-10-25. Deployment
`01be74b3`. Campaign closed itself on `max_windows = 8`
(`armed: false`, `halt_latch: CLEAR`, `submission_gate.reason: NOT_ARMED`).
Approval ran to 04:00Z and was **not** used up by time; the window budget bound
first. No configuration was changed, no limit raised, nothing redeployed, and
the campaign was not extended.

All P&L below is **settled** (`VENUE_SETTLEMENT_PX` / `MARKET_STATE_EXPIRED`).
Every leg carries `commission: 0.0000`, so fee-inclusive equals gross. **No
maker rebate was credited**; the schedule estimate is $0.00 at these clip sizes
(banker's rounding to the cent) and is not counted anywhere here.

---

## 1. Settled net P&L by window

Log `avg_px` is the **wire (YES/UP)** price. A DOWN leg's own cost is `1 − wire`.
Pair cost below is in own-cost terms, against the `0.96` fee-safe ceiling.

| Window | Pairs | Legs (own cost) | Pair cost | Settled |
|---|---|---|---|---|
| 0030z | 1 | UP 1.61 @ 0.55 + DOWN 1.61 @ 0.27 | 0.82 | **+$0.289800** |
| 0045z | 1 | UP 1.66 @ 0.50 + DOWN 1.66 @ 0.45 | 0.95 | **+$0.083000** |
| 0100z | 2 | UP 2.00 @ 0.20 + DOWN 2.00 @ 0.76<br>UP 0.43 @ 0.17 + DOWN 0.43 @ 0.75 | 0.96<br>0.92 | **+$0.114400** |
| 0115z | 2 | UP 1.78 @ 0.40 + DOWN 1.78 @ 0.53<br>UP 1.68 @ 0.48 + DOWN 1.68 @ 0.24 | 0.93<br>0.72 | **+$0.595000** |
| 0130z | 0 | DOWN 1.87 @ 0.62 — **stranded, no complement** | — | **−$1.159400** |
| 0145z | 1 | DOWN 1.56 @ 0.35 + UP 1.56 @ 0.52 | 0.87 | **+$0.202800** |
| 0200z | 0 | no orders placed (OPEN budget exhausted) | — | no entry |
| 0215z | 0 | no orders placed | — | no entry |

**Campaign settled net: +$0.1256.**

Ledger check: `realized_today` (account-wide, all-time) moved
**−11.6213 → −11.49570000**, a delta of **+$0.1256**. Matches exactly.

### Stranded inventory loss, isolated

- Seven completed pairs earned **+$1.2850** in total.
- The one stranded leg lost **−$1.1594** — a **total** loss of its own cost
  (1.87 × 0.62 = 1.15940), i.e. it redeemed at zero.
- The single strand consumed **90.2%** of all pair gains.

---

## 2. Every submission accounted for

35 submissions spent of 56. Class budgets at close:
**OPEN 16/16 (EXHAUSTED)**, COMPLETE 14/24, REPLACE 5/32.

35 orders appear in `L5_LIVE_LIFECYCLE`, and the per-order classification below
reconciles **exactly** against the authorization counters at five independent
checkpoints (00:38:03 `O1 C1 R1`; 00:39:04 `O2 C1 R2`; 01:20:33 `O11 C9 R4`;
01:21:34 `O13 C11 R4`; 01:24:41 `O14 C12 R4`; close `O16 C14 R5`).

| Terminal state | Count |
|---|---|
| `ORDER_STATE_FILLED` | 15 |
| `ORDER_STATE_CANCELED` (all `cum_qty 0`) | 12 |
| `ORDER_STATE_REJECTED` (all `cum_qty 0`) | 6 |
| never terminal — last seen `ORDER_STATE_NEW` | 2 |
| **total** | **35** |

15 fills = 7 completed pairs (14 legs) + 1 stranded leg.

### The 16 OPEN budget claims, classified

| # | coid | Window | Side | Outcome |
|---|---|---|---|---|
| 1 | `13deb91a` | 0030z | UP | **filled** 1.61 @ 0.55 → complemented (pair 0.82) |
| 2 | `c57c734c` | 0030z | DOWN | **rejected**, `cum_qty 0` |
| 3 | `bce06c54` | 0030z | UP | **cancelled** unfilled |
| 4 | `2ab16ab4`/`68d54a5d` | 0045z | — | **filled** → complemented (pair 0.95) |
| 5 | `0d3bbd04` | 0045z | DOWN | **rejected**, `cum_qty 0` |
| 6 | `7bc340c2` | 0045z | DOWN | **cancelled** unfilled |
| 7 | `0b806921` | 0100z | UP | **filled** 2.00 @ 0.20 → complemented (pair 0.96) |
| 8 | `5e627a91` | 0100z | UP | **rejected**, `cum_qty 0` |
| 9 | `793eb766` | 0100z | DOWN | **cancelled** unfilled |
| 10 | `d8fab1eb` | 0100z | UP | **rejected**, `cum_qty 0` |
| 11 | `cf00ab77` | 0115z | UP | **filled** 1.78 @ 0.40 → complemented (pair 0.93) |
| 12 | `6392fd06` | 0115z | UP | **cancelled** unfilled |
| 13 | `dd9b1c54` | 0115z | DOWN | **unresolved** — never left `ORDER_STATE_NEW` |
| 14 | `2334d6b4` | 0115z | UP | **cancelled** unfilled |
| 15 | `491f0e82` | 0130z | DOWN | **filled** 1.87 @ 0.62 → **never complemented (−$1.1594)** |
| 16 | `9965cee3` | 0145z | DOWN | **filled** 1.56 @ 0.35 → complemented (pair 0.87) |

**OPEN summary: 5 filled-and-complemented · 1 filled-and-stranded · 4 rejected ·
5 cancelled unfilled · 1 unresolved.**

Only **5 of 16** OPEN tickets produced a completed pair — a 31% conversion.
Two pairs (0100z pair B, 0115z pair B) consumed **no** OPEN ticket at all: both
their legs classed COMPLETE, because each reduced an imbalance left behind by a
rejected sibling.

### COMPLETE (14) and REPLACE (5)

- **COMPLETE 14** = 6 filled pair legs · 2 rejected (`49f3944d`, `62268e6c`) ·
  5 cancelled unfilled · 1 unresolved (`be31b2bd`, 0130z).
- **REPLACE 5** = 3 filled (`78b76e00` 0030z, `dde44694` 0100z, `bec4b707`
  0115z — each the leg that actually *completed* its pair) · 2 cancelled.

---

## 3. The four requested distinctions

**(a) Rejected because they would cross the book — 6.** All `cum_qty 0`,
17.1% of the 35 submissions. **Exactly 4 of the 6 consumed OPEN tickets**
(`c57c734c`, `0d3bbd04`, `5e627a91`, `d8fab1eb`); the other 2 (`49f3944d`,
`62268e6c`) consumed COMPLETE tickets.

**(b) Cancelled or replaced without filling — 12 cancelled** (`cum_qty 0`),
plus **2 never resolved** (`dd9b1c54`, `be31b2bd` — stuck in `ORDER_STATE_NEW`,
no terminal event ever observed). The 2 cancelled REPLACEs are re-prices that
did not fill; the 3 filled REPLACEs are counted with their pairs.

**(c) Opening orders that filled but never received a complement — 1.**
`491f0e82`, 0130z, DOWN 1.87 @ own 0.62. This is the whole stranded loss.

**(d) Completed pairs and their settlement profits — 7**, totalling **+$1.2850**:
0030z +$0.2898 · 0045z +$0.0830 · 0100z +$0.1144 (two pairs combined) ·
0115z +$0.5950 (two pairs combined) · 0145z +$0.2028.

Ticket cost per pair varied: OPEN+REPLACE (3 pairs), OPEN+COMPLETE (2 pairs),
COMPLETE+COMPLETE (2 pairs).

---

## 4. Would the pre-send crossing guard have prevented the six rejections?

Checked against `L5_FILL_CALIB_PRESUBMIT`, which records `own_px`, `wire_px`,
`bid`, `ask` and `stream_age_s` with `capture_source:
mirror_snapshot_at_read` — the authoritative book as read immediately before
each send, which is the same read the guard performs. Rule applied is the
venue's own post-only rule, `TS.leg_crosses`: UP crosses when `wire ≥ ask`;
DOWN crosses when `wire ≤ bid`.

| # | coid | Side | own | wire | bid / ask | age | Crossing at the pre-send read? |
|---|---|---|---|---|---|---|---|
| 1 | `c57c734c` | DOWN | 0.40 | 0.60 | 0.7500 / 0.7600 | 0.001s | **yes** — 0.60 ≤ 0.75 |
| 2 | `0d3bbd04` | DOWN | 0.45 | 0.55 | 0.6700 / 0.6800 | 0.188s | **yes** — 0.55 ≤ 0.67 |
| 3 | `49f3944d` | UP | 0.66 | 0.66 | 0.5800 / 0.5900 | 0.063s | **yes** — 0.66 ≥ 0.59 |
| 4 | `5e627a91` | UP | 0.20 | 0.20 | 0.1900 / 0.2000 | 0.002s | **yes** — 0.20 ≥ 0.20 (at the touch) |
| 5 | `d8fab1eb` | UP | 0.12 | 0.12 | 0.0600 / 0.0700 | 0.001s | **yes** — 5 ticks through |
| 6 | `62268e6c` | DOWN | 0.55 | 0.45 | 0.5100 / 0.5200 | 0.068s | **yes** — 0.45 ≤ 0.51 |

**All six were already crossing at the freshest authoritative read, so the guard
would have declined all six** and kept 4 OPEN + 2 COMPLETE tickets.

This was not assumed — it was measured per order, and the guard's selectivity
was checked on the siblings that were *not* rejected. Each correctly does not
cross and each did rest: `7bc340c2` (DOWN own 0.29, wire 0.71 vs bid 0.57),
`44e7058a`/`793eb766` (DOWN own 0.78, wire 0.22 vs bid 0.19), `3e1ad5c4`
(DOWN own 0.83, wire 0.17 vs bid 0.06), `6392fd06` (UP own 0.40 vs ask 0.52),
`dd9b1c54` (DOWN own 0.47, wire 0.53 vs bid 0.50), `2240727d` (UP own 0.48 vs
ask 0.51). Zero false positives on the sample.

Note on #2 and #5: these were not tight races. #2's frame was 0.188s stale and
#5 was priced five ticks through the ask, i.e. the *plan* was stale rather than
the book being unlucky. A pre-send re-read catches both.

---

## 5. Why 0130z stranded — established from source

This is the dominant economic event of the campaign, so it is traced in full.

1. **01:31:27.756** — plan placed: UP `be31b2bd` own 0.33 (wire 0.33) and DOWN
   `491f0e82` own 0.62 (wire 0.38), 1.87 each, book **0.3500 / 0.3600**.
   Planned pair cost **0.95 ≤ 0.96** — compliant. Neither leg crossed. The plan
   was correct and the guard would not have touched it.
2. **DOWN filled, UP did not.** The DOWN was resting on the thinner side
   (`level_depth 372` vs the UP's `920`); the market fell through it.
3. **01:33:03** — one REPLACE of the UP leg at own 0.31, book now
   **0.3300 / 0.3400**. Implied pair 0.93, still compliant. It did not fill.
   The maker ladder re-quotes behind each new touch, so a falling market leaves
   the UP leg permanently behind.
4. **The market then reversed hard.** By ~01:40 the book was
   **0.9200 / 0.9300**. Completing the pair under the 0.96 ceiling needed UP at
   own ≤ 0.34; UP now cost ~0.92, implying a pair of ~1.54. **Completion had
   become economically impossible, not merely blocked.**
5. Decision records in the gap show `key:
   TWO_SIDED_PROBE:PAST_COMPLETE_CUTOFF` (38 cycles), with
   `plan_unexecutable: LEG_WOULD_CROSS:DOWN` and `retirement_blocked_by:
   LEG_HOLDS_INVENTORY:DOWN`.
6. 0130z settled YES/UP; the naked DOWN redeemed at zero → **−$1.1594**.

Nothing malfunctioned. The pair ceiling, the cutoff and the watchdog all behaved
as specified. What failed was carrying an unhedged 1.87-contract residual
through a ~57-cent adverse move. This is precisely the exposure
`L5_OPERATING_MODEL.md` warns about: residual directional exposure has no
demonstrated predictive skill.

---

## 6. Single highest-impact next change, by realized economics

Ranked by measured dollars, not by how broken each thing looks.

| Candidate | Measured value |
|---|---|
| Pre-send crossing guard (built, validated, **not** deployed) | recovers 6 tickets (4 OPEN). At the observed 31% OPEN→pair conversion and $0.1836 mean pair profit, ≈ **+$0.23** |
| Eliminating the single strand | **+$1.2342** swing on the same fills |

**Recommendation: bound the one-sided-fill residual. It is worth roughly five
times the crossing guard.**

The arithmetic that makes it the priority: at 01:33, with the book at
0.3300/0.3400, the stranded DOWN (own cost 0.62) could have been closed at the
NO bid of 0.66 for **+$0.0748**. It was carried to expiry instead and realized
**−$1.1594**. That one decision is a **$1.2342** swing — it would have taken the
campaign from +$0.1256 to about **+$1.36**, a ~10× improvement with no extra
fills, no extra submissions, and no change to any approved limit.

The risk/reward of the opening leg is the underlying defect: a 1.87 clip at own
0.62 risks **$1.1594** of total loss against a mean realized pair profit of
**$0.1836** — about **6:1 against**. The cheapest correction needs no new
permission and *reduces* risk: **scale the opening clip down as the opening own
price rises**, so worst-case strand loss stays proportionate to expected pair
profit. A high-priced first leg is the expensive one to be wrong about, and
`MIN_FIRST_LEG_PRICE = 0.20` currently bounds only the cheap end.

The larger-upside variant — actively exiting a stranded residual — requires
crossing the spread, which the current authorization forbids ("maker-first",
"no automatic late taker behavior copied from mature August L5"). **It is not
implemented and must not be armed without explicit owner authorization.** It is
recorded here as the option with the biggest measured payoff, for a decision.

Secondary, cheap, and independent of the above: **2 of 35 orders never reached a
terminal state** (`dd9b1c54`, `be31b2bd`). In 0115z that was survivable; in
0130z the unresolved order was the complement of the leg that stranded. Order
resolution should be made to converge, or treated as an alarm.

---

## 7. Accounting labels

- Realized figures are **settled** only. No theoretical fill, modelled pair
  profit, mark-to-market gain or estimated rebate is counted as realized.
- `realized_today` is an **all-time** account-wide total, not a daily figure.
- The historical attribution discrepancy (`identity_holds: false`, 49
  unknown-cost-basis legs) is **unchanged and still open** — see
  `L5_OUTSTANDING_ACCOUNTING_ISSUES.md` §A1. Nothing here reset, reclassified or
  netted against it.
- $480/day remains a target to measure against. This campaign realized
  **+$0.1256** across eight windows.
