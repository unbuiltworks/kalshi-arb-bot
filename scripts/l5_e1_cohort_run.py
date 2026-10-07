"""THE E1 COHORT ENTRYPOINT. DISARMED. NOT AUTHORIZED.

`python3 -m scripts.l5_e1_cohort_run`

This module exists because its absence was a DEFECT. COHORT-E1-LIVE's frozen
spec inherited `entrypoint` verbatim from XR2W, so it still named
`scripts.l5_exec1xr2w_run` and a call graph through
`analysis.l5.live_exec3.runner:run_experiment` and XR2W's SINGLE-SLOT latch.
There was no E1 entrypoint in `scripts/` at all. Arming "per the spec" would
have launched XR2W's ONE-ORDER probe through the single-slot latch and
measured nothing -- and every test passed while that was true, because they
all started from the E1 runner rather than from the DECLARED entrypoint.

So this file is deliberately thin. It contains no decision, no bound and no
order construction; it is the named, executable door that the frozen spec
points at, and it delegates immediately to the E1 cohort runner. Everything
it reaches is already frozen and already tested.

WHAT IT DOES NOT DO. It does not import `analysis.l5.live_exec3.runner`, it
does not import XR2W's single-slot `analysis.l5.live_exec2.latch`, and it
does not place an order. It boots, asserts the frozen spec, prints the
DISARMED refusal and returns 0.
"""
from __future__ import annotations

import sys

from analysis.l5.e1_cohort.runner import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
