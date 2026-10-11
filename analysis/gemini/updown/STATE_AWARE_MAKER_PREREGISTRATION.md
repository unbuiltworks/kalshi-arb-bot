# STATIC_MAKER baseline (FROZEN) + STATE_AWARE_MAKER (PREREGISTERED)

RESEARCH ONLY. **No live maker authorization.** Neither file sends an order,
arms anything, touches the live UNDERDOG cohort or the recorder, modifies
either frozen analyzer (f2800e3e...960e90b, b2f6d50a...142f9bc6), or opens
any venue surface.

## Fingerprints

| artifact | sha256 |
|---|---|
| `scripts/gemini_btc5m_maker_study.py` -- **STATIC_MAKER, FROZEN** | `267ef1d15944feb49c5e25c897e61064e446f9d5a5e4ce7d7a33ac965f763a63` |
| `scripts/gemini_btc5m_state_aware_maker.py` -- STATE_AWARE, preregistered | `513eff6eb6d10562e5d60a2f9f3188fcc7fff981299a6163965f09292f936842` |

Both print their own sha256 on every run. The state-aware study **asserts
the baseline fingerprint before it computes anything** and refuses with
`status: refused` if the baseline has changed -- so the comparison cannot
quietly be made against a moved baseline.

## Order of operations when the prospective snapshot lands

1. Run the **frozen STATIC_MAKER comparison first**, unchanged, and report
   it before making any change to anything.
2. Then run STATE_AWARE, unchanged.
3. Only after both are reported does any modification get discussed.

The baseline is **not tuned on the validation sample**. Its placements,
queue convention, evidence tiers and fee model were fixed before the seven
proof-run fills were seen. The two corrections made before freezing were
DEFECTS, not tuning -- the aggressor side was being ignored, and unsettled
windows were being counted as unfilled orders -- and neither changed a
threshold.

## The three policies, kept separate on purpose

| policy | what it adds | isolates |
|---|---|---|
| TAKER | cross immediately | the directional decision alone |
| STATIC_MAKER | rest inside the spread, never cancel | + passive pricing |
| STATE_AWARE_MAKER | rest, and cancel on a causal condition | + stale-fill avoidance |

`passive_pricing_delta = STATIC - TAKER`.
`stale_fill_avoidance_delta = STATE_AWARE - STATIC`.
A gain that also appears in TAKER is directional and is not about making.

## Predeclared cancellation variants -- fixed, independent, not fitted

Every constant was written before the file was first executed. Variants are
reported **independently**: no sweep, no argmax, no best-variant output.
`ALL` is the predeclared conjunction, not a selection.

* **C1_PASSIVE_RECEDED** -- the passive touch we were improving moved >= 1
  tick against us; our quote is stale-rich.
* **C2_SPREAD_LEFT** -- our price is no longer strictly inside the spread.
* **C3_QUEUE_GREW** -- size displayed ahead of us is >= 2x placement.
* **C4_REFERENCE_MOVED** -- the published reference moved adversely >= 5 bps
  of the strike, using the last value published at or before the
  observation.
* **C5_AGE** -- unfilled after 60s. A time-in-force, not a signal.

## Causality, structural rather than promised

* a cancel decision reads **only** observations strictly after placement and
  strictly before the hypothetical execution;
* `assert_causal()` raises on any cancel feature whose name mentions an
  outcome, and **the settlement is not a parameter of the cancel function**;
* NO TOUCH = FILL is inherited unchanged: a qualifying later print, with the
  aggressor on the side that could have hit us, and enough size behind it;
* **ties go against the cancel.** A cancel and a fill on the same
  observation counts as a FILL. A backtest that wins ties is measuring its
  own optimism.

## Proof-run output is NOT a result

Run on the 23 settled validation-tape opportunities only, because the
prospective tape does not exist yet (census: 0 complete windows). At p50:
taker +0.0065/opportunity, static -0.0948 (passive-pricing delta
**-0.1013**), and the cancel variants recover between 0.0000 and 0.0626 of
that -- C1/C5/ALL avoiding 4 of 7 fills, C2 avoiding 2, C4 one, C3 none.
**Not one variant gets back to the taker.** With 7 static fills in total
these are counts, not estimates, and nothing is concluded from them. The
seven fills were not used to choose any constant above.
