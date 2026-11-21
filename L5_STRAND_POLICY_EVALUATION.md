# L5 — stranded-leg opportunity: executability, policy comparison, and the smallest justified change

Read-only investigation. **Nothing was deployed, rearmed, or changed; no limit
was raised; no sell/taker path was enabled or written.** The engine and its
risk limits are untouched.

This supersedes the recommendation in `L5_CAMPAIGN_20261025_RECONCILIATION.md`
§6. That section named a residual exit as the highest-impact change on a
+$1.2342 swing. **That figure was gross, latency-free, and wrong as an
expectation.** The corrected analysis is below and reaches a different
conclusion.

---

## 1. Was the 0.66 DOWN bid executable for 1.87 contracts at 01:33?

**Partly yes — the depth was there, the price was real, but the opportunity was
7–20 seconds wide and worth about 4.5 cents net.**

Order-side semantics first, since the arithmetic depends on them. The engine
records `wire is YES/UP; own cost of DOWN at wire p is 1 - p`, and a *buy* of
DOWN rests on the **ASK** (`resting_side: ASK` on `491f0e82`). So buying DOWN is
selling YES, and therefore **selling our DOWN is buying YES, which crosses the
YES ask.** That makes "sell the DOWN at the NO bid" and "taker-complete the pair
by buying UP" the *same wire action at the same price*:

- sell DOWN 1.87 at NO bid 0.66  →  1.87 × (0.66 − 0.62) = +$0.0748
- buy UP 1.87 at YES ask 0.34   →  pair 0.62 + 0.34 = 0.96, redeem $1.00 → +$0.0748

Identical. **The "residual exit" at 01:33 was not a new kind of trade at all —
it was a taker completion at exactly the existing 0.96 ceiling.**

### Depth, from the engine's own sampler

`L5_RESIDUAL_BOOK_SAMPLE` fires every 5s while inventory is unmatched and
records a full ladder plus a `taker_walk` sized to the actual need:

| time | YES ask top rung | walk for 1.87 | VWAP | pair if exited |
|---|---|---|---|---|
| 01:32:04 | 0.4400 × 973.00 | 1.87 | 0.4400 | 1.06 |
| 01:32:37 | 0.3900 × 364.73 | 1.87 | 0.3900 | 1.01 |
| 01:32:43 | 0.3600 × 1153.14 | 1.87 | 0.3600 | 0.98 |
| 01:32:50 | 0.3500 × 1229.48 | 1.87 | 0.3500 | 0.97 |
| **01:32:56** | **0.3300 × 244.72** | **1.87** | **0.3300** | **0.95** |
| **01:33:03** | **0.3400 × 1277.39** | **1.87** | **0.3400** | **0.96** |
| 01:33:10 | 0.4100 × 775.00 | 1.87 | 0.4100 | 1.03 |
| 01:33:44 | 0.3800 × 643.52 | 1.87 | 0.3800 | 1.00 |
| 01:34:04 | 0.4200 × 825.58 | 1.87 | 0.4200 | 1.04 |

At 01:33:03 the top rung displayed **1277.39 contracts against 1.87 needed**
(≈683×), `levels_used: 1`, `insufficient_displayed_depth: false`. Depth was not
the constraint.

### Fees and net

Taker fee is `θ·C·p·(1−p)` with `θ_taker = 0.0695`:

| exit point | pair | gross | taker fee | **net** |
|---|---|---|---|---|
| 01:32:56 (best) | 0.95 | +$0.0935 | −$0.0287 | **+$0.0648** |
| 01:33:03 (cited) | 0.96 | +$0.0748 | −$0.0292 | **+$0.0456** |

### Timing and latency — the real constraint

The exit was profitable (`pair ≤ 1.00`) only from **01:32:43 to ~01:33:44**, and
better than the 0.96 ceiling only from **01:32:56 to 01:33:03 — about 7
seconds.** Observed decision latency in this very window reached
**`latency_s: 8.818`**, and the sampler runs on a 5-second cadence. The engine
*did* have a real decision cycle at **01:33:03.366** (it submitted the maker
REPLACE `d2f217fb` there), so a taker policy evaluated on that cycle would have
captured +$0.0456. But one cycle inside a 7-second window, against ~9s latency,
is not a repeatable edge.

**Conclusion:** a displayed quote was not treated as a guaranteed fill, and the
honest figure is **+$0.046 net at the engine's own cycle**, not +$0.0748, and
certainly not a reliable +$1.23 expectation. The swing versus the realized
−$1.1594 is real (**+$1.2242**) but it is one draw from a 7-second window.

After ~01:34 the market ran to 0.92+, and by 01:44 the **ask side was entirely
empty** (`asks: []`, `n_asks: 0`, `insufficient_displayed_depth: true`). No
later exit existed at any price.

---

## 2. Three policies across the campaign, plus out-of-sample evidence

### Realized baseline — Policy A (existing maker-only pairing)

| window | outcome | P&L |
|---|---|---|
| 0030z | pair 0.82 | +$0.2898 |
| 0045z | pair 0.95 | +$0.0830 |
| 0100z A / B | pair 0.96 / 0.92 | +$0.0800 / +$0.0344 |
| 0115z A / B | pair 0.93 / 0.72 | +$0.1246 / +$0.4704 |
| 0130z | **strand** | **−$1.1594** |
| 0145z | pair 0.87 | +$0.2028 |
| **total** | | **+$0.1256** |

Mean pair profit **$0.1199 per matched contract** (10.72 matched contracts).
**Worst-case residual exposure: $1.1594** (one leg, 1.87 × own 0.62).

### Out-of-sample: L5's own 2,216 residual states

`src/l5_residual_state_replay.py` over L5's BTC/ETH 5m+15m history — 2,216
windows with a first leg, **1,610 paired (72.7%)**, 606 carried to settlement.
Completion by opening price (predictive — known when the residual opens):

| opening own px | n | completes |
|---|---|---|
| 0.0–0.1 | 95 | 28.4% |
| 0.1–0.2 | 114 | 67.5% |
| 0.2–0.3 | 109 | 80.7% |
| 0.3–0.4 | 142 | 80.3% |
| 0.4–0.5 | 315 | **88.9%** |
| 0.5–0.6 | 494 | 85.2% |
| **0.6–0.7** | **408** | **76.2%** |
| 0.7–0.8 | 257 | 60.7% |
| 0.8–0.9 | 184 | 46.7% |

Also: 15-minute windows complete **93.3%** vs 5-minute **65.7%** (our lane is
15m); and balanced markets (0.35–0.65) complete 84.2% vs lopsided (<0.20 or
>0.80) 48.8%.

**0130z's opening leg was own 0.62 — the 0.6–0.7 bucket, 23.8% strand
probability, measured on 408 out-of-sample windows.** It was not unlucky; it was
the second-worst-selected leg of the campaign.

### Expected value per contract

`EV(p) = P(complete|p) × $0.1199 − (1 − P(complete|p)) × p`

| opening px | EV / contract |
|---|---|
| 0.0–0.1 | **−$0.0017** |
| 0.1–0.2 | +$0.0322 |
| 0.2–0.3 | +$0.0485 |
| 0.3–0.4 | +$0.0273 |
| 0.4–0.5 | **+$0.0566** |
| 0.5–0.6 | +$0.0208 |
| 0.6–0.7 | **−$0.0633** |
| 0.7–0.8 | **−$0.2220** |
| 0.8–0.9 | **−$0.3971** |

EV goes negative **above 0.60** and again below 0.10. The existing
`MIN_FIRST_LEG_PRICE = 0.20` guards the cheap end. **There is no guard at the
expensive end, and that gap is exactly what 0130z walked through.**

Sensitivity: the 0.6–0.7 bucket turns positive only if mean pair profit exceeds
**$0.2030/contract — 69.3% above the measured $0.1199.** The sign is robust.

### Policy B — maker pairing with smaller high-priced opening clips

Modelled as capping worst-case opening notional (`own px × qty`) at $0.60:

| window | clip scale | P&L |
|---|---|---|
| 0030z | ×0.6776 | +$0.1964 |
| 0045z | ×0.7229 | +$0.0600 |
| 0115z A | ×0.8427 | +$0.1050 |
| 0130z | ×0.5175 | **−$0.6000** |
| others | unchanged | +$0.5876 |
| **total** | | **+$0.5490** |

**Worst-case residual exposure: $0.60, by construction.**

### Policy C — bounded, inventory-aware residual exits (taker, gated at pair ≤ 0.96)

Measured where book data exists:

| window | fires | result | vs actual |
|---|---|---|---|
| 0130z | 01:32:56, pair 0.95 | +$0.0648 | **+$1.2242** |
| 0145z | 01:52:14, pair 0.93 | +$0.0828 | **−$0.1200** |

For the five remaining completed pairs I have no residual-period ladder, so I
can only **bound** the damage: any window whose maker pair beat 0.96 is degraded
by at least `qty × (0.96 − achieved)` plus a taker fee —

0030z $0.2254 · 0045z $0.0166 · 0100z B $0.0172 · 0115z A $0.0534 ·
0115z B **$0.4032** → **up to $0.7158 of degradation**, before fees.

**Policy C range: +$0.26 (if it fires early everywhere) to +$1.10 (if it fires
only late, as it did in 0145z).** Both below Policy A + a price cap. The upside
case rests on the unverified assumption that the gate stays dormant while a
cheap maker pair is still coming.

### Policy D — cap the opening own price at 0.60 (refuse only; no new action type)

| window | effect |
|---|---|
| 0130z (own 0.62) | **REFUSED — never opened, no strand** |
| 0100z `793eb766` (own 0.78) | REFUSED (was cancelled unfilled anyway) |
| all seven pairs | **unchanged** |
| **total** | **+$1.2850** |

Worst-case residual exposure is **not** improved by a price cap alone:
`0.60 × OPENING_LEG_MAX_CONTRACTS(2) = $1.20`. A price cap lowers the
*probability* of stranding (23.8% → ≤14.8%); it does not lower the ceiling.

### Composition — D + B

Refuse own > 0.60 **and** cap worst-case opening notional at $0.60:
**+$1.1490, worst-case residual exposure $0.60.** Nearly all of D's upside at
half of A's worst case.

### Summary

| policy | campaign P&L | worst-case residual | new permission needed | L5 support |
|---|---|---|---|---|
| A existing | +$0.1256 | $1.1594 | — | yes (this is L5's shape) |
| B smaller high-priced clips | +$0.5490 | **$0.60** | none (reduces only) | yes (sizing) |
| C residual exits | +$0.26 … +$1.10 | $1.1594 until it fires | **taker cross — forbidden** | **none (see §6)** |
| **D opening-price cap** | **+$1.2850** | $1.20 | none (refuses only) | yes (selection) |
| D + B | +$1.1490 | **$0.60** | none | yes |

---

## 3. Would Policy C prematurely exit a profitable position such as 0145z?

**Yes, marginally — and the measurement is the strongest argument against C.**

0145z held DOWN 1.56 at own 0.35 for 5m23s (fill 01:47:01 → complement
01:52:24). Taker-exit pair cost over the whole residual period:

- 01:47:24 → 01:50:25: **1.03 → 1.15**, never better than 1.01. Every exit a loss.
- 01:50:31: 0.98 · 01:50:38: 0.99 · 01:50:50 / 01:50:57: 1.00
- 01:52:14: **0.93** · 01:52:20: **0.90**
- 01:52:24: **maker fill landed at pair 0.87** — better than any taker exit all window.

A 0.96-gated exit fires at **01:52:14, ten seconds before the maker fill**, and
takes 0.93 instead of 0.87: **+$0.0828 instead of +$0.2028, a cost of $0.1200.**

The deeper point: the maker edge *is* the patience. Pairs came in at 0.72–0.96;
a gate that crosses at 0.96 systematically converts cheap maker pairs into
ceiling-priced taker pairs and pays a taker fee to do it. **0115z B alone
(pair 0.72) would lose $0.4032** if the gate fired early there.

Any viable C must distinguish "this residual will complete" from "this residual
will strand" *before* the outcome. That is precisely the selection problem
Policy D solves at the opening instead — earlier, cheaper, and without a new
action type.

---

## 4. The two orders that never reached terminal states

> **SUPERSEDED — see §9. Both were venue-confirmed CANCELLED. The
> record was in `L5_ACC_DEADLINE`, not the lifecycle stream. My
> "auditability defect" conclusion below was wrong.**

| coid | window | side | polled | last state | venue id |
|---|---|---|---|---|---|
| `dd9b1c54` | 0115z | DOWN | 18× 01:21:30→01:23:27 | `ORDER_STATE_NEW`, cum 0, `terminal: false` | `DA5Y75NG11MQ` |
| `be31b2bd` | 0130z | UP | 5× 01:31:33→01:32:01 | `ORDER_STATE_NEW`, cum 0, `terminal: false` | `DA62T9S011TB` |

In both cases polling simply **stopped** and no terminal lifecycle event was
ever emitted. `TERMINAL_HINTS = ("FILLED","CANCEL","REJECT","EXPIR","DONE")`
matches none of `ORDER_STATE_NEW`, so the engine correctly recorded
`terminal: false` and then went quiet.

**What is established:**
- Neither ever filled. `cum_qty 0` at every observation, and both settlements
  reconcile exactly without them (0115z matched 1.68 + 1.68 from other orders;
  0130z shows the 1.87 DOWN unmatched).
- `L5_LIVE_PENDING_UNRESOLVABLE` correctly did **not** fire: that branch is
  reached only when `venue_order_id` is absent, and both had one.
- No `L5_LIVE_ORPHAN_CANCEL` for either.
- Trading continued immediately after both (0115z round 4 at 01:23:46;
  `d2f217fb` at 01:33:03), so the engine did not consider itself blocked —
  consistent with resolution via the "absent from the venue's open orders is
  terminal there" path, which releases the slot.

**What is NOT established, and must be before further live promotion:**
- *Which* terminal state each reached. There is no record distinguishing
  CANCELED from EXPIRED from absent-and-assumed-dead.
- That they were not still live at the venue. I can show they never *filled*;
  I cannot show from logs that they were *dead*.

**I did not find a demonstrated exposure hole** — I checked, and I am not
claiming one. The defect is **auditability**, not money: an order that leaves
the poll set without an attributed terminal state is a silent resolution.

Fail-closed handling to require before promotion (**not implemented, not
deployed**): every order leaving the tracking set must emit either a terminal
lifecycle event carrying the authoritative venue read that justified it, or
`PENDING_UNRESOLVABLE` with submissions refused — and until attributed, its
worst-case obligation should remain inside the exposure and deficit
computations, so the bound is never computed on an understated book.

---

## 5. Pre-send crossing guard — OPEN budget effect only

Preserved, tested (28 tests, mutation-verified), **still not deployed.**
Reported here as a budget metric with **no profitability claim attached.**

| metric | value |
|---|---|
| OPEN tickets spent on orders the guard would have declined | **4 of 16 (25.0%)** |
| COMPLETE tickets likewise | 2 of 24 (8.3%) |
| REPLACE tickets likewise | 0 of 32 |
| OPEN consumption with the guard | **12/16 instead of 16/16** |
| `SUBMISSION_CLAIM:OPEN_BUDGET_EXHAUSTED` refusals that followed exhaustion | 98 |
| windows that traded nothing because OPEN was spent | 2 (0200z, 0215z) |

So the guard's measured effect is that **OPEN would not have been exhausted**,
and 0200z/0215z could have opened. Whether those openings would have been
profitable is **unknown and deliberately not estimated** — per §2 it depends
entirely on their opening price, which is the variable Policy D governs.

---

## 6. L5's inventory reductions: complementary purchases, not sales

Scanned every payload row of `data/l5/raw/activity.jsonl` +
`activity_gap.jsonl.gz` (wallet `0xb945…db68`, 165.5 days):

```
payload rows scanned : 784,500
type census          : TRADE 768,325 | REDEEM 15,971 | MAKER_REBATE 111
                       TAKER_REBATE 73 | REWARD 20
TRADE side census    : BUY 768,323 | SELL 2
```

**Two sell rows in 768,325 trades — 0.00026%.** Both are on the first day of the
corpus (2026-03-19, 10:10:01Z and 10:22:09Z), both in `btc-updown-5m`, both tiny
(1.22 and 1.54 contracts at 0.9 and 0.7). In the **BTC15m series — our lane —
there are zero sells**, which is what the summary's `sell_rows: []` records.

*(Correction: `data/l5/l5_btc15m_realized_economics.json` reports `sell_rows: []`,
which is true for the 15m series only. Across all series the raw count is 2, not
0. The producing script raises on any TRADE that is neither BUY nor SELL, so the
classification is strict.)*

**L5 reduces inventory by buying the complement and redeeming** — 15,971 REDEEM
rows — and effectively never by selling into the book. He also accepts strands
as a cost of doing business: 606 of 2,216 residual states carried to settlement,
and 1,490 of 5,546 windows were losses (min −$751.73, p05 −$88.30).

**Consequence for Policy C: it has no support in 165 days of L5 behavior.**
Enabling residual exits would be a novel strategy, not a replication — which the
anti-drift rule in `CLAUDE.md` weighs against, independently of its economics.
Policies B and D are *selection and sizing* changes and sit inside L5's observed
behavior space.

---

## 7. Fill assumptions, stated plainly

| figure | basis | assumption |
|---|---|---|
| All seven pair P&Ls, the strand | actual venue fills + `VENUE_SETTLEMENT_PX` | **none** — settled |
| §1 exit at +$0.0456 | `taker_walk` 1.87 against `0.3400 × 1277.39` displayed, at a real 01:33:03.366 decision cycle | displayed depth is executable; taker fee charged explicitly; engine's own label warns "displayed, not guaranteed" |
| Policy B | linear scaling of a realized pair | fill probability unchanged by clip size — **conservative**, smaller clips fill more easily |
| Policy D | refusal of one leg; others untouched | **none for the kept pairs**; they are realized |
| Policy C | full residual crosses at the sampled VWAP | **optimistic** — ignores the 5s sampling gap and ~9s latency |
| EV table | L5's 2,216-window completion rates × our $0.1199/contract | L5's completion rates transfer to our ladder. Our depth-2 maker ladder with limited REPLACE attempts is structurally *worse* at completing, so true strand rates are likely **higher**, strengthening D |

---

## 8. The smallest justified change

**Add a maximum opening-leg own price of 0.60.** One new bound, the exact mirror
of the `MIN_FIRST_LEG_PRICE = 0.20` that already exists, in the same module and
the same shape. It **refuses** trades; it introduces no new action type, needs no
taker permission, moves no limit, and cannot increase exposure.

Justification, in order of weight:

1. **Out-of-sample**: L5's own 408 windows at opening price 0.6–0.7 complete
   76.2% — a 23.8% strand rate — versus 88.9% at 0.4–0.5.
2. **Expected value**: −$0.0633/contract above 0.60 against our measured
   $0.1199/contract pair profit; sign robust to a 69% error in that profit.
3. **Structural**: the cheap end is already guarded and the expensive end is not.
4. **This campaign**: it refuses exactly the one leg that stranded and one other
   (own 0.78) that was cancelled unfilled, and preserves all seven pairs.

**On that last point, the honest framing:** +$1.2850 vs +$0.1256 is **one
realization, not an expectation.** The $1.1594 saved was a 23.8%-probability
event that happened to land. The expectation argument is item 2, not item 4.

Deliberately **not** recommended now:
- **Policy C / residual exits** — needs a forbidden taker cross, has no support
  in 768,325 L5 trades, degrades 0145z by $0.1200 as measured, and its one
  favourable data point lived inside a 7-second window against ~9s latency.
- **Dynamic order mirroring** and **larger submission budgets** — both are
  downstream of selection quality. The campaign's binding constraint was that
  5 of 16 OPEN tickets became pairs, not that there were too few tickets.

Natural follow-on, if a worst-case *number* rather than a *probability* is
wanted: add Policy B's worst-case opening notional cap ($0.60). D + B gives
**+$1.1490 with worst-case residual exposure $0.60** against A's $1.1594.

**No deployment, rearming, limit change, or sell-exit enablement is proposed or
performed here. All of the above requires fresh explicit owner authorization.**

---

## 9. Item 1 RESOLVED — both orders were venue-confirmed cancelled

Searching by **venue order id** rather than client order id found the
authoritative record. It was always there, under `L5_ACC_DEADLINE`.

### `dd9b1c54` (0115z DOWN) — cancelled 01:23:27.944Z on the 120s TTL

```
reason      : acc_order_deadline        overdue : [l5f-dd9b1c54...]
ttl_s       : 120.0                     polled  : 1
cancelled   : [{ venue_order_id : "DA5Y75NG11MQ",
                 venue_state    : "ORDER_STATE_CANCELED",
                 cum_qty        : "0",   leaves_qty : "0",
                 cancel_error   : null }]
errors: []   unverified: []   no_venue_id: []   still_open: []   filled: []
```

Placed 01:21:25, cancelled 01:23:27 — **122s, the TTL exactly.**

### `be31b2bd` (0130z UP) — cancelled 01:32:05.209Z on stale-by-distance

Preceded by `L5_ACC_RECOVER` at 01:32:04.959:

```
side UP   own_px 0.3300   would_quote_now 0.4100
drift_ticks 8.00   allowed_ticks 4   rest_s 37.24   book_age_s 0.101
recoveries_used 0   max_per_market 1
note: "stale by distance, zero filled."
```

then

```
cancelled : [{ venue_order_id : "DA62T9S011TB",
               venue_state    : "ORDER_STATE_CANCELED",
               cum_qty        : "0",  leaves_qty : "0",
               cancel_error   : null }]
errors: []  unverified: []  no_venue_id: []  still_open: []  filled: []
stale_by_distance: [be31b2bd]   recoveries_used: 1
```

### Verdict

| coid | disposition | authority | cause |
|---|---|---|---|
| `dd9b1c54` | **CANCELLED, zero fill** | venue re-read, `cancel_error: null` | 120s TTL |
| `be31b2bd` | **CANCELLED, zero fill** | venue re-read, `cancel_error: null` | 8-tick drift > 4 allowed |

Neither filled, neither expired, neither remains unknown, and nothing was
`unverified` or `still_open`.

**Fail-closed behavior was preserved and demonstrably worked.** The deadline
route polls the venue, cancels by exact venue order id, and proves the cancel
terminal before any replacement is priced — as its own label states. The store
marks UNCERTAIN on anything other than a venue-confirmed terminal; that path was
not needed here.

The only residual gap is **cosmetic**: the deadline route emits no *terminal*
`L5_LIVE_LIFECYCLE` row, so a reader following only the lifecycle stream sees
the order stop at `ORDER_STATE_NEW`. That is what misled me. The authoritative
record exists and is complete. A one-line terminal lifecycle emit on the
deadline path would remove the trap for future readers; **no safety change is
required and none is proposed.**

### What this reveals about 0130z — a sharper mechanism

The cancel timings change the strand story materially:

| time | event |
|---|---|
| 01:31:27.756 | UP `be31b2bd` placed at own 0.33 (book 0.35/0.36), DOWN `491f0e82` at own 0.62 |
| ~01:31:28 | **DOWN fills.** UP rests. |
| 01:32:04.959 | drift 8 ticks > 4 allowed → recovery proposed, `recoveries_used 0 / max 1` |
| 01:32:05.209 | **UP cancelled, venue-confirmed, zero fill** |
| 01:32:05 → 01:33:03 | **no UP order resting at all — 58 seconds** |
| **01:32:56.650** | **pair achievable at 0.95 by crossing (ask 0.3300 × 244.72)** |
| **01:33:03.007** | **pair achievable at 0.96 by crossing (ask 0.3400 × 1277.39)** |
| 01:33:03.366 | replacement `d2f217fb` placed at own **0.31**, two ticks behind the 0.33 bid → pair 0.93, never fills |
| 01:44 → | ask side **empty**; no exit at any price |

The engine detected the drift correctly and spent its single allowed recovery
correctly. But the re-price rule — *"priced by the same rule two ticks BEHIND
the current touch and re-tested against the pair cap; it never moves to the
touch and never crosses"* — means that at **01:33:03.366**, 359 ms after a frame
showing an executable 0.96 pair, it quoted 0.93 instead of taking 0.96.

So the strand was not a failure to try. It was a maker-only re-price rule
quoting *through* a 6.4-second executable window, with its one recovery already
spent. That is a far more specific finding than "the complement never filled",
and it is the mechanism any candidate policy has to beat.
