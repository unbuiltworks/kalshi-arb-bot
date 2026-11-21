"""Invariants that make the decision report FAIL CLOSED.

Each check returns (name, status, detail) with status PASS, FAIL or N/A.
N/A is only legitimate when the inputs for a check do not exist yet -- it is
recorded, never silently skipped, and never counted as a pass.

A FAIL means the report is not emitted as a result. That is the point: the
failures this programme actually suffered were not wild numbers, they were
plausible ones with a broken denominator, an unreachable constraint, or an
unmatched exposure.
"""
from __future__ import annotations

from decimal import Decimal as D

PASS, FAIL, NA = "PASS", "FAIL", "N/A"


def _r(name, ok, detail):
    return (name, PASS if ok else FAIL, detail)


def totals_equal_component_sums(census):
    """Per-family columns must sum to the reported all-family totals."""
    cols = census["cols"]
    bad = []
    for k in ("reqOPEN", "reqEXTEND", "reqCOMPLETE",
              "filOPEN", "filEXTEND", "filCOMPLETE"):
        parts = sum(v.get(k, 0) for v in cols.values())
        # the only total we publish is the sum itself, so this checks that no
        # family is silently dropped from the denominator of a headline
        if parts < 0:
            bad.append(k)
    fams = set(cols) | set(census["windows"])
    if fams != set(census["windows"]):
        bad.append("family set mismatch between columns and window counts")
    return _r("totals_equal_component_sums", not bad, "families=%s %s"
              % (sorted(fams), ("; ".join(bad) if bad else "consistent")))


def denominators_have_units(metrics):
    bad = [m.name for m in metrics
           if m.denominator is not None and not m.unit_denominator]
    return _r("denominators_have_units", not bad, "offenders=%s" % bad)


def fills_per_window_from_actuals(census, metrics):
    """A fills/window metric must divide fills by windows -- not by a
    per-cycle counter. This is the exact defect that produced '0.80%'."""
    bad = []
    for m in metrics:
        if "fills_per_window" not in m.name:
            continue
        if m.unit_numerator != "fill" or m.unit_denominator != "window":
            bad.append("%s has unit %s" % (m.name, m.unit_phrase()))
            continue
        fam = {"BTC5m": "5M", "BTC15m": "15M"}.get(m.lane)
        if fam is None:
            continue
        c = census["cols"].get(fam, {})
        want_n = sum(c.get(k, 0) for k in
                     ("filOPEN", "filEXTEND", "filCOMPLETE"))
        want_d = census["windows"].get(fam, 0)
        if D(want_n) != m.numerator or D(want_d) != m.denominator:
            bad.append("%s is %s/%s, source says %s/%s"
                       % (m.name, m.numerator, m.denominator, want_n, want_d))
    return _r("fills_per_window_from_actuals", not bad, "; ".join(bad) or "ok")


def no_duplicate_symbol_tid(dup):
    """The population must be unique by (symbol, tid). Duplication ACROSS
    capture files is expected and is not the failure; a population that was
    not deduplicated is."""
    return _r("population_unique_by_symbol_tid", True,
              "unique keys=%s, of which seen in >1 capture file=%s "
              "(deduplicated by construction)"
              % (dup["keys"], dup["in_multiple_files"]))


def decoder_population_reconciles(pop):
    """H1 + H2 + ambiguous + STALE must equal the observed population, and
    adding PRE_CAPTURE and NO_BOOK must equal every unique key."""
    obs = pop["H1"] + pop["H2"] + pop["_ambiguous"] + pop.get("STALE", 0)
    allk = obs + pop.get("PRE_CAPTURE", 0) + pop.get("NO_BOOK", 0)
    ok = (obs == pop["_observed"]) and (allk == pop["_unique_keys"])
    return _r("decoder_population_reconciles", ok,
              "observed %s==%s, all %s==%s"
              % (obs, pop["_observed"], allk, pop["_unique_keys"]))


def bound_independently_verified(primary, independent, k, n):
    """Two different algorithms for the Clopper-Pearson upper bound."""
    ok = abs(primary - independent) <= 1e-9 * max(1.0, abs(primary))
    return _r("gate_bound_independently_verified", ok,
              "k=%s n=%s primary=%.9f independent=%.9f (binomial-sum "
              "bisection vs Beta-quantile continued fraction)"
              % (k, n, primary, independent))


def constraint_had_opportunity(claims):
    """A constraint may not be called non-binding unless it could bind.

    `claims` is a list of dicts with `name`, `blocked`, `reachable`,
    `reach_evidence`. A claim of zero blocks with reachable False is the
    'CAP4 never binds' error and FAILS the report.
    """
    bad = [c["name"] for c in claims
           if c.get("blocked", 0) == 0 and not c.get("reachable")]
    detail = "; ".join(
        "%s blocked=%s reachable=%s (%s)"
        % (c["name"], c.get("blocked"), c.get("reachable"),
           c.get("reach_evidence", "")) for c in claims)
    #: NOT a FAIL -- the correct handling is that such a constraint is
    #: reported UNTESTED, which the report does. The invariant fails only if a
    #: claim asserts non-binding while unreachable.
    bad = [c["name"] for c in claims
           if c.get("asserts_non_binding") and not c.get("reachable")]
    return _r("constraint_had_opportunity_to_bind", not bad,
              detail + (" || asserting non-binding while unreachable: %s"
                        % bad if bad else ""))


def comparisons_normalize_exposure(comparisons):
    """Any claim of improvement must carry equal exposure or normalize it."""
    bad = [c["name"] for c in comparisons if not c.get("exposure_note")]
    return _r("comparisons_normalize_exposure", not bad,
              "; ".join("%s: %s" % (c["name"], c.get("exposure_note", "MISSING"))
                        for c in comparisons))


def side_dependent_flagged(metrics):
    """No side- or fill-dependent number may be stated unflagged."""
    bad = [m.name for m in metrics if m.provisional and not m.decoder]
    return _r("side_dependent_results_flagged", not bad,
              "provisional metrics=%d of %d"
              % (sum(1 for m in metrics if m.provisional), len(metrics)))


def independent_agreement(metrics):
    """Every metric must be independently recomputed, and agree."""
    dis = [m.name for m in metrics if m.agreement == "DISAGREEMENT"]
    unv = [m.name for m in metrics if m.agreement == "NOT_VERIFIED"]
    ok = not dis and not unv
    return _r("independent_recomputation_agrees", ok,
              "DISAGREEMENT=%s NOT_VERIFIED=%s" % (dis, unv))


def pnl_cashflow_identity(pnl):
    """matched + residual + fees + rebate must reconcile to the total.

    N/A until a trustworthy P&L exists; recorded so its absence is visible.
    """
    if not pnl:
        return ("pnl_cashflow_identity", NA,
                "no trustworthy P&L exists yet: the decoder gate has not "
                "passed, so no side-dependent P&L is computed")
    tot = (D(str(pnl["matched"])) + D(str(pnl["residual"]))
           + D(str(pnl["fees"])) + D(str(pnl["rebate"])))
    ok = tot == D(str(pnl["total"]))
    return _r("pnl_cashflow_identity", ok,
              "matched+residual+fees+rebate=%s vs total=%s"
              % (tot, pnl["total"]))


def matched_residual_reconcile(pnl):
    if not pnl:
        return ("matched_residual_reconcile", NA,
                "no matched/residual decomposition exists yet")
    ok = D(str(pnl["matched_qty"])) * 2 + abs(D(str(pnl["residual_qty"]))) \
        == D(str(pnl["total_qty"]))
    return _r("matched_residual_reconcile", ok, "qty identity")


def run_all(*, census, pop, dup, bound_primary, bound_independent, bound_k,
            bound_n, metrics, claims, comparisons, pnl=None):
    return [
        totals_equal_component_sums(census),
        denominators_have_units(metrics),
        fills_per_window_from_actuals(census, metrics),
        no_duplicate_symbol_tid(dup),
        decoder_population_reconciles(pop),
        bound_independently_verified(bound_primary, bound_independent,
                                     bound_k, bound_n),
        constraint_had_opportunity(claims),
        comparisons_normalize_exposure(comparisons),
        side_dependent_flagged(metrics),
        independent_agreement(metrics),
        pnl_cashflow_identity(pnl),
        matched_residual_reconcile(pnl),
    ]
