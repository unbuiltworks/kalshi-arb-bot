"""A refused OPENING is named, not only counted.

THE BLINDNESS THIS REMOVES. `controller_open:*` was a counter and nothing
more, so the 1315z-1400z campaign could report
`controller_open:PAIR_COST_ABOVE_CEILING` 683 times -- its largest opening
refusal -- and could not say whether one of those 683 gave up a pair worth
having. The executor's refusal dict carries `effective_pair_cost` and
`ceiling` and neither ever reached the tape; the completion layer has
carried `L5_MISSED_PAIR` since the 1400z work, the opening layer did not.

THE PAIR IDENTITY, and an earlier version of this file had its SIGN
BACKWARDS. UP's touch is the BID and DOWN's is `1 - ask`, so

    (bid - d*t) + (1 - ask - d*t) = 1 - (ask - bid) - 2*d*t

The spread is SUBTRACTED. A wider spread makes the pair CHEAPER, because
both legs quote away from a touch that is further apart. On BTC15m's
median ONE-TICK book, depth 2 plans 0.95 -- comfortably inside the 0.96
ceiling, not outside it.

So the 683 `controller_open:PAIR_COST_ABOVE_CEILING` refusals are NOT
openings refused by an unreachable ceiling. That gate reads
`eff = (lc[UP] + lc[DOWN] + px*qty) / matched_after` and only fires when
the order CREATES new matched quantity -- so it refuses SECOND legs,
whose achieved pair drifted above 0.96 after the first leg filled. The
cause is drift between the two fills, i.e. adverse selection, not
arithmetic.

This file pins the record, not the policy. No ceiling, depth or price
changes here.
"""
from __future__ import annotations

from decimal import Decimal as D

from src import l5_missed_pair_blocker as MPB
from src import l5_pmus_live_runner as R
from src import l5_two_sided_probe as TS


class _Stats(dict):
    def __missing__(self, k):            # a defaultdict the tests can read
        self[k] = 0
        return 0


class _Runner:
    """Only what `_note_open_missed` touches."""
    def __init__(self):
        self.stats = _Stats()
        self.emitted = []

    _note_open_missed = R.Runner._note_open_missed


def _run(monkeypatch, rec, book, tick="0.01"):
    out = []
    monkeypatch.setattr(R, "emit", lambda label, payload: out.append(
        (label, payload)))
    r = _Runner()
    r._note_open_missed("cpc-btc-updown-15m-2026-11-02-1400z", rec, book,
                        tick)
    return r, out


# --------------------------------------------------- the measured case
ONE_TICK = {"bid": "0.4700", "ask": "0.4800", "stream_age_s": "0.05"}


def test_a_one_tick_book_records_an_achievable_pair_of_0_95(monkeypatch):
    """The median BTC15m book, and the whole point of the record.

    An earlier version of this test asserted 0.97 and PASSED, because the
    helper it tested carried the same sign error. A test that restates the
    code's own mistake confirms nothing, which is why the pair is now
    taken from `quote_own_price` in the test below as well.
    """
    _, out = _run(monkeypatch,
                  {"refused_reason": "PAIR_COST_ABOVE_CEILING"}, ONE_TICK)
    assert len(out) == 1
    label, p = out[0]
    assert label == "L5_MISSED_PAIR"
    assert p["stage"] == "OPENING"
    assert p["reason"] == "PAIR_COST_ABOVE_CEILING"
    assert D(p["pair"]["pair_own_cost"]) == D("0.95")
    # below redemption AND inside the 0.96 ceiling: this pair was one the
    # strategy would have accepted, which is what makes the refusal worth
    # recording at all
    assert p["pair"]["locks_gain_vs_redemption"] == "YES"
    assert p["pair"]["inside_entry_ceiling"] == "YES"
    assert p["missed_profitable_pair"] == "YES"


def test_the_ceiling_cannot_be_reached_at_depth_two_on_any_real_book():
    """The arithmetic behind the 683, stated as a test.

    `pair = 1 + spread - 2*d*t`. At d=2, t=0.01 the depth term is 0.04,
    so pair <= 0.96 demands spread <= 0.00 -- which is a locked or
    crossed book and refused elsewhere. Every TRADEABLE book is above
    the ceiling at this depth.
    """
    tick, d, cap = D("0.01"), D("2"), D("0.96")
    for spread_ticks in (1, 2, 3, 4):
        spread = tick * spread_ticks
        pair = D(1) + spread - (D(2) * d * tick)
        assert pair > cap, (spread_ticks, pair)
        assert pair <= D(1)              # and still profitable if filled
    # only a zero spread reaches it, and that book is not tradeable
    assert D(1) + D(0) - (D(2) * d * tick) == cap


def test_a_wider_spread_is_recorded_as_a_CHEAPER_pair(monkeypatch):
    """A 2-tick spread quotes both legs further out: 0.94, not 0.98."""
    _, out = _run(monkeypatch, {"refused_reason": "PAIR_COST_ABOVE_CEILING"},
                  {"bid": "0.4700", "ask": "0.4900", "stream_age_s": "0.05"})
    assert D(out[0][1]["pair"]["pair_own_cost"]) == D("0.94")


# --------------------------------------------------- it stays honest
def test_a_successful_open_emits_nothing(monkeypatch):
    """NOT_REFUSED is not a missed pair."""
    _, out = _run(monkeypatch, {"submitted": True}, ONE_TICK)
    assert out == []
    _, out2 = _run(monkeypatch, {"refused_reason": None}, ONE_TICK)
    assert out2 == []


def test_an_unreadable_book_records_UNKNOWN_not_a_guess(monkeypatch):
    """A refusal that fired before the book could be priced."""
    for bad in ({"bid": None, "ask": None},
                {"bid": "0", "ask": "0"},
                {"bid": "0.5000", "ask": "0.4900"},     # crossed
                {}):
        _, out = _run(monkeypatch, {"refused_reason": "BOOK_UNCERTAIN"}, bad)
        assert len(out) == 1, bad
        p = out[0][1]
        assert p["pair"]["pair_own_cost"] is None, bad
        assert p["missed_profitable_pair"] in ("UNKNOWN", "N/A"), bad


def test_an_unreadable_tick_records_UNKNOWN_rather_than_assuming_0_01(
        monkeypatch):
    """A guessed tick rescales the depth term and invents the pair."""
    for bad_tick in (None, "", "0", "nonsense"):
        _, out = _run(monkeypatch,
                      {"refused_reason": "PAIR_COST_ABOVE_CEILING"},
                      ONE_TICK, bad_tick)
        assert out[0][1]["pair"]["pair_own_cost"] is None, bad_tick


def test_it_can_never_raise_into_the_decision_path(monkeypatch):
    """Observability must not change the refusal it describes."""
    def boom(*a, **k):
        raise RuntimeError("telemetry is down")
    monkeypatch.setattr(MPB, "record", boom)
    monkeypatch.setattr(R, "emit", lambda *a, **k: None)
    r = _Runner()
    r._note_open_missed("m", {"refused_reason": "PAIR_COST_ABOVE_CEILING"},
                        ONE_TICK, "0.01")                  # must not raise
    assert r.stats["open_missed_emit_failed:RuntimeError"] == 1


def test_the_refusal_record_names_the_blocker_class(monkeypatch):
    """So the histogram is actionable rather than merely large."""
    seen = {}
    for reason in ("PAIR_COST_ABOVE_CEILING", "ORDER_ALREADY_RESTING",
                   "PAST_TERMINAL", "PAST_ENTRY_CUTOFF"):
        _, out = _run(monkeypatch, {"refused_reason": reason}, ONE_TICK)
        seen[reason] = out[0][1]["blocker_class"]
    assert seen["PAIR_COST_ABOVE_CEILING"] == MPB.PAIR_CEILING
    assert seen["ORDER_ALREADY_RESTING"] == MPB.ORDER_CONCURRENCY
    # a refusal past the window is a CORRECT refusal, never a missed pair
    assert seen["PAST_TERMINAL"] == MPB.NOT_AN_OPPORTUNITY


# ===================================================================
# THE PAIR IDENTITY, which an earlier version of this file got
# backwards. UP's touch is the BID and DOWN's is `1 - ask`, so
#     (bid - d*t) + (1 - ask - d*t) = 1 - (ask - bid) - 2*d*t
# The spread is SUBTRACTED: a wider spread makes the pair CHEAPER,
# because both legs quote away from a touch that is further apart.
# ===================================================================

def test_the_spread_makes_the_pair_cheaper_not_dearer():
    """A one-tick book plans 0.95 at depth 2, not 0.97."""
    for spread_t, want in ((1, D("0.95")), (2, D("0.94")), (4, D("0.92"))):
        bid = D("0.5000")
        ask = bid + D("0.01") * spread_t
        u = TS.quote_own_price(TS.UP, bid=bid, ask=ask, tick=D("0.01"),
                               depth_ticks=2)
        dn = TS.quote_own_price(TS.DOWN, bid=bid, ask=ask, tick=D("0.01"),
                                depth_ticks=2)
        assert u + dn == want, (spread_t, u + dn)
        assert u + dn <= TS.max_pair_cost()      # and it CLEARS the ceiling


def test_deeper_quotes_plan_cheaper_pairs(monkeypatch):
    """Two ticks of pair per tick of depth, both legs giving up one."""
    bid, ask = D("0.5000"), D("0.5100")
    seen = {}
    for d in (2, 3, 4, 5):
        u = TS.quote_own_price(TS.UP, bid=bid, ask=ask, tick=D("0.01"),
                               depth_ticks=d)
        dn = TS.quote_own_price(TS.DOWN, bid=bid, ask=ask, tick=D("0.01"),
                                depth_ticks=d)
        seen[d] = u + dn
    assert seen == {2: D("0.95"), 3: D("0.93"),
                    4: D("0.91"), 5: D("0.89")}


def test_the_recorded_pair_matches_the_engines_own_pricing(monkeypatch):
    """The record must not restate the arithmetic, it must ask."""
    out = []
    monkeypatch.setattr(R, "emit", lambda label, p: out.append(p))
    r = _Runner()
    r._note_open_missed("m", {"refused_reason": "PAIR_COST_ABOVE_CEILING"},
                        {"bid": "0.5600", "ask": "0.5700",
                         "stream_age_s": "0.05"}, "0.01")
    u = TS.quote_own_price(TS.UP, bid=D("0.5600"), ask=D("0.5700"),
                           tick=D("0.01"), depth_ticks=TS.depth_ticks())
    dn = TS.quote_own_price(TS.DOWN, bid=D("0.5600"), ask=D("0.5700"),
                            tick=D("0.01"), depth_ticks=TS.depth_ticks())
    assert D(out[0]["pair"]["pair_own_cost"]) == u + dn == D("0.95")


def test_an_opening_record_is_not_labelled_a_completion(monkeypatch):
    """The tape must name the decision that was actually refused."""
    out = []
    monkeypatch.setattr(R, "emit", lambda label, p: out.append(p))
    r = _Runner()
    r._note_open_missed("m", {"refused_reason": "PAIR_COST_ABOVE_CEILING"},
                        ONE_TICK, "0.01")
    assert "an OPENING pair that was DECIDED AGAINST" in out[0]["labels"]
    assert "a completion that was" not in out[0]["labels"]
    # and the completion layer keeps its own wording
    comp = MPB.record(market="m", reason="PAIR_ABOVE_REDEMPTION",
                      decision={"pair_own_cost": "1.10"}, stage="DECISION")
    assert "a completion that was DECIDED AGAINST" in comp["labels"]


# ===================================================================
# THE PAIR COST IS VERY NEARLY A CONSTANT, which was this record's
# defect. Both legs quote `d` ticks behind their own touch, so
#     pair = 1 - spread - 2*d*t
# is INVARIANT to the price level: 0.95 on every one-tick book,
# whether it sits at 0.50 or at 0.90. Reporting that as
# `missed_profitable_pair: YES` was reporting a constant and calling
# it an opportunity.
#
# THE 2215z EVIDENCE, from the live campaign. The record carried
# `pair_own_cost 0.9500` beside PAIR_COST_ABOVE_CEILING and read as a
# lost 5c pair. At bid 0.8900 / ask 0.9000 the legs price at own
# 0.8700 and 0.0800 -- one above the 0.60 ceiling, one below the 0.20
# floor -- so NEITHER leg could be opened. There was no opportunity.
# ===================================================================

def test_the_pair_cost_is_invariant_to_the_price_level():
    """Which is why it cannot, alone, signal an opportunity."""
    seen = set()
    for bid_c in range(5, 95, 5):
        bid = D(bid_c) / D(100)
        ask = bid + D("0.01")
        u = TS.quote_own_price(TS.UP, bid=bid, ask=ask, tick=D("0.01"),
                               depth_ticks=2)
        dn = TS.quote_own_price(TS.DOWN, bid=bid, ask=ask, tick=D("0.01"),
                                depth_ticks=2)
        seen.add(u + dn)
    assert seen == {D("0.95")}, seen


def test_an_unplaceable_book_is_not_a_missed_pair(monkeypatch):
    """The exact 2215z book, which used to read YES."""
    _, out = _run(monkeypatch, {"refused_reason": "PAIR_COST_ABOVE_CEILING"},
                  {"bid": "0.8900", "ask": "0.9000", "stream_age_s": "0.05"})
    r = out[0][1]
    assert D(r["pair"]["pair_own_cost"]) == D("0.95")   # still reported
    assert r["leg_own_prices"] == {"UP": "0.8700", "DOWN": "0.0800"}
    assert r["first_leg_placeable"] is False
    assert r["missed_profitable_pair"] == "N/A"
    assert "NO_LEG_IN_FIRST_LEG_BAND" in r["not_an_opportunity_because"]


def test_a_placeable_book_with_the_same_pair_still_reads_YES(monkeypatch):
    """The correction must not silence genuine misses.

    Identical pair cost, a book whose UP leg sits inside the band.
    """
    _, out = _run(monkeypatch, {"refused_reason": "PAIR_COST_ABOVE_CEILING"},
                  {"bid": "0.5600", "ask": "0.5700", "stream_age_s": "0.05"})
    r = out[0][1]
    assert D(r["pair"]["pair_own_cost"]) == D("0.95")
    assert r["first_leg_placeable"] is True
    assert r["missed_profitable_pair"] == "YES"
    assert "not_an_opportunity_because" not in r


def test_one_leg_inside_the_band_is_enough(monkeypatch):
    """Only ONE leg is opened, so one in-band leg is an opportunity."""
    # UP own 0.2800, DOWN own 0.6700: UP is in [0.20,0.60], DOWN is not
    _, out = _run(monkeypatch, {"refused_reason": "PAIR_COST_ABOVE_CEILING"},
                  {"bid": "0.3000", "ask": "0.3100", "stream_age_s": "0.05"})
    r = out[0][1]
    assert r["leg_own_prices"]["UP"] == "0.2800"
    assert r["first_leg_placeable"] is True


def test_placeability_is_None_when_the_book_is_unreadable(monkeypatch):
    """Never asserted as placeable from a book we could not price."""
    _, out = _run(monkeypatch, {"refused_reason": "BOOK_UNCERTAIN"},
                  {"bid": None, "ask": None})
    r = out[0][1]
    assert r["first_leg_placeable"] is None
    assert r["pair"]["pair_own_cost"] is None
