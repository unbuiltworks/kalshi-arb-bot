# Result: ADVERSE_EXTEND_REFUSAL — NEGATIVE. The rule is dropped.

Pre-registered in `ADVERSE_EXTEND_PREREGISTRATION.md`, committed as
`ca54fde3` before this evaluation produced a number. Nothing in the
pre-registration was changed after seeing results.

**Verdict: the rule fails two of the four pre-registered criteria, on the
HOLDOUT split, and it is dropped.** It is not proposed for shadow
deployment.

---

## The answer to the research question

> Does refusing an EXTEND when our existing position has moved adversely
> improve net expectancy, after accounting for missed recoveries, missed
> profitable pairs, and reduced trading opportunities?

**No. It is not selective — it is an across-the-board ~9% reduction in
activity that removes profitable completions along with the losing
extends.** The mechanism I diagnosed from three production windows does not
generalise: on this horizon, a BTC 15-minute position that has moved
against us usually comes back.

## Criterion-by-criterion

| pre-registered criterion | result |
|---|---|
| conservative net improves on HOLDOUT | **not demonstrable** — replay money is not expectancy (pre-registered), and every behavioural proxy points the wrong way |
| improvement survives removal of the 3 motivating windows | **n/a** — no improvement to survive. (Satisfied by construction anyway: this arm is frozen historical L5 data, the motivating windows are today's production.) |
| pair completion rate does not fall | **FAILS** |
| missed recoveries do not exceed losses prevented | **FAILS, by 45 to 1** |

## 1. Pair completion rate falls

| | candidate | ARGMAX | change |
|---|---|---|---|
| HOLDOUT matched pairs | 24,015 | 26,373 | **−8.9%** |
| HOLDOUT pairs/window | 10.857 | 11.923 | **−8.9%** |
| HOLDOUT zero-completion windows | 274 | 273 | **+1, worse** |
| DEV matched pairs | 20,563 | 22,589 | **−9.0%** |
| DEV pairs/window | 6.197 | 6.808 | **−9.0%** |
| DEV zero-completion windows | 654 | 654 | unchanged |

The rule was supposed to *reduce* zero-completion windows — that was the
whole premise. It does not reduce them at all on DEV and makes them
marginally worse on HOLDOUT.

## 2. Missed recoveries dwarf losses prevented

| | HOLDOUT | DEV |
|---|---|---|
| refusals | 158,294 | 119,461 |
| losses prevented | 3,466 | 3,159 |
| **profitable recoveries missed** | **154,828** | **116,302** |
| recovery share | **97.8%** | **97.4%** |

**In 97.8% of the cases where our side had fallen below our average cost,
that side recovered above our average again before expiry.** The signal the
rule keys on is almost pure noise on this horizon.

LIMITATION, STATED PLAINLY: "recovered" means the side's own-outcome price
*touched* above our average at some point in the remaining prints. That is
a generous definition — it does not mean we would have captured it. So
97.8% is an upper bound on recovery *opportunity*. But a 45:1 asymmetry is
not going to invert under a stricter definition, and the completion-rate
failure is independent of this measure.

## 3. It reduces exposure, but only by doing less of everything

HOLDOUT residual units 5,048 against ARGMAX's 5,436 (−7.1%), with windows
left holding residual essentially unchanged (2,005 vs 2,013). Pairs fell
8.9% and residual fell 7.1% — it scales both down together. That is a
volume cut, not better selection.

## 4. The concrete counter-example, from the window that motivated it

1700z, ARGMAX, the one window where pair economics earned: it bought
NO at 0.39, then extended NO at 0.38 and 0.35. The market settled
`settlementPx 0.0000`, so **NO paid $1 and all three units were winners.**
The rule would have refused the 0.38 and the 0.35 — the two cheapest
winning units in the best window of the six.

That is the 97.8% finding in miniature, inside my own motivating evidence.

## What this does not overturn

The *diagnosis* still stands: 3 of 6 settled production windows completed
zero pairs and those three windows are the entire loss. Extending into a
falling side genuinely did destroy those windows. What is now falsified is
the **generalisation** — that refusing such extends improves expectancy.
Over 5,530 windows it does not, because the same signal fires on far more
winners than losers.

The real weakness is therefore narrower than "extend while adverse" and
still unidentified. Candidate directions, none evaluated and none
pre-registered:

- the *completion* side, not the extend side: those three windows failed
  because no fee-safe completion was ever reachable, which is a question
  about whether to OPEN at all in a window whose complement is priced out;
- time-conditioning: the 1815z extends were at tte 728s and 590s, not
  late, so "late extend" is not it either;
- position sizing rather than refusal.

Each would need its own pre-registration. **No rule is adopted on this
evidence.**

## Honest limits of this evaluation

- The replay's absolute P&L is excluded by the pre-registration (synthetic
  one-tick spread; 24–33% nets against L5's realized 0.30%). Only
  behaviour is read from it.
- The production arm still has six settled windows, three of which
  motivated the rule. It cannot establish expectancy and is not used to.
- Replay completion counts for PAIR_AVAILABLE and RESIDUAL_URGENCY
  (30–70 pairs/window) contradict production, where PAIR_AVAILABLE did
  *least*. That is the synthetic spread making completion always
  available, and it is why cross-policy replay economics are not quoted.
