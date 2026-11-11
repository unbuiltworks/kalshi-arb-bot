"""FULL-SEQUENTIAL-TAPE REPLAY: the tape is the clock.

The properties that make this engine different from the cycle-stepping ones,
and the accounting hygiene that must hold whatever the numbers do.
"""
from decimal import Decimal as D

import pytest

from src.gemini_execution import tape_replay as TR


# ========================================================= queue accounting

def test_THE_QUEUE_EATS_FIRST_AND_ONLY_WHAT_IS_LEFT_FILLS_US():
    q = TR.Quote(leg="YES", price=D("0.40"), qty=D("0.01"),
                 placed_ms=D("0"), ahead=D("100"), queue_known=True,
                 kind="COMPLETE")
    got, ate = q.take(D("60"))
    assert (got, ate) == (D("0"), D("60"))
    assert q.ahead_left == D("40")
    got, ate = q.take(D("40"))
    assert (got, ate) == (D("0"), D("40"))
    got, ate = q.take(D("5"))
    assert got == D("0.01") and ate == D("0")


def test_a_quote_never_fills_more_than_its_own_size():
    q = TR.Quote(leg="YES", price=D("0.4"), qty=D("0.01"), placed_ms=D("0"),
                 ahead=D("0"), queue_known=True, kind="OPEN")
    assert q.take(D("9999"))[0] == D("0.01")
    assert q.take(D("9999"))[0] == D("0")


def test_an_unknown_queue_is_treated_as_zero_and_that_is_OPTIMISTIC():
    """An UNREADABLE ladder still yields no queue, and that is generous, so
    every fill resting on it is counted separately.

    THE PREMISE OF THIS TEST WAS THE BUG. It used to assert that a NO rung is
    unknown even against a READABLE ladder -- "Gemini publishes no NO ladder"
    -- which made the generous assumption permanent for one leg and
    unreachable for the other. Gemini publishes ONE ladder; our NO bid at n
    rests at ask (1 - n) on it. The optimism is still here, but it now
    belongs to unreadability rather than to a side.
    """
    q = TR.Quote(leg="NO", price=D("0.4"), qty=D("0.01"), placed_ms=D("0"),
                 ahead=None, queue_known=False, kind="COMPLETE")
    assert q.ahead_left == D("0")
    assert q.take(D("1"))[0] == D("0.01")
    # unreadable -> unknown, for BOTH legs
    assert TR.size_ahead({"readable": False}, "NO", "0.4") is None
    assert TR.size_ahead({"readable": False}, "YES", "0.4") is None
    # readable -> a real queue from the ask ladder, no longer unknown
    depth = {"readable": True,
             "asks": {"levels": [{"price": "0.60", "qty": "42"}]}}
    assert TR.size_ahead(depth, "NO", "0.4") == D("42")


def test_size_ahead_counts_every_rung_at_or_better_than_ours():
    depth = {"readable": True, "bids": {"levels": [
        {"price": "0.45", "qty": "10"}, {"price": "0.40", "qty": "7"},
        {"price": "0.35", "qty": "99"}]}}
    assert TR.size_ahead(depth, "YES", "0.40") == D("17")
    assert TR.size_ahead(depth, "YES", "0.46") == D("0")
    assert TR.size_ahead({"readable": False}, "YES", "0.4") is None


# ====================================================== the side convention

def test_extend_and_complete_are_reached_by_opposite_aggressors():
    for held in ("YES", "NO"):
        assert (TR.reaching_aggressor(held)
                != TR.reaching_aggressor(TR.complement_leg(held)))
    assert TR.reaching_aggressor("YES") == "sell"
    assert TR.reaching_aggressor("NO") == "buy"


def test_a_NO_rung_is_reached_by_a_buyer_at_or_above_its_mirror():
    p = {"type": "buy", "price": "0.54", "amount": "3"}
    assert TR.reaches("NO", "0.46", p) is True
    assert TR.reaches("NO", "0.45", p) is False
    assert TR.reaches("NO", "0.46", dict(p, type="sell")) is False


# ============================================== requoting costs queue place

def test_KEEPING_A_PRICE_KEEPS_THE_QUEUE_AND_CHANGING_IT_DOES_NOT():
    depth = {"readable": True, "bids": {"levels": [
        {"price": "0.40", "qty": "50"}, {"price": "0.41", "qty": "10"}]}}
    live = {}
    TR._reconcile(live, [{"leg": "YES", "price": "0.40", "qty": "0.01",
                          "action": "OPEN"}],
                  now_ms=D("0"), depth=depth, default_qty=D("0.01"))
    live["YES"].ahead_left = D("5")                 # partly worked off
    ev = TR._reconcile(live, [{"leg": "YES", "price": "0.40", "qty": "0.01",
                               "action": "OPEN"}],
                       now_ms=D("9000"), depth=depth, default_qty=D("0.01"))
    assert ev["kept"] == 1 and live["YES"].ahead_left == D("5")
    ev = TR._reconcile(live, [{"leg": "YES", "price": "0.41", "qty": "0.01",
                               "action": "OPEN"}],
                       now_ms=D("18000"), depth=depth, default_qty=D("0.01"))
    assert ev["requoted"] == 1
    assert live["YES"].ahead_left == D("10")        # back of the new level
    assert live["YES"].placed_ms == D("18000")


def test_a_leg_the_policy_no_longer_wants_is_cancelled():
    live = {}
    TR._reconcile(live, [{"leg": "NO", "price": "0.5", "qty": "0.01",
                          "action": "OPEN"}],
                  now_ms=D("0"), depth=None, default_qty=D("0.01"))
    ev = TR._reconcile(live, [], now_ms=D("1"), depth=None,
                       default_qty=D("0.01"))
    assert ev["cancelled"] == 1 and live == {}


# =============================================================== the policies

def _cycle(ms, tte, ybid="0.40", yask="0.50", nbid="0.45", nask="0.55"):
    return {"utc_ms": ms, "utc": "u", "tte_s": tte,
            "book": {"readable": True, "identity_consistent": True,
                     "tick": "0.01", "t_utc": ms / 1000.0,
                     "yes": {"bid": ybid, "ask": yask},
                     "no": {"bid": nbid, "ask": nask}},
            "depth": {"readable": False}}


@pytest.fixture()
def _terms(monkeypatch):
    from src.gemini_execution import metadata as MD
    monkeypatch.setattr(MD, "terms_for", lambda s: {"priceIncrement": "0.01"})


class _Inv:
    def __init__(self, r):
        self._r = D(r)

    def residual(self):
        return self._r

    def matched(self):
        return D("0")

    def avg_yes(self):
        return D("0.4")

    def avg_no(self):
        return D("0.5")


def test_POLICY_B_VETOES_EXTEND_ONLY_ONCE_RESIDUAL_EXISTS(_terms):
    from src.gemini_execution import continuous_probe as CP
    book = _cycle(0, 200)["book"]
    flat = CP.Inventory()
    a = TR.desired(TR.POLICY_B, symbol="S", tte_s=200, inv=flat, book=book)
    assert a["vetoed"] == []                        # flat: native L5

    held = CP.Inventory()
    held.apply_fill("YES", "0.01", "0.40", source="T")
    b = TR.desired(TR.POLICY_B, symbol="S", tte_s=200, inv=held, book=book)
    assert all(x.get("action") == CP.COMPLETE for x in b["kept"])
    assert all(x.get("action") != CP.COMPLETE for x in b["vetoed"])


def test_POLICY_A_NEVER_VETOES_ANYTHING(_terms):
    from src.gemini_execution import continuous_probe as CP
    held = CP.Inventory()
    held.apply_fill("YES", "0.01", "0.40", source="T")
    a = TR.desired(TR.POLICY_A, symbol="S", tte_s=200, inv=held,
                   book=_cycle(0, 200)["book"])
    assert a["vetoed"] == []
    assert a["kept"] == a["model_a"]


def test_NEITHER_POLICY_INVENTS_AN_ACTION_MODEL_A_DID_NOT_ASK_FOR(_terms):
    from src.gemini_execution import continuous_probe as CP
    held = CP.Inventory()
    held.apply_fill("NO", "0.01", "0.45", source="T")
    for pol in TR.POLICIES:
        d = TR.desired(pol, symbol="S", tte_s=150, inv=held,
                       book=_cycle(0, 150)["book"])
        for a in d["kept"]:
            assert a in d["model_a"]


# ============================================================ the replay

def test_A_WINDOW_WITHOUT_ENOUGH_BOOK_IS_REFUSED_NOT_GUESSED(_terms):
    w = TR.replay_window(symbol="S", policy=TR.POLICY_A,
                         cycles=[_cycle(0, 200)], prints=[], resolution={},
                         expiry_ms=10 ** 9)
    assert w["sufficient"] is False
    assert "below the %d needed" % TR.MIN_CYCLES_FOR_A_WINDOW in w["why"]


def test_every_window_starts_FLAT(_terms):
    cyc = [_cycle(1000 * i, 200 - 8 * i) for i in range(1, 9)]
    w = TR.replay_window(symbol="S", policy=TR.POLICY_A, cycles=cyc,
                         prints=[], resolution={}, expiry_ms=10 ** 9)
    assert w["inventory"]["yes_qty"] == "0" and w["inventory"]["no_qty"] == "0"
    assert w["initial_fills"] == 0


def test_INVENTORY_AND_THE_ACTION_ARE_RECOMPUTED_BEFORE_THE_NEXT_PRINT(_terms):
    """The whole reason this engine exists. Two prints an instant apart: the
    first fills us, and the second must meet the quote the policy wants GIVEN
    that fill -- not the one it wanted before."""
    from src.gemini_execution import continuous_probe as CP
    cyc = [_cycle(1000 * i, 250 - 8 * i) for i in range(1, 12)]
    prints = [{"utc_ms": 5500, "type": "sell", "price": "0.10",
               "amount": "500", "tid": 1},
              {"utc_ms": 5501, "type": "buy", "price": "0.95",
               "amount": "500", "tid": 2}]
    w = TR.replay_window(symbol="S", policy=TR.POLICY_A, cycles=cyc,
                         prints=prints, resolution={}, expiry_ms=10 ** 9)
    kinds = [f["kind"] for f in w["fills"]]
    assert w["initial_fills"] == 1
    assert kinds[0] == CP.OPEN
    # the second print met a post-fill surface, so it is NOT another OPEN
    assert len(kinds) == 1 or kinds[1] != CP.OPEN


def test_policy_B_takes_no_EXTEND_where_policy_A_does(_terms):
    cyc = [_cycle(1000 * i, 250 - 8 * i) for i in range(1, 14)]
    prints = [{"utc_ms": 5500, "type": "sell", "price": "0.05",
               "amount": "900", "tid": 1},
              {"utc_ms": 9500, "type": "sell", "price": "0.05",
               "amount": "900", "tid": 2},
              {"utc_ms": 13500, "type": "sell", "price": "0.05",
               "amount": "900", "tid": 3}]
    a = TR.replay_window(symbol="S", policy=TR.POLICY_A, cycles=cyc,
                         prints=prints, resolution={}, expiry_ms=10 ** 9)
    b = TR.replay_window(symbol="S", policy=TR.POLICY_B, cycles=cyc,
                         prints=prints, resolution={}, expiry_ms=10 ** 9)
    assert b["extend_fills"] <= a["extend_fills"]
    if a["extend_fills"] > 0:
        assert b["extends_vetoed"] > 0


def test_excluding_bursts_removes_only_the_hundred_plus_prints(_terms):
    cyc = [_cycle(1000 * i, 250 - 8 * i) for i in range(1, 10)]
    prints = [{"utc_ms": 5500, "type": "sell", "price": "0.05",
               "amount": "500", "tid": 1},
              {"utc_ms": 6500, "type": "sell", "price": "0.05",
               "amount": "9", "tid": 2}]
    full = TR.replay_window(symbol="S", policy=TR.POLICY_A, cycles=cyc,
                            prints=prints, resolution={}, expiry_ms=10 ** 9)
    trim = TR.replay_window(symbol="S", policy=TR.POLICY_A, cycles=cyc,
                            prints=prints, resolution={}, expiry_ms=10 ** 9,
                            exclude_bursts=True)
    assert full["prints_walked"] == 2 and trim["prints_walked"] == 1
    assert trim["excluded_bursts"] is True


def test_fills_resting_on_an_unknown_queue_are_counted(_terms):
    cyc = [_cycle(1000 * i, 250 - 8 * i) for i in range(1, 10)]
    prints = [{"utc_ms": 5500, "type": "buy", "price": "0.95",
               "amount": "500", "tid": 1}]
    w = TR.replay_window(symbol="S", policy=TR.POLICY_A, cycles=cyc,
                         prints=prints, resolution={}, expiry_ms=10 ** 9)
    if w["fills"]:
        assert w["fills_resting_on_an_unknown_queue"] == sum(
            1 for f in w["fills"] if not f["queue_known"])


# ============================================================ the frontier

def _ep(price="0.40", ask="0.50", qty="0.01", held_avg="0.45",
        lo=0, hi=100000, levels=None):
    return {"leg": "YES", "price": price, "qty": qty, "ask": ask,
            "placed_ms": lo, "ended_ms": hi, "held_avg_price": held_avg,
            "held_leg": "NO",
            "depth": {"readable": True, "bids": {"levels": levels or []}}}


def test_the_frontier_finds_the_MINIMUM_improvement_that_fills():
    prints = [{"utc_ms": 50, "type": "sell", "price": "0.42", "amount": "5"}]
    f = TR.frontier_for(_ep(), prints)
    assert f["ticks"] == 2 and f["bucket"] == "2"
    assert f["price"] == "0.42"
    assert f["effective_pair_basis"] == "0.87"


def test_a_frontier_price_that_reaches_the_ask_is_a_CROSS_and_is_refused():
    prints = [{"utc_ms": 50, "type": "sell", "price": "0.99", "amount": "500"}]
    f = TR.frontier_for(_ep(price="0.48", ask="0.50"), prints)
    assert f["bucket"] == "crossing_required"
    assert f["reachable"] is False
    assert "crossing is not authorized" in f["why"]


def test_zero_ticks_means_model_A_s_own_price_would_have_filled():
    prints = [{"utc_ms": 50, "type": "sell", "price": "0.40", "amount": "5"}]
    f = TR.frontier_for(_ep(), prints)
    assert f["ticks"] == 0 and f["bucket"] == "0"


def test_THE_QUEUE_IS_RE_MEASURED_AT_EACH_CANDIDATE_PRICE():
    """Moving up a tick joins a DIFFERENT level with its own queue. A bid at
    a BETTER price is ahead of ours, so improving does not always help."""
    levels = [{"price": "0.42", "qty": "0"}, {"price": "0.41", "qty": "1000"}]
    prints = [{"utc_ms": 50, "type": "sell", "price": "0.39", "amount": "5"}]
    # at 0.41 there are 1000 contracts at or better than us
    assert TR.size_ahead(_ep(levels=levels)["depth"], "YES", "0.41") == D("1000")
    # at 0.42 only the empty better level counts, so the queue is clear
    assert TR.size_ahead(_ep(levels=levels)["depth"], "YES", "0.42") == D("0")
    f = TR.frontier_for(_ep(price="0.41", ask="0.50", levels=levels), prints)
    assert f["reachable"] is True
    assert f["ticks"] == 1                     # 0.41 is queued out; 0.42 is not
    assert f["size_ahead_at_that_price"] == "0"


def test_prints_outside_the_quotes_life_do_not_count():
    prints = [{"utc_ms": 999999, "type": "sell", "price": "0.20",
               "amount": "500"}]
    f = TR.frontier_for(_ep(lo=0, hi=1000), prints)
    assert f["reachable"] is False
    assert f["prints_in_window"] == 0


def test_UNREACHABLE_IS_NOT_THE_SAME_BUCKET_AS_EXPENSIVE():
    """">5 ticks" means reachable but dear. Nothing reaching it at any maker
    price is a different fact and gets its own bucket -- conflating them read
    as "expensive" what was actually "impossible"."""
    f = TR.frontier_for(_ep(ask="0.99"), [])
    assert f["bucket"] == "unreachable_at_any_maker_price"
    assert f["reachable"] is False
    c = TR.frontier_census([f])
    assert c["by_minimum_improvement"][">5"] == 0
    assert c["unreachable_at_any_maker_price"] == 1


def test_the_census_buckets_and_the_ceilings_are_ANALYTICAL_ONLY():
    rows = [{"computable": True, "bucket": "0", "reachable": True,
             "effective_pair_basis": "0.97"},
            {"computable": True, "bucket": "1", "reachable": True,
             "effective_pair_basis": "1.01"},
            {"computable": True, "bucket": "crossing_required",
             "reachable": False}]
    c = TR.frontier_census(rows)
    assert c["episodes"] == 3
    assert c["by_minimum_improvement"]["0"] == 1
    assert c["needing_a_cross"] == 1
    assert c["at_pair_ceiling"]["0.98"]["complements_available"] == 1
    assert c["at_pair_ceiling"]["1.02"]["complements_available"] == 2
    assert c["at_pair_ceiling"]["unrestricted_maker"][
        "complements_available"] == 2
    assert "not gates" in c["ceilings_are"]


# =========================================================== the economics

def test_REBATES_ARE_COMPUTED_FROM_THE_PUBLISHED_RULES_AND_KEPT_APART():
    fills = [{"qty": "0.01", "price": "0.50", "kind": "OPEN"},
             {"qty": "0.01", "price": "0.05", "kind": "COMPLETE"}]
    e = TR.economics(fills, None, {})
    assert e["rebate_eligible_fills"] == 1       # 0.05 is outside 0.20-0.80
    assert e["rebate_ineligible_fills"] == 1
    assert e["rebate_payable_after_rounding_usd"] == "0.00"
    assert D(e["rebate_accrued_usd"]) > 0
    assert "never summed" in e["separation"]


def test_maker_fees_use_the_published_maker_rate():
    from src.gemini_execution import rebate_program as RP
    e = TR.economics([{"qty": "1", "price": "0.50"}], None, {})
    assert D(e["maker_fees_usd"]) == D("0.50") * RP.MAKER_RATE
    assert D(e["gross_filled_usd"]) == D("0.50")


def test_an_unreadable_fill_is_skipped_not_valued_at_zero():
    e = TR.economics([{"qty": None, "price": "0.5"},
                      {"qty": "1", "price": "0.5"}], None, {})
    assert e["gross_contracts"] == "1"


# ============================================================ the census

def test_THE_UNIT_IS_THE_WINDOW_AND_INSUFFICIENT_ONES_ARE_REPORTED():
    ws = [{"sufficient": False}, {
        "sufficient": True, "policy": TR.POLICY_A, "fills": [
            {"qty": "0.01", "price": "0.4", "kind": "OPEN"}],
        "initial_fills": 1, "complement_fills": 0, "extend_fills": 0,
        "extends_vetoed": 0, "requotes": 2,
        "fills_resting_on_an_unknown_queue": 0,
        "inventory": {"matched_qty": "0", "directional_residual": "0.01",
                      "total_spend_usd": "0.004",
                      "effective_matched_pair_cost": None},
        "settlement": {}, "economics": {"maker_fees_usd": "0.00007",
                                        "rebate_accrued_usd": "0.0000504",
                                        "rebate_payable_after_rounding_usd":
                                            "0.00"}}]
    c = TR.policy_census(ws)
    assert c["independent_windows"] == 1
    assert c["insufficient_windows_skipped"] == 1
    assert c["windows_filled"] == 1 and c["windows_paired"] == 0
    assert c["matched_over_gross"] == "0.0000"
    assert D(c["maker_rebate_payable_after_rounding_usd"]) == D("0")


def test_nothing_in_this_module_claims_a_realized_number():
    import ast
    import inspect
    banned = ("realized", "realised", "profit", "pnl")
    denials = ("never", "not ", "nothing was", "no order existed", "cannot",
               "is not a rebate", "separate")
    # a field that NAMES the separated settlement residual line may say pnl --
    # that is the frozen report's own name for it and keeping the name is what
    # stops it being merged into anything. A bare "pnl" still fails.
    named_lines = ("settlement_residual_pnl", "directional_residual_pnl")
    for node in ast.walk(ast.parse(inspect.getsource(TR))):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            low = node.value.lower()
            if any(d in low for d in denials):
                continue
            if any(n in low for n in named_lines):
                continue
            for w in banned:
                assert w not in low, node.value


def test_A_KEPT_QUOTE_KEEPS_ITS_QUEUE_BUT_REFRESHES_ITS_LABEL():
    """Found by the sequential test: the NO rung resting while we were flat is
    an OPEN, and becomes a COMPLETE the instant a YES fill lands. Keeping the
    old label booked complements as opens."""
    live = {}
    TR._reconcile(live, [{"leg": "NO", "price": "0.45", "qty": "0.01",
                          "action": "OPEN"}],
                  now_ms=D("0"), depth=None, default_qty=D("0.01"))
    live["NO"].ahead_left = D("7")
    TR._reconcile(live, [{"leg": "NO", "price": "0.45", "qty": "0.01",
                          "action": "COMPLETE"}],
                  now_ms=D("9000"), depth=None, default_qty=D("0.01"))
    assert live["NO"].kind == "COMPLETE"
    assert live["NO"].ahead_left == D("7")          # queue untouched
    assert live["NO"].placed_ms == D("0")           # and its age
