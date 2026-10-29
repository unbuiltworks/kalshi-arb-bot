# Bounded complement-cross shadow — result against the frozen spec

**Spec:** `PREREGISTRATION.md`, frozen at commit `135f5d79`,
sha256 `7e31c447eaa1028c546787b23bdc16a7925478d9d9e0e7d11635ebb7caea3dd0`,
committed **before** `src/l5_cross_shadow.py` or either runner existed.
Nothing below relaxes a rule in it.

**Production unchanged.** `f7a3e224`, maker-only, cap 6, current band, 160
seats, watch 160, existing envelope. **No live taker order. The $1.00 pair
ceiling did not move.**

---

## Headline: ZERO supported crosses in all 16 cells

260 real production COMPLETE decisions, 8 markets.

| | thr .97 | .98 | .99 | 1.00 |
|---|---|---|---|---|
| **T0** | 0 | 0 | 0 | 0 |
| **T10** | 0 | 0 | 0 | 0 |
| **T30** | 0 | 0 | 0 | 0 |
| **T60** | 0 | 0 | 0 | 0 |

Not because the prices failed. **Because the evidence required to say an
order was executable does not exist.** Where every T0 cell stops, first
reason only:

| first stop | n | share |
|---|---|---|
| **no authenticated quote age recorded** | **146** | 56.2% |
| **observed ask depth not recorded** | **48** | 18.5% |
| authenticated quote **stale** (>5 s; 5.1 s–826 s) | **65** | 25.0% |
| malformed evidence | 1 | 0.4% |

**No row in the corpus ever reached the price test.** T10/T30/T60 are 100%
UNKNOWN because no book observation exists 10–60 s after the decision.

This is the outcome §11 of the pre-registration named in advance:
*"depth may be absent … in which case promotion-gate condition 2 fails by
definition and the study's honest output is the UNKNOWN_EXECUTABILITY
count, not a fill count."*

### The instrumentation gap is one line, and it is read-only

The recorder **already computes and serves `bid_qty` / `ask_qty`** per
market (`pmus_ws_evidence_recorder.py:366`) and **already prints them** in
its `PMUS_WS_STATUS` rows — I recovered one verbatim from its log stream:

```
{"slug":"aec-mlb-lad-mia-2026-09-13","bid":"0.4950","ask":"0.5900",
 "bid_qty":"5.0000","ask_qty":"61.0000"}      20:37:27Z
```

The probe's `recorder_book` capture persists `yes_bid`, `yes_ask`,
`opp_ask_authenticated`, `authenticated_age_s`, `both_sides_served` — and
**drops the two depth fields.** Depth existed at decision time and was not
written down. **0 of 589 order rows carry it.**

Recovering it from recorder logs after the fact is not viable at scale:
each status row is ~50–80 KB covering all 160 markets, Railway truncates
them mid-JSON, retention starts at 19:39Z for the current recorder
deployment, and the emission cadence yields roughly **one observation per
market per 75 s** — against a 5-second contemporaneity requirement.

---

## P1 — fees are now CONFIRMED

**docs.polymarket.us/fees, fetched 2026-09-13**, effective 12 AM ET
Wednesday 1 July 2026, exchange-wide:

* **taker** `Fee = Θ · C · p · (1−p)`, **Θ = 0.06**, max $1.50 at p = 0.50
* **maker rebate** same structure, **Θ = −0.0125**, max −$0.31
* reduced taker tiers at **$250,000** prior-month taker volume (10/25/50%)
  — we are orders of magnitude below the first tier, so **base 0.06 applies**
* **"All fees and rebates are rounded to the nearest $0.01 using banker's
  rounding (round half to even)."**
* per-category differentiation: **NOT STATED**

**`fee_status = CONFIRMED`.** Promotion-gate condition 1 is met.

### And the rounding is not a detail at our size

| clip | p | formula value | **charged** | all-in cost/contract | **max pair for a net-positive cross** | arms that can ever be net-positive |
|---|---|---|---|---|---|---|
| 1.15 | 0.50 | $0.017 | **$0.02** | $0.0174 | **0.9826** | .97, .98 |
| **1.72** | **0.50** | **$0.0258** | **$0.03** | **$0.0233** | **0.9767** | **.97 only** |
| 1.92 | 0.50 | $0.0288 | **$0.03** | $0.0208 | 0.9792 | .97 only |
| 10 | 0.50 | $0.15 | $0.15 | $0.0180 | 0.9820 | .97, .98 |
| 100 | 0.50 | $1.50 | $1.50 | $0.0181 | 0.9819 | .97, .98 |

All-in cost = confirmed taker fee **plus the maker rebate we forgo by
crossing**, which is a real cost of the cross and is charged as one.

**The .99 and 1.00 arms cannot be net-positive at any size we will trade.**
Break-even sits at a pair of roughly **0.977–0.988**. At our actual clip
(1.72 contracts, p ≈ 0.5) only the **.97** arm survives, and even there a
worked example at a 0.97 pair is gross **$0.0516**, fee **$0.03**, rebate
forgone **$0.01**, **net $0.0116 — fees take 77% of the edge.**

This conclusion needs no depth data and is the most robust thing in the
report.

---

## P5 — adverse selection: the complement got CHEAPER after we would have crossed

Run over the 48 rows that passed **every** pre-registered condition except
depth — labelled `DEPTH_UNKNOWN_not_a_fill` throughout, never as fills.
No interpolation: an unobserved book is UNKNOWN, not a line between points.

| horizon | observed | **ask got cheaper** | ask got dearer | mean change |
|---|---|---|---|---|
| **+10 s** | 48 | **12.5%** | **0.0%** | −0.011 |
| **+30 s** | 20 | **50.0%** | 10.0% | **−0.1028** |
| **+60 s** | 14 | 50.0% | 42.9% | +0.034 |

At every horizon inside a minute the complement was **more likely to get
cheaper than dearer**, and at +30 s the mean move is **−10 cents**. **This
is a negative result for T0 crossing and is reported as one.** On this
corpus, paying the ask bought a worse price than waiting did — which is
precisely the failure mode the pre-registration named as the principal
risk.

Caveats, stated: n = 48 / 20 / 14; the cheapening is concentrated in
markets that were collapsing; and "cheaper" for the complement means the
held side was winning, so this partly measures which way our positions
happened to go today.

---

## P4 — BUF-HOU forensic (case study, not evidence)

31 COMPLETE attempts, confirmed realized **−$2.5896**.

| | n |
|---|---|
| attempts reaching the depth test (fresh authenticated quote) | **24** |
| blocked earlier (1 no quote age, 6 stale: 31.8 s, 41.8 s, 134.5 s) | 7 |
| **our maker price actually touched** | **11 of 31** |

**No policy in the pre-registered matrix eliminates this residual**, at any
threshold or timing, because every attempt stops at a missing or stale
authenticated quote or at unrecorded depth **before any price test runs**.

The market's complement was reachable at a pair of **0.515** at some point
(prior cycle). Our maker price was touched 11 times. We still finished
flat-zero matched and −$2.5896. **Nothing in this study can say whether
crossing would have fixed it, and the honest answer is UNKNOWN.**

---

## P6 — trajectory fidelity

**No claim is made that L5 crosses.** With zero depth-supported crosses the
shadow trajectory is **identical to production's**, so the comparison has
nothing to measure and reports **UNKNOWN** — not "no change". L5's
reference distributions (median switch interval 9 s, median reconstructed
switch pair 0.99, 47.2% above 1.00, median window matched fraction 0.625)
are carried forward unused.

---

## Promotion gate

| # | condition | status |
|---|---|---|
| 1 | PMUS fee economics source-confirmed | **MET** |
| 2 | contemporaneous depth proves executability | **FAILS — 0 of 260** |
| 3 | multiple independent markets | **FAILS — 0 supported** |
| 4 | incremental NET positive after confirmed fees | **NOT MEASURABLE**; and the break-even table says .99 and 1.00 cannot be |
| 5 | residual losses fall | **NOT MEASURABLE** |
| 6 | tail risk not materially worse | **NOT MEASURABLE** |
| 7 | trajectory at least as L5-faithful | **UNKNOWN** |
| 8 | fresh realtime shadow confirms | **not reached** |

**→ MAKER-ONLY STAYS. No live bounded taker is proposed.**

---

## The two things this establishes

1. **Fees are confirmed, and they bound the question before any execution
   evidence arrives.** At our clip size the all-in cost of crossing is
   1.6–2.3 cents per contract. **Two of the four pre-registered thresholds
   are dead on arrival** and the other two are thin. Any future version of
   this test should carry `.97` as its ceiling, not `1.00`.

2. **We cannot currently answer the execution question at all**, because
   the probe discards the depth the recorder already produces and 56% of
   COMPLETE orders carry no authenticated quote age. **The blocker is
   instrumentation, not economics or policy**, and the fix is a read-only
   change to what the probe writes down — which is still a change to a
   money-moving service, so it is **named here and not made.**

## Files

`PREREGISTRATION.md` (frozen), `cross_shadow.json` (matrix + break-even),
`p4_p5_p6.json`, `run.py`, `p4_p5_p6.py`, `src/l5_cross_shadow.py`,
`tests/test_l5_cross_shadow.py` (16 regressions).
