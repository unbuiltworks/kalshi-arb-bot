"""The missing leg RESTS at the ceiling instead of being refused.

WHAT HAPPENED, 2026-11-11 04:00Z and 04:15Z. One side filled inside half a
minute, the market ran away from the other, and for 462 seconds of each
window the controller proposed the missing leg AT THE TOUCH -- a pair of
1.11 to 1.35 -- and the submission gate refused it 365 times. Not one quote
rested. L5, over the same two windows, was never without a working order:
72 and 62 fills, in 14 and 13 of the 15 minutes.

`L5_TS_COMPLETION_RESTS_AT_CEILING=ON` (default off) rests the missing leg
at the dearest price the EXISTING ceilings permit: the completion ceiling
`complement_order` already computes and the owner's completion pair ceiling
(`L5_COMPLETION_PAIR_CEILING`, 1.00). It moves neither. It cannot cross,
because the resting price is strictly cheaper than a quote that did not.

THE PRICE IT RESTS AT IS THE OWNER'S MAKER TARGET
(`L5_TS_COMPLETION_MAX_PAIR`, the 0.96 opening ceiling when unset; the
owner chose 0.98 on 2026-11-11), never above the hard completion pair
ceiling of $1.00.

These tests pin: off is today's behaviour exactly; on never prices a pair
above the target or the hard ceiling; and the real runner goes from no
order to one resting hedge that completes the pair when the market comes
back.
"""
from decimal import Decimal as D

import pytest

import tests.test_l5_continuous_config as CC
import tests.test_l5_ladder_runner as LAD
from src import l5_two_sided_probe as TS

ENV = TS.COMPLETION_RESTS_AT_CEILING_ENV
TARGET = TS.COMPLETION_MAX_PAIR_ENV
TICK = D("0.01")


def comp(side, held_side, hq, hc, bid, ask, **over):
    kw = dict(side=side, qty=D(hq), cost_px=D(hc) / D(hq), held_qty=D(hq),
              held_cost_usd=D(hc), deficient_qty=D(0),
              deficient_cost_usd=D(0), residual_budget_usd=D("1.00"),
              bid=D(bid), ask=D(ask), tick=TICK, min_qty=D("0.01"),
              depth_ticks=0, book_age_s=D("0.5"), gap_suspected=False,
              market="m", held_side=held_side, bid_shares=D(500),
              ask_shares=D(500))
    kw.update(over)
    return TS.complement_order(**kw)


#: the two inventories of 2026-11-11, at the book each sat against
W0400 = ("UP", "DOWN", "1.18", "0.5508", "0.88", "0.89")
W0415 = ("DOWN", "UP", "0.44", "0.1408", "0.20", "0.21")


# ======================================================================
# 1. OFF IS TODAY, EXACTLY
# ======================================================================
class TestOffChangesNothing:

    @pytest.mark.parametrize("raw", [None, "", "on", "1", "true", "ON ",
                                     " ON", "YES"])
    def test_only_the_exact_token_turns_it_on(self, monkeypatch, raw):
        if raw is None:
            monkeypatch.delenv(ENV, raising=False)
        else:
            monkeypatch.setenv(ENV, raw)
        assert TS.completion_rests_at_ceiling() is False

    def test_the_exact_token(self, monkeypatch):
        monkeypatch.setenv(ENV, "ON")
        assert TS.completion_rests_at_ceiling() is True

    @pytest.mark.parametrize("state,px", [(W0400, "0.88"), (W0415, "0.79")])
    def test_off_still_prices_the_leg_at_the_touch(self, monkeypatch,
                                                   state, px):
        monkeypatch.delenv(ENV, raising=False)
        r = comp(*state)
        assert r["ok"] and r["leg"]["price"] == px
        assert r["judged_as"] == "COMPLETION"
        assert r["rested_behind_quote_ticks"] is None
        assert D(r["pair_cost"]) > D("1.00")      # what the gate refused

    def test_off_and_on_agree_wherever_the_touch_is_inside_the_target(
            self, monkeypatch):
        monkeypatch.setenv(TARGET, "1.00")
        seen = 0
        for bid in ("0.30", "0.40", "0.48", "0.52"):
            ask = str(D(bid) + TICK)
            monkeypatch.delenv(ENV, raising=False)
            off = comp("UP", "DOWN", "1.18", "0.5508", bid, ask)
            monkeypatch.setenv(ENV, "ON")
            on = comp("UP", "DOWN", "1.18", "0.5508", bid, ask)
            assert off == on
            seen += 1
        assert seen == 4


# ======================================================================
# 2. ON: THE DEAREST PRICE THE EXISTING CEILINGS PERMIT, AND NO DEARER
# ======================================================================
class TestOnRestsAtTheCeiling:

    def test_0400z_at_a_one_dollar_target(self, monkeypatch):
        monkeypatch.setenv(ENV, "ON")
        monkeypatch.setenv(TARGET, "1.00")
        r = comp(*W0400)
        assert r["ok"] and r["judged_as"] == "COMPLETION_RESTING_AT_CEILING"
        assert r["leg"]["price"] == "0.53"        # 1.00 - 0.4668, rounded down
        assert r["leg"]["post_only"] is True
        assert r["rested_behind_quote_ticks"] == 35
        assert D("0.99") < D(r["pair_cost"]) <= D("1.00")
        assert D(r["up_pnl"]) >= 0 and D(r["dn_pnl"]) >= 0

    def test_0415z_at_a_one_dollar_target(self, monkeypatch):
        monkeypatch.setenv(ENV, "ON")
        monkeypatch.setenv(TARGET, "1.00")
        r = comp(*W0415)
        assert r["ok"] and r["leg"]["side"] == "DOWN"
        assert r["leg"]["price"] == "0.68" and D(r["pair_cost"]) == D("1.00")
        assert r["leg"]["wire_price"] == "0.32"   # a YES offer above the ask

    def test_the_owners_target_of_98_cents(self, monkeypatch):
        """OWNER, 2026-11-11: 0.98 for maker completion, $1.00 the hard
        ceiling."""
        monkeypatch.setenv(ENV, "ON")
        monkeypatch.setenv(TARGET, "0.98")
        assert TS.maker_completion_target() == D("0.98")
        assert TS.completion_pair_ceiling() == D("1.00")
        a, b = comp(*W0400), comp(*W0415)
        assert a["leg"]["price"] == "0.51" and D(a["pair_cost"]) <= D("0.98")
        assert b["leg"]["price"] == "0.66" and D(b["pair_cost"]) == D("0.98")

    def test_with_no_target_named_it_is_the_opening_ceiling(
            self, monkeypatch):
        monkeypatch.setenv(ENV, "ON")
        monkeypatch.delenv(TARGET, raising=False)
        assert TS.maker_completion_target() == D("0.96")
        r = comp(*W0400)
        assert r["leg"]["price"] == "0.49" and D(r["pair_cost"]) <= D("0.96")

    def test_a_touch_between_the_target_and_one_dollar_rests_at_the_target(
            self, monkeypatch):
        """UP offered where the pair would be 0.9968: inside the hard
        ceiling, above the maker target. The quote rests at the target."""
        monkeypatch.setenv(ENV, "ON")
        monkeypatch.setenv(TARGET, "0.98")
        r = comp("UP", "DOWN", "1.18", "0.5508", "0.53", "0.54")
        assert r["leg"]["price"] == "0.51"
        assert r["judged_as"] == "COMPLETION_RESTING_AT_CEILING"
        monkeypatch.delenv(ENV, raising=False)
        off = comp("UP", "DOWN", "1.18", "0.5508", "0.53", "0.54")
        assert off["leg"]["price"] == "0.53"      # today: at the touch

    def test_the_hard_ceiling_caps_the_target(self, monkeypatch):
        monkeypatch.setenv(ENV, "ON")
        monkeypatch.setenv(TARGET, "1.00")
        monkeypatch.setenv(TS.COMPLETION_PAIR_CEILING_ENV, "0.98")
        r = comp(*W0400)
        assert r["leg"]["price"] == "0.51"
        assert D(r["pair_cost"]) <= D("0.98")

    def test_it_cannot_be_set_above_one_dollar(self, monkeypatch):
        monkeypatch.setenv(ENV, "ON")
        monkeypatch.setenv(TARGET, "1.05")
        monkeypatch.setenv(TS.COMPLETION_PAIR_CEILING_ENV, "1.05")
        r = comp(*W0400)
        assert D(r["pair_cost"]) <= D("1.00")

    def test_the_one_recovery_switch_turns_it_on_too(self, monkeypatch):
        monkeypatch.delenv(ENV, raising=False)
        monkeypatch.setenv(TS.INVENTORY_RECOVERY_ENV, "ON")
        monkeypatch.setenv(TARGET, "0.98")
        assert TS.completion_rests_at_ceiling() is True
        assert comp(*W0400)["leg"]["price"] == "0.51"

    def test_never_a_pair_above_the_ceiling_and_never_a_crossing_price(
            self, monkeypatch):
        """Every first-leg cost against every book, both sides."""
        monkeypatch.setenv(ENV, "ON")
        monkeypatch.setenv(TARGET, "0.98")
        tgt = TS.maker_completion_target()
        n = rested = 0
        for paid_c in range(6, 95, 4):
            paid = D(paid_c) / 100
            for bid_c in range(6, 94, 3):
                bid, ask = D(bid_c) / 100, D(bid_c + 1) / 100
                for side, held in (("UP", "DOWN"), ("DOWN", "UP")):
                    r = comp(side, held, "1.00", str(paid), str(bid),
                             str(ask))
                    n += 1
                    if not r.get("ok"):
                        continue
                    px = D(r["leg"]["price"])
                    touch = bid if side == "UP" else D(1) - ask
                    assert px <= touch                # never through the book
                    assert not TS.leg_crosses(side, px, bid=bid, ask=ask)
                    if r["judged_as"] == "COMPLETION_RESTING_AT_CEILING":
                        rested += 1
                        assert px < touch
                        assert paid + px <= tgt <= TS.completion_pair_ceiling()
                        # and one tick dearer would have passed the target
                        assert paid + px + TICK > tgt \
                            or px + TICK > D(r["completion_ceiling"])
                    else:
                        # the touch itself was permitted: nothing moved
                        assert px == touch
        assert n > 1000 and rested > 200

    def test_without_the_inventory_it_is_still_a_refusal(self, monkeypatch):
        monkeypatch.setenv(ENV, "ON")
        r = comp(*W0400, held_qty=None, held_cost_usd=None,
                 residual_budget_usd=None)
        assert r["ok"] is False
        assert r["reason"] == "COMPLEMENT_PAIR_ABOVE_CAP"
        assert r["completion_rescue_available"] is False

    def test_a_first_leg_too_dear_to_complete_is_not_given_an_order(
            self, monkeypatch):
        """Held at 0.97: the only permitted price is under the engine's
        five-cent band, so nothing new is quoted."""
        monkeypatch.setenv(ENV, "ON")
        on = comp("UP", "DOWN", "1.00", "0.97", "0.40", "0.41")
        monkeypatch.delenv(ENV, raising=False)
        off = comp("UP", "DOWN", "1.00", "0.97", "0.40", "0.41")
        assert on == off

    @pytest.mark.parametrize("bad", [dict(book_age_s=D("9")),
                                     dict(gap_suspected=True)])
    def test_a_book_that_cannot_be_trusted_still_refuses(self, monkeypatch,
                                                         bad):
        monkeypatch.setenv(ENV, "ON")
        assert comp(*W0400, **bad)["ok"] is False

    def test_a_locked_book_still_refuses(self, monkeypatch):
        monkeypatch.setenv(ENV, "ON")
        r = comp("UP", "DOWN", "1.18", "0.5508", "0.60", "0.60")
        assert r["ok"] is False and r["reason"] == "BOOK_CROSSED_OR_LOCKED"

    def test_the_helper_rounds_down_and_refuses_what_it_cannot_price(self):
        f = TS.rest_at_ceiling_price
        assert f(D("0.5332"), D("0.88"), TICK) == D("0.53")
        assert f(D("0.53"), D("0.53"), TICK) is None     # nothing behind it
        assert f(D("0.60"), D("0.53"), TICK) is None
        assert f(D("0.04"), D("0.53"), TICK) is None     # under the band
        assert f(None, D("0.53"), TICK) is None
        assert f(D("0.53"), D("0.88"), D("0")) is None


# ======================================================================
# 3. THE REAL RUNNER: FROM NO ORDER TO ONE WORKING HEDGE
# ======================================================================
CFG = dict(CC.FOUR, PMUS_RESIDUAL_CAP_K="4", **CC.STEER)
CFG[TARGET] = "0.98"                      # the owner's maker target
T98 = D("0.98")


def stranded(tmp_path, monkeypatch, on, away=("0.88", "0.89"),
             fill="DOWN"):
    """Open, let one side fill whole, and send the market away."""
    env = dict(CFG)
    if on:
        env[ENV] = "ON"
    h = CC.steered(CC.harness(tmp_path, monkeypatch, env))
    h.at(6.5).l5_action()
    off = 6.8
    for _ in range(4):
        off += 1.0
        h.tick(off)
    for oid in LAD.resting_ids(h, fill):
        h.venue.fill(oid)
    off += 3.0
    h.tick(off)
    h.book.set(*away)
    n0 = len(h.venue.sent)
    for _ in range(60):
        off += 5.0
        h.tick(off)
    return h, off, n0


def refused(h):
    return sum(v for k, v in h.r.stats.items()
               if "PAIR_COST_ABOVE_CEILING" in k
               or k == "book_completion:PAIR_ABOVE_REDEMPTION")


class TestInTheRunner:

    def test_off_reproduces_the_night_no_order_for_five_minutes(
            self, tmp_path, monkeypatch):
        h, _off, n0 = stranded(tmp_path, monkeypatch, on=False)
        assert h.pos("DOWN") > D("1.5") and h.pos("UP") == 0
        assert len(h.venue.sent) == n0             # nothing was sent
        assert LAD.resting_ids(h) == []
        assert refused(h) >= 60

    def test_on_one_hedge_rests_at_the_ceiling(self, tmp_path, monkeypatch):
        h, _off, n0 = stranded(tmp_path, monkeypatch, on=True)
        held = h.pos("DOWN")
        avg = h.r.store.leg_cost(CC.SLUG)["DOWN"] / held
        after = h.sent()[n0:]
        assert after and all(s == "UP" for s, _p, _q in after)
        for _s, p, q in after:
            assert avg + p <= T98                  # the owner's target
            assert avg + p + TICK > T98            # and the dearest such
            assert q == held                       # exactly the imbalance
            assert p < D("0.88")                   # behind the touch
        assert len(LAD.resting_ids(h, "UP")) == 1  # one hedge, not a stack
        assert LAD.resting_ids(h, "DOWN") == []
        assert h.r.store.orders_uncertain() is None
        assert refused(h) <= 2
        assert CC.worst_loss(h) <= CC.DEFICIT + CC.CENT

    def test_on_the_pair_completes_when_the_market_comes_back(
            self, tmp_path, monkeypatch):
        h, off, _n0 = stranded(tmp_path, monkeypatch, on=True)
        h.book.set("0.50", "0.51")
        for oid in LAD.resting_ids(h, "UP"):
            h.venue.fill(oid)
        h.tick(off + 3.0)
        assert h.pos("UP") == h.pos("DOWN") > D("1.5")
        lc = h.r.store.leg_cost(CC.SLUG)
        assert (lc["UP"] + lc["DOWN"]) / h.pos("UP") <= T98
        assert h.r.store.orders_uncertain() is None

    def test_on_the_mirror_a_stranded_up_leg_gets_a_down_hedge(
            self, tmp_path, monkeypatch):
        h, _off, n0 = stranded(tmp_path, monkeypatch, on=True,
                               away=("0.12", "0.13"), fill="UP")
        held = h.pos("UP")
        avg = h.r.store.leg_cost(CC.SLUG)["UP"] / held
        after = h.sent()[n0:]
        assert after and all(s == "DOWN" for s, _p, _q in after)
        for _s, p, _q in after:
            assert avg + p <= T98 and p < D("0.87")
        assert len(LAD.resting_ids(h, "DOWN")) == 1

    def test_on_the_hedge_is_cancelled_with_everything_else_at_the_terminal(
            self, tmp_path, monkeypatch):
        h, off, _n0 = stranded(tmp_path, monkeypatch, on=True)
        while off < 640.0:
            off += 10.0
            h.tick(off)
        assert LAD.resting_ids(h) == []
        assert h.r.store.orders_uncertain() is None
        n = len(h.venue.sent)
        h.tick(off + 10.0)
        assert len(h.venue.sent) == n              # and nothing after it
