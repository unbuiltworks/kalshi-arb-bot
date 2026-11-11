"""A shorter quote lifetime, through the real runner on a recorded book.

`L5_TS_ORDER_TTL_S` already bounds how long an order may rest. Our own
venue fills show orders that had rested more than 30 s when they were hit
did worst afterwards, so the question is whether the existing setting does
what it says when set to 30: nothing rests much longer than that, the pair
is re-priced from the current book rather than left behind, and both legs
still rest together. No behaviour is changed here; the default is as it was.
"""
from __future__ import annotations

import importlib.util
import pathlib
from decimal import Decimal as D

from src import l5_two_sided_probe as TS

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "runner_replay_20261111_ttl",
    ROOT / "analysis" / "l5" / "runner_replay_20261111.py")
RR = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RR)

QUIET = "cpc-btc-updown-15m-2026-11-08-0130z"


def rests(ttl):
    """(side, price, seconds it rested) for every order cancelled unfilled
    before the first fill, and the per-tick log."""
    born, spans = {}, []

    def on_submit(h, intent):
        if not born:
            cancel = h.venue.cancel

            def wrapped(order_id, market_slug=None):
                i = h.venue.by_oid[order_id]
                spans.append((str(i["side"]), D(str(i["price"])),
                              h.t - born[i["client_order_id"]]))
                return cancel(order_id, market_slug)
            h.venue.cancel = wrapped
        born[intent["client_order_id"]] = h.t

    env = dict(RR.CONFIGS["as_deployed"], L5_LIVE_AUTH_MAX_REPLACE="400",
               L5_ACC_MAX_ATTEMPTS="40")
    if ttl is not None:
        env[TS.ORDER_TTL_ENV] = str(ttl)
    res = RR.replay(RR.books()[QUIET]["rows"], env, end=44,
                    on_submit=on_submit)
    return spans, res


def test_the_default_lifetime_is_unchanged(monkeypatch):
    monkeypatch.delenv(TS.ORDER_TTL_ENV, raising=False)
    assert TS.order_ttl_s() == TS.ORDER_TTL_S == 120.0
    monkeypatch.setenv(TS.ORDER_TTL_ENV, "30")
    assert TS.order_ttl_s() == 30.0
    monkeypatch.setenv(TS.ORDER_TTL_ENV, "600")       # never longer
    assert TS.order_ttl_s() <= 120.0


def test_as_deployed_the_opening_pair_is_left_where_it_was_put():
    spans, res = rests(None)
    assert spans == []                     # nothing cancelled in 44 s
    assert res["log"][0]["up_rest"] and res["log"][0]["dn_rest"]


def test_at_thirty_seconds_the_pair_is_cancelled_and_priced_again():
    spans, res = rests(30)
    assert spans, "the opening pair should have been cancelled by 44 s"
    assert {s for s, _, _ in spans} == {"UP", "DOWN"}
    assert all(28 <= age <= 36 for _, _, age in spans)
    # and it is quoted again, both sides, from the book as it is then
    last = res["log"][-1]
    assert last["up_rest"] and last["dn_rest"]
    assert D(last["up_rest"][0]) + D(last["dn_rest"][0]) <= D("0.96")
    assert res["filled"] == {"UP": D("0"), "DOWN": D("0")}
