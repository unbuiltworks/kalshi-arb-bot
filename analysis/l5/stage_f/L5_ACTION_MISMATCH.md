# Why L5 acts differently, and whether copying him pays

**Date:** 2026-11-01
**Data:** `analysis/l5/l5_canonical_sequence.csv.gz` — **63,686 BTC15m fills
across 556 windows**, inventory reconstructed immediately before every fill.
Engine gates evaluated at the deployed SHA `4d52345d`.

## Verdict up front

**The mismatch is real, large and precisely located: our engine abandons the
last third of every window, and 15.1% of L5's fills there are
risk-*reducing*.** 9,592 of his reducing fills land after T+600, where our
deployed completion terminal refuses everything.

**The policy change that addresses it fails out-of-sample.** Permitting late
risk-reducing fills cuts residual inventory by 20% (2.03 → 1.63 contracts) and
moves net P&L by **−$0.0114 per window, t = −0.71, 95% CI [−$0.043, +$0.020]**
— straddling zero and leaning negative. **No economic case. Do not deploy.**

Also corrected: **maker-only is not the mismatch.** L5 is 87.5–88.7% maker on
the actions that matter, and maker-only explains only **2.0%** of the refusals.

---

## 1. What L5 actually does (request items 1–4)

Every fill classified by its effect on the inventory imbalance, with the
imbalance reconstructed immediately before it:

| action | n | share | p10 | **p50** | p90 | % after T+600 | maker |
|---|---|---|---|---|---|---|---|
| OPEN | 556 | 0.9% | 6s | **16s** | 221s | 2.9% | 48.4% |
| **ADD** | 33,490 | **52.6%** | 102s | **481s** | 801s | **36.7%** | 87.5% |
| **REDUCE** | 28,255 | **44.4%** | 126s | **463s** | 789s | **33.9%** | 88.7% |
| REVERSE | 1,379 | 2.2% | 73s | 430s | 796s | 33.4% | 73.5% |

**He is not a pair-completer. He is an inventory worker.** The ratio is
**ADD : REDUCE = 1.19 : 1** — he adds *more often* than he reduces, and both
run at the same cadence across the whole window. Our engine models
`open → complete the pair`; his process is a continuously worked two-way book
in which completion is one move among several, not the goal.

Exactly one OPEN per window, almost always in the first 20 seconds
(p50 = 16s), at a middling own-price (p50 = 0.51). Our arming at ~T+12 is
compatible with that; the opening move is **not** where we differ.

## 2. Where we differ (request items 5–6)

Each L5 action scored against our deployed, scale-free gates:

| L5 action | our verdict | n | share of action |
|---|---|---|---|
| ADD | **permitted** | 20,550 | 61.4% |
| ADD | **`ENTRY_CUTOFF_600`** | **12,282** | **36.7%** |
| ADD | `MAKER_ONLY` | 658 | 2.0% |
| REDUCE | **permitted** | 18,126 | 64.2% |
| REDUCE | **`COMPLETION_TERMINAL_600`** | **9,592** | **33.9%** |
| REDUCE | `MAKER_ONLY` | 537 | 1.9% |
| OPEN | permitted | 474 | 85.3% |
| OPEN | `MAKER_ONLY` | 63 | 11.3% |

**Our engine would permit 40,041 of 63,686 fills (62.9%)** on scale-free
gates. The two mismatches that matter are both the clock, and they are **not
equal in standing**:

**Mismatch 1 — late ADD (12,282 fills, 19.3% of all).** Refused by
`ENTRY_CUTOFF_S = 600`. **This refusal is a validated feature, not a defect.**
`RESIDUAL_MATCHED_RATIO.md` tested exactly this on 556 windows with a
chronological split: a 70% (T+630) gate on imbalance-*increasing* fills was
the **only** gate value positive out of sample (+0.070/$100 against −0.156
with no gate). Our 600 is already tighter than that validated optimum. **Do
not relax it.**

**Mismatch 2 — late REDUCE (9,592 fills, 15.1% of all, 84.5% maker).**
Refused by the completion terminal at T+600. This has **no** supporting
evidence: the validated clock finding was specifically about
imbalance-*increasing* fills, and the engine's own design comment
(`l5_two_sided_probe.py:780-786`) says completion "must stay available later
than opening". This is the collapsed deadline confirmed in
`STRANDED_LEG_CODE_AUDIT.md` §7, now with L5's behaviour quantifying what it
costs us in forgone *actions*.

**So the economically consequential mismatch is late risk-reduction, and only
that.** Maker-only (2.0%) and `MIN_FIRST_LEG_OWN_PRICE` (0.5% of OPENs) are
rounding errors by comparison.

### A representative window

`btc-updown-15m-1788103800`, 114 fills, settled Up:

```
 T+     action   side  own_px    qty   pre_imb  post_imb   our engine
 49     OPEN     DOWN   0.460  18.23      0.00    -18.23   permitted
 150    ADD      DOWN   0.460  50.00    -26.38    -76.38   permitted
 180    REDUCE   UP     0.600  15.00    -76.38    -61.38   permitted
 604    ADD      DOWN   0.160   0.02   -471.78   -471.80   ENTRY_CUTOFF
 609    ADD      DOWN   0.130  10.00   -473.01   -483.01   ENTRY_CUTOFF
 676    REDUCE   UP     0.890   6.66   -493.01   -486.35   TERMINAL_600
 705    REDUCE   UP     0.890  18.00   -486.35   -468.35   TERMINAL_600
 715    REDUCE   UP     0.910  50.00   -468.35   -418.35   TERMINAL_600
 721    REDUCE   UP     0.860  29.14   -427.92   -398.78   TERMINAL_600
 726    REDUCE   UP     0.790   6.00   -394.06   -388.06   TERMINAL_600
```

Two things our engine cannot do, visible in one window: a sustained
**risk-reducing sequence from T+676 to T+726** (buying UP at 0.89, 0.91, 0.86,
0.79 against a DOWN imbalance), and **cheap-tail adds at own-price 0.13–0.16**
after the entry cutoff.

It also shows the scale gap honestly: he runs |imbalance| to **493
contracts** against our cap of **3** — 164× — so his inventory process is not
one our capital can express even where the clock permits it.

## 3. The one policy change, and its out-of-sample verdict (items 7–8)

**Proposed change (causal, one knob):** permit a fill that strictly *reduces*
|imbalance| until T+720, keeping the ADD/OPEN cutoff at T+600. Reducing fills
are clamped so they can never overshoot zero into a fresh imbalance, so the
action can only ever shrink exposure. Maker-only, cap 3, $12 envelope — all
existing limits unchanged.

Replayed on L5's tape with our policy and our limits, chronological split
(IS 2026-03-19…08-28, OOS 08-28…08-31, 278 windows each):

| arm | IS net/win | OOS net/win | OOS fills | OOS \|res\| |
|---|---|---|---|---|
| **A — deployed (600)** | +0.0262 | **+0.1165** | 15.8 | 1.37 |
| **B — late reduce (720)** | +0.0287 | **+0.1052** | 16.1 | **1.13** |
| B′ — late reduce (780) | +0.0269 | +0.1043 | 16.2 | 1.10 |
| B″ — late reduce (900) | +0.0268 | +0.1059 | 16.2 | **1.03** |

B looked marginally better in-sample (+0.0287 vs +0.0262) and is **worse out
of sample**. Residual falls monotonically as the window widens, so the
mechanism works — it simply does not pay.

**Paired test, OOS, same tape and same fills with one knob changed:**

| | |
|---|---|
| all 278 windows | mean **−$0.01135**, sd 0.26636, **t = −0.71** |
| 95% CI | **[−$0.04267, +$0.01996]** |
| windows where the policy acted | **169 of 278** |
| residual in those windows | **2.03 → 1.63 contracts (−20%)** |
| better / worse / unchanged | **35 / 24 / 110** |

**Verdict: NO ECONOMIC CASE.** The confidence interval includes zero and leans
negative. The policy is more often better than worse (35 vs 24) but the losses
are larger, which is the signature of adverse selection operating on late
reducing fills too: you complete when the market has already moved against the
position, locking a loss that holding would sometimes have recovered.

This is the same shape as every other completion result in this program, and
consistent with `STRANDED_LEG_CODE_AUDIT.md`: completing more reduces variance
and does not create expectancy.

## 4. One further observation, flagged but not proposed

The **ADD : REDUCE ratio separates profitable from losing windows**, ex post:

| windows | n | median ADD : REDUCE |
|---|---|---|
| profitable | 262 | **1.088** |
| losing | 241 | **1.447** |

Adding relatively more than reducing marks a losing window, by 33%.

**I am not proposing this as a policy** and it should not be read as one. It
has the same endogeneity as the dispersion result in
`RESIDUAL_MATCHED_RATIO.md` §3a: a window whose price trends makes you add
more (chasing), and that window loses. The ratio is a marker of a trending
window, not a lever on one. The ex-ante version of exactly that structure was
tested there and returned **−79.5 bp/$ out of sample** against +6.8 for
trading everything. A running mid-window ratio is observable at decision time,
so it is testable, but on the prior it should be expected to fail and I have
not spent a replay on it.

## 5. Answer to the central question

**Why does L5 open, add, reduce or reverse where our engine would differ?**

1. **The clock, in both directions.** 36.7% of his ADDs and 33.9% of his
   REDUCEs land after T+600. Our engine is wholly absent from the last third
   of the window. For ADDs that absence is a validated advantage; for REDUCEs
   it is an untested restriction, now tested — and worth nothing.
2. **Capital, not logic.** He works a 493-contract imbalance; our cap is 3.
   Most of his adds and reduces are moves inside a position our envelope
   cannot hold, so the question "would we do the same thing" has no answer at
   our size for the majority of his fills.
3. **He is working inventory, not completing pairs.** ADD : REDUCE = 1.19 : 1
   at equal cadence across the window. Our `open → complete` model is a
   different process, and the difference is architectural rather than a
   parameter we can move.
4. **Not maker/taker.** He is 88% maker where it counts; maker-only costs us
   2.0% of his actions. This closes off the Grok hypothesis that passive
   execution is the mismatch — it is not.

**What I am not claiming.** This compares L5's actions to our *gates*, which
is a necessary condition for a mismatch and not a sufficient one. The tape
carries his executed price but **not the book depth at each fill**, so I
cannot verify that a fill he obtained at the touch was obtainable by us at our
queue position. Where that matters — the 9,592 late reducing fills — the
replay credits us with his fills at his prices, which is **optimistic**, and
the result is still null. A pessimistic queue assumption can only make B
worse, so the verdict is robust in the direction that matters.

## 6. Recommendation

**Change nothing in live behaviour.** The collapsed completion deadline stays
visible via `binding_completion_deadline_s()` and its three verifier rows, as
instructed — it is a real correctness defect and the heartbeat should keep
saying so — but the economic case for closing it does not exist, and the
instruction was not to change behaviour without one.

The honest state of the program: our engine differs from L5 mostly by
**capital** and by a **clock restriction that is either validated or null**.
Neither is a defect to fix. No live deployment, no risk increase, and no new
authorization is requested.
