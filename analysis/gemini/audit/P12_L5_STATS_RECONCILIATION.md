# P12 — reconciling 23/2,216 against 1/5,546, and verifying the cited L5 statistics

Independent recomputation: `analysis/gemini/audit/p12_l5_stats_verify.py`.
It implements the inventory walk, the episode segmentation and every statistic
itself. It imports `iter_activity` and `window_of` (the raw reader and the slug
parser) because re-implementing the multiset dedupe would risk a new I/O bug
rather than check the analysis; it imports **nothing** from
`residual_exit/forensics.py`, the code under audit.

## OBSERVED FACTS — the two populations are different, and both figures are right

| | **Population A** | **Population B** |
|---|---|---|
| artifact | `analysis/l5/l5_all_trades.csv` (= `l5_canonical_sequence.csv.gz`, same 71,984 rows) | `data/l5/raw/activity.jsonl` + `activity_gap.jsonl.gz` via `redemption/audit.iter_activity` |
| instrument filter | **none** — BTC 1,484 + ETH 732; 5m 1,660 + 15m 556 | **BTC15m only** (`window_of` == btc15m) |
| date range | 2026-03-19 → 08-31 | 2026-03-19 → 08-31 (identical) |
| windows | **2,216** ✓ | **5,546** ✓ |
| fills | **71,984** ✓ (71,982 BUY + 2 SELL) | **693,916** ✓ (0 non-buy skipped) |
| definition of the statistic | terminal `Σup − Σdn == 0` at window end | window not `CARRIED_TO_SETTLEMENT` |
| headline | **23 of 2,216 = 1.04%** ✓ | **1 of 5,546 = 0.018%** ✓ |

Both reproduced exactly. Neither is wrong. **They are 58× apart because they
are not measuring the same instrument**, and the proof is the breakdown of
Population A by asset and duration:

| asset | minutes | windows | zero-residual | rate | fills/window |
|---|---|---|---|---|---|
| BTC | **15** | 527 | **0** | **0.00%** | **120.6** |
| BTC | 5 | 957 | 19 | 1.99% | 6.5 |
| ETH | 15 | 29 | 0 | 0.00% | 4.0 |
| ETH | 5 | 703 | 4 | 0.57% | 2.9 |
| ALL | | 2,216 | 23 | 1.04% | 32.5 |

**All 23 flat windows are 5-minute windows.** Population A's own BTC15m cell —
the same instrument as Population B, at comparable density (120.6 fills/window
against B's 125.1) — contains **zero** flat windows in 527, which agrees with
B's 1 in 5,546 rather than contradicting it.

The mechanism is fill density, not a definitional trick: with 2.9–6.5 fills in a
window, ending with exactly equal Up and Down quantity is a plausible
coincidence; with 120 fills it essentially never happens.

### The correction this forces

**"Exactly one of 5,546 windows ended flat" is a BTC15m statement and must
never be quoted as a general statement about L5.** On 5-minute windows L5 ends
flat about **2%** of the time — two orders of magnitude more often. Our Gemini
lane runs **both** BTC5m and BTC15m, so the BTC15m figure is the wrong
comparator for the BTC5m arm. `POST_DECODER_EVALUATION_PREREG.md` cited the
BTC15m trajectory targets for both durations and is corrected accordingly.

## OBSERVED FACTS — independent verification of every newly cited statistic

Population B (BTC15m activity cache), my walk against the published figure:

| statistic | published | independent | verdict |
|---|---|---|---|
| windows | 5,546 | **5,546** | EXACT |
| fills | 693,916 | **693,916** | EXACT |
| episodes | 38,180 | **38,180** | EXACT |
| CARRIED_TO_SETTLEMENT | 5,545 | **5,545** | EXACT |
| windows ending flat | 1 | **1** | EXACT |
| FLIPPED share | 84.3% | **84.303%** | EXACT |
| COMPLETED_FLAT share | 1.2% | **1.173%** | agrees to the published precision |
| COMPLETED_FLAT count | 452 | **448** | **DISAGREEMENT, −4 (0.9%)** |
| carried residual / window volume, median | 7.7% | **7.684%** | EXACT to the published precision |
| flat share Mar | 15.4% | **15.444%** | EXACT |
| flat share Apr | 2.5% | **2.540%** | EXACT |
| flat share May | 2.1% | **2.102%** | EXACT |
| flat share Jun | 1.3% | **1.308%** | EXACT |
| flat share Jul | 1.6% | **1.616%** | EXACT |
| flat share Aug | 0.32% | **0.3148%** | EXACT |

The March→August abandonment trend reproduces on all six months.

**The one disagreement is COMPLETED_FLAT: 448 against the published 452.** It
does not move any share to published precision (1.17% vs 1.18%) and does not
touch the flat-window count, which is exact. It is not resolved here and is not
explained away; the most likely cause is a boundary convention on a fill that
lands exactly on zero *and* is the window's last fill, which my walk books as
CARRIED and the original may book as FLAT. **Recorded as DISAGREEMENT.**

## OBSERVED FACTS — the CAP4 justification is Population A, not B

`live_envelope.LIVE_Q1_BTC15M_CAP4` cites "2,216 windows, 35,576 same-side
actions, q-normalized to each window's own action unit". **2,216 is Population
A.** Measuring these on Population B gives numbers about ten times larger
(355,529 same-side actions, peak imbalance p50 17.1) purely because B holds ten
times the fills. Recomputed on the population the comment actually names:

| statistic | cited | independent (Population A) | verdict |
|---|---|---|---|
| windows | 2,216 | **2,216** | EXACT |
| same-side actions | 35,576 | **35,578** | **+2 — exactly A's 2 SELL rows**, which the original excludes and my walk signed |
| extends while holding ≥2 units | 82.1% | **81.9%** | −0.2pp |
| peak imbalance p50 | 2.76 | **2.63** | −4.7% |
| peak imbalance p75 | 5.21 | **5.04** | −3.3% |
| cap coverage k=2 | 38.2% | **39.8%** | +1.6pp |
| cap coverage k=4 | 66.9% | **68.2%** | +1.3pp |

These are the same measurement on the same population, agreeing to within a few
percent. The residual gap is consistent with a different choice of "the
window's own action unit" — I used the window's median clip and the original
does not state which statistic it used. **Reported as reproduced-with-caveat,
not as exact agreement.** The qualitative claim is unaffected: **a cap of 4
truncates roughly a third of L5's windows** (31.8% by my count, 33.1% by
theirs).

**This is a defect in my own earlier reporting**: `POST_DECODER_EVALUATION_PREREG.md`
listed the CAP4 coverage figures (Population A, mixed instruments) beside the
trajectory targets (Population B, BTC15m) as though they came from one source.
They do not.

## OBSERVED FACTS — the CAP replay, preserved precisely

From `analysis/gemini/P2_REPLAY_OVERLAYS.md`, 11 windows, sequential replay
(a refused EXTEND changes inventory, which changes the next decision):

| arm | fills | matched | residual | net |
|---|---|---|---|---|
| BASELINE | 58 | 3.32 | −7.10 | −3.78 |
| CAP4 | **58** | 3.32 | −7.10 | −3.78 |
| CAP3 | **57** | 3.16 | −6.44 | −3.28 |
| CAP2 | **52** | 2.82 | −5.67 | −2.85 |
| CAP1 | **38** | 1.50 | −3.18 | −1.68 |

**What this establishes:** on that corpus, CAP4 altered nothing and CAP3 was the
first cap that altered the sequence. The first bite sits between 3 and 4.

**What it does NOT establish**, and must not be quoted as establishing:

* it does **not** establish economic impact — every arm is net negative, and the
  net *improves* monotonically as the cap tightens (−3.78 → −1.68), which is a
  statement about buying less of a losing leg, not about CAP4 being good;
* it does **not** prove CAP4 safe — 11 windows and 58 fills is a corpus in which
  CAP4 had almost no opportunity to bind, which is the same power problem as
  the live-shadow sample;
* it does **not** transfer to the live lane, whose fill rate is far lower still.

The envelope conclusion stands at **UNTESTED AT RELEVANT INVENTORY DEPTH**.

## INTERPRETATION

1. The two flat-window figures are consistent once instrument and duration are
   held fixed. No number needs withdrawing; one needs **scoping**.
2. Fidelity targets must be **duration-matched**: BTC5m arms compare against
   L5's 5-minute behaviour, BTC15m arms against his 15-minute behaviour.
   Comparing a BTC5m clone to "1 in 5,546 ends flat" would score a correct
   clone as divergent.
3. Population A is the right source for anything cited "2,216 windows";
   Population B for anything cited "5,546 windows / BTC15m". Mixing them
   silently is how a 58× discrepancy looked like a contradiction.
