"""L5 redemption / settlement lifecycle audit.

WHAT THESE PIN. The audit exists to stop one specific error -- reading a
redemption as profit -- so the tests are about the accounting identity and
the deduplication that makes it correct, not about plumbing.
"""
import gzip
import json

import pytest

from analysis.l5.redemption import audit as A


def _row(**kw):
    base = {"timestamp": 1000, "type": "TRADE", "conditionId": "0xc1",
            "transactionHash": "0xt1", "outcome": "Up", "side": "BUY",
            "size": "10", "usdcSize": "4.0", "slug": "btc-updown-5m-1000"}
    base.update(kw)
    return base


def _cache(tmp_path, name, rows, gz=False):
    p = tmp_path / name
    payload = json.dumps({"payload": rows}) + "\n"
    if gz:
        with gzip.open(p, "wt") as fh:
            fh.write(payload)
    else:
        p.write_text(payload)
    return (p, gzip.open if gz else open)


# ------------------------------------------------------------ deduplication

def test_duplicate_rows_across_caches_are_counted_once():
    """Cross-response duplicates are the SAME fill harvested twice: the two
    caches overlap by 3,149 TRADE rows."""
    r = _row()
    assert A.row_key(r) == A.row_key(dict(r))


def test_repeats_WITHIN_one_response_are_distinct_fills_not_duplicates():
    """THE CORRECTION THIS PINS. A global set is WRONG here: inside a single
    cached response two rows sharing every field are two genuinely distinct
    fills -- a maker order filling in equal parts at one instant and price.
    Collapsing them dropped 205 real fills, understated acquisition by
    $2,740.62 and overstated net trading cash by exactly that."""
    r = _row()
    import tempfile, pathlib as _p
    d = _p.Path(tempfile.mkdtemp())
    src = _cache(d, "a.jsonl", [dict(r), dict(r), dict(r)])
    got = list(A.iter_activity([src]))
    assert len(got) == 3                       # NOT collapsed to 1


def test_multiplicity_across_responses_is_the_max_not_the_sum():
    """The same response re-harvested must not double the fills."""
    import tempfile, pathlib as _p
    r = _row()
    d = _p.Path(tempfile.mkdtemp())
    a = _cache(d, "a.jsonl", [dict(r), dict(r)])
    b = _cache(d, "b.jsonl", [dict(r), dict(r)])
    assert len(list(A.iter_activity([a, b]))) == 2      # max(2,2), not 4


def test_row_key_matches_the_canonical_ledger_key():
    """Both accounts must dedup the same way or they cannot be reconciled."""
    import inspect
    from src.polymarket_l5 import ledger as L
    doc = inspect.getdoc(L) or ""
    assert "multiset union" in doc.lower()
    k = A.row_key(_row())
    assert len(k) == 8                       # type,tx,market,outcome,side,size,price,ts


def test_row_key_separates_genuinely_different_fills():
    """Dedup must not collapse two real fills that merely share a timestamp."""
    a, b = _row(), _row(size="11", usdcSize="4.4")
    assert A.row_key(a) != A.row_key(b)
    c = _row(outcome="Down")
    assert A.row_key(a) != A.row_key(c)


def test_iter_activity_dedupes_across_two_sources(tmp_path):
    shared = _row(transactionHash="0xshared")
    s1 = _cache(tmp_path, "a.jsonl", [shared, _row(transactionHash="0xa")])
    s2 = _cache(tmp_path, "b.jsonl.gz", [shared, _row(transactionHash="0xb")],
                gz=True)
    got = list(A.iter_activity([s1, s2]))
    assert len(got) == 3                      # not 4
    assert {g["transactionHash"] for g in got} == {"0xshared", "0xa", "0xb"}


# -------------------------------------------------------------- the identity

def test_the_accounting_identity_holds(tmp_path):
    """acquisition + sales + redemption = net trading cash, and a redemption
    is NEVER income."""
    src = _cache(tmp_path, "a.jsonl", [
        _row(usdcSize="40.0", size="100"),                      # buy  -40
        _row(transactionHash="0xs", side="SELL", usdcSize="5.0", size="10"),
        _row(transactionHash="0xr", type="REDEEM", side="", size="90",
             usdcSize="90.0"),                                  # redeem +90
        _row(transactionHash="0xi", type="MAKER_REBATE", side="",
             usdcSize="1.5", size="0"),
    ])
    out = A.audit([src])
    i = out["identity"]
    assert i["acquisition_cashflow_usd"] == -40.0
    assert i["trading_sales_usd"] == 5.0
    assert i["settlement_redemption_cashflow_usd"] == 90.0
    assert i["net_trading_cash_usd"] == 55.0          # 90 + 5 - 40
    # the rebate is OUTSIDE trading cash and inside incentives
    assert i["incentives_usd"] == 1.5
    assert i["grand_total_cash_usd"] == 56.5
    assert out["incentives"]["MAKER_REBATE"]["usd"] == 1.5


def test_redemption_is_never_counted_as_income(tmp_path):
    src = _cache(tmp_path, "a.jsonl", [
        _row(transactionHash="0xr", type="REDEEM", side="", size="90",
             usdcSize="90.0")])
    out = A.audit([src])
    assert out["identity"]["incentives_usd"] == 0.0
    assert out["identity"]["settlement_redemption_cashflow_usd"] == 90.0


def test_overstatement_is_reported_explicitly(tmp_path):
    """The owner's rule: a $1,701.60 redemption is NOT $1,701.60 profit. The
    audit must state the gap, not leave it to be inferred."""
    src = _cache(tmp_path, "a.jsonl", [
        _row(usdcSize="1700.0", size="4000"),
        _row(transactionHash="0xr", type="REDEEM", side="", size="1701.6",
             usdcSize="1701.6")])
    i = A.audit([src])["identity"]
    assert i["net_trading_cash_usd"] == 1.6
    assert i["overstatement_if_redemption_called_profit_usd"] == 1700.0


# --------------------------------------------------------------- semantics

def test_window_parsing_gives_the_resolution_instant(tmp_path):
    assert A.window_of({"slug": "btc-updown-5m-1000"}) == ("btc", "5m", 1300)
    assert A.window_of({"slug": "eth-updown-15m-1000"}) == ("eth", "15m", 1900)
    assert A.window_of({"slug": "nfl-something"}) is None


def test_delay_is_measured_from_resolution_not_from_the_last_trade(tmp_path):
    """A 5m window's last trade sits near expiry, so 'last trade -> redeem'
    flatters the delay. The window END is the resolution instant."""
    src = _cache(tmp_path, "a.jsonl", [
        _row(usdcSize="4.0", size="10"),
        _row(transactionHash="0xr", type="REDEEM", side="", size="10",
             usdcSize="10.0", timestamp=1400)])          # end 1300 -> +100 s
    d = A.audit([src])["lifecycle"]["delay_resolution_to_redeem_s"]
    assert d["p50"] == 100 and d["negative"] == 0


def test_a_redemption_before_resolution_would_be_flagged(tmp_path):
    src = _cache(tmp_path, "a.jsonl", [
        _row(usdcSize="4.0", size="10"),
        _row(transactionHash="0xr", type="REDEEM", side="", size="10",
             usdcSize="10.0", timestamp=1200)])          # BEFORE end 1300
    assert A.audit([src])["lifecycle"][
        "delay_resolution_to_redeem_s"]["negative"] == 1


def test_matched_and_residual_split_the_bought_inventory(tmp_path):
    src = _cache(tmp_path, "a.jsonl", [
        _row(size="10", usdcSize="4.0", outcome="Up"),
        _row(transactionHash="0xd", size="6", usdcSize="2.0", outcome="Down"),
        _row(transactionHash="0xr", type="REDEEM", side="", size="10",
             usdcSize="10.0", outcome="Up", timestamp=1400)])
    inv = A.audit([src])["inventory"]
    assert inv["bought_qty"] == 16.0
    assert inv["matched_qty"] == 6.0             # min(10, 6)
    assert inv["residual_qty"] == 4.0            # |10 - 6|
    assert inv["matched_pct_of_bought"] == 75.0  # 2*6/16


def test_lifecycle_vocabularies_are_venue_specific_not_forced(tmp_path):
    """PMUS has no REDEEM verb. Forcing Polymarket's states onto it would
    invent a submission step that does not exist there."""
    assert "REDEEM_SUBMITTED" in A.LIFECYCLE["POLYMARKET"]
    assert "REDEEM_SUBMITTED" not in A.LIFECYCLE["PMUS"]
    assert A.LIFECYCLE["PMUS"][-1] == "CASHFLOW_CONFIRMED"
    assert A.LIFECYCLE["GEMINI"] == ["UNKNOWN_FOR_OUR_ACCOUNT"]


def test_audit_has_no_order_or_network_path():
    import inspect
    src = inspect.getsource(A)
    for forbidden in ("place_order", "new_order", "cancel_order", "requests.",
                      "httpx", "urllib.request", "private_key"):
        assert forbidden not in src, forbidden
