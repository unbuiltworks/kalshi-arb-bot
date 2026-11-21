"""The episode ledger must never book a penny it has not been paid.

OWNER 2026-09-11, on the pair the engine had just filled: "currently filled
+ paired, not realized. Its 0.9900 cost implies roughly $0.056 gross pair
economics before actual fees/incentives, but we shouldn't book a penny yet."

That sentence is the whole specification for this module's accounting, and
these tests are it in executable form:

  * an episode that never filled has NO realized figure -- not 0.00;
  * a paired-but-unsettled episode reports a DERIVED pre-fee figure, and
    realized stays absent;
  * a derived figure is refused outright when venue and ledger quantities
    do not reconcile, because a fill the ledger missed makes a directional
    position look flat (the measured case: a market settled with ledger
    residual 0.00 and a locked +0.3318 while the venue's realized was
    -1.5778);
  * "no incentive evidence" and "the venue credited nothing" never collapse
    into the same value.

And the reason the table exists at all: the settled-cohort builders are
keyed on settlement, so an episode that never filled is invisible to every
one of them. On 2026-09-11 the engine opened three markets in seven minutes
and one filled both legs. A table that sees only what settles reports a
100% pair rate on that.
"""
from __future__ import annotations

import json

import pytest

from src import l5_episode_ledger as E


def _dump(tmp_path, rows):
    """A Railway log dump in the shape the analysis layer reads."""
    p = tmp_path / "dump.json"
    p.write_text(json.dumps({"deploy": [
        {"timestamp": ts, "message": "PMUS_L5_CAMPAIGN " + json.dumps(o)}
        for ts, o in rows]}))
    return str(p)


SLUG = "aec-itfwo-kaycro-sofjoh-2026-09-11"


def _start(slug=SLUG, rank=1):
    return {"kind": "session_start", "slug": slug, "rank": rank,
            "state": "p45-55c|tte>10m|flat|open|q<5", "state_n": 5,
            "p_buy": 0.9434,
            "probe_entry_action": {"relation": "open", "side": "NO",
                                   "target_price": "0.41"}}


def _end_matched(slug=SLUG):
    """The live 12:04:07Z row, trimmed to the fields this module reads."""
    return {"kind": "session_end", "slug": slug, "status": "SPRING_MATCHED",
            "inventory": {"up": "5.6", "dn": "5.6", "matched": "5.6",
                          "residual": "0.0", "avg_up": "0.4200",
                          "avg_dn": "0.5700", "spent": "5.54400"},
            "orders": [
                {"stage": "spring_open_3", "side": "YES", "qty": "5.60",
                 "filled_qty": "0", "order_id": "CE4JWVMZTTN0"},
                {"stage": "spring_open_4", "side": "YES", "qty": "5.60",
                 "filled_qty": "5.6", "order_id": "CE4K0JQEETN3"},
                {"stage": "spring_complete_5", "side": "NO", "qty": "5.6",
                 "filled_qty": "5.6", "order_id": "CE4K0S6A2TN5"}],
            "decisions": [{"state": "p45-55c|tte>10m|flat|open|q<5",
                           "v2_recommended": "open", "acted": "open",
                           "refusal": None}]}


def _end_nofill(slug="aec-cs2-wc-nem-2026-09-11"):
    return {"kind": "session_end", "slug": slug, "status": "SPRING_NO_FILL",
            "inventory": {"up": "0", "dn": "0", "matched": "0",
                          "residual": "0", "avg_up": None, "avg_dn": None},
            "orders": [{"stage": "spring_open_1", "side": "NO", "qty": "4.7",
                        "filled_qty": "0", "order_id": "X1"}],
            "decisions": []}


# ------------------------------------------------- absent is not zero
def test_a_no_fill_episode_has_no_realized_figure(tmp_path):
    res = E.build([_dump(tmp_path, [
        ("t1", _start("aec-cs2-wc-nem-2026-09-11")), ("t2", _end_nofill())])])
    ep, = res["episodes"]
    assert ep["stage"] == "NO_FILL"
    assert ep["realized_net_usd"] is None, "never 0.00"
    assert ep["pair_gross_usd"] is None
    assert ep["matched_qty"] is None and ep["residual_units"] is None
    # but what DID happen is recorded
    assert ep["submitted"]["orders"] == 1
    assert ep["fills"]["filled_orders"] == 0
    assert ep["fills"]["filled_qty"] == "0"


def test_a_paired_but_unsettled_episode_books_nothing_realized(tmp_path):
    res = E.build([_dump(tmp_path, [("t1", _start()), ("t2", _end_matched())])])
    ep, = res["episodes"]
    assert ep["stage"] == "PAIRED"
    assert ep["realized_net_usd"] is None, (
        "OWNER: filled + paired, not realized -- we shouldn't book a penny")
    assert ep["settlement"] is None


def test_the_derived_pair_figure_matches_the_owners_arithmetic(tmp_path):
    """0.4200 + 0.5700 = 0.9900 on 5.6 matched -> 5.6 x 0.01 = 0.0560."""
    res = E.build([_dump(tmp_path, [("t1", _start()), ("t2", _end_matched())])])
    ep, = res["episodes"]
    assert ep["effective_pair_cost"] == "0.990000"
    pg = ep["pair_gross_usd"]
    assert pg["value_usd"] == "0.0560"
    assert pg["derived"] is True
    assert "PRE-FEE" in pg["labels"] and "never" in pg["labels"]
    assert pg["qty_reconciliation"]["reconciles"] is None, (
        "unsettled: there is no venue quantity to reconcile against yet")


def test_a_settled_market_carries_the_venues_number_not_ours(tmp_path):
    settle = {"kind": "settlement", "slug": SLUG, "status": "REALIZED",
              "realized_usd": "-1.5778", "realized_confirmed": True,
              "venue_qty_total": "11.2000", "ledger_qty_total": "11.2",
              "breakdown": {"matched_pairs": "5.6", "maker_fills": 2,
                            "taker_fills": 0, "fees_usd": "venue_unreported"}}
    res = E.build([_dump(tmp_path, [
        ("t1", _start()), ("t2", _end_matched()), ("t3", settle)])])
    ep, = res["episodes"]
    assert ep["stage"] == "REALIZED"
    assert ep["realized_net_usd"] == "-1.5778", "the venue's, not ours"
    assert ep["maker_taker"] == {"maker_fills": 2, "taker_fills": 0}
    assert ep["settlement"]["fees_usd"] == "venue_unreported"


def test_a_derived_figure_is_refused_when_quantities_disagree(tmp_path):
    """The measured failure: ledger residual 0.00, locked +0.3318, venue
    realized -1.5778, because the ledger missed a 4.34-unit partial."""
    settle = {"kind": "settlement", "slug": SLUG, "status": "SETTLED",
              "venue_qty_total": "13.82", "ledger_qty_total": "9.48",
              "breakdown": {"matched_pairs": "5.6"}}
    res = E.build([_dump(tmp_path, [
        ("t1", _start()), ("t2", _end_matched()), ("t3", settle)])])
    ep, = res["episodes"]
    pg = ep["pair_gross_usd"]
    assert pg["value_usd"] is None
    assert "reconcile" in pg["refused"]


# ------------------------------------------------- what a pair cost IS
def test_avg_up_plus_avg_dn_is_refused_on_a_residual_position():
    """MEASURED FAILURE. The first version summed the averages
    unconditionally and reported a pair cost of 1.110705 on a market
    carrying a 10.35-unit residual -- a pair bought above par, which never
    happened. On unequal quantities the averages are not a price anything
    was paid, so this refuses rather than misleads."""
    inv = {"avg_up": "0.7121", "avg_dn": "0.3986", "residual": "10.35",
           "matched": "4.11"}
    assert E._effective_pair_cost(inv) is None


def test_the_guard_is_source_independent():
    """The breakdown's own matched_pair_cost is built as avg_up + avg_dn
    too, so accepting it on a residual position launders the same artifact
    through a more authoritative-looking field. Seventeen markets reported
    a pair cost above 1.00 that way before this guard moved."""
    inv = {"avg_up": "0.7121", "avg_dn": "0.3986", "residual": "10.35"}
    assert E._effective_pair_cost(
        inv, {"matched_pair_cost": "1.110705"}) is None


def test_the_breakdown_is_preferred_on_a_clean_pair():
    inv = {"avg_up": "0.42", "avg_dn": "0.57", "residual": "0"}
    assert E._effective_pair_cost(
        inv, {"matched_pair_cost": "0.9612"}) == "0.961200"


def test_the_averages_are_still_used_when_the_pair_is_clean():
    inv = {"avg_up": "0.4200", "avg_dn": "0.5700", "residual": "0.0"}
    assert E._effective_pair_cost(inv) == "0.990000"


# --------------------------------------- the view only this table gives
def test_the_no_fill_episodes_are_the_denominator(tmp_path):
    """One market filled and paired; two submitted and did not fill. A
    settlement-keyed table sees one episode and reports a 100% pair rate."""
    res = E.build([_dump(tmp_path, [
        ("t1", _start()), ("t2", _end_matched()),
        ("t3", _start("aec-cs2-wc-nem-2026-09-11")), ("t4", _end_nofill()),
        ("t5", _start("aec-itfme-rustiu-dmipop-2026-09-11")),
        ("t6", _end_nofill("aec-itfme-rustiu-dmipop-2026-09-11"))])])
    c = res["counts"]
    assert c["episodes"] == 3
    assert c["submitted_episodes"] == 3
    assert c["filled_episodes"] == 1
    assert c["paired_to_zero_residual"] == 1
    assert c["fill_rate_of_submitted"] == pytest.approx(1 / 3, abs=1e-4)
    assert c["pair_rate_of_filled"] == 1.0
    assert c["by_stage"] == {"PAIRED": 1, "NO_FILL": 2}


def test_a_residual_episode_is_distinguished_from_a_paired_one(tmp_path):
    end = {"kind": "session_end", "slug": "aec-county-not-ham-2026-09-08",
           "status": "SPRING_RESIDUAL_HOLD",
           "inventory": {"up": "16.67", "dn": "28.34", "matched": "16.67",
                         "residual": "-11.67", "avg_up": "0.7121",
                         "avg_dn": "0.1885"},
           "orders": [{"stage": "spring_open_1", "qty": "5", "side": "NO",
                       "filled_qty": "5", "order_id": "A"}],
           "decisions": []}
    res = E.build([_dump(tmp_path, [("t1", end)])])
    ep, = res["episodes"]
    assert ep["stage"] == "RESIDUAL_HELD"
    assert ep["residual_units"] == "-11.67"
    assert ep["pair_gross_usd"] is None, (
        "a residual position is not locked and has no derived value")
    assert ep["realized_net_usd"] is None


# ------------------------------------------------------- the incentive
def test_no_incentive_evidence_is_not_a_zero_credit(tmp_path):
    res = E.build([_dump(tmp_path, [("t1", _start()), ("t2", _end_matched())])])
    ep, = res["episodes"]
    assert ep["venue_incentive_usd"] is None, (
        "no evidence supplied at all -> the field is absent")

    res = E.build([_dump(tmp_path, [("t1", _start()), ("t2", _end_matched())])],
                  credited={"some-other-market": []})
    ep, = res["episodes"]
    inc = ep["venue_incentive_usd"]
    assert inc["credited_usd"] is None
    assert "no incentive evidence for this market" in inc["note"]


def test_a_credited_rebate_uses_the_venues_sign_convention(tmp_path):
    """The venue writes a maker rebate as a NEGATIVE commission on our own
    execution. Reported here as a positive credit."""
    res = E.build([_dump(tmp_path, [("t1", _start()), ("t2", _end_matched())])],
                  credited={SLUG: [
                      {"our_commission": "-0.013", "credited_to_us": True},
                      {"our_commission": "-0.013", "credited_to_us": True}]})
    inc = res["episodes"][0]["venue_incentive_usd"]
    assert inc["credited_usd"] == "0.026000"
    assert inc["fills_with_a_credit"] == 2


# --------------------------------------------- the rule, enforced at build
def test_stage_discipline_is_enforced_not_merely_documented():
    """Every field is placed in the stage table, and populating one early
    raises at BUILD time rather than producing a plausible wrong row."""
    row = {"slug": "x", "stage": "NO_FILL", "realized_net_usd": "0.00"}
    with pytest.raises(AssertionError) as e:
        E._assert_stage_discipline(row)
    assert "realized_net_usd" in str(e.value)


def test_every_reported_field_is_placed_in_the_stage_table(tmp_path):
    """Adding a field to the row without deciding which stage it belongs to
    must fail here, not silently escape the discipline."""
    res = E.build([_dump(tmp_path, [("t1", _start()), ("t2", _end_matched())])])
    ep = res["episodes"][0]
    bookkeeping = {"slug", "stage", "status"}
    unplaced = set(ep) - bookkeeping - set(E.STAGE_OF_FIELD)
    assert not unplaced, f"fields with no stage: {sorted(unplaced)}"


def test_the_module_delegates_settled_economics_rather_than_restating(tmp_path):
    """l5_r5f_cohort owns the reconciliation guard. A second copy here
    would be the duplication this project keeps paying for."""
    import inspect
    src = inspect.getsource(E)
    assert "cohort.qty_reconciliation" in src
    assert "QTY_RECONCILE_TOL" not in src, "the tolerance has one owner"


def test_rendering_shows_absence_as_a_dash_not_a_zero(tmp_path):
    res = E.build([_dump(tmp_path, [
        ("t1", _start("aec-cs2-wc-nem-2026-09-11")), ("t2", _end_nofill())])])
    md = E.render_markdown(res)
    assert "0.00" not in md
    assert "—" in md
    assert "not zero" in md
