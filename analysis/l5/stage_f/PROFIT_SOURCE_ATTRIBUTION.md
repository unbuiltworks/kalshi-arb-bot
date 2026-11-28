# Where L5's +$16,436 BTC15m trading profit comes from

**Date:** 2026-11-01
**Answer: fills 21–50 of each window.** That band earns **+123.5 bp/$
out-of-sample (t = +2.68)**, with an ex-ante edge of **+0.46¢ per contract**
over the price L5 paid. Every fill band our capital can reach — **1 through
14** — is **negative out-of-sample**.

**Verdict: NO on bounded live validation.** The profit source is real,
located and measured, and it sits in a fill range our envelope cannot reach.
The reachable part is not a weaker version of it; it is the opposite sign.

---

## 0. A correction that invalidates part of my last two analyses

`l5_canonical_sequence.csv.gz` — which I used for the action census and the
trajectory verdict — is **not the population**:

| | windows | fills | dollars | net |
|---|---|---|---|---|
| authoritative tape (`redemption/audit.py`) | **5,546** | 693,916 | $6,379,594 | **+$16,436** |
| `l5_canonical_sequence.csv.gz` | **556** (10.0%) | 63,686 (9.2%) | $574,746 (9.0%) | **−$1,105** |

**The 10% sample is net NEGATIVE while the population is net positive.** It is
not a representative sample of the profitable whole. So the trajectory
verdict's headline numbers — the −$2,842.95 late cheap-side programme and the
"17.3% of his net" framing — describe **that sample**, not L5's book. The
NO-GO conclusion there still stands (the cheap-side fills lost money *in the
sample*, 98.5% expiring worthless against a 94.33% implied rate, which is a
property of those trades), but **the magnitude and the share-of-net claim are
withdrawn.** Everything below is computed on the full 5,546-window population.

## 1. Attribution with no double-counting (items 1, 2)

Every contract redeems exactly $1.00 or $0.00, so per-fill P&L
`(qty if won else 0) − usdcSize` is exact and **sums to the net by
construction** — matched and residual cannot be double-counted because no
contract is counted twice.

| | |
|---|---|
| fills | 693,916 |
| spent (`usdcSize`, fee-inclusive) | $6,379,593.70 |
| winning contracts | 6,397,668.0 |
| **implied net** | **+$18,074.26** |
| canonical net | +$16,436.09 |
| **discrepancy** | **+$1,638.17 (10.0%)** |

The $1,638 gap is the settlement-winner inference (validated at **98.10%**
against the independent `winning_outcome` field) plus contracts never
redeemed. It does not move any sign or ranking below, and every figure is
accurate to about ±10%.

The matched/residual view of the same money, from `BTC15M_NET_SOURCE.md`:
matched **+$162,257**, residual **−$145,821**, sum +$16,436. The per-fill view
is the better attribution tool because it partitions the money once.

## 2. The ex-post attribution, and why it is a trap (item 4)

Splitting each window's fills into thirds of its own fill sequence:

| cohort | fills | mean px | P(win) | edge/ctr | net | **t (window-clustered)** |
|---|---|---|---|---|---|---|
| EARLY 1/3 | 229,450 | 0.5014 | 0.5054 | +0.0040 | **+$17,198** | **+3.26** |
| MIDDLE 1/3 | 231,356 | 0.4820 | 0.4834 | +0.0014 | +$6,175 | +1.17 |
| LATE 1/3 | 233,110 | 0.4525 | 0.4514 | −0.0011 | −$5,299 | −1.25 |

This looks like a clean answer — "the profit is early" — and it **is not
actionable**, because the cohort is defined by `i / n` and **`n` is the
window's total fill count, known only when the window ends.** At fill #5 you
cannot know whether you are in the early third. The clustering also matters:
the sample size is **5,546 windows**, not 229,450 fills, because fills in one
window share a single price path.

## 3. Ex-ante cohorts only (items 4, 5, 6)

Re-cut using **only what is observable at order time** — elapsed time, and
absolute fill index. Chronological split, IS 2026-03-19…07-21, OOS
07-21…08-31, 2,773 windows each:

| cohort | mean px | P(win) | edge/ctr | IS bp/$ | IS t | **OOS bp/$** | **OOS t** |
|---|---|---|---|---|---|---|---|
| TIME 0–300s | 0.5062 | 0.5086 | +0.0024 | +108.6 | +2.17 | **+20.0** | +0.67 |
| TIME 300–600s | 0.4768 | 0.4778 | +0.0010 | +48.5 | +1.23 | +7.9 | +0.29 |
| TIME 600–900s | 0.4584 | 0.4593 | +0.0009 | +40.0 | +1.11 | +8.5 | +0.31 |
| IDX 1–10 | 0.5147 | 0.5150 | +0.0003 | +70.2 | +0.76 | **−44.8** | −0.48 |
| **IDX 11–50** | 0.4887 | 0.4933 | **+0.0046** | +86.6 | +2.37 | **+98.4** | **+2.64** |
| IDX 51+ | 0.4683 | 0.4683 | +0.0001 | +39.4 | +1.33 | −12.6 | −0.75 |

**Every time-based cohort collapses out-of-sample.** The 0–300s edge falls
from +108.6 to +20.0 bp/$. So "trade early" is not the answer — timing is not
the variable.

**One cohort survives and strengthens: fill index 11–50.** It is the only
ex-ante cut that is positive and significant in the held-out half.

## 4. But the surviving band is unreachable (item 7)

Our live engine does **3.17 fills/window**; our $12 envelope at cap 3 permits
**~14.2** (`BTC15M_NET_SOURCE.md`). Decomposing the band:

| fill band | mean px | P(win) | edge/ctr | IS bp/$ | **OOS bp/$** | OOS t |
|---|---|---|---|---|---|---|
| **1–3 — our live range** | 0.5175 | 0.5197 | +0.0022 | +168.5 | **−53.5** | −0.32 |
| 4–10 | 0.5133 | 0.5127 | −0.0006 | +24.6 | **−40.5** | −0.37 |
| **11–14 — our $12 frontier** | 0.5030 | 0.5028 | −0.0002 | +223.4 | **−193.9** | −1.33 |
| 15–20 | 0.4967 | 0.5041 | +0.0073 | +109.5 | +179.7 | +1.52 |
| **21–50** | 0.4850 | 0.4897 | **+0.0046** | +61.0 | **+123.5** | **+2.68** |

**The entire 11–50 edge lives in fills 21–50.** The part we could touch —
11–14 — is **−193.9 bp/$ out-of-sample**, and both ranges below it are also
negative. This is not a matter of getting a smaller slice of the same edge:
**the reachable bands have the opposite sign.**

### Multiplicity, stated against my own result

Twelve ex-ante cohorts were tested in §3 and §4. Bonferroni at α = 0.05 gives
α/12 = 0.0042, needing **|t| ≥ 2.86**. The surviving band is **t = +2.68**, so
it **fails** the multiplicity-corrected threshold. The 11–50 aggregate
(t = +2.64) sits exactly on the 6-test threshold and below the 12-test one.

**So the profit source is located but not statistically established.** It is
the strongest ex-ante signal found anywhere in this program, and it is still
below the bar once the search is priced in.

## 5. Reconciling the positive replay with the negative live results (item 8)

These three facts were previously hard to hold together. They now reconcile
exactly:

| observation | explanation |
|---|---|
| historical replay at cap 3 / $12 gave **+$0.1110/window** | it spanned fills 1–14 and credited us **L5's fills at L5's prices**, the optimism flagged in `BTC15M_NET_SOURCE.md` §5 |
| our live campaign realized **−$0.0108/window** | our live fill range is **1–3**, which is **−53.5 bp/$ out-of-sample** |
| the two disagree in sign | they are **not the same fill range**, and the replay's prices were not ours |

**Our live result is not an execution failure. It is the correct sign for the
fill range we operate in.** The replay was measuring a band that includes
−193.9 bp/$ territory while handing us prices we could not obtain. Both
numbers are right about different things.

## 6. The four answers

**Where does L5's trading profit originate?**
In **fills 21–50 of each window**: mean price paid 0.4850 against a 0.4897 win
rate, an ex-ante edge of **+0.46¢/contract**, **+123.5 bp/$ out-of-sample**.
Not from timing (every time cohort collapses OOS), not from the first ten
fills (which reverse OOS), not from the final third (negative in both halves).
The "early cohort earns +$17,198" framing is an ex-post artifact of `i/n`.

**Which part can we reproduce economically?**
**None of it.** Fills 1–3 (live): −53.5 bp/$ OOS. Fills 4–10: −40.5. Fills
11–14 (our envelope's ceiling): −193.9. Every band we can reach is negative
out-of-sample. Reaching fill 21 needs ~21+ fills/window against our ~14.2
maximum at the authorized envelope, so the positive band is **outside our
capital**, not merely harder for us.

**The single highest-value remaining test**
**Whether our maker fill conversion supports 21+ fills per window at a
1-contract clip.** This is the *same* missing observation named in
`BTC15M_NET_SOURCE.md` §7 — now with a precise target and a precise payoff
rather than a general wish for more fills. It is unambiguously decisive:

- if conversion supports ~21+ fills within the **existing** $12 / cap-3 /
  maker-only envelope, the positive band becomes reachable and is worth
  ~+123 bp/$ on the capital deployed in it;
- if it does not, L5's profit source is permanently outside our envelope and
  the replication program should stop looking for it in BTC15m.

It cannot be answered from shadow (`QUEUE_AWARE_THREE_SCENARIO` is modelled,
not observed) or from L5's tape (his queue at 2.39× average fill size is not
ours). **It is a measurement question, not a strategy question**, and it
requires no new capital, no loosened limit and no new service — only more
order placements inside the limits already approved.

**Is there enough evidence to justify a bounded live validation?**
**No.** Three reasons, any one sufficient:

1. the only positive band is **unreachable** at the authorized envelope, so a
   validation inside current limits would test fills 1–14 — the range already
   measured at **negative** OOS;
2. the surviving result **fails multiplicity correction** (t = 2.68 against a
   required 2.86 over 12 cohorts), so it is suggestive, not established;
3. per the power recheck in `RESIDUAL_MATCHED_RATIO.md`, detecting a
   per-window effect of this size live needs **~956 windows minimum** and our
   seven-window sd is 1.05 — a bounded probe cannot resolve it either way.

I am **not** proposing more frequency or more capital: §4 is the evidence that
more fills in the **reachable** range would make things worse, not better.
What I am proposing is **nothing**, pending a read-only measurement of fill
conversion that the existing shadow infrastructure cannot supply and that I
have not been authorized to obtain.

## 7. Limitations

1. **±10% on every dollar figure**, from the 98.10%-accurate winner inference
   ($1,638 on $18,074). Signs and rankings are unaffected.
2. **No order-book depth for L5's windows** (`TRAJECTORY_TRANSFER_VERDICT.md`
   §5). Every "edge" here is `P(win) − price L5 actually paid`, which is a
   statement about his trades. It is **not** a claim that we could have
   obtained those prices, and the §4 conclusion does not depend on one.
3. **One chronological split.** The OOS half is 2026-07-21…08-31, which is
   L5's weakest trading regime (+4.3 bp/$ in August). A result that survives
   there is conservative; a result that dies there might have lived in a
   stronger regime.
4. **Fill index is a proxy for inventory depth**, not a cause. Why fills 21–50
   should beat their price while 1–14 do not is unexplained, and an
   unexplained index effect across 12 tested cohorts is exactly the kind of
   finding that fails to replicate.

No infrastructure created, no strategy deployed, no live authorization
requested, nothing armed.
