# Frozen PMUS entry rule — result on 13 untouched windows

**2026-10-26.** PMUS only. No Gemini figure is used anywhere below. No live
gate changed, nothing armed, no capital change. The live floor stays at its
deployed `L5_TS_MIN_FIRST_LEG_PRICE`, the live ceiling stays 0.96,
`L5_MAX_LIVE_ORDERS` stays 1.

---

## Verdict: the frozen rule FAILED

It looked like it won by **+$2.11**, and all of that was a settlement lottery.

| | pairs | strands | **realized net** |
|---|---|---|---|
| no floor (control) | +0.8761 | −2.3420 | **−1.4659** |
| **FROZEN floor 0.45** | +0.6606 | −0.0178 | **+0.6428** |
| live floor 0.20 | +0.8761 | −1.4874 | −0.6113 |

**100% of the gain is in the strand term, and the pair term got worse**
(+0.8761 → +0.6606). The rule was supposed to improve *completion*. It did
not: it changed the price distribution of the residual, and the residual
happened to resolve in our favour.

**3 of the frozen rule's 4 strands won**, at a mean own cost of **0.8000**.
The control's 6 strands, at mean own cost 0.3467, saw **1 of 6** win.

A leg held at own cost `c` settles 1 if its side wins and 0 if not. At fair
pricing `P(win) = c`, so `E[pnl] = c(1−c) + (1−c)(−c) = 0` for **any** `c`.
A floor that pushes entries dearer therefore buys residuals that **win more
often and lose bigger** — EV-neutral, not an edge. `L5_OPERATING_MODEL` is
explicit that residual directional exposure carries no assumed skill, so
fair-value is the right neutral baseline here, not a conservative one.

### With the lottery removed, the frozen rule is the worst arm

| arm | completed pairs | **pair net/window [95% CI]** | realized net/window |
|---|---|---|---|
| no floor (control) | 6 | **+0.0674 [+0.0149, +0.1295]** | −0.1128 |
| **FROZEN 0.45** | 5 | **+0.0508 [+0.0167, +0.0883]** | +0.0494 |
| live floor 0.20 | 6 | **+0.0674 [+0.0149, +0.1295]** | −0.0470 |

The frozen rule gives up **$0.2155** of completed-pair profit to buy a
zero-EV change in residual composition. **Rejected on its own
pre-registered test.** The deployed 0.20 floor is not beaten by 0.45 and
should stay where it is.

---

## What was frozen, and when

`src/l5_pmus_entry_rule_frozen.py`, committed at **`4c1aa98f`** — *before*
any untouched window was read. Floor **0.45** chosen by a stated **maximin**
criterion over the two chronological halves of the 26-window corpus (the
floor whose worse half is least bad). Everything else baseline: ceiling 0.96,
`STRICT` fill, plain `JOIN` completion, residual reduction **off**. So the
single difference against the control is the entry floor, and the result is
attributable to it alone.

`evaluate()` drops all 34 previously-examined windows by name unless
explicitly overridden, and a test pins that — scoring the selection corpus
would report the fit as a result.

## The corpus

**1,091 rows across 13 untouched windows**, every book `authenticated_ws`,
harvested from `l5-pmus-live`: 10 windows from **2026-10-25** (1630z–1745z,
2115z–2200z) and 3 from **2026-10-26** (1200z, 1215z, 1230z — gaps the
earlier 500-row-capped fetches missed). Every window touched by any prior
analysis in this programme is excluded by name, including the 5 residual-book
windows of 2026-10-25 and the 5 PMUS observer windows of 2026-09-23.

**13 windows and 9 sequences.** Small. Every figure carries that.

---

## Completed-pair profits, in full

| window | entry own | complement own | pair | qty | net |
|---|---|---|---|---|---|
| 10-25 1645z | 0.6900 | 0.1800 | 0.8700 | 1.15 | **+0.1495** |
| 10-25 1730z | 0.5800 | 0.3200 | 0.9000 | 1.37 | **+0.1370** |
| 10-25 1745z | 0.4900 | 0.4400 | 0.9300 | 1.63 | **+0.1141** |
| 10-26 1200z | 0.7500 | 0.0800 | 0.8300 | 1.06 | **+0.1802** |
| 10-26 1230z | 0.7000 | 0.2300 | 0.9300 | 1.14 | **+0.0798** |

Total **+$0.6606**, mean **+$0.1321** per pair. Every pair is below the 0.96
ceiling and is locked profit by construction — which is why a positive pair
term is *arithmetic*, not evidence of an edge.

## Stranded-leg losses, in full

| window | side | own cost | qty | winner | net | evidence |
|---|---|---|---|---|---|---|
| 10-25 1700z | DOWN | 0.6800 | 1.17 | DOWN | **+0.3744** | TERMINAL_BOOK_INFERRED |
| 10-25 1745z | DOWN | 0.7800 | 1.02 | DOWN | **+0.2244** | TERMINAL_BOOK_INFERRED |
| 10-25 2145z | DOWN | 0.8200 | 0.97 | DOWN | **+0.1746** | TERMINAL_BOOK_INFERRED |
| 10-26 1215z | DOWN | 0.9200 | 0.86 | UP | **−0.7912** | TERMINAL_BOOK_INFERRED |

Total **−$0.0178**. Had all four lost, **−$3.1778**; had all four won,
**+$0.8422**. That spread across 4 observations is the entire result.

---

## Fill assumptions, stated plainly

| mode | completed | stranded | pairs | realized net |
|---|---|---|---|---|
| **STRICT** (headline) | 5 | 4 | +0.6606 | +0.6428 |
| AT_TOUCH (optimistic) | 6 | 3 | +0.7285 | +0.5361 |

A resting order fills only when the opposite touch reaches our price;
`STRICT` requires it to pass strictly **through**, which is the conservative
reading of queue priority. Observed PMUS queue depths run 870–3,872 contracts
and **our position within a level is UNKNOWN**.

**No venue-confirmed maker fill exists anywhere in this programme.** Every
fill above is modelled. Every settlement above is `TERMINAL_BOOK_INFERRED` —
no venue settlement exists for these windows. **Nothing here is realized
profit.**

---

## Net expectancy, and the honest size of it

If residuals are genuinely zero-EV, expectancy is the pair term:
**+$0.0674 per window** (control and live-0.20 arms), CI **[+0.0149,
+0.1295]**. At ~96 windows/day that is **≈ $6.47/day** — **1.3% of the
$480/day target** at the current $0.80 opening notional.

That number rests on three things, none of them established: the modelled
maker fill, residuals being fair-priced, and terminal-book settlement
inference. The realized strand term has been negative in most samples
(−2.34 here for the control, −5.85 on the 26-window corpus), so the
zero-EV assumption is the load-bearing one and it has never been confirmed.

## Biggest remaining blocker

**Residual variance, not entry selection and not completion.** Completed
pairs are reliably positive and small; the residual is a high-variance
zero-EV lottery that swamps them. Four strands moved this sample by $4.02
while five pairs earned $0.66.

Nothing tested reduces the residual for less than it is worth: selling it
back crosses the spread and pays a taker fee, which is *strictly* negative
against holding a zero-EV position — consistent with reduction measuring
worse on the 26-window corpus.

**No live activation proposed.** With the realized net dominated by four
settlement coin-flips, a bounded probe at $0.80 notional cannot distinguish
any of these arms from each other or from flat.

**Next:** keep collecting PMUS windows. The strand-EV question — are
residuals actually fair-priced? — is answerable from accumulated windows
read-only, and it is the one assumption everything else now rests on.
