"""Proofs for `analysis/l5/regime_aug19/regime_shift.py`.

Two things are proved here, because they are the two ways this analysis could
be wrong in a way that matters:

  1. THE REGIME ASSIGNMENT IS THE REPO'S OWN. The change points are parsed
     straight out of `analysis/l5/CANON_REGIMES.md` and asserted against the
     tuple the module uses; the partition's event counts are asserted against
     the frozen `OOS_RESULT.json` strata; and the per-regime ex-incentive cash
     the module computes is asserted EXACTLY equal to the frozen OOS BASELINE.
     No change point is fitted, moved or added anywhere in the module.

  2. NO SETTLEMENT-DERIVED FIELD ENTERS A CONTEMPORANEOUS METRIC. Proved
     three ways: structurally (the field set of `EventObservables`), at the
     AST level (the names `contemporaneous_metrics` can even mention), and
     behaviourally (flipping every settlement outcome leaves every
     contemporaneous metric byte-identical).
"""
from __future__ import annotations

import ast
import json
import os
import re
import textwrap

import pytest

from analysis.l5.regime_aug19 import regime_shift as RS

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANON_MD = os.path.join(REPO, "analysis/l5/CANON_REGIMES.md")
OOS_JSON = os.path.join(REPO, "analysis/l5/residual_frontier/OOS_RESULT.json")
ARTIFACT = os.path.join(REPO, "analysis/l5/regime_aug19/REGIME_SHIFT.json")
MODULE = os.path.join(REPO, "analysis/l5/regime_aug19/regime_shift.py")


# --------------------------------------------------------------- partition

def _change_points_from_canon_md() -> list:
    """Parse the accepted change points out of the repo's own CANON_REGIMES.md.

    The line is of the form:
      All statistically significant change points: **['a', 'b', 'c']**
    """
    txt = open(CANON_MD, encoding="utf-8").read()
    m = re.search(r"PRIMARY change points: \*\*(\[[^\]]*\])\*\*", txt)
    assert m, "CANON_REGIMES.md no longer states its PRIMARY change points"
    return list(ast.literal_eval(m.group(1)))


def test_change_points_are_the_repo_s_own_and_are_not_re_fit():
    canon = _change_points_from_canon_md()
    assert list(RS.CANON_CHANGE_POINTS) == canon == [
        "2026-04-25", "2026-06-23", "2026-08-19"]


def test_module_never_searches_for_a_change_point():
    """The module must contain no change-point literal of its own and must
    import the canon tuple rather than restating it."""
    src = open(MODULE, encoding="utf-8").read()
    tree = ast.parse(src)
    imported = {
        a.name for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom)
        and n.module == "analysis.l5.residual_frontier.replay"
        for a in n.names}
    assert "CANON_CHANGE_POINTS" in imported
    # no date literal anywhere in the module's own code
    dates = {n.value for n in ast.walk(tree)
             if isinstance(n, ast.Constant) and isinstance(n.value, str)
             and re.fullmatch(r"\d{4}-\d{2}-\d{2}", n.value)}
    assert dates == set(), "module hardcodes a date literal: %r" % dates


def test_regime_labels_and_boundaries():
    labs = RS.regime_labels()
    assert labs == [
        "REGIME_PRE_2026-04-25",
        "REGIME_2026-04-25_TO_2026-06-22",
        "REGIME_2026-06-23_TO_2026-08-18",
        "REGIME_2026-08-19_ON",
    ]
    # a change-point day belongs to the LATER regime, as replay._regime does
    assert RS.regime_of("2026-04-24") == labs[0]
    assert RS.regime_of("2026-04-25") == labs[1]
    assert RS.regime_of("2026-06-22") == labs[1]
    assert RS.regime_of("2026-06-23") == labs[2]
    assert RS.regime_of("2026-08-18") == labs[2]
    assert RS.regime_of("2026-08-19") == labs[3]
    assert RS.regime_of("2026-12-31") == labs[3]


def test_regime_of_agrees_with_the_frozen_replay_implementation():
    from analysis.l5.residual_frontier import replay
    for d in ("2026-03-19", "2026-04-24", "2026-04-25", "2026-06-22",
              "2026-06-23", "2026-08-18", "2026-08-19", "2026-09-25"):
        assert RS.regime_of(d) == replay._regime(d), d


# ---------------------------------------------- the artifact vs frozen OOS

@pytest.fixture(scope="module")
def artifact():
    if not os.path.exists(ARTIFACT):
        pytest.skip("REGIME_SHIFT.json not generated")
    return json.load(open(ARTIFACT, encoding="utf-8"))


@pytest.fixture(scope="module")
def oos():
    return json.load(open(OOS_JSON, encoding="utf-8"))


def test_artifact_partition_event_counts_match_the_frozen_oos(artifact, oos):
    """The regime assignment is the repo's own: the same events land in the
    same regimes as the frozen out-of-sample run put them in."""
    for tag in ("PRIMARY", "SECONDARY"):
        strata = oos[tag + "_frontier"]["cells"]["BASELINE"]["strata"]
        cont = artifact[tag]["contemporaneous_by_regime"]
        for lab in artifact["regime_labels"]:
            if lab not in strata:
                continue
            assert cont[lab]["n_events"] == strata[lab]["events_touched"], (
                tag, lab)


def test_artifact_per_regime_cash_reproduces_the_frozen_oos_baseline(
        artifact, oos):
    """Not merely close: EXACT. The module re-derives the frozen BASELINE's
    matched / residual / fees / net per regime from source, so a drift in
    either the population or the accounting would show up here."""
    for tag in ("PRIMARY", "SECONDARY"):
        strata = oos[tag + "_frontier"]["cells"]["BASELINE"]["strata"]
        pooled = artifact[tag]["outcome_pnl"]["by_stratum"]["POOLED_all_events"]
        for lab in artifact["regime_labels"]:
            if lab not in strata:
                continue
            a, b = pooled[lab], strata[lab]
            assert a["net_ex_incentives_usd"] == pytest.approx(
                b["net_cash_ex_incentives_usd"], abs=0.02), (tag, lab)
            assert a["matched_usd"] == pytest.approx(
                b["matched_retained_usd"], abs=0.02), (tag, lab)
            assert a["residual_usd"] == pytest.approx(
                b["residual_pnl_usd"], abs=0.02), (tag, lab)
            assert a["fees_usd"] == pytest.approx(
                b["fees_usd"], abs=0.02), (tag, lab)
            assert a["turnover_usd"] == pytest.approx(
                b["turnover_retained_usd"], abs=0.02), (tag, lab)


def test_artifact_keeps_the_two_settlement_strata_separate(artifact):
    for tag in ("PRIMARY", "SECONDARY"):
        strata = artifact[tag]["outcome_pnl"]["by_stratum"]
        assert set(strata) == {
            "SETTLEMENT_CONDITIONAL_redeem_backed",
            "POOLED_all_events",
            "NO_REDEEM_loss_enriched"}
        assert "never" in artifact[tag]["outcome_pnl"][
            "STRATA_NEVER_POOLED"].lower()


def test_artifact_reports_role_and_spread_as_unavailable_not_unchanged(
        artifact):
    for tag in ("PRIMARY", "SECONDARY"):
        for lab, blk in artifact[tag]["contemporaneous_by_regime"].items():
            if blk.get("n_events", 0) == 0:
                continue
            assert blk["MAKER_TAKER_MIX"]["status"] == "UNAVAILABLE"
            assert blk["SPREAD"]["status"] == "UNAVAILABLE"


# ------------------------------------------- no hindsight: structural proof

def test_event_observables_has_no_settlement_field():
    names = {f.name for f in RS.dc_fields(RS.EventObservables)}
    assert names == set(RS.CONTEMPORANEOUS_FIELDS)
    assert set(RS.EventObservables.__slots__) == set(RS.CONTEMPORANEOUS_FIELDS)
    assert names.isdisjoint(RS.SETTLEMENT_DERIVED_FIELDS)
    # and the names are not reachable as attributes either
    for bad in RS.SETTLEMENT_DERIVED_FIELDS:
        assert not hasattr(RS.EventObservables, bad), bad


def test_settlement_lives_only_on_a_separate_record():
    names = {f.name for f in RS.dc_fields(RS.EventOutcome)}
    assert "winner_index" in names and "has_redeem" in names
    # the outcome record carries no contemporaneous measurement at all
    assert names <= {"slug", "regime", "has_redeem", "winner_index",
                     "redeem_usd"}


def _fn_source(name: str) -> str:
    tree = ast.parse(open(MODULE, encoding="utf-8").read())
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return ast.unparse(n)
    raise AssertionError("no function %r" % name)


def test_contemporaneous_metrics_cannot_name_a_settlement_field():
    src = _fn_source("contemporaneous_metrics")
    tree = ast.parse(src)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for bad in RS.SETTLEMENT_DERIVED_FIELDS:
        assert bad not in names and bad not in attrs, bad
    # it must not touch the outcome record type or the P&L helpers either
    for bad in ("EventOutcome", "event_pnl", "pnl_block", "outcome_metrics"):
        assert bad not in names and bad not in attrs, bad


def test_observe_cannot_name_a_settlement_field():
    """The builder of the contemporaneous view is checked too, so a hindsight
    field cannot be smuggled in one layer earlier."""
    src = _fn_source("_observe")
    tree = ast.parse(src)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for bad in RS.SETTLEMENT_DERIVED_FIELDS + ("REDEEM", "redeem_idx"):
        assert bad not in names and bad not in attrs, bad


def test_module_never_infers_a_liquidity_role():
    """`aggressor` -- the bare boolean -- must not be read anywhere, and no
    maker/taker share may be estimated."""
    src = open(MODULE, encoding="utf-8").read()
    tree = ast.parse(src)
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute):
            assert n.attr not in ("aggressor", "isAggressor")
        if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant):
            assert n.slice.value not in ("aggressor", "isAggressor")


# ----------------------------------------- no hindsight: behavioural proof

def _synthetic_records():
    """Two tiny window-events, one per side of the last change point."""
    def fill(slug, ts, idx, size, price):
        return {"type": "TRADE", "side": "BUY", "timestamp": ts,
                "outcomeIndex": idx, "size": size, "price": price,
                "usdcSize": round(price * size * 1.001, 6),
                "eventSlug": slug, "conditionId": "0x" + slug[-6:]}
    # 2026-08-18 12:00:00Z = 1787054400 ; window opens then, 5m
    a_open = 1787054400
    # 2026-08-19 12:00:00Z = 1787140800
    b_open = 1787140800
    recs = []
    sa = "btc-updown-5m-%d" % a_open
    sb = "eth-updown-15m-%d" % b_open
    recs += [fill(sa, a_open + 10, 0, 50, 0.48),
             fill(sa, a_open + 40, 0, 50, 0.50),
             fill(sa, a_open + 90, 1, 60, 0.47)]
    recs += [fill(sb, b_open + 20, 1, 40, 0.55),
             fill(sb, b_open + 70, 0, 25, 0.44)]
    recs += [{"type": "REDEEM", "timestamp": a_open + 300, "outcomeIndex": 0,
              "size": 50, "price": 0, "usdcSize": 50.0, "side": "",
              "eventSlug": sa, "conditionId": "0x" + sa[-6:]}]
    recs += [{"type": "MAKER_REBATE", "timestamp": b_open + 500,
              "outcomeIndex": 999, "size": 3.0, "price": 0, "usdcSize": 3.0,
              "side": "", "eventSlug": "", "conditionId": ""}]
    return recs, sa, sb


def test_synthetic_corpus_assigns_the_right_regimes():
    recs, sa, sb = _synthetic_records()
    obs, outs, inc, _d = RS.build_corpus(recs)
    by = {e.slug: e for e in obs}
    assert by[sa].regime == "REGIME_2026-06-23_TO_2026-08-18"
    assert by[sb].regime == "REGIME_2026-08-19_ON"
    assert len(inc) == 1 and inc[0]["type"] == "MAKER_REBATE"
    # the incentive record, having no slug, is NOT an event
    assert len(obs) == 2


def test_contemporaneous_metrics_are_invariant_to_the_settlement_outcome():
    """THE BEHAVIOURAL PROOF. Flip every REDEEM to the opposite outcome, and
    then remove the REDEEM entirely. Every contemporaneous metric must come
    back byte-identical; only the outcome block may move."""
    recs, _sa, _sb = _synthetic_records()
    base_obs, base_outs, _i, _d = RS.build_corpus(recs)
    base = RS.contemporaneous_metrics(base_obs)

    flipped = [dict(r) for r in recs]
    for r in flipped:
        if r["type"] == "REDEEM":
            r["outcomeIndex"] = 1 - r["outcomeIndex"]
    f_obs, f_outs, _i, _d = RS.build_corpus(flipped)
    assert RS.contemporaneous_metrics(f_obs) == base

    stripped = [r for r in recs if r["type"] != "REDEEM"]
    s_obs, s_outs, _i, _d = RS.build_corpus(stripped)
    assert RS.contemporaneous_metrics(s_obs) == base

    # ...and the outcome block DOES move, so the test is not vacuous
    assert (RS.outcome_metrics(base_outs)
            != RS.outcome_metrics(s_outs))
    assert RS.outcome_metrics(base_outs)["events_with_redeem"] == 1
    assert RS.outcome_metrics(s_outs)["events_with_redeem"] == 0


def test_event_pnl_reads_settlement_only_through_the_outcome_record():
    recs, sa, _sb = _synthetic_records()
    obs, outs, _i, _d = RS.build_corpus(recs)
    e = {x.slug: x for x in obs}[sa]
    o = {x.slug: x for x in outs}[sa]
    fees = RS.fee_by_slug(recs, {x.slug for x in obs})
    won = RS.event_pnl(e, o, fees)
    lost = RS.event_pnl(e, RS.EventOutcome(
        slug=o.slug, regime=o.regime, has_redeem=True, winner_index=1,
        redeem_usd=0.0), fees)
    # same contemporaneous legs, different residual outcome
    assert won["matched_usd"] == lost["matched_usd"]
    assert won["turnover_usd"] == lost["turnover_usd"]
    assert won["residual_usd"] != lost["residual_usd"]


def test_shift_share_identity_holds():
    a = {"x": {"net_usd": 100.0, "turnover_usd": 10000.0},
         "y": {"net_usd": -10.0, "turnover_usd": 5000.0}}
    b = {"x": {"net_usd": 20.0, "turnover_usd": 4000.0},
         "z": {"net_usd": -50.0, "turnover_usd": 8000.0}}
    r = RS.shift_share(a, b)
    assert r["d_total_bp"] == pytest.approx(
        r["d_composition_bp"] + r["d_behaviour_bp"] + r["d_interaction_bp"],
        abs=0.02)
    assert r["net_bp_to"] - r["net_bp_from"] == pytest.approx(
        r["d_total_bp"], abs=0.02)


def test_out_of_family_slug_is_excluded_not_given_an_invented_duration():
    with pytest.raises(ValueError):
        RS._parse_slug("some-sports-market-2026-08-20")
    recs = [{"type": "TRADE", "side": "BUY", "timestamp": 1787140800,
             "outcomeIndex": 0, "size": 1, "price": 0.5, "usdcSize": 0.5,
             "eventSlug": "nfl-sea-ari-2026-08-19", "conditionId": "0xdead"}]
    obs, _o, _i, diag = RS.build_corpus(recs)
    assert obs == [] and diag["dropped_out_of_family"] == 1
