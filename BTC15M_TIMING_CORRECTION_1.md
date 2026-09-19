# BTC15M_TIMING_CORRECTION_1 — `quote_between_tte_s` [0, 300] → [0, 900]

**Status:** SHADOW ONLY. Not authorized for any live path. Zero venue
mutations. `mutations_sent = 0` on every arm, every cycle.

**Artifacts:**
`artifacts/l5_surface/L5_ADAPTED_GEMINI_BTC15M_V2.json`
(`cd8ad6a914dc9fc0…`), derived from and shadowed against
`L5_ADAPTED_GEMINI_CRYPTO` (`62d004d80b4af8b9…`, **unchanged**).

---

## 1. The question

Model A quotes a Gemini BTC15m contract only inside `tte <= 300`. Is that
supported by anything L5 does?

## 2. Provenance of `[0, 300]` — it is not an L5 measurement

Established in `TTE_PROVENANCE_1.md` and restated here because the
correction rests on it:

| | |
|---|---|
| status in the frozen artifact | `OURS_ADAPTED` |
| `evidence` | `null` |
| `transferred_from` | `null` |
| L5's own measured `TTE_policy` | `OBSERVED_PROSPECTIVE`, **`[0, 900]`** |

Classification: **category 4 — Gemini adaptation.** The 300 is the length of
a Gemini **BTC5m** contract. Commit `9b8a1ebe` (2026-09-17 21:37:55Z) chose
it for the 5-minute cohort, where it is correct and never binds. Commit
`fdd19d06` (2026-09-19 14:44:01Z) bound BTC15m to that same lane without
re-deriving the number.

`lanes.py` already declares `TTE_policy` INSTRUMENT_SPECIFIC *precisely
because the number is a window length*. The defect is not a disagreement
with that rule — it is the rule not being applied a second time. A Gemini
BTC15m window is 900s, so the rule, applied to this instrument, gives 900.
That it coincides with L5's measured `[0, 900]` is a second, independent
route to the same number.

## 3. What L5 actually does — 63,569 fills, 527 windows

Per-fill segmentation of L5's own Polymarket BTC15m activity. Rows deduped
by `(hash, asset, side, size, price, ts)`; the activity API paginates by
offset and its pages overlap, so the 67,511 raw rows are 63,569 distinct
fills.

| bucket | fills | USD share | **OPEN** | EXTEND | COMPLETE | recycle | median basis |
|---|---|---|---|---|---|---|---|
| 600–900 | 18,604 | 30.3% | **560** | 9,028 | 9,016 | 0.810 | 0.9737 |
| 300–600 | 22,687 | 34.2% | **46** | 11,155 | 11,486 | 1.003 | 0.9520 |
| 180–300 | 10,100 | 16.1% | **13** | 5,271 | 4,816 | 0.925 | 0.9854 |
| 120–180 | 4,644 | 7.7% | **6** | 2,404 | 2,234 | 0.886 | 0.9924 |
| 60–120 | 4,890 | 8.5% | **1** | 2,638 | 2,251 | 0.866 | 0.9876 |
| 30–60 | 2,165 | 2.5% | **0** | 1,218 | 947 | 0.865 | 0.8792 |
| <30 | 472 | 0.7% | **0** | 267 | 205 | 0.765 | 1.1622 |

First-fill TTE per window: median **885.0s**, p25 853, p10 741, max 896.
Last-fill TTE: median 102s.

**560 of 626 OPEN fills (89.5%) land at tte 600–900. 606 of 626 (96.8%)
land above tte 300. None land below tte 60.** Under `[0, 300]` our clone is
present only for the 3.2% tail in which L5 has essentially stopped opening,
and is absent for the entire interval in which he builds inventory.

`NO_NEW_OPEN_EXTEND_TTE_S = 60` is, separately, **confirmed** by this table:
zero OPEN fills below tte 60.

## 4. Does earlier timing actually recycle on Gemini? — the paired control

Not an argument from L5's behaviour: a direct measurement on Gemini's own
tape. 96 expired BTC15m contracts, **one fetch each**, replayed under both
bands, so treatment and control differ in timing and in nothing else.

Both legs are priced from prints that **actually traded**. Gemini's tape is
priced in YES and carries the aggressor's direction, so a resting YES rung
at `h` is reached by a SELL at `<= h` and a resting NO rung at `n` by a BUY
at `>= 1 - n`. Gemini serves **no book history**, so queue position at the
moment of the print is unrecoverable: `maker_reachable` is **UNOBSERVABLE**
on every row and is never promoted to True. Every figure below is an upper
bound conditional on having been filled.

| | EARLY `[300, 900]` | LATE `[0, 300]` (frozen) |
|---|---|---|
| contracts | 96 | 96 |
| any print inside the band | 92 | **58** |
| first-fill states | 92 | 58 |
| opposite flow | **76** | **28** |
| price-observable complements | **76** | **28** |
| maker-reachable complements | UNOBSERVABLE | UNOBSERVABLE |
| pairs at or below $1 | **33** | 14 |
| fee-positive pairs | **28** | **12** |
| median actual pair basis | 1.0200 | 1.0050 |
| median seconds to complement | 81.0 | 88.0 |
| prints inside the band | 812 | 178 |
| distinct bases | 40 | 22 |

Paired, per contract:

|  | LATE fee+ | LATE not |
|---|---|---|
| **EARLY fee+** | 6 | **22** |
| **EARLY not** | **6** | 62 |

Two-sided sign test on the 28 discordant contracts: **p = 0.0037**.

Two things in this table point the same way and one points the other:

- **Conditional quality is slightly better late** (50.0% of late complements
  are at or below par against 43.4% early; median basis 1.005 vs 1.020).
- **Absolute opportunity is far greater early** — 2.33× the fee-positive
  pairs, 4.6× the prints, and **38 of 96 contracts have no prints at all**
  inside the frozen band.

The owner's stated metric is recycling, not first-fill count and not
conditional rate. On that metric the frozen band is where the flow is not.

## 5. The correction

**One field. `quote_between_tte_s: [0, 300] → [0, 900]`.**

A test asserts that exactly one field differs between
`L5_ADAPTED_GEMINI_BTC15M_V2` and `L5_ADAPTED_GEMINI_CRYPTO`, and that the
probe-scoped specs the two running arms actually use also differ in exactly
that one field — including nothing about size.

**Unchanged:** side selection, `tick_offsets`, `rung_count`,
`both_sides_policy`, sequential inventory state, EXTEND/COMPLETE
classification, `base_parent_qty`, `secondary_rung_qty`, `max_residual`,
`NO_NEW_OPEN_EXTEND_TTE_S` (60s), `WITHDRAW_ALL_TTE_S` (30s), `MAX_ACTIONS`
(12), the GEMINI_RECYCLE EXTEND veto, the crossed-book refusal, and the
canonical execution/inventory accounting.

**The correction is not a lane.** `lane_gates.SOURCES` is keyed by
`lanes.LANES`; a correction that joined `LANES` would acquire a
live-progression gate by existing rather than by earning one. It is declared
in `lanes.CORRECTIONS` and pinned out of `LANES` by test.

**The corrected field stays `OURS_ADAPTED`.** It coincides with a value
measured about L5, but we did not measure it here and arrived at it from our
own instrument's length. Calling it observed would be claiming a finding this
venue has not produced. The provenance — including the 63,569, the 89.5%,
the p=0.0037 and the 38-of-96 — lives in the artifact's `prior_evidence`
text, where anything reading the spec can find it.

## 6. One schedule change, and why it was unavoidable

`CC.BTC15M.entry_band` moves from `(240, 280)` to `(840, 880)`.

The old band was correct *given* `[0, 300]`: enter inside the quoting window
so the lane is live from its first cycle. With the corrected arm running, an
entry at 240–280 would leave the process unattached to the contract for the
entire interval the correction is about — the arm would produce no evidence
and **its silence would read as a finding about the correction rather than
about the schedule**.

The entry band is experimental scheduling and has always been explicitly
outside Model A. **The control's decisions do not change:** above tte 300 the
frozen band withholds its surface, exactly as before. What changes is that
the withholding is now recorded cycle by cycle instead of inferred from the
lane's absence.

**BTC5m is untouched** — band still `(240, 280)`, no correction arm. Its
window is 300s, so `[0, 300]` and `[0, 900]` are the same band there and a
second arm would be the first one wearing a different label.

## 7. How the two arms run

One process, one cohort loop, both arms on the **same tape, book and depth**
every cycle. Each arm owns its guard, its inventory and its hypothetical
book; nothing is shared. Sharing either would make the second arm a function
of the first's fills and the comparison would be measuring itself.

Per cycle the evidence row carries `correction_arms[label]` with that arm's
decision, `model_a_wanted`, adapter vetoes, hypothetical fills, live rungs
and inventory. The per-window record and the cohort heartbeat carry the arm's
counters **separately**; they are never summed into the control's.

An arm that raises is recorded and the window continues — driven by test, not
asserted about. A research arm must not be able to take down a 24/7
collector.

## 8. What would falsify the correction

The correction is a claim about *recycling*, so first-fill count cannot
confirm it. It is supported if the corrected arm produces, prospectively:

**OPEN fill → inventory change → COMPLETE fill → par-constrained matched
inventory**

repeatedly, where the control does not. If the corrected arm accumulates
first fills at tte 600–900 and still shows **zero complements and zero
matched pairs** over a comparable sample, timing was not the binding
constraint, and the honest report is that earlier timing does not rescue
Gemini either — not that more uptime is needed.

## 9. Labels

Nothing in this document is realized profit. Every Gemini figure is
HYPOTHETICAL: no order was submitted, nothing was filled, settled or
realized, and no cost basis exists. The paired replay is an upper bound
conditional on fills that never happened, and `maker_reachable` is
UNOBSERVABLE rather than assumed. The L5 table is his fills, not ours.

**Sources:** `analysis/l5/tte/btc15m_by_fill_tte.json`,
`analysis/gemini/early_15m_observed.json`,
`analysis/gemini/early_15m_observed_late_15m_control.json`,
`TTE_PROVENANCE_1.md`, `L5_TTE_SEGMENTS_1.md`.
