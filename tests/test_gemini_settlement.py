"""The Gemini lane's binding constraint, and a gate that has to be able to fail.

A gate nobody can fail authorizes everything. Each requirement is knocked out
on its own here, because the failure being guarded against -- a position
called realized on the strength of a last print, a model, or a balance change
nobody attributed -- needs only one of them to go unnoticed.
"""
import pytest

from src import gemini_settlement as G


def _legs():
    return [G.Leg("BUY", "1", "0.40", "0.00", source="/v1/mytrades", trade_id="a"),
            G.Leg("SELL", "1", "0.55", "0.00", source="/v1/mytrades",
                  trade_id="b")]


def _full(**over):
    s = G.Settlement("GEMI-BTC05M2609172155-UP", legs=_legs(),
                     settlement_value="1", settlement_source="/v1/transfers",
                     cash_delta_observed="0.15",
                     cash_delta_source="/v1/balances")
    for k, v in over.items():
        setattr(s, k, v)
    return s


def test_nothing_evidenced_blocks_on_everything():
    s = G.Settlement("GEMI-X")
    assert s.missing() == list(G.REQUIREMENTS)
    assert s.report()["gate"] == "BLOCKED"
    with pytest.raises(G.NotSettled):
        s.realized()


def test_a_fully_evidenced_instrument_opens_and_reports_realized():
    s = _full()
    r = s.report()
    assert r["gate"] == "OPEN" and r["missing"] == []
    assert r["realized"] == "0.15"


# ------------------------------------------- each requirement blocks alone

def test_a_shadow_fill_is_not_an_authoritative_fill():
    """VOLUME_AHEAD_CLEARED is our model of a queue we cannot see."""
    s = _full(legs=[G.Leg("BUY", "1", "0.40", "0.00", source="SHADOW")])
    assert G.AUTHORITATIVE_FILLS in s.missing()
    with pytest.raises(G.NotSettled):
        s.realized()


def test_a_settlement_value_without_a_source_does_not_count():
    s = _full(settlement_source=None)
    assert G.SETTLEMENT_VALUE in s.missing()


def test_cash_without_attribution_is_not_confirmation():
    """A balance change with insufficient source identity is not attribution."""
    s = _full(cash_delta_source=None)
    assert G.CASH_CONFIRMED in s.missing()
    with pytest.raises(G.NotSettled):
        s.realized()


def test_an_inferred_settlement_never_opens_the_gate():
    """BTC against the strike may be right every time and is still a model."""
    s = _full(settlement_value=None, settlement_source=None,
              inferred_settlement_value="1")
    r = s.report()
    assert r["gate"] == "BLOCKED"
    assert G.SETTLEMENT_VALUE in r["missing"]
    assert r["inferred_settlement_value"] == "1"
    with pytest.raises(G.NotSettled):
        s.realized()


# ------------------------------------------------- realized is a venue fact

def test_realized_is_the_venues_cash_not_our_arithmetic():
    """When they disagree the venue is right, and the delta is reported rather
    than reconciled away."""
    s = _full(cash_delta_observed="0.11")
    r = s.report()
    assert r["realized"] == "0.11"
    assert r["reconstructed_cash"] == "0.15"
    assert r["reconstruction_agrees"] is False
    assert r["reconstruction_delta"] == "-0.04"


def test_reconstruction_agrees_when_it_should():
    r = _full().report()
    assert r["reconstruction_agrees"] is True
    assert r["reconstruction_delta"] == "0.00"


def test_fees_are_a_line_not_a_price_adjustment():
    s = _full(legs=[G.Leg("BUY", "1", "0.40", "0.02", source="/v1/mytrades"),
                    G.Leg("SELL", "1", "0.55", "0.03", source="/v1/mytrades")])
    r = s.report()
    assert r["fees"] == "0.05"
    assert r["fills_cash"] == "0.15"       # fees NOT folded into the cash
    assert r["reconstructed_cash"] == "0.10"


def test_position_is_net_of_authoritative_legs_only():
    s = G.Settlement("GEMI-X", legs=[
        G.Leg("BUY", "3", "0.40", "0", source="/v1/mytrades"),
        G.Leg("SELL", "1", "0.55", "0", source="/v1/mytrades")])
    assert s.position() == 2


def test_rebates_and_rewards_stay_out_of_trading_pnl():
    assert set(G.SEPARATE_LEDGERS) == {"maker_rebate", "taker_rebate",
                                       "liquidity_reward"}
    assert "separate ledgers" in " ".join(_full().report()["labels"])


# --------------------------------------------------- the public-data finding

def test_the_public_surface_is_reported_as_unable_not_as_unchecked():
    """'Cannot' and 'has not yet' decide whether anyone keeps looking."""
    v = G.public_surface_verdict()
    assert v["verdict"] == "PUBLIC_SURFACE_CANNOT_ESTABLISH_SETTLEMENT"
    assert len(v["evidence"]) == 4
    assert all(e["book"] == "0 bids / 0 asks" for e in v["evidence"])
    # the one that makes the point: a 0.97 last print is not a settlement
    assert any(e["last_price"] == "0.97" for e in v["evidence"])


def test_structure_proven_is_not_reported_as_the_path_proven():
    v = G.public_surface_verdict()
    assert v["status"] == "NOT_EXERCISED"
    assert "not reported as PASS" in v["not_exercised_why"]


def test_the_venue_terms_are_measured_not_assumed():
    t = G.venue_terms()
    assert t["min_order_size"] == "0.01"     # 100x smaller than the shadow UNIT
    assert t["status"] == "limit_only"
    assert "100x the venue minimum" in " ".join(t["notes"])


# ------------------------------------------------------------- the gate call

def test_the_gate_blocks_if_any_instrument_blocks():
    g = G.gate([_full(), G.Settlement("GEMI-Y")])
    assert g["gate"] == "BLOCKED" and g["n_blocked"] == 1
    assert G.SETTLEMENT_VALUE in g["missing_union"]


def test_an_empty_gate_is_blocked_not_open():
    """Zero instruments is not evidence that accounting works."""
    assert G.gate([])["gate"] == "BLOCKED"


def test_an_open_gate_authorizes_nothing():
    g = G.gate([_full()])
    assert g["gate"] == "OPEN"
    assert "nothing on its own" in g["authorizes"]
    assert "separate human decision" in g["authorizes"]


def test_the_module_holds_no_credential_and_places_no_order():
    import ast
    import inspect
    src = inspect.getsource(G)
    tree = ast.parse(src)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for forbidden in ("private_rest_request", "credentials", "place_limit_buy",
                      "getenv", "environ", "requests", "urlopen"):
        assert forbidden not in names, "%s is reachable from the gate" % forbidden
