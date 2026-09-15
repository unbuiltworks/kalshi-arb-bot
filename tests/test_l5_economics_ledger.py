"""P2 — the economic ledger, and the six ways a shadow number becomes a lie.

This program has measured how often we could have been there. This is the
first module that says whether being there makes money, so the labelling rules
are not stylistic — each one corresponds to a specific way a shadow figure gets
reported as profit it is not:

  a theoretical fill counted as a fill; an unknown verdict counted as either
  outcome; an unsettled position counted as a result; an estimated rebate
  counted as income; a sum over nothing printed as zero; and a matched pair's
  gain used to cover a residual's loss.
"""
from decimal import Decimal as D

import pytest

import analysis.l5.economics.build as B
from src.l5_shadow import ledger as LG

RESOLVED_UP = {"resolved": True, "winning_outcome": "Up",
               "resolution_source": "gamma_outcome_prices"}
RESOLVED_DN = {"resolved": True, "winning_outcome": "Down",
               "resolution_source": "gamma_outcome_prices"}
OPEN = {"resolved": False, "resolution_source": "UNRESOLVED"}


def _fill(outcome, qty, price, *, venue="PMUS", market="m", cohort="BTC15m",
          fee="0", clock="feed", t=1.0, iid="i"):
    return {"venue": venue, "market_key": market, "cohort": cohort,
            "outcome": outcome, "qty": str(qty), "price": str(price),
            "fee": str(fee), "clock": clock, "t": t, "intent_id": iid}


def _one(fills, resolution):
    return LG.settle(list(LG.positions(fills).values())[0], resolution)


# ------------------------------------------------ only evidenced fills enter

def test_only_an_evidenced_fill_becomes_a_position():
    outs = [{"intent_id": "a", "venue": "PMUS", "market_key": "m",
             "desired_outcome": "Up", "quote_price": "0.4", "quote_qty": "10",
             "evidenced_fill": True, "placed_at": 1.0},
            {"intent_id": "b", "venue": "PMUS", "market_key": "m",
             "desired_outcome": "Up", "quote_price": "0.4", "quote_qty": "10",
             "evidenced_fill": False, "placed_at": 2.0},
            {"intent_id": "c", "venue": "PMUS", "market_key": "m",
             "desired_outcome": "Up", "quote_price": "0.4", "quote_qty": "10",
             "evidenced_fill": None, "placed_at": 3.0}]
    f = B.fills_from(outs, [])
    assert len(f) == 1 and f[0]["intent_id"] == "a"


def test_an_unknown_verdict_is_counted_as_neither_outcome():
    """It is a quote we could not read, not a quote that did not fill."""
    outs = [{"venue": "PMUS", "evidenced_fill": None,
             "reason": "prints could not be read"},
            {"venue": "PMUS", "evidenced_fill": False},
            {"venue": "GEMINI", "evidenced_fill": True}]
    c = B.census(outs)
    assert c["by_venue_verdict"] == {"GEMINI:FILLED": 1, "PMUS:NO_FILL": 1,
                                     "PMUS:UNKNOWN": 1}
    assert "not a no-fill" in c["labels"]
    assert c["unknown_reasons"]


def test_a_zero_or_nonsense_quote_is_not_a_fill():
    outs = [{"intent_id": "a", "venue": "PMUS", "market_key": "m",
             "desired_outcome": "Up", "quote_price": "0", "quote_qty": "10",
             "evidenced_fill": True},
            {"intent_id": "b", "venue": "PMUS", "market_key": "m",
             "desired_outcome": "Up", "quote_price": "0.4", "quote_qty": "0",
             "evidenced_fill": True},
            {"intent_id": "c", "venue": "PMUS", "market_key": "m",
             "desired_outcome": "Up", "quote_price": "1", "quote_qty": "10",
             "evidenced_fill": True}]
    assert B.fills_from(outs, []) == []


# --------------------------------------- an unsettled market has no result

def test_an_unresolved_market_has_a_cost_basis_and_no_realized_line():
    r = _one([_fill("Up", 10, "0.40")], OPEN)
    assert r["state"] == "OPEN_UNSETTLED"
    assert r["realized_shadow"] is None
    assert r["cost_basis"] == "4.000000"
    assert "cost, not a result" in r["labels"]


def test_a_missing_resolution_is_unsettled_not_a_loss():
    r = _one([_fill("Up", 10, "0.40")], None)
    assert r["state"] == "OPEN_UNSETTLED" and r["realized_shadow"] is None


def test_a_closed_but_disputed_market_is_still_unsettled():
    r = _one([_fill("Up", 10, "0.40")],
             {"resolved": False, "closed": True, "uma_status": "disputed",
              "resolution_source": "UNRESOLVED"})
    assert r["state"] == "OPEN_UNSETTLED"


# --------------------------------- matched and residual are scored apart

def test_a_matched_pair_pays_a_dollar_whatever_settles():
    up = _one([_fill("Up", 10, "0.45", iid="a"),
               _fill("Down", 10, "0.50", iid="b")], RESOLVED_UP)
    dn = _one([_fill("Up", 10, "0.45", iid="a"),
               _fill("Down", 10, "0.50", iid="b")], RESOLVED_DN)
    assert up["matched_pair_pnl"] == dn["matched_pair_pnl"] == "0.500000"
    assert up["residual_pnl"] == "0.000000"


def test_a_pair_bought_above_a_dollar_loses_whatever_settles():
    r = _one([_fill("Up", 10, "0.55", iid="a"),
              _fill("Down", 10, "0.50", iid="b")], RESOLVED_UP)
    assert r["matched_pair_pnl"] == "-0.500000"


def test_a_winning_residual_is_scored_separately_from_the_pair():
    r = _one([_fill("Up", 20, "0.45", iid="a"),
              _fill("Down", 10, "0.50", iid="b")], RESOLVED_UP)
    assert r["matched_qty"] == "10" and r["residual_qty"] == "10"
    assert r["matched_pair_pnl"] == "0.500000"
    assert r["residual_pnl"] == "5.500000"        # 10 x (1 - 0.45)


def test_a_losing_residual_is_not_hidden_by_a_winning_pair():
    """The whole reason they are separated: a total can be positive while the
    directional leg is bleeding."""
    r = _one([_fill("Up", 20, "0.45", iid="a"),
              _fill("Down", 10, "0.50", iid="b")], RESOLVED_DN)
    assert r["matched_pair_pnl"] == "0.500000"
    assert r["residual_pnl"] == "-4.500000"
    assert r["realized_shadow"] == "-4.000000"


def test_the_residual_side_is_named():
    r = _one([_fill("Up", 20, "0.45", iid="a"),
              _fill("Down", 10, "0.50", iid="b")], RESOLVED_UP)
    assert r["residual_side"] == "Up"


def test_a_flat_position_has_no_residual():
    r = _one([_fill("Up", 10, "0.45", iid="a"),
              _fill("Down", 10, "0.50", iid="b")], RESOLVED_UP)
    assert r["residual_side"] is None and r["residual_qty"] == "0"


# ------------------------------------------ fees are charged, never modelled

def test_fees_are_subtracted_from_the_total_and_reported_apart():
    r = _one([_fill("Up", 10, "0.45", iid="a", fee="0.05"),
              _fill("Down", 10, "0.50", iid="b", fee="0.05")], RESOLVED_UP)
    assert r["fees_charged"] == "0.100000"
    assert r["realized_shadow"] == "0.400000"     # 0.50 pair - 0.10 fees


def test_polymarket_charges_a_maker_nothing_because_it_says_so():
    assert B.maker_fee("PMUS", D("10"), D("0.45")) == D("0")


def test_gemini_charges_a_maker_the_vendored_ceil_cent_fee():
    """0.0175 * 10 * 0.45 * 0.55 = 0.0433 -> ceil cent 0.05."""
    assert B.maker_fee("GEMINI", D("10"), D("0.45")) == D("0.05")


def test_the_fee_is_called_price_first():
    """The order was wrong here first. It is loud rather than silent: at any
    realistic size a swapped call passes the quantity as a price, and the
    vendored guard rejects it."""
    from src.l5_shadow import terms as T
    assert B.maker_fee("GEMINI", D("10"), D("0.45")) == \
        D(str(T.gemini_terms().maker_fee(D("0.45"), D("10"))))
    with pytest.raises(ValueError, match="invalid Gemini fee inputs"):
        T.gemini_terms().maker_fee(D("10"), D("0.45"))


def test_below_one_share_the_ceil_cent_floor_makes_both_orders_a_penny():
    """So the swap is harmless exactly where the guard cannot catch it --
    stated because the alternative reading (a silent wrong number) is the one
    this docstring originally claimed, and it is not true."""
    from src.l5_shadow import terms as T
    t = T.gemini_terms()
    assert t.maker_fee(D("0.5"), D("0.8")) == t.maker_fee(D("0.8"), D("0.5")) \
        == D("0.01")


def test_a_venue_with_no_declared_maker_fee_is_refused_not_zeroed(monkeypatch):
    from src.l5_shadow import terms as T

    class _T:
        maker_fee = None

    monkeypatch.setattr(T, "gemini_terms", lambda: _T())
    with pytest.raises(ValueError, match="undeclared"):
        B.maker_fee("GEMINI", D("10"), D("0.45"))


def test_a_negative_fee_is_refused_rather_than_booked_as_income(monkeypatch):
    from src.l5_shadow import terms as T

    class _T:
        maker_fee = staticmethod(lambda p, q: D("-1"))

    monkeypatch.setattr(T, "gemini_terms", lambda: _T())
    with pytest.raises(ValueError, match="negative"):
        B.maker_fee("GEMINI", D("10"), D("0.45"))


# ------------------------------- incentives are credited, never estimated

def test_rebates_and_rewards_are_zero_by_fact_not_by_estimate():
    r = _one([_fill("Up", 10, "0.45", iid="a"),
              _fill("Down", 10, "0.50", iid="b")], RESOLVED_UP)
    assert r["maker_rebates_credited"] == "0"
    assert r["rewards_credited"] == "0"
    assert "credited nothing" in r["incentives_note"]
    # and they are NOT folded into the trading result
    assert r["realized_shadow"] == r["matched_pair_pnl"]


def test_the_label_is_realized_shadow_and_says_no_cash_moved():
    r = _one([_fill("Up", 10, "0.45", iid="a"),
              _fill("Down", 10, "0.50", iid="b")], RESOLVED_UP)
    assert "realized_shadow" in r
    assert "realized" not in {k for k in r if k != "realized_shadow"}
    assert "no cash moved" in r["labels"]
    assert "never be reported as realized profit" in r["labels"]


# -------------------------------------------- a sum over nothing is not zero

def test_a_group_with_nothing_settled_reports_none_not_zero():
    rows = [_one([_fill("Up", 10, "0.40", venue="GEMINI")], OPEN)]
    rows[0]["venue"] = "GEMINI"
    g = LG.roll_up(rows, by="venue")["GEMINI"]
    assert g["realized_shadow"] is None and g["settled"] == 0
    assert g["open_cost_basis"] == "4.000000"
    assert "nothing has settled" in g["note"] or "no settled position" in g["note"]


def test_settled_and_open_are_never_summed_together():
    rows = [_one([_fill("Up", 10, "0.45", iid="a"),
                  _fill("Down", 10, "0.50", iid="b")], RESOLVED_UP),
            _one([_fill("Up", 10, "0.40", market="m2")], OPEN)]
    rows[1]["market_key"] = "m2"
    g = LG.roll_up(rows, by="venue")["PMUS"]
    assert g["settled"] == 1 and g["open"] == 1
    assert g["realized_shadow"] == "0.500000"      # the open one contributes 0
    assert g["open_cost_basis"] == "4.000000"


# -------------------------------------- positions are per market, per clock

def test_two_markets_are_two_positions_not_one_matched_pair():
    """Up in one window and Down in another is not a matched pair; treating it
    as one would invent a riskless position out of two directional bets."""
    p = LG.positions([_fill("Up", 10, "0.45", market="m1"),
                      _fill("Down", 10, "0.50", market="m2")])
    assert len(p) == 2


def test_two_venues_are_two_positions():
    p = LG.positions([_fill("Up", 10, "0.45", venue="PMUS"),
                      _fill("Down", 10, "0.50", venue="GEMINI")])
    assert len(p) == 2


def test_a_position_built_under_two_clocks_is_marked_mixed():
    """A feed-clock fill and a chain-clock fill are not the same experiment."""
    p = LG.positions([_fill("Up", 10, "0.45", clock="feed"),
                      _fill("Down", 10, "0.50", clock="chain")])
    assert list(p.values())[0]["clock"] == "MIXED"


def test_the_report_splits_by_venue_cohort_and_clock(tmp_path):
    import json
    (tmp_path / "maker_outcomes.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"intent_id": "a", "venue": "PMUS", "market_key": "m1",
         "desired_outcome": "Up", "quote_price": "0.45", "quote_qty": "10",
         "evidenced_fill": True, "placed_at": 1.0},
        {"intent_id": "b", "venue": "GEMINI", "market_key": "m2",
         "desired_outcome": "Up", "quote_price": "0.40", "quote_qty": "10",
         "evidenced_fill": True, "placed_at": 2.0}]) + "\n")
    (tmp_path / "routes.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"intent_id": "a", "cohort": "BTC15m"},
        {"intent_id": "b", "cohort": "BTC5m"}]) + "\n")
    rep = B.build(str(tmp_path), resolver=lambda k: OPEN, now=1.0)
    assert set(rep["by_venue"]) == {"PMUS", "GEMINI"}
    assert set(rep["by_cohort"]) == {"BTC15m", "BTC5m"}
    assert set(rep["by_clock"]) == {"feed"}
    assert rep["evidenced_fills"] == 2 and rep["positions"] == 2


def test_the_ledger_is_append_only(tmp_path):
    import json
    (tmp_path / "maker_outcomes.jsonl").write_text("")
    B.write(str(tmp_path), {"computed_at": 1.0, "positions": 0})
    B.write(str(tmp_path), {"computed_at": 2.0, "positions": 1})
    rows = [json.loads(l) for l in
            (tmp_path / B.LEDGER).read_text().splitlines() if l.strip()]
    assert [r["computed_at"] for r in rows] == [1.0, 2.0]


def test_an_unreadable_resolution_leaves_the_position_open(tmp_path):
    import json

    def boom(_k):
        raise RuntimeError("gamma down")

    (tmp_path / "maker_outcomes.jsonl").write_text(json.dumps(
        {"intent_id": "a", "venue": "PMUS", "market_key": "m1",
         "desired_outcome": "Up", "quote_price": "0.45", "quote_qty": "10",
         "evidenced_fill": True, "placed_at": 1.0}) + "\n")
    rep = B.build(str(tmp_path), resolver=boom, now=1.0)
    assert rep["rows"][0]["state"] == "OPEN_UNSETTLED"
