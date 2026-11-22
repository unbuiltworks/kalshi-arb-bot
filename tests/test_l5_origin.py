"""Origin labels: BTC15M, BTC5M, MANUAL, UNKNOWN.

Owner decision, 2026-11-11: label every position and fill; only
authenticated, reconciled fills establish a cost basis; unknown positions
stay conservatively in account-wide risk; strategy-level realized reporting
must not be a way around the account's loss limits.

What these tests pin:
  * a label is decided from evidence and never guessed;
  * a label changes no number -- the account-wide realized lines move by
    the full settlement amount whatever the label says;
  * the per-origin report always adds up to the plain sum of its rows.
"""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from src import l5_live_execution as E
from src import l5_origin as OG

M15 = "cpc-btc-updown-15m-2026-11-11-1915z"
M15B = "cpc-btc-updown-15m-2026-11-11-1900z"
M5 = "cpc-btc-updown-5m-2026-11-11-1915z"
OTHER = "aec-nfl-bal-atl-2026-11-08"
UPPER = OG.BASIS_UPPER_BOUND


@pytest.fixture(autouse=True)
def no_attestation(monkeypatch):
    monkeypatch.delenv(OG.MANUAL_ATTESTED_ENV, raising=False)
    monkeypatch.delenv(E.FOREIGN_COID_PREFIXES_ENV, raising=False)


@pytest.fixture
def store(tmp_path):
    return E.InventoryStore(str(tmp_path / "inv.json"))


# ---------------------------------------------------------------- orders

@pytest.mark.parametrize("coid,want", [
    ("l5f-" + "0" * 24, "BTC15M"), ("b5m-" + "0" * 24, "BTC5M"),
    ("manual-1", "UNKNOWN"), ("", "UNKNOWN"), (None, "UNKNOWN"),
    ("L5F-abc", "UNKNOWN"), ("xl5f-abc", "UNKNOWN"), (123, "UNKNOWN"),
])
def test_an_orders_origin_is_its_id_prefix_and_is_never_manual(coid, want):
    assert OG.origin_of_coid(coid) == want
    assert OG.origin_of_coid(coid) != OG.MANUAL


def test_the_registry_names_two_strategies_with_distinct_prefixes_and_series():
    assert set(OG.STRATEGY_COID_PREFIX) == set(OG.STRATEGY_SERIES) \
        == {OG.BTC15M, OG.BTC5M}
    a, b = OG.STRATEGY_COID_PREFIX.values()
    assert not a.startswith(b) and not b.startswith(a)
    x, y = OG.STRATEGY_SERIES.values()
    assert not x.startswith(y) and not y.startswith(x)
    assert OG.other_strategy_prefixes(OG.BTC15M) == ("b5m-",)
    assert OG.other_strategy_prefixes(OG.BTC5M) == ("l5f-",)
    assert OG.strategy_of_market(M15) == OG.BTC15M
    assert OG.strategy_of_market(M5) == OG.BTC5M
    assert OG.strategy_of_market(OTHER) is None
    assert OG.strategy_of_market(None) is None


def test_the_sports_controller_is_a_ledger_owner_and_not_a_strategy_series():
    """Owner 2026-11-22. Its markets may be restated from its own order
    ledger; nothing else about a sports market changes: it is in no
    strategy's series (so its uncertainty is never "only another
    strategy's") and it has no client-id prefix (so no order or position is
    ever labelled SPORTS by name)."""
    assert OG.SPORTS in OG.ORIGINS
    assert OG.SPORTS not in OG.STRATEGY_SERIES
    assert OG.SPORTS not in OG.STRATEGY_COID_PREFIX
    assert OG.strategy_of_market(OTHER) is None
    assert OG.ledger_owner_of_market(OTHER) == OG.SPORTS
    assert OG.ledger_owner_of_market(
        "atc-sea-bol-udi-2026-11-22-bol") == OG.SPORTS
    assert OG.ledger_owner_of_market(M15) == OG.BTC15M
    assert OG.ledger_owner_of_market(M5) == OG.BTC5M
    for other in ("cpc-eth-updown-1d-2026-11-22", "xaec-nba", "", None):
        assert OG.ledger_owner_of_market(other) is None
    for seen in (None, "BTC5M", "SPORTS"):
        got = OG.origin_of_position(OTHER, own=OG.BTC15M,
                                    booked_by_own_fills=False,
                                    other_strategy_order_seen=seen)
        assert got["origin"] == OG.UNKNOWN
        assert got["cost_basis"] == OG.BASIS_UPPER_BOUND


def test_the_registry_agrees_with_the_approval_layer():
    from src import l5_campaign_approval as CA
    assert OG.STRATEGY_SERIES[OG.BTC15M] == CA.BTC15M_SERIES
    assert OG.STRATEGY_SERIES[OG.BTC5M] == CA.BTC5M_SERIES
    assert {v: k for k, v in OG.STRATEGY_SERIES.items()} == CA.SERIES_STRATEGY


# ------------------------------------------------------------- positions

def test_only_own_fills_are_an_authenticated_cost():
    got = OG.origin_of_position(M15, own=OG.BTC15M, booked_by_own_fills=True)
    assert got == {"origin": "BTC15M", "cost_basis": OG.BASIS_AUTHENTICATED,
                   "evidence": "BOOKED_FROM_OWN_ORDER_FILLS"}


@pytest.mark.parametrize("market,seen,attested,want", [
    # nothing known: UNKNOWN, whatever series the market is in
    (M15, None, (), "UNKNOWN"),
    (M5, None, (), "UNKNOWN"),
    (OTHER, None, (), "UNKNOWN"),
    # the venue listed the 5m strategy's order in that 5m market
    (M5, "BTC5M", (), "BTC5M"),
    # ... but the same sighting does not label a market outside its series
    (M15, "BTC5M", (), "UNKNOWN"),
    (OTHER, "BTC5M", (), "UNKNOWN"),
    # our own strategy is never "another strategy"
    (M15, "BTC15M", (), "UNKNOWN"),
    # an unregistered sighting proves nothing
    (M5, "MANUAL", (), "UNKNOWN"),
    (M5, "x9", (), "UNKNOWN"),
    # the owner's attestation wins, and is only a label
    (M15, None, (M15,), "MANUAL"),
    (M5, "BTC5M", (M5,), "MANUAL"),
    (M15, None, (M15B,), "UNKNOWN"),
])
def test_a_position_this_ledger_did_not_book_is_labelled_from_evidence_only(
        market, seen, attested, want):
    got = OG.origin_of_position(
        market, own=OG.BTC15M, booked_by_own_fills=False,
        other_strategy_order_seen=seen, attested_manual=attested)
    assert got["origin"] == want
    assert got["cost_basis"] == UPPER          # never a measured cost


@pytest.mark.parametrize("raw,want", [
    (None, ()), ("", ()), (M15, (M15,)),
    (" %s , %s " % (M15, M5), (M15, M5)),
    ("%s,%s" % (M15, M15), (M15,)),
    ("*", ()), ("cpc-*", ()), ("UPPER-case-slug", ()), ("a", ()),
])
def test_the_manual_attestation_is_exact_slugs_only(monkeypatch, raw, want):
    if raw is not None:
        monkeypatch.setenv(OG.MANUAL_ATTESTED_ENV, raw)
    assert OG.manual_attested_markets() == want


# ------------------------------------------------- through the real ledger

def hold(store, market, up="0", dn="0", up_cost="0", dn_cost="0"):
    """Own inventory, as if booked from our own fills."""
    if D(up):
        store.state["positions"]["%s|UP" % market] = up
        store.state.setdefault("leg_cost", {})["%s|UP" % market] = up_cost
    if D(dn):
        store.state["positions"]["%s|DOWN" % market] = dn
        store.state.setdefault("leg_cost", {})["%s|DOWN" % market] = dn_cost
    store.save()


def lines(store):
    return (D(store.state.get("realized_attributed") or 0),
            D(store.state.get("realized_today") or 0))


def test_a_position_adopted_from_the_venue_is_unknown_at_the_bound(store):
    store.adopt_venue_position(M15, {"%s|DOWN" % M15: "5"}, why="test")
    leg = store.position_origins()["legs"]["%s|DOWN" % M15]
    assert (leg["origin"], leg["cost_basis"], leg["evidence"]) == (
        "UNKNOWN", UPPER, "NOT_BOOKED_BY_THIS_LEDGER")
    assert D(leg["qty"]) == D("5") and D(leg["booked_cost_usd"]) == D("5")
    # and it is in account-wide risk at that bound
    risk = store.account_wide_risk_by_market()
    assert risk["ok"] and risk["risk_by_market"][M15] == D("5")


def test_the_owners_attestation_relabels_and_changes_no_number(
        store, monkeypatch):
    """The 19:15Z shape: 5 DOWN the bot never traded, booked at the $1
    bound, settled against it. Attesting it MANUAL moves the result to the
    MANUAL column and leaves every account-wide line exactly where it was."""
    store.adopt_venue_position(M15, {"%s|DOWN" % M15: "5"}, why="test")
    risk_before = store.account_wide_risk_by_market()["total_usd"]
    monkeypatch.setenv(OG.MANUAL_ATTESTED_ENV, M15)
    assert store.position_origins()["legs"]["%s|DOWN" % M15]["origin"] \
        == "MANUAL"
    assert store.account_wide_risk_by_market()["total_usd"] == risk_before
    out = store.retire_settled_market(M15, yes_value=D("1"))   # DOWN lost
    assert D(out["settlement_pnl"]) == D("-5")
    row = store.state["retired_markets"][M15]
    assert row["origin"] == "MANUAL" and row["origin_cost_basis"] == UPPER
    assert row["pnl_provenance"] == "UNKNOWN_COST_UPPER_BOUND"
    # the account-wide lines took the whole amount
    assert lines(store) == (D("-5"), D("-5"))
    rep = store.realized_by_origin()
    assert D(rep["by_origin"]["MANUAL"]["settled_usd"]) == D("-5")
    assert D(rep["by_origin"]["BTC15M"]["settled_usd"]) == D("0")
    assert D(rep["account_wide"]["realized_attributed"]) == D("-5")


def test_without_an_attestation_the_same_result_is_unknown(store):
    store.adopt_venue_position(M15, {"%s|DOWN" % M15: "5"}, why="test")
    store.retire_settled_market(M15, yes_value=D("1"))
    row = store.state["retired_markets"][M15]
    assert row["origin"] == "UNKNOWN" and row["origin_cost_basis"] == UPPER
    assert lines(store) == (D("-5"), D("-5"))


def test_an_own_result_is_btc15m_and_says_whether_it_was_verified(store):
    hold(store, M15B, up="0.30", up_cost="0.141")
    out = store.retire_settled_market(M15B, yes_value=D("0"))
    assert D(out["settlement_pnl"]) == D("-0.141")
    row = store.state["retired_markets"][M15B]
    assert row["origin"] == "BTC15M"
    # booked from own orders; the L5-owner attribution proof is absent here
    assert row["origin_cost_basis"] == OG.BASIS_OWN_UNVERIFIED
    assert row["l5_owner_attribution_verified"] is False


def test_a_5m_result_is_labelled_btc5m_only_when_its_order_was_seen(store):
    store.state["foreign_orders_seen"] = {M5: "BTC5M"}
    store.adopt_venue_position(M5, {"%s|UP" % M5: "0.17"}, why="test")
    store.retire_settled_market(M5, yes_value=D("0"))
    row = store.state["retired_markets"][M5]
    assert row["origin"] == "BTC5M" and row["origin_cost_basis"] == UPPER
    # booked at the bound in the ACCOUNT-WIDE ledger: -0.17, not the -0.0986
    # the 5-minute ledger knows it cost. Conservative, and labelled as such.
    assert D(row["settlement_pnl"]) == D("-0.17")
    assert lines(store) == (D("-0.17"), D("-0.17"))


def test_the_split_always_adds_up_and_never_touches_the_totals(
        store, monkeypatch):
    hold(store, M15B, up="0.30", up_cost="0.141")
    store.retire_settled_market(M15B, yes_value=D("0"))           # -0.141
    store.adopt_venue_position(M15, {"%s|DOWN" % M15: "5"}, why="t")
    store.retire_settled_market(M15, yes_value=D("1"))            # -5
    store.state["foreign_orders_seen"] = {M5: "BTC5M"}
    store.adopt_venue_position(M5, {"%s|UP" % M5: "0.17"}, why="t")
    store.retire_settled_market(M5, yes_value=D("1"))             # 0 at bound
    store.adopt_venue_position(OTHER, {"%s|UP" % OTHER: "2"}, why="t")
    store.retire_settled_market(OTHER, yes_value=D("0"))          # -2
    before = lines(store)
    assert before == (D("-7.141"), D("-7.141"))
    rep = store.realized_by_origin()
    by = {k: D(v["settled_usd"]) for k, v in rep["by_origin"].items()}
    assert by == {"BTC15M": D("-0.141"), "BTC5M": D("0"), "SPORTS": D("0"),
                  "MANUAL": D("0"), "UNKNOWN": D("-7")}
    assert sum(by.values()) == D(rep["sum_of_rows_usd"]) == D("-7.141")
    assert rep["rows"] == 4 and rep["complete"] is True
    assert D(rep["account_wide"]["realized_attributed"]) == D("-7.141")
    # a label written at settlement stays what it was: attesting later does
    # not rewrite history, and nothing in the report changes a total
    monkeypatch.setenv(OG.MANUAL_ATTESTED_ENV, M15)
    again = store.realized_by_origin()
    assert again["by_origin"] == rep["by_origin"]
    assert lines(store) == before
    assert store.state["retired_markets"][M15]["origin"] == "UNKNOWN"


def test_rows_from_before_the_label_existed_are_classified_when_read(store):
    """Every row on the production volume today predates `origin`."""
    store.state["retired_markets"] = {
        M15B: {"settlement_pnl": "-0.1410",
               "l5_owner_attribution_verified": True,
               "unverified_cost_basis_keys": []},
        M15: {"settlement_pnl": "-5.00",
              "l5_owner_attribution_verified": False,
              "unverified_cost_basis_keys": ["%s|DOWN" % M15]},
        OTHER: {"settlement_pnl": "0.0200",
                "l5_owner_attribution_verified": False,
                "unverified_cost_basis_keys": []},
    }
    rep = OG.realized_by_origin(store.state["retired_markets"],
                                own=OG.BTC15M, attested_manual=(M15,))
    assert rep["by_origin"]["BTC15M"] == {
        "settled_usd": "-0.1210", "markets": 2,
        "cost_basis": {OG.BASIS_AUTHENTICATED: 1,
                       OG.BASIS_OWN_UNVERIFIED: 1}}
    assert rep["by_origin"]["MANUAL"] == {
        "settled_usd": "-5.00", "markets": 1, "cost_basis": {UPPER: 1}}
    assert rep["sum_of_rows_usd"] == "-5.1210"


@pytest.mark.parametrize("bad", [
    None, "x", {"settlement_pnl": None}, {"settlement_pnl": "NaN"},
    {"settlement_pnl": True}, {},
])
def test_a_row_that_cannot_be_read_is_counted_never_zeroed(bad):
    rep = OG.realized_by_origin(
        {M15: bad, M15B: {"settlement_pnl": "-1",
                          "l5_owner_attribution_verified": True}},
        own=OG.BTC15M)
    assert rep["unreadable"] == [M15] and rep["complete"] is False
    assert rep["sum_of_rows_usd"] == "-1" and rep["rows"] == 1


def test_the_report_of_nothing_is_empty_and_complete():
    for empty in (None, {}, []):
        rep = OG.realized_by_origin(empty, own=OG.BTC15M)
        assert rep["rows"] == 0 and rep["complete"] is True
        assert rep["sum_of_rows_usd"] == "0"
        assert set(rep["by_origin"]) == set(OG.ORIGINS)


def test_the_module_reads_one_variable_and_writes_nothing():
    import pathlib
    src = pathlib.Path(OG.__file__).read_text()
    for forbidden in ("open(", "urllib", "requests", "socket", "subprocess",
                      "os.environ[", "putenv", "import json"):
        assert forbidden not in src, forbidden
    assert src.count("os.getenv(") == 1
