"""Next-stage live ECONOMIC-MEASUREMENT probe: preparation only.

NOTHING IN THIS PACKAGE MOVES MONEY. There is no order verb anywhere in it --
no place_order, submit_order or cancel_order -- and
tests/test_l5_live_probe_closure.py asserts that over the source in a
subprocess. What lives here is the harness a future probe would need: a
preflight that fails closed, an incentive join, an accounting chain, a
reconciliation loop and a written promotion/rollback procedure.

WHY "ECONOMIC MEASUREMENT" AND NOT "STRATEGY". The frozen prospective
experiment ADAPTED_RESIDUAL_CAP_PROSPECTIVE_V0 returned FAIL on all three arms,
and the failure decomposition found the residual is an adversely selected
directional bet no observable risk state controls. There is therefore NO
qualified strategy to arm. This harness exists to measure venue economics --
minimums, fees, actual credited rebates, queue behaviour -- against our own
account, which is a different question from whether a policy is profitable and
does not depend on one being.

THE ENVELOPE IS DELIBERATELY UNSET. `shell()` raises until someone chooses the
bounds. A risk shell that works without being configured is one that can be
deployed without anyone having decided the limits.
"""
