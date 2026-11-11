"""A completed pair's cash counts as returned when the venue returns it.

OWNER DIRECTION, 2026-11-11: "Reuse capital: completed pairs should not
permanently consume the ability to trade if the risk and cash accounting
permit another round."

WHAT THE VENUE DOES, read from its own balance (`/v1/account/balances`):

    2026-11-11 0030z   340.42775  before
                       340.33975  UP 0.20 @ 0.44 filled        (-0.0880)
                       340.43375  DOWN 0.20 @ 0.53 filled, 00:31:31 -- all of
                                  it back plus the pair's +0.0060, fourteen
                                  minutes before the 00:45 settlement
    2026-11-11 0045z   340.43375 -> 340.34375 -> 340.44375, the same shape

DOWN is a sale of the contract UP buys, so equal quantities are a flat
position and the venue holds nothing against them. The ledger's `committed`
keeps the pair's full cost until settlement, and the cash gate read that as
cash deployed: four pairs of 0.20 "spent" $0.76 of a $0.90 cap while the
account held all of it.

`L5_CASH_POLICY=RECYCLE_ON_COMPLETE`, exactly, releases the MATCHED part of
a market's cost -- never the unmatched remainder, never an unattested
basis, never a settled market twice. Off by default. The ceiling, the
exposure bound, the unmatched cap and every loss gate are untouched.
"""
from decimal import Decimal as D

import pytest

import tests.test_l5_ladder_runner as LAD
from src import l5_live_validation_gates as VG
from src import l5_unmatched_disposition as UD

SLUG = LAD.SLUG
BASE = {"L5_MAX_CASH_DEPLOYED_USD": "0.90", "L5_TS_ROUND_ALLOCATIONS": "3",
        "L5_TS_ROUND_CHARGED_ON_SUBMISSION": "ON",
        "L5_REOPEN_FLOOR_PER_CONTRACT": "ON", "L5_HEDGE_CASH_AT_LIMIT": "ON",
        UD.OWN_EVIDENCE_GATE_ENV: "WAIVED_BY_OWNER"}
RECYCLE = {"L5_CASH_POLICY": "RECYCLE_ON_COMPLETE"}


class VenueCash:
    """The venue's buying power, computed from the VENUE'S OWN fills.

    `nets=True` is what the venue was observed to do: a DOWN fill against
    UP inventory closes it, so matched quantity pays its dollar at once.
    `nets=False` is a venue that keeps every fill's cash until settlement.
    """

    def __init__(self, h, start="340.44375", nets=True):
        self.h, self.start, self.nets = h, D(start), nets
        self.unreadable = False

    def value(self):
        q = {"UP": D(0), "DOWN": D(0)}
        c = D(0)
        for oid, i in self.h.venue.by_oid.items():
            f = D(str(self.h.venue.cum[oid]))
            q[str(i["side"])] += f
            c += f * D(str(i["price"]))
        return self.start - c + (min(q.values()) if self.nets else D(0))

    def account(self):
        if self.unreadable:
            raise ConnectionError("balances unavailable")
        return {"balances": [{"buyingPower": str(self.value())}]}


def with_venue_cash(h, nets=True):
    """Give the harness venue a balance and read it where the main loop
    does: after every tick, self-throttled by the runner."""
    h.cash = VenueCash(h, nets=nets)
    h.venue.account = h.cash.account
    base = h.tick

    def tick(offset_s, poll=True):
        out = base(offset_s, poll)
        h.r.refresh_available_cash(h.r.transport, h.t)
        return out
    h.tick = tick
    # the process runs between windows too: a reading taken while the
    # account is flat and nothing is at the venue is the anchor
    h.at(1.0)
    h.r.refresh_available_cash(h.r.transport, h.t)
    return h


def harness(tmp_path, monkeypatch, nets=True, **env):
    monkeypatch.delenv("L5_CASH_POLICY", raising=False)
    h = LAD.harness(tmp_path, monkeypatch, **dict(BASE, **env))
    LAD.seed(h, 30, D("-0.02"))          # the losing record; the gate waived
    h.book.set("0.50", "0.51")
    return with_venue_cash(h, nets)


def read_cash(h):
    """One fresh balance read, as the main loop would take it."""
    h.r.available_cash_t = None
    h.r.refresh_available_cash(h.r.transport, h.t)


def held(h):
    ex = h.r.executor
    return ex.validation_gates.cash_committed(ex.cash_legs_for_gates())


def run(h, until=600.0, fill=lambda h: LAD.resting_ids(h), watch=None):
    h.at(6.5).l5_action()
    off = 6.8
    while off < until:
        for oid in fill(h):
            h.venue.fill(oid)
        off += 3.5
        h.tick(off)
        if watch is not None:
            watch(h)
    return h


# ======================================================================
# 1. OFF UNLESS THE OWNER'S VARIABLE SAYS SO
# ======================================================================

class TestOffByDefault:

    def test_the_policy_is_no_recycling(self, tmp_path, monkeypatch):
        h = harness(tmp_path, monkeypatch)
        assert h.r.executor.validation_gates.cash_policy == VG.NO_RECYCLING
        assert h.r.executor.cash_recycles_completed_pairs() is False

    @pytest.mark.parametrize("value", ["", "ON", "1", "recycle_on_complete",
                                       "RECYCLE", "YES"])
    def test_only_the_exact_token_turns_it_on(self, tmp_path, monkeypatch,
                                              value):
        h = harness(tmp_path, monkeypatch, L5_CASH_POLICY=value)
        assert h.r.executor.validation_gates.cash_policy == VG.NO_RECYCLING

    def test_as_deployed_four_pairs_then_the_cash_cap(self, tmp_path,
                                                      monkeypatch):
        h = run(harness(tmp_path, monkeypatch))
        assert len(h.venue.sent) == 8
        assert held(h) == D("0.76")                 # four pairs' full cost
        assert LAD.reasons(h, "CASH_COMMITMENT")
        assert not any(l["closing"]
                       for l in h.r.executor.cash_legs_for_gates())

    def test_the_heartbeat_reports_the_policy_the_process_uses(
            self, tmp_path, monkeypatch):
        assert harness(tmp_path, monkeypatch).r.effective_cash_gate()[
            "cash_policy"] == "NO_RECYCLING"
        assert harness(tmp_path, monkeypatch, **RECYCLE
                       ).r.effective_cash_gate()[
            "cash_policy"] == "RECYCLE_ON_COMPLETE"


# ======================================================================
# 2. WHAT IS RELEASED, AND WHAT IS NOT
# ======================================================================

class TestOnlyTheMatchedPart:

    def one_leg(self, h, side):
        h.at(6.5).l5_action()
        for off in (6.8, 7.8, 8.8):
            h.tick(off)
        h.venue.fill(LAD.resting_ids(h, side)[0])
        LAD.book_fills(h, 12)
        return h

    def test_one_filled_leg_releases_nothing(self, tmp_path, monkeypatch):
        h = self.one_leg(harness(tmp_path, monkeypatch, **RECYCLE), "UP")
        assert h.pos("UP") == D("0.20") and h.pos("DOWN") == 0
        assert h.r.executor.matched_pair_cost(SLUG) == 0
        assert held(h) == D("0.20") * D("0.48")

    def test_a_completed_pair_releases_its_whole_cost(self, tmp_path,
                                                      monkeypatch):
        h = harness(tmp_path, monkeypatch, **RECYCLE)
        h.at(6.5)
        read_cash(h)                                # flat: the anchor
        st = h.r.store
        st.apply_fill(SLUG, "UP", D("0.20"), D("0.48"), basis="OWN")
        st.apply_fill(SLUG, "DOWN", D("0.20"), D("0.47"), basis="OWN")
        ex = h.r.executor
        assert ex.matched_pair_cost(SLUG) == D("0.190")
        assert held(h) == D("0.190")                # not yet confirmed
        read_cash(h)
        assert held(h) == 0
        credit = [l for l in ex.cash_legs_for_gates() if l["closing"]]
        assert [l["cash_out"] for l in credit] == [D("-0.190")]
        assert credit[0]["basis"] == "VENUE_BUYING_POWER_CONFIRMED_PAIR"

    def ledger(self, h, up="0.20", dn="0.20"):
        """Anchor while flat, then book fills straight into the ledger."""
        h.at(6.5)
        read_cash(h)
        st = h.r.store
        st.apply_fill(SLUG, "UP", D(up), D("0.48"), basis="OWN")
        st.apply_fill(SLUG, "DOWN", D(dn), D("0.47"), basis="OWN")
        return st

    def test_the_unmatched_remainder_keeps_its_full_charge(self, tmp_path,
                                                           monkeypatch):
        h = harness(tmp_path, monkeypatch, **RECYCLE)
        self.ledger(h, up="0.50")
        read_cash(h)
        # matched 0.20: 0.20 * 0.48 + 0.20 * 0.47; unmatched UP 0.30 * 0.48
        assert h.r.executor.matched_pair_cost(SLUG) == D("0.190")
        assert held(h) == D("0.30") * D("0.48")

    def test_an_unattested_basis_releases_nothing(self, tmp_path,
                                                  monkeypatch):
        h = harness(tmp_path, monkeypatch, **RECYCLE)
        st = self.ledger(h)
        monkeypatch.setattr(type(st), "basis_attestation",
                            lambda self, m, s: {"ok": s != "DOWN"})
        read_cash(h)
        assert h.r.executor.matched_pair_cost(SLUG) is None
        assert held(h) == D("0.190")

    def test_an_unreadable_position_releases_nothing(self, tmp_path,
                                                     monkeypatch):
        h = harness(tmp_path, monkeypatch, **RECYCLE)
        st = self.ledger(h)
        read_cash(h)
        assert held(h) == 0

        def boom(self, m, s):
            raise OSError("ledger unreadable")
        monkeypatch.setattr(type(st), "position", boom)
        assert h.r.executor.matched_pair_cost(SLUG) is None
        assert h.r.executor.recycle_confirmed_share(SLUG) == 0
        assert held(h) == D("0.190")

    def test_the_credit_never_exceeds_what_the_market_was_charged(
            self, tmp_path, monkeypatch):
        h = harness(tmp_path, monkeypatch, **RECYCLE)
        st = self.ledger(h)
        st.state["committed"][SLUG] = "0.05"        # a smaller charge
        read_cash(h)
        assert held(h) == 0                         # clamped, not negative

    def test_a_settled_market_is_not_released_twice(self, tmp_path,
                                                    monkeypatch):
        h = harness(tmp_path, monkeypatch, **RECYCLE)
        st = self.ledger(h)
        read_cash(h)
        st.state.setdefault("retired_markets", {})[SLUG] = {
            "t": "x", "settlement_pnl": "0.01"}
        legs = h.r.executor.cash_legs_for_gates()
        assert [l for l in legs if l.get("market") == SLUG and l["closing"]
                ] == []
        assert held(h) == 0                 # settled: skipped, as before


# ======================================================================
# 3. THROUGH THE RUNNER: PAIRS KEEP OPENING, THE CAP KEEPS BINDING
# ======================================================================

class TestThroughTheRunner:

    def test_eight_rounds_are_eight_pairs(self, tmp_path, monkeypatch):
        peak = []
        h = run(harness(tmp_path, monkeypatch, **RECYCLE),
                watch=lambda h: peak.append(held(h)))
        rl = h.r.executor.round_lifecycle(SLUG)
        assert rl["rounds_with_a_submitted_order"] == 8
        assert len(h.venue.sent) == 16
        assert h.pos("UP") == h.pos("DOWN") == D("1.60")
        assert max(peak) <= D("0.90")
        assert not LAD.reasons(h, "CASH_COMMITMENT")
        assert LAD.reasons(h, "ACCUMULATION_EXHAUSTED")

    def test_with_more_rounds_permitted_it_keeps_going(self, tmp_path,
                                                       monkeypatch):
        """THE ROUND LIMIT IS THE OWNER'S VARIABLE. At 32 the same $0.90
        turns over $6.08 of orders, and never holds more than one pair."""
        peak = []
        h = run(harness(tmp_path, monkeypatch, L5_ACC_MAX_ROUNDS="32",
                        **RECYCLE), watch=lambda h: peak.append(held(h)))
        assert h.r.executor.round_lifecycle(SLUG)[
            "rounds_with_a_submitted_order"] == 32
        assert sum(p * q for _s, p, q in h.sent()) == D("6.08")
        assert max(peak) <= D("0.20") * D("0.48") + D("0.001")
        coids = [s["client_order_id"] for s in h.venue.sent]
        assert len(coids) == len(set(coids)) == 64

    def test_an_unmatched_leg_stops_new_pairs_and_keeps_its_charge(
            self, tmp_path, monkeypatch):
        """Only UP ever fills. The second leg of that round is worked; no
        further pair is opened behind an unhedged clip, recycling or not."""
        h = run(harness(tmp_path, monkeypatch, **RECYCLE), until=200.0,
                fill=lambda h: LAD.resting_ids(h, "UP"))
        assert h.pos("UP") == D("0.20") and h.pos("DOWN") == 0
        assert h.r.executor.round_lifecycle(SLUG)[
            "rounds_with_a_submitted_order"] == 1
        assert {s for s, _p, _q in h.sent()} == {"UP", "DOWN"}
        assert sum(1 for s, _p, _q in h.sent() if s == "UP") == 1
        assert held(h) == D("0.20") * D("0.48")

    def test_every_pair_still_clears_the_opening_ceiling(self, tmp_path,
                                                         monkeypatch):
        h = run(harness(tmp_path, monkeypatch, **RECYCLE))
        ups = [p for s, p, _q in h.sent() if s == "UP"]
        dns = [p for s, p, _q in h.sent() if s == "DOWN"]
        assert len(ups) == len(dns) == 8
        assert all(u + d <= D("0.96") for u, d in zip(ups, dns))
