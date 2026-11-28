"""Reorg handling — gate G-C5.

The confirmation-depth filter stops us ACTING on an unconfirmed fill. It says
nothing about the fills already seen, and the entire claim being made for the
chain is that it knows ~15s before the venue does — which means reading near
the head, where a reorg is something that happens to evidence we already hold.

Both silent outcomes are failures. A retracted fill quietly dropped leaves
inventory the engine believes in with no event to contradict it; quietly kept
is worse. And an unreadable receipt must never be mistaken for either.
"""
from src.l5_chain_observer import finality as FN


def _obs(**fills):
    st = {}
    FN.observe(st, fills, 1000)
    return st


# ---------------------------------------------------- what counts as evidence

def test_a_fresh_fill_is_provisional_not_countable():
    st = _obs(**{"0x1": {"block": 990}})
    assert st["0x1"]["status"] == "provisional"
    assert FN.countable(st) == []


def test_a_fill_deep_enough_becomes_final_and_countable():
    st = _obs(**{"0x1": {"block": 900, "block_hash": "0xA"}})
    r = FN.recheck(st, 1000, lambda tx: {"blockNumber": 900,
                                         "blockHash": "0xA"})
    assert [e["kind"] for e in r["events"]] == ["chain_final"]
    assert FN.countable(st) == ["0x1"]


def test_one_block_short_of_the_depth_is_still_provisional():
    st = _obs(**{"0x1": {"block": 1000 - FN.CONFIRMATIONS + 1}})
    FN.recheck(st, 1000, lambda tx: {"blockNumber": st["0x1"]["block"],
                                     "blockHash": None})
    assert FN.countable(st) == []


# --------------------------------------------------------------- the reorg

def test_a_transaction_the_chain_no_longer_carries_is_retracted():
    st = _obs(**{"0x1": {"block": 990}})
    r = FN.recheck(st, 1000, lambda tx: None)
    assert [e["kind"] for e in r["events"]] == ["chain_retraction"]
    assert FN.retracted(st) == ["0x1"] and FN.countable(st) == []


def test_the_retraction_names_where_the_fill_had_been_seen():
    """A marker nobody can trace back is not a marker."""
    st = _obs(**{"0x1": {"block": 990, "block_hash": "0xA"}})
    e = FN.recheck(st, 1000, lambda tx: None)["events"][0]
    assert e["block"] == 990 and e["block_hash"] == "0xA"
    assert "no longer carries" in e["why"]


def test_re_inclusion_in_a_different_block_is_still_a_reorg():
    """(block, blockHash) is the identity a reorg changes. A tracker keyed on
    the transaction hash alone would see nothing happen at all."""
    st = _obs(**{"0x1": {"block": 990, "block_hash": "0xA"}})
    r = FN.recheck(st, 1000, lambda tx: {"blockNumber": 995,
                                         "blockHash": "0xC"})
    assert [e["kind"] for e in r["events"]] == ["chain_reblocked"]
    assert st["0x1"]["block"] == 995 and st["0x1"]["block_hash"] == "0xC"


def test_a_reblocked_transaction_restarts_its_confirmation_count():
    st = _obs(**{"0x1": {"block": 900, "block_hash": "0xA"}})
    FN.recheck(st, 1000, lambda tx: {"blockNumber": 995, "blockHash": "0xC"})
    assert FN.countable(st) == []          # 5 confirmations, not 100


def test_the_same_block_with_a_different_hash_is_a_reorg():
    st = _obs(**{"0x1": {"block": 990, "block_hash": "0xA"}})
    r = FN.recheck(st, 1000, lambda tx: {"blockNumber": 990,
                                         "blockHash": "0xB"})
    assert [e["kind"] for e in r["events"]] == ["chain_reblocked"]


def test_a_hex_block_number_is_read_the_same_as_an_integer():
    st = _obs(**{"0x1": {"block": 900, "block_hash": "0xA"}})
    FN.recheck(st, 1000, lambda tx: {"blockNumber": "0x384",
                                     "blockHash": "0xA"})
    assert FN.countable(st) == ["0x1"]


# --------------------------------------------- unasked is not answered

def test_an_unreadable_receipt_is_not_a_retraction():
    """Otherwise every network blip invents a reorg."""
    st = _obs(**{"0x1": {"block": 990}})

    def boom(_tx):
        raise RuntimeError("timeout")

    r = FN.recheck(st, 1000, boom)
    assert r["events"] == [] and r["retracted"] == 0
    assert r["unreadable"] == 1 and st["0x1"]["status"] == "provisional"


def test_a_recovered_check_clears_the_error():
    st = _obs(**{"0x1": {"block": 900, "block_hash": "0xA"}})

    def boom(_tx):
        raise RuntimeError("timeout")

    FN.recheck(st, 1000, boom)
    assert st["0x1"]["last_check_error"]
    FN.recheck(st, 1000, lambda tx: {"blockNumber": 900, "blockHash": "0xA"})
    assert "last_check_error" not in st["0x1"]
    assert FN.countable(st) == ["0x1"]


# ------------------------------------------------- terminal states are sticky

def test_a_retracted_transaction_is_never_silently_revived():
    st = _obs(**{"0x1": {"block": 990}})
    FN.recheck(st, 1000, lambda tx: None)
    FN.recheck(st, 1100, lambda tx: {"blockNumber": 990, "blockHash": "0xA"})
    assert FN.retracted(st) == ["0x1"] and FN.countable(st) == []


def test_observing_a_transaction_twice_does_not_reset_it():
    st = _obs(**{"0x1": {"block": 990}})
    FN.recheck(st, 1000, lambda tx: None)
    assert FN.observe(st, {"0x1": {"block": 990}}, 1100) == []
    assert FN.retracted(st) == ["0x1"]


def test_a_fill_with_no_block_is_not_tracked():
    st = {}
    assert FN.observe(st, {"0x1": {"block": None}}, 1000) == []
    assert st == {}


def test_every_event_including_the_first_sighting_carries_a_kind():
    """They share an append-only audit file; a record with no kind is one
    nobody can filter."""
    st = {}
    ev = FN.observe(st, {"0x1": {"block": 990}}, 1000)
    assert [e["kind"] for e in ev] == ["chain_provisional"]
    assert all("tx" in e and "t" in e for e in ev)


# -------------------------------------------------------- the gate's own test

def test_the_self_test_simulates_a_reorg_and_passes():
    r = FN.self_test()
    assert r["passed"] is True
    assert set(r["events"]) >= {"chain_retraction", "chain_reblocked"}
    assert r["countable"] == ["0xstays"]
    assert r["retracted"] == ["0xgone"]
    assert r["unreadable_is_not_retracted"] is True


def test_the_self_test_would_fail_if_retraction_stopped_working(monkeypatch):
    """A negative control: the gate runs this, so it has to be able to fail."""
    monkeypatch.setattr(FN, "recheck",
                        lambda *a, **k: {"events": [], "provisional": 0,
                                         "final": 0, "retracted": 0,
                                         "unreadable": 0,
                                         "confirmations_required": 64})
    assert FN.self_test()["passed"] is False


def test_the_depth_is_the_preregistered_one():
    assert FN.CONFIRMATIONS == 64
