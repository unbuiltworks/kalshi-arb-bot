# This directory is the SESSION-RECORDER population. It is closed.

Collected 2026-09-22T21:00Z – 2026-09-23T01:10Z by `p10a_capture.py` running as
background processes inside an interactive session. **That substrate is reaped
when a turn ends**: recorders died silently at 23:32Z, 23:52Z and repeatedly
afterwards, and the population stopped growing without any error.

**Final state of the pre-registered gate on this population:**

| gate | threshold | observed | verdict |
|---|---|---|---|
| G1 decoded n | ≥ 150 | 170 | PASS |
| G2 ambiguous rate | ≤ 25% | 26.4% | FAIL |
| G3 decoder agreement | ≥ 98% | 164/164 | PASS |
| G4 95% upper bound on H2 | ≤ 2.0% | 4.5% (k=3) | FAIL |
| G5 structural prediction | ≥ 80% | 78.7% | FAIL |

**GATE NOT PASSED.** Required n at k=3 is 386; this population reached 170 and
its collection has ended. G2 additionally carries an unresolved
merged-vs-per-file denominator (`G2_DENOMINATOR_RESOLUTION.md`) which is
FAIL CLOSED.

**Do not merge this with `p10_persistent/`.** That directory holds the
prospective population from the 24/7 recorder. Combining them would let a
failed gate be rescued by data collected under a different substrate after the
fact. Any claim against the persistent population needs its own
pre-registration.
