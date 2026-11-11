"""The repair must preserve a genuine loss. The first test is the one that
matters: on settlement price alone 1245z looks like a phantom, and erasing it
would have destroyed a real -$1.68."""
from __future__ import annotations

from decimal import Decimal as D

from src import l5_authoritative_ledger_repair as R

NFL = "aec-nfl-bal-atl-2026-10-11"
Z0030 = "cpc-btc-updown-15m-2026-10-12-0030z"
Z1245 = "cpc-btc-updown-15m-2026-10-12-1245z"


# --------------------------------- the loss that must never be erased

def test_1245z_realises_a_genuine_loss_not_a_phantom():
    r = R.market_report(Z1245)
    assert r["ok"]
    assert r["order_ids"] == ["D23N485QAZ89"]
    assert r["net_yes_position"] == "2"
    assert D(r["cash_out_usd"]) == D("1.6800")
    assert D(r["realized_usd"]) == D("-1.6800"), \
        "the attested 2 YES @ 0.8400 settling at 0 is a REAL -$1.68 loss"


def test_the_1245z_correction_books_a_cost_and_never_drops_the_quantity():
    c = [x for x in R.corrections() if x["market"] == Z1245][0]
    assert c["action"] == "BOOK_COST_BASIS_THEN_SETTLE"
    assert c["book_cost_basis_usd"] == "1.6800"
    assert c["recognise_realized_usd"] == "-1.6800"
    assert "DROP" not in c["action"] and "drop" not in c["action"]


def test_a_zero_settlement_price_alone_cannot_retire_a_position():
    """Settlement says what a share PAID; only the order says what it COST."""
    r = R.market_report(Z1245)
    assert D(r["settlement_px"]) == 0
    assert D(r["cash_out_usd"]) > 0, "cost is independent of settlement price"
    assert D(r["realized_usd"]) != 0


# ---------------------------------------------- per-market economics

def test_nfl_nets_flat_and_realises_only_its_trade_margin():
    r = R.market_report(NFL)
    assert r["net_yes_position"] == "0"
    assert r["gross_yes_bought"] == "4" and r["gross_yes_sold"] == "4"
    assert D(r["commission_usd"]) == D("0.0400")
    assert D(r["realized_usd"]) == D("0.0200")


def test_nfl_settlement_cash_is_zero_because_the_position_is_flat():
    r = R.market_report(NFL)
    assert D(r["settlement_cash_usd"]) == 0
    assert D(r["settlement_px"]) == 1


def test_0030z_short_keeps_its_proceeds_when_yes_settles_zero():
    r = R.market_report(Z0030)
    assert r["net_yes_position"] == "-2"
    assert D(r["cash_out_usd"]) == D("-1.7000"), "a short takes cash IN"
    assert D(r["realized_usd"]) == D("1.7000")


def test_0030z_matches_the_venue_s_own_realized_figure():
    """The venue independently reported realized 1.7000 for this market."""
    assert D(R.market_report(Z0030)["realized_usd"]) == D("1.7000")


# ------------------------------------------- the short-side convention

def test_a_no_buy_is_a_sale_of_yes():
    o = {"market": "m", "outcome": R.NO, "qty": D(2), "avg_px": D("0.85"),
         "commission": D(0), "state": "ORDER_STATE_FILLED"}
    assert R._signed_yes(o) == D(-2)
    assert R.execution_cash(o) == D("-1.70")


def test_a_yes_buy_pays_its_price():
    o = {"market": "m", "outcome": R.YES, "qty": D(2), "avg_px": D("0.84"),
         "commission": D(0), "state": "ORDER_STATE_FILLED"}
    assert R._signed_yes(o) == D(2)
    assert R.execution_cash(o) == D("1.68")


# ----------------------------------------------- refusals, not guesses

def test_a_non_terminal_order_disqualifies_the_whole_market():
    bad = [{"market": "m", "outcome": R.YES, "qty": D(1), "avg_px": D("0.5"),
            "commission": D(0), "state": "ORDER_STATE_OPEN", "id": "X"}]
    r = R.market_report("m", orders=bad,
                        settlement={"state": "MARKET_STATE_EXPIRED",
                                    "settlement_px": D(0)})
    assert not r["ok"] and r["refuse"] == "ORDER_NOT_TERMINAL:X"


def test_an_unreadable_order_disqualifies_the_whole_market():
    bad = [{"market": "m", "outcome": R.YES, "qty": None, "avg_px": None,
            "commission": D(0), "state": "ORDER_STATE_FILLED", "id": "Y"}]
    r = R.market_report("m", orders=bad,
                        settlement={"state": "MARKET_STATE_EXPIRED",
                                    "settlement_px": D(0)})
    assert not r["ok"] and r["refuse"] == "ORDER_UNREADABLE:Y"


def test_a_non_terminal_market_is_refused():
    good = [{"market": "m", "outcome": R.YES, "qty": D(1), "avg_px": D("0.5"),
             "commission": D(0), "state": "ORDER_STATE_FILLED", "id": "Z"}]
    r = R.market_report("m", orders=good,
                        settlement={"state": "MARKET_STATE_OPEN",
                                    "settlement_px": D(0)})
    assert not r["ok"] and r["refuse"] == "NOT_TERMINAL"


def test_a_missing_settlement_price_is_refused():
    good = [{"market": "m", "outcome": R.YES, "qty": D(1), "avg_px": D("0.5"),
             "commission": D(0), "state": "ORDER_STATE_FILLED", "id": "Z"}]
    r = R.market_report("m", orders=good,
                        settlement={"state": "MARKET_STATE_EXPIRED",
                                    "settlement_px": None})
    assert not r["ok"] and r["refuse"] == "NO_SETTLEMENT_PX"


def test_a_market_with_no_attested_orders_is_refused():
    r = R.market_report("never-traded")
    assert not r["ok"] and r["refuse"] == "NO_ATTESTED_ORDERS"


# ------------------------------------------------ the whole correction

def test_authoritative_total_and_the_ledger_overstatement():
    rec = R.reconciliation()
    assert D(rec["authoritative_realized_total_usd"]) == D("0.0400")
    assert D(rec["ledger_realized_before_usd"]) == D("1.6800")
    assert D(rec["ledger_overstatement_usd"]) == D("1.6400")


def test_the_overstatement_decomposes_into_exactly_three_unbooked_items():
    """1245z's loss, NFL's margin, and the 0030z rounding gap. Nothing else."""
    loss_1245 = D(R.market_report(Z1245)["realized_usd"])          # -1.6800
    nfl_margin = D(R.market_report(NFL)["realized_usd"])           # +0.0200
    gap_0030 = D(R.market_report(Z0030)["realized_usd"]) - D("1.6800")
    unbooked = loss_1245 + nfl_margin + gap_0030                   # -1.6400
    rec = R.reconciliation()
    assert D(rec["ledger_overstatement_usd"]) == -unbooked == D("1.6400")


def test_every_order_on_these_markets_is_manual():
    """The root cause: the executor never placed them, so it held no cost."""
    assert R.reconciliation()["every_order_manual"] is True


def test_no_correction_erases_a_quantity():
    for c in R.corrections():
        assert c["action"] in ("BOOK_COST_BASIS_THEN_SETTLE", "REFUSE")


def test_committed_capital_to_release_is_reported():
    rec = R.reconciliation()
    assert rec["committed_usd_to_release"][NFL] == "0.8800"


# ------------------------------------- the rebate claim this evidence kills

def test_both_btc15m_fills_carry_exactly_zero_commission():
    """A zero commission is NOT a rebate. The NFL fills show +/-0.01 and
    +0.03; both BTC15m fills show 0.0000, so no maker credit was earned."""
    btc = [o for o in R.ATTESTED_ORDERS if o["market"].startswith("cpc-btc")]
    assert len(btc) == 2
    assert all(D(str(o["commission"])) == 0 for o in btc)
    nfl = [o for o in R.ATTESTED_ORDERS if o["market"] == NFL]
    assert any(D(str(o["commission"])) < 0 for o in nfl), \
        "NFL did earn credits, which is why the rates must not be transferred"
