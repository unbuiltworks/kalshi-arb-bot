"""RN1 pilot settled-P&L meter: venue-reconciled fills, venue resolution,
fees on our side only, and the latched campaign loss stop."""
from decimal import Decimal as D

from src import pmus_pilot_pnl as PP


def mk(yes, no, cy, cn, px=None, fills=None, stage="rn1_open", t=2000.0):
    fills = fills if fills is not None else [yes, no]
    orders = {f"o{i}": {"side": s, "qty": str(q), "filled": str(q),
                        "open": False, "stage": stage, "placed_at": t}
              for i, (s, q) in enumerate(zip(("YES", "NO"), fills)) if q}
    m = {"pos": {"YES": str(yes), "NO": str(no)},
         "cost": {"YES": str(cy), "NO": str(cn)}, "orders": orders}
    if px is not None:
        m["settle_px"] = px
    return m


def trade(slug, qty, fee=None, aggressor=False):
    ex = {"commissionNotionalCollected": {"value": str(fee)}} if fee else {}
    return {"trade": {"marketSlug": slug, "qtyDecimal": str(qty),
                      "isAggressor": aggressor, "state": "TRADE_STATE_NEW",
                      ("aggressorExecution" if aggressor
                       else "passiveExecution"): ex,
                      ("passiveExecution" if aggressor
                       else "aggressorExecution"): {
                          "commissionNotionalCollected": {"value": "9"}}}}


def res(slug):
    return {"positionResolution": {"marketSlug": slug}}


def test_completed_pair_settles_to_its_locked_edge():
    m = {"aec-a": mk(5, 5, "2.00", "2.75", px="1")}    # pair 0.95
    rows = [trade("aec-a", 5), trade("aec-a", 5), res("aec-a")]
    r = PP.meter(m, rows)
    a = r["by_market"]["aec-a"]
    assert a["status"] == "settled"
    assert D(a["pair_edge"]) == D("0.25") and D(a["residual"]) == 0
    assert D(r["settled_net_usd"]) == D("0.25")


def test_residual_leg_loss_and_fees_are_in_net():
    # 5 YES @0.40 paired with 3 NO @0.55; YES loses (px 0)
    m = {"aec-b": mk(5, 3, "2.00", "1.65", px="0")}
    rows = [trade("aec-b", 5, fee="0.02", aggressor=True),
            trade("aec-b", 3), res("aec-b")]
    a = PP.meter(m, rows)["by_market"]["aec-b"]
    assert D(a["gross"]) == D("3") - D("3.65")          # -0.65
    assert D(a["pair_edge"]) == 3 * (1 - D("0.40") - D("0.55"))
    assert D(a["residual"]) == D(a["gross"]) - D(a["pair_edge"])
    assert D(a["fees_charged"]) == D("0.02")           # not the 9 of theirs
    assert D(a["net"]) == D("-0.67")


def test_rebates_reported_separately_never_netted():
    m = {"aec-c": mk(5, 5, "2.00", "2.75", px="1")}
    rows = [trade("aec-c", 5, fee="-0.01"), trade("aec-c", 5), res("aec-c")]
    r = PP.meter(m, rows)
    assert D(r["settled_net_usd"]) == D("0.25")
    assert D(r["settled_rebates_credited_usd"]) == D("0.01")


def test_a_fill_the_book_missed_is_unconfirmed_not_counted():
    """The Gaskan case: the venue traded 10, the book knows 5."""
    m = {"aec-g": mk(5, 0, "1.55", "0", px="1")}
    rows = [trade("aec-g", 5), trade("aec-g", 5), res("aec-g")]
    r = PP.meter(m, rows)
    assert r["by_market"]["aec-g"]["status"] == "fills_unconfirmed"
    assert r["unconfirmed"] == ["aec-g"] and r["settled_markets"] == 0
    assert D(r["settled_net_usd"]) == 0


def test_unresolved_or_unpriced_markets_are_not_realized():
    m = {"aec-o": mk(5, 5, "2", "2.75", px="1"),
         "aec-p": mk(5, 5, "2", "2.75")}
    rows = [trade("aec-o", 5), trade("aec-o", 5),
            trade("aec-p", 5), trade("aec-p", 5), res("aec-p")]
    r = PP.meter(m, rows)
    assert r["by_market"]["aec-o"]["status"] == "open"
    assert r["by_market"]["aec-p"]["status"] == "awaiting_settle_px"
    assert r["settled_markets"] == 0


def test_only_pilot_markets_since_the_start_are_counted():
    m = {"aec-old": mk(5, 5, "2", "2.75", px="1", t=100.0),
         "aec-legacy": mk(5, 5, "2", "2.75", px="1", stage="two_sided_open"),
         "aec-new": mk(5, 5, "2", "2.75", px="1", t=2000.0)}
    assert PP.pilot_slugs(m, since=1000.0) == ["aec-new"]


# --------------------------------------------- engine: campaign loss stop
def test_campaign_loss_stop_latches_and_blocks_openings(tmp_path, monkeypatch):
    from src import pmus_sports_two_sided as M
    from src import pmus_rn1_replica as R
    from tests.test_pmus_sports_two_sided import FakeVenue
    monkeypatch.setattr(R, "ON", True)
    monkeypatch.setattr(M, "TOTAL_CAP", D("20"))
    monkeypatch.setattr(M, "PILOT_LOSS_USD", D("3"))
    monkeypatch.setenv(M.ENABLED_ENV, "YES")
    v = FakeVenue()
    v.bid, v.ask = D("0.48"), D("0.50")
    e = M.Engine(v, M.Book(tmp_path / "s.json"))
    small = {"aec-s": mk(5, 0, "2.00", "0", px="0")}     # -2.00 < limit
    e.pilot_meter([trade("aec-s", 5), res("aec-s")], small)
    assert not e.pilot_loss_stopped()
    lost = {"aec-l": mk(5, 0, "4.00", "0", px="0")}      # -4.00
    e.pilot_meter([trade("aec-l", 5), res("aec-l")], lost)
    assert e.pilot_loss_stopped()
    e._pilot = None                                      # latched in book
    assert e.pilot_loss_stopped()
    assert D(e.b.d["pilot_loss_stop"]["net"]) == D("-4.00")
    e.b.m("aec-x")
    e.step_market("aec-x")
    assert v.orders == {}                                # no openings


def test_loss_stop_off_by_default(tmp_path):
    from src import pmus_sports_two_sided as M
    from tests.test_pmus_sports_two_sided import FakeVenue
    e = M.Engine(FakeVenue(), M.Book(tmp_path / "s.json"))
    assert M.PILOT_LOSS_USD == 0 and not e.pilot_loss_stopped()


# ------------------------------------- fail closed (owner 2026-11-21)
def _engine(tmp_path, monkeypatch, limit="4"):
    from src import pmus_sports_two_sided as M
    from tests.test_pmus_sports_two_sided import FakeVenue
    monkeypatch.setattr(M, "PILOT_LOSS_USD", D(limit))
    return M, M.Engine(FakeVenue(), M.Book(tmp_path / "s.json"))


def test_no_meter_result_yet_blocks_new_openings(tmp_path, monkeypatch):
    M, e = _engine(tmp_path, monkeypatch)
    assert e.pilot_block() == "pilot_meter_unavailable"


def test_meter_error_blocks(tmp_path, monkeypatch):
    M, e = _engine(tmp_path, monkeypatch)
    e.pilot_meter(None, {"aec-z": {"bad": 1}})           # raises inside
    assert e.pilot_block() == "pilot_meter_unavailable"


def test_stale_meter_blocks(tmp_path, monkeypatch):
    M, e = _engine(tmp_path, monkeypatch)
    e.pilot_meter([], {})
    assert e.pilot_block() is None
    e._pilot_at -= M.PILOT_METER_MAX_AGE_S + 1
    assert e.pilot_block() == "pilot_meter_unavailable"


def test_unreconciled_fills_block_instead_of_dropping_out(tmp_path,
                                                           monkeypatch):
    M, e = _engine(tmp_path, monkeypatch)
    m = {"aec-g": mk(5, 0, "1.55", "0", px="1")}
    e.pilot_meter([trade("aec-g", 5), trade("aec-g", 5), res("aec-g")], m)
    assert e.pilot_block() == "pilot_fills_unconfirmed"
    assert not e.b.d.get("pilot_loss_stop")             # lifts once clean
    e.pilot_meter([trade("aec-g", 5), res("aec-g")], m)
    assert e.pilot_block() is None


def test_open_markets_count_at_their_worst_outcome(tmp_path, monkeypatch):
    """Unsettled lone legs: 5 YES @0.85 can lose 4.25 -> already at risk."""
    M, e = _engine(tmp_path, monkeypatch)
    m = {"aec-w": mk(5, 0, "4.25", "0")}
    r = PP.meter(m, [trade("aec-w", 5)])
    assert D(r["open_worst_case_usd"]) == D("-4.25")
    e.pilot_meter([trade("aec-w", 5)], m)
    assert e.pilot_block() == "pilot_loss_at_risk"
    assert not e.b.d.get("pilot_loss_stop")             # not realized: no latch


def test_completed_open_pair_has_no_downside(tmp_path, monkeypatch):
    m = {"aec-p": mk(5, 5, "2.00", "2.75")}
    r = PP.meter(m, [trade("aec-p", 5), trade("aec-p", 5)])
    assert D(r["open_worst_case_usd"]) == D("0.25")


def test_blocked_openings_still_allow_completions(tmp_path, monkeypatch):
    from src import pmus_rn1_replica as R
    M, e = _engine(tmp_path, monkeypatch)
    monkeypatch.setattr(R, "ON", True)
    monkeypatch.setattr(M, "TOTAL_CAP", D("20"))
    monkeypatch.setenv(M.ENABLED_ENV, "YES")
    e.v.bid, e.v.ask = D("0.48"), D("0.50")
    mkt = e.b.m("aec-x")
    mkt["pos"]["YES"], mkt["cost"]["YES"] = D("5"), D("2.20")  # lone 0.44
    assert e.pilot_block() == "pilot_meter_unavailable"
    e.step_market("aec-x")
    sides = {o["side"] for o in e.v.orders.values() if o["open"]}
    assert sides == {"NO"}                              # only the completion


def test_execution_economics_report_tonights_two_pairs():
    """The 2026-11-22 pilot, as booked: two completed pairs, no residual."""
    m = {"aec-cfb-utsa-uab": mk(5, 5, "3.10", "1.75"),       # YES .62, NO .35
         "aec-nba-lac-dal": mk(5, 5, "1.95", "2.85")}        # YES .39, NO .57
    rows = [trade(s, 5) for s in m for _ in (0, 1)]
    r = PP.meter(m, rows)
    ex = r["execution"]
    assert ex["fills"] == 4 and D(ex["contracts"]) == 20
    assert D(ex["spent_usd"]) == D("9.65")
    assert D(ex["completed_pairs"]) == 10
    assert D(ex["avg_pair_cost"]) == D("0.965")
    assert D(ex["locked_pair_edge_usd"]) == D("0.35")
    assert D(ex["residual_cost_usd"]) == 0
    a = r["by_market"]["aec-cfb-utsa-uab"]
    assert D(a["avg_no"]) == D("0.35") and D(a["avg_yes"]) == D("0.62")
    assert a["status"] == "open"                 # not realized yet


def test_execution_economics_report_a_residual_leg():
    m = {"aec-r": mk(7, 2, "3.15", "1.00")}                  # 5 YES unpaired
    r = PP.meter(m, [trade("aec-r", 7), trade("aec-r", 2)])
    a = r["by_market"]["aec-r"]
    assert a["residual_side"] == "YES" and D(a["residual_qty"]) == 5
    assert D(a["residual_cost"]) == D("2.25")
    assert D(r["execution"]["residual_cost_usd"]) == D("2.25")
