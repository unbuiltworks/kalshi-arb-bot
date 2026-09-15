"""Decoding L5's exchange logs, and the two bugs that faked disagreement.

Gate G-C4 asks whether the chain and the venue agree about WHAT happened in a
transaction, not merely that both saw it. Twice that check reported a
disagreement that was entirely on our side of it:

  1. the feed reader collapsed a transaction's several fills into one row, so
     80 @ 0.44 + 80 @ 0.43 was compared against whichever arrived first;
  2. the chain reader summed every leg it found, mixing L5's taker leg, L5's
     maker leg on the other token, and a second event restating the same fill,
     into "240 @ 0.35".

A gate that can be failed by its own measurement error is worthless, so these
pin both sides.
"""
import json

import pytest

from src.l5_chain_observer import decode as DE
from src.l5_chain_observer import observer as O

PAD = O.PAD
EX = O.EXCHANGES[0]


def _word(n: int) -> str:
    return f"{n:064x}"


def _log(*, price_usdc: int, size_shares: int, token: int, pos: int,
         event: str = "0xd543adfd", tx: str = "0xaa", block: int = 0x10,
         log_index: int = 0, address: str = EX, n_words: int = 7) -> dict:
    """A log shaped like the ones measured live: 7 data words, w1 token,
    w2 maker amount (USDC 6dp), w3 taker amount (shares 6dp)."""
    topics = ["0x" + "0" * 64] * 4
    topics[0] = event + "0" * 56
    topics[pos] = PAD
    words = [0, token, price_usdc, size_shares] + [0] * (n_words - 4)
    return {"topics": topics, "data": "0x" + "".join(_word(w) for w in words),
            "transactionHash": tx, "blockNumber": hex(block),
            "logIndex": hex(log_index), "address": address}


# --------------------------------------------------------------- the decode

def test_price_is_maker_over_taker_as_measured_against_the_feed():
    """The live calibration case: 45.60 USDC for 80 shares is 0.57."""
    leg = DE.decode_log(_log(price_usdc=45_600_000, size_shares=80_000_000,
                             token=7208, pos=2), l5_topic=PAD)
    assert leg["price"] == "0.570000"
    assert leg["size"] == "80"
    assert leg["token_id"] == "7208"


def test_a_log_without_l5_in_any_topic_is_not_a_fill_of_ours():
    lg = _log(price_usdc=1, size_shares=1, token=1, pos=2)
    lg["topics"][2] = "0x" + "1" * 64
    assert DE.decode_log(lg, l5_topic=PAD) is None


def test_zero_size_is_not_a_fill():
    assert DE.decode_log(_log(price_usdc=100, size_shares=0, token=1, pos=2),
                         l5_topic=PAD) is None


def test_a_short_data_field_is_declined_rather_than_guessed():
    lg = _log(price_usdc=1, size_shares=1, token=1, pos=2)
    lg["data"] = "0x" + _word(1) * 3          # three words, need four
    assert DE.decode_log(lg, l5_topic=PAD) is None


def test_provenance_is_retained_on_every_leg():
    """Gate G-C8: an attributed transaction must stay re-checkable."""
    leg = DE.decode_log(_log(price_usdc=45_600_000, size_shares=80_000_000,
                             token=7208, pos=2, tx="0xdead", block=0x2a,
                             log_index=0x7), l5_topic=PAD)
    assert leg["tx"] == "0xdead"
    assert leg["block"] == 42 and leg["log_index"] == 7
    assert leg["contract"] == EX.lower()
    assert leg["l5_topic_pos"] == 2
    assert leg["event"].startswith("0xd543adfd")


def test_only_exchange_logs_are_decoded():
    """A transaction touches many contracts; the others are not our fills."""
    rc = {"logs": [_log(price_usdc=100, size_shares=100, token=1, pos=2,
                        address="0xfeedfacefeedfacefeedfacefeedfacefeedface")]}
    assert DE.decode_receipt(rc, l5_topic=PAD, exchanges=O.EXCHANGES) == []


# --------------------------------------------------- grouping, not summing

def _live_shaped_receipt():
    """Transaction 0x15d925d6…, the one that exposed the summing bug:
    L5 maker 27+53 @ 0.95 on one token, L5 taker 80 @ 0.05 on the other, and
    a second event restating the taker leg. The feed reported 80 @ 0.05.

    The restating event is now declined by the allowlist, which removes the
    duplicate at the source; grouping still has to separate the two roles."""
    return {"logs": [
        _log(price_usdc=25_650_000, size_shares=27_000_000, token=7208, pos=3,
             log_index=1),
        _log(price_usdc=50_350_000, size_shares=53_000_000, token=7208, pos=3,
             log_index=2),
        _log(price_usdc=4_000_000, size_shares=80_000_000, token=2007, pos=2,
             log_index=3),
        _log(price_usdc=4_000_000, size_shares=80_000_000, token=2007, pos=2,
             event="0x174b3811", log_index=4),
    ]}


def test_summing_every_leg_is_the_bug_and_grouping_is_the_fix():
    legs = DE.decode_receipt(_live_shaped_receipt(), l5_topic=PAD,
                             exchanges=O.EXCHANGES)
    assert len(legs) == 3                    # the restating event is declined
    # the old behaviour, kept only as the negative control
    blind = DE.aggregate(legs)
    assert blind["size"] == "160"
    assert blind["price"] != "0.050000"
    gs = DE.groups(legs)
    assert len(gs) == 2                      # L5 as maker, and L5 as taker
    m = DE.best_match(gs, price="0.05", size="80")
    assert m is not None and m["price"] == "0.050000" and m["size"] == "80"


def test_the_topic_position_separates_l5s_two_roles():
    gs = DE.groups(DE.decode_receipt(_live_shaped_receipt(), l5_topic=PAD,
                                     exchanges=O.EXCHANGES))
    roles = {g["group_key"]["l5_topic_pos"] for g in gs}
    assert roles == {2, 3}
    maker = [g for g in gs if g["group_key"]["l5_topic_pos"] == 3]
    assert len(maker) == 1
    # the two maker legs on the same token DO belong together
    assert maker[0]["n_legs"] == 2 and maker[0]["size"] == "80"
    assert maker[0]["price"] == "0.950000"


def test_two_fills_in_one_transaction_at_adjacent_prices_aggregate():
    """The 80 @ 0.44 / 80 @ 0.43 case, where the aggregate is the comparable."""
    legs = DE.decode_receipt({"logs": [
        _log(price_usdc=35_200_000, size_shares=80_000_000, token=5, pos=2,
             log_index=1),
        _log(price_usdc=34_400_000, size_shares=80_000_000, token=5, pos=2,
             log_index=2)]}, l5_topic=PAD, exchanges=O.EXCHANGES)
    g = DE.groups(legs)
    assert len(g) == 1 and g[0]["size"] == "160"
    assert g[0]["price"] == "0.435000"


def test_best_match_is_exact_not_close():
    """A decode that is merely nearly right is a decode that is wrong."""
    gs = DE.groups(DE.decode_receipt(_live_shaped_receipt(), l5_topic=PAD,
                                     exchanges=O.EXCHANGES))
    assert DE.best_match(gs, price="0.06", size="80") is None
    assert DE.best_match(gs, price="0.05", size="81") is None
    assert DE.best_match(gs, price=None, size="80") is None


def test_groups_are_ordered_largest_first():
    gs = DE.groups(DE.decode_receipt(_live_shaped_receipt(), l5_topic=PAD,
                                     exchanges=O.EXCHANGES))
    sizes = [float(g["size"]) for g in gs]
    assert sizes == sorted(sizes, reverse=True)


def test_aggregate_of_nothing_is_none_not_zero():
    assert DE.aggregate([]) is None
    assert DE.groups([]) == []


# ------------------------------------------ the feed side of the same bug

def _feed(rows):
    import urllib.request

    class _R:
        def __init__(self, d):
            self._d = d

        def read(self):
            return json.dumps(self._d).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return lambda *a, **k: _R(rows)


def test_a_transaction_with_two_fills_keeps_both(monkeypatch):
    """22 of 500 rows were being discarded by keying on the hash alone."""
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", _feed([
        {"transactionHash": "0x1", "timestamp": 200, "price": "0.44",
         "size": "80", "side": "BUY", "slug": "m", "outcome": "Up"},
        {"transactionHash": "0x1", "timestamp": 100, "price": "0.43",
         "size": "80", "side": "BUY", "slug": "m", "outcome": "Up"},
    ]))
    f = O.activity_fills(limit=2)
    assert f["0x1"]["n_fills"] == 2
    assert f["0x1"]["agg_size"] == "160"
    assert f["0x1"]["agg_price"] == "0.435000"


def test_the_earliest_timestamp_wins_so_the_window_filter_sees_the_first_fill(
        monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", _feed([
        {"transactionHash": "0x1", "timestamp": 500, "price": "0.5",
         "size": "1"},
        {"transactionHash": "0x1", "timestamp": 100, "price": "0.5",
         "size": "1"},
    ]))
    assert O.activity_fills()["0x1"]["ts"] == 100


def test_an_unreadable_feed_is_none_never_an_empty_dict(monkeypatch):
    import urllib.request

    def boom(*a, **k):
        raise OSError("down")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert O.activity_fills() is None


def test_an_unparseable_fill_does_not_corrupt_the_aggregate(monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", _feed([
        {"transactionHash": "0x1", "timestamp": 1, "price": "0.44",
         "size": "80"},
        {"transactionHash": "0x1", "timestamp": 1, "price": None,
         "size": None},
    ]))
    f = O.activity_fills()
    assert f["0x1"]["n_fills"] == 2 and f["0x1"]["agg_size"] == "80"


def test_a_transaction_with_no_usable_fill_reports_none_not_zero(monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", _feed([
        {"transactionHash": "0x1", "timestamp": 1, "price": None,
         "size": None}]))
    f = O.activity_fills()
    assert f["0x1"]["agg_size"] is None and f["0x1"]["agg_price"] is None


def test_the_join_key_is_still_the_transaction_hash():
    """feed_only / chain_only are computed off these keys; changing their
    meaning would silently change what a promotion blocker is."""
    import inspect
    src = inspect.getsource(O.activity_fills)
    assert "transactionHash" in src


# ------------------------- only layouts verified against the feed are decoded

def test_an_unverified_event_signature_is_declined_rather_than_guessed():
    """0x4a39dc06 (TransferBatch) has enough words to pass a length check and
    encodes DYNAMIC ARRAYS. Reading w2/w3 as amounts gave a size of 2.5e70 --
    and the classifier called it 'a decodable L5 fill the venue has not
    published', its most serious verdict, about a token transfer."""
    lg = _log(price_usdc=0x80, size_shares=0xc0, token=1, pos=2,
              event="0x4a39dc06")
    assert DE.decode_log(lg, l5_topic=PAD) is None


def test_the_allowlist_is_the_signature_measured_against_the_feed():
    assert DE.DECODABLE_EVENTS == ("0xd543adfd",)


def test_an_implausible_price_is_declined_even_on_an_allowlisted_event():
    """Defence in depth: a fill has a probability for a price."""
    lg = _log(price_usdc=200_000_000, size_shares=80_000_000, token=1, pos=2)
    assert DE.decode_log(lg, l5_topic=PAD) is None


def test_an_implausible_size_is_declined_even_on_an_allowlisted_event():
    huge = int(DE.MAX_SANE_SIZE) * 10 ** 6 * 10
    lg = _log(price_usdc=huge // 2, size_shares=huge, token=1, pos=2)
    assert DE.decode_log(lg, l5_topic=PAD) is None


def test_a_price_of_exactly_one_is_a_real_fill():
    lg = _log(price_usdc=80_000_000, size_shares=80_000_000, token=1, pos=2)
    assert DE.decode_log(lg, l5_topic=PAD)["price"] == "1.000000"


def test_declined_logs_are_reported_by_name_not_silently_dropped():
    """An absence must be attributable: a signature that started carrying
    fills would otherwise just stop appearing."""
    rc = {"logs": [
        _log(price_usdc=45_600_000, size_shares=80_000_000, token=7, pos=2),
        _log(price_usdc=0x80, size_shares=0xc0, token=1, pos=2,
             event="0x4a39dc06")]}
    d = DE.declined(rc, l5_topic=PAD, exchanges=O.EXCHANGES)
    assert len(d) == 1
    assert d[0]["event"] == "0x4a39dc06"
    assert "allowlist" in d[0]["reason"]


def test_a_receipt_of_only_verified_fills_declines_nothing():
    rc = {"logs": [_log(price_usdc=45_600_000, size_shares=80_000_000,
                        token=7, pos=2)]}
    assert DE.declined(rc, l5_topic=PAD, exchanges=O.EXCHANGES) == []


def test_logs_without_l5_are_not_reported_as_declined():
    lg = _log(price_usdc=1, size_shares=1, token=1, pos=2,
              event="0x4a39dc06")
    lg["topics"][2] = "0x" + "1" * 64
    assert DE.declined({"logs": [lg]}, l5_topic=PAD,
                       exchanges=O.EXCHANGES) == []
