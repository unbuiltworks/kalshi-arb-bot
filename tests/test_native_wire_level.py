"""Wire-level correctness of the native money lane, and the real entrypoint.

Four independently-found gaps in 11c207a, each with a negative control.
"""
from __future__ import annotations

import ast
import json
from decimal import Decimal as D

import pytest

import scripts.l5_pmus_live_oneshot as EP
from src import l5_order_body as OB
from src import pmus_l5_native_engine as E
from src import pmus_native_money_path as MP
from src import pmus_live_mandate as MANDATE

MARKET = "cpc-btc-updown-15m-2026-09-28-1600z"


# 1 -------------------------------------------------- DOWN price, once only
@pytest.mark.parametrize("kind,post_only", [(E.REST, True),
                                            (E.COMPLETE, False),
                                            (E.SECURE, False)])
@pytest.mark.parametrize("side,own,expect_wire", [("up", "0.40", "0.40"),
                                                  ("down", "0.40", "0.60"),
                                                  ("up", "0.75", "0.75"),
                                                  ("down", "0.75", "0.25")])
def test_own_terms_price_is_converted_exactly_once(kind, post_only, side,
                                                   own, expect_wire):
    if kind == E.REST:
        it = E.Intent(E.REST, side=side, price=D(own), qty=D(5))
    else:
        it = E.Intent(kind, side=side, qty=D(5), cost=D(own) * 5,
                      fee=D(0), limit_price=D(own))
    body = MP.body_for(it, MARKET)
    assert body["price"]["value"] == expect_wire, body
    assert body["participateDontInitiate"] is post_only
    assert body["outcomeSide"] == MP.SIDE_WIRE[side]


def test_the_builder_is_the_only_serializer_and_owns_the_inversion():
    """Negative control: pre-converting would invert DOWN twice."""
    pre = OB.venue_price("OUTCOME_SIDE_NO", D("0.40"))          # 0.60
    twice = OB.build(slug=MARKET, side="OUTCOME_SIDE_NO", price=pre,
                     qty=D(1), post_only=True)["price"]["value"]
    assert twice == "0.40", "premise wrong"
    once = MP.body_for(E.Intent(E.REST, side="down", price=D("0.40"),
                                qty=D(1)), MARKET)["price"]["value"]
    assert once == "0.60" and once != twice


# 2 ------------------------------------------- multi-level taker limit price
def test_the_taker_limit_is_the_worst_admitted_level_not_the_vwap():
    """5 @ .30 + 5 @ .40 averages .35, and a .35 limit cannot reach .40."""
    levels = [["0.30", "5"], ["0.40", "5"]]
    worst = E.worst_admitted_price(levels, D(10), D("0.10"), D("0.96"))
    assert worst == D("0.40"), worst
    vwap = (D(5) * D("0.30") + D(5) * D("0.40")) / D(10)
    assert vwap == D("0.35") and worst > vwap


def test_the_limit_never_admits_a_level_theta_rejected():
    """The 0.90 level fails basis+px+fee <= theta and must not be reachable."""
    levels = [["0.30", "5"], ["0.90", "5"]]
    worst = E.worst_admitted_price(levels, D(10), D("0.10"), D("0.96"))
    assert worst == D("0.30"), worst


@pytest.mark.parametrize("side,expect", [("up", "0.40"), ("down", "0.60")])
def test_a_multi_level_completion_sends_the_worst_level_on_both_sides(side,
                                                                      expect):
    it = E.Intent(E.COMPLETE, side=side, qty=D(10), cost=D("3.50"),
                  fee=D("0.10"), limit_price=D("0.40"))
    body = MP.body_for(it, MARKET)
    assert body["price"]["value"] == expect
    assert body["tif"] == OB.TIF_IOC, "a taker leg must not rest"


def test_the_floor_securing_walk_reports_its_own_worst_level():
    levels = [["0.30", "5"], ["0.95", "5"]]
    assert E.worst_walked_price(levels, D(10)) == D("0.95")
    assert E.worst_walked_price(levels, D(5)) == D("0.30")


def test_an_intent_without_a_limit_price_refuses_to_build():
    it = E.Intent(E.COMPLETE, side="up", qty=D(1), cost=D("0.5"), fee=D(0))
    with pytest.raises(ValueError, match="no executable limit"):
        MP.body_for(it, MARKET)


# 3 --------------------------------------------- partial taker / remainder
class Venue:
    def __init__(self, fill=None, open_after=None):
        self.fill = fill
        self.open_after = open_after if open_after is not None else []
        self.cancelled = []
        self.raise_open = False

    def cancel_resting(self, side):
        pass

    def cancel_order(self, oid):
        self.cancelled.append(oid)

    def open_orders(self):
        if self.raise_open:
            raise RuntimeError("unreadable")
        return list(self.open_after)

    def unbooked_fills(self):
        return []

    def completion_fill(self, intent):
        return self.fill


class TP:
    def __init__(self):
        self.posts = []

    def submit_body(self, body):
        """The seam the PRODUCTION path uses.

        These doubles only had `request`, which is exactly why the
        2345z window armed and then died: `PmusTransport` has no
        public `request`, and every test here mocked the boundary
        instead of crossing it. Delegating keeps each subclass's
        recorded behaviour (including refusals) in one place.
        """
        return self.request("POST", "/v1/orders", body)

    def request(self, method, path, body=None, **kw):
        self.posts.append(body)
        return {"orderId": "o%d" % len(self.posts)}


def _mp(venue):
    return MP.build(market=MARKET, transport=TP(), feed=None, venue=venue)


def _intent():
    return E.Intent(E.COMPLETE, side="down", held="up", comp="down", qty=D(10),
                    cost=D("2.00"), fee=D("0.05"), basis=D("0.40"),
                    now=0.0, limit_price=D("0.20"))


def test_a_full_fill_proceeds():
    mp = _mp(Venue(fill=(D(10), D("2.00"), D("0.05"))))
    assert mp._execute_taker(_intent()) == (D(10), D("2.00"), D("0.05"))


def test_a_partial_fill_with_a_live_remainder_fails_closed():
    """THE NEGATIVE CONTROL. Remainder still open -> nothing may proceed."""
    v = Venue(fill=(D(6), D("1.20"), D("0.03")), open_after=["o1"])
    mp = _mp(v)
    assert mp._execute_taker(_intent()) is None, "proceeded on a live remainder"
    assert v.cancelled == ["o1"], "the remainder was never cancelled"
    assert mp.remainders[-1]["why"] == "REMAINDER_STILL_LIVE"


def test_a_partial_fill_whose_remainder_is_confirmed_gone_proceeds():
    v = Venue(fill=(D(6), D("1.20"), D("0.03")), open_after=[])
    mp = _mp(v)
    assert mp._execute_taker(_intent()) == (D(6), D("1.20"), D("0.03"))
    assert mp.remainders[-1]["resolved"] is True


def test_an_unreadable_remainder_state_fails_closed():
    v = Venue(fill=(D(6), D("1.20"), D("0.03")))
    v.raise_open = True
    mp = _mp(v)
    assert mp._execute_taker(_intent()) is None
    assert mp.remainders[-1]["why"] == "OPEN_ORDERS_UNREADABLE"


def test_unknown_fill_state_fails_closed_and_books_nothing():
    mp = _mp(Venue(fill=None))
    assert mp._execute_taker(_intent()) is None
    assert mp.remainders[-1]["why"] == "FILL_STATE_UNKNOWN"


def test_an_ack_alone_never_counts_as_a_fill():
    mp = _mp(Venue(fill=(D(0), D(0), D(0))))
    assert mp._execute_taker(_intent()) is None, "an ACK was treated as a fill"
    assert mp.eng.bk.taker_qty == D(0)


# 4 ------------------------------------------------ the REAL entrypoint
def test_the_executable_reaches_the_native_money_path():
    tree = ast.parse(open("scripts/l5_pmus_live_oneshot.py").read())
    txt = open("scripts/l5_pmus_live_oneshot.py").read()
    assert "pmus_native_money_path" in txt and "pmus_native_venue" in txt
    assert hasattr(EP, "native_work_window") and hasattr(EP,
                                                         "native_engine_enabled")


def test_the_money_lane_modules_never_reach_the_translator():
    for mod in ("src/pmus_l5_native_engine.py", "src/pmus_native_money_path.py",
                "src/pmus_native_live_overlay.py", "src/pmus_native_venue.py"):
        tree = ast.parse(open(mod).read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.update(node.module.split("."))
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    imported.update(a.name.split("."))
        for banned in ("l5_faithful_translation", "l5_window_actions"):
            assert banned not in imported, (mod, banned)
    E.assert_no_translate_fallback()


def test_the_native_lane_is_selected_by_the_env_flag(monkeypatch):
    monkeypatch.delenv(EP.NATIVE_ENGINE_ENV, raising=False)
    assert EP.native_engine_enabled() is False
    monkeypatch.setenv(EP.NATIVE_ENGINE_ENV, "1")
    assert EP.native_engine_enabled() is True


def test_native_work_window_drives_the_full_chain(monkeypatch, tmp_path):
    """fresh book -> REST -> guarded POST -> ACK -> authenticated fill ->
    inventory -> LOCK transaction, through the real native_work_window."""
    from src import l5_session_journal as JN

    class Feed:
        ladder = {"t": "2026-09-28T16:00:10Z", "bids": [["0.80", "50"]],
                  "offers": []}

        def __init__(self):
            self.n = 0

        def fresh(self):
            return True

        def age_s(self):
            return 0.1

        def latest(self):
            self.n += 1
            return {"state": "MARKET_STATE_OPEN",
                    "t": "2026-09-28T16:00:%02dZ" % min(self.n, 59),
                    "bid": "0.40", "ask": "0.60"}

    class Probe:
        market = MARKET

        def __init__(self, tp):
            self.tp = tp

    venue = Venue(fill=None)
    tp = TP()
    journal = JN.Journal(str(tmp_path / "j.jsonl"))
    t = {"now": 0.0}
    mp = EP.native_work_window(
        Probe(tp), Feed(), 3.0, journal=journal,
        clock=lambda: t.__setitem__("now", t["now"] + 1.0) or t["now"],
        sleep=lambda *_a, **_k: None, venue=venue)

    assert tp.posts, "no order reached the transport"
    assert all(b["participateDontInitiate"] is True for b in tp.posts), (
        "the acquisition legs must be post-only makers")
    assert mp.eng.bk.q == {"up": D(0), "down": D(0)}, (
        "an ACK moved inventory")
    mp.eng.on_fill("up", D(50), D("20.00"), maker=True)
    assert mp.eng.bk.q["up"] == D(50)
    kinds = [json.loads(l)["kind"] for l in
             open(str(tmp_path / "j.jsonl")).read().splitlines() if l.strip()]
    assert "native_tick" in kinds and "native_window_done" in kinds, kinds


# ------------------------------------------------- L5_NATIVE_CLIP: capacity only
#
# The seat exists so a capacity rung is a variable change, not a rebuild. It
# sets HOW MUCH and nothing else.

def test_unset_clip_is_exactly_the_frozen_fifty(monkeypatch):
    monkeypatch.delenv(MP.CLIP_ENV, raising=False)
    assert MP.configured_clip() == D(50) == E.CLIP
    monkeypatch.setenv(MP.CLIP_ENV, "")
    assert MP.configured_clip() == D(50), "empty must mean the frozen default"
    mp = MP.build(market=MARKET, transport=TP(), venue=Venue())
    assert mp.eng.clip == D(50)


@pytest.mark.parametrize("raw,want", [("10", D(10)), ("25", D(25)),
                                      ("50", D(50)), (" 10 ", D(10)),
                                      ("0.5", D("0.5"))])
def test_a_valid_clip_is_taken_verbatim(monkeypatch, raw, want):
    monkeypatch.setenv(MP.CLIP_ENV, raw)
    assert MP.configured_clip() == want


@pytest.mark.parametrize("raw", ["0", "-1", "-0.0001", "nan", "NaN", "inf",
                                 "-inf", "Infinity", "abc", "1,000", "1e",
                                 "None", "5%"])
def test_an_unusable_clip_fails_closed(monkeypatch, raw):
    monkeypatch.setenv(MP.CLIP_ENV, raw)
    with pytest.raises(E.BadClip):
        MP.configured_clip()
    with pytest.raises(E.BadClip):
        MP.build(market=MARKET, transport=TP(), venue=Venue())


def test_clip_changes_sizing_only_not_decisions():
    """Same book, same ladder, two clips: identical decisions, different size."""
    out = {}
    for clip in (D(10), D(50)):
        eng = E.NativeEngine(clip=clip)
        row = {"state": "MARKET_STATE_OPEN", "t": "2026-09-28T16:00:10Z",
               "bid": "0.40", "ask": "0.60"}
        its = eng.on_book(row)
        out[clip] = [(i.kind, i["side"], str(i.get("price")),
                      str(i.get("qty"))) for i in its]
    a, b = out[D(10)], out[D(50)]
    assert [(k, s, p) for k, s, p, _ in a] == [(k, s, p) for k, s, p, _ in b], (
        "clip changed the decision or the price, not just the size")
    assert [q for *_x, q in a] == ["10", "10"]
    assert [q for *_x, q in b] == ["50", "50"]


def test_clip_changes_no_threshold_or_completion_rule():
    for clip in (D(1), D(10), D(50), D(500)):
        eng = E.NativeEngine(clip=clip)
        assert eng.theta == D("0.96")
        assert eng.profit_lock_usd == D(5)
        assert eng.residual_cap_k == D(2)
        assert eng.hold_s == 60.0 and eng.variant == "B"


def test_completion_eligibility_is_independent_of_clip():
    """Same residual and ladder -> same admitted qty and limit, any clip."""
    got = set()
    for clip in (D(10), D(50)):
        eng = E.NativeEngine(clip=clip)
        eng.on_fill("up", D(10), D("4.00"), maker=True)
        its = eng.on_book({"state": "MARKET_STATE_OPEN",
                           "t": "2026-09-28T16:00:10Z",
                           "bid": "0.40", "ask": "0.60"},
                          {"t": "2026-09-28T16:00:10Z",
                           "bids": [["0.80", "10"]], "offers": []})
        c = [i for i in its if i.kind == E.COMPLETE]
        assert c, "no completion at clip %s" % clip
        got.add((str(c[0]["qty"]), str(c[0]["limit_price"])))
    assert len(got) == 1, "clip changed completion eligibility: %s" % got


def test_the_mandate_still_bounds_every_clip():
    """Capacity is not authority. A bigger clip is refused, not allowed."""
    for clip in (D(10), D(50)):
        tp = TP()

        class Bounded(TP):
            def request(self, method, path, body=None, **kw):
                notional = (D(str(body["price"]["value"]))
                            * D(str(body["quantity"])))
                if notional > D("1.00"):
                    raise RuntimeError("MANDATE_REFUSED:%s" % notional)
                return super().request(method, path, body, **kw)

        b = Bounded()
        mp = MP.build(market=MARKET, transport=b, venue=Venue(), clip=clip)
        with pytest.raises(RuntimeError, match="MANDATE_REFUSED"):
            mp.on_book_state({"state": "MARKET_STATE_OPEN",
                              "t": "2026-09-28T16:00:10Z",
                              "bid": "0.40", "ask": "0.60"})
        assert b.posts == []


def test_ioc_and_remainder_behaviour_survive_a_clip_change():
    for clip in (D(10), D(50)):
        it = E.Intent(E.COMPLETE, side="down", qty=clip, cost=D("2.00"),
                      fee=D("0.05"), limit_price=D("0.20"))
        body = MP.body_for(it, MARKET)
        assert body["tif"] == OB.TIF_IOC
        assert body["participateDontInitiate"] is False
    v = Venue(fill=(D(6), D("1.20"), D("0.03")), open_after=["o1"])
    mp = MP.build(market=MARKET, transport=TP(), venue=v, clip=D(10))
    assert mp._execute_taker(_intent()) is None
    assert mp.remainders[-1]["why"] == "REMAINDER_STILL_LIVE"


def test_the_real_entrypoint_honours_the_clip(monkeypatch, tmp_path):
    from src import l5_session_journal as JN

    class Feed:
        ladder = None

        def fresh(self):
            return True

        def age_s(self):
            return 0.1

        def latest(self):
            return {"state": "MARKET_STATE_OPEN", "t": "2026-09-28T16:00:10Z",
                    "bid": "0.40", "ask": "0.60"}

    class Probe:
        market = MARKET

        def __init__(self, tp):
            self.tp = tp

    for raw, want in (("10", "10.0"), ("50", "50.0")):
        monkeypatch.setenv(MP.CLIP_ENV, raw)
        tp = TP()
        t = {"now": 0.0}
        mp = EP.native_work_window(
            Probe(tp), Feed(), 2.0, journal=JN.Journal(str(tmp_path / "j.jsonl")),
            clock=lambda: t.__setitem__("now", t["now"] + 1.0) or t["now"],
            sleep=lambda *_a, **_k: None, venue=Venue())
        assert tp.posts, "no order at clip %s" % raw
        assert all(str(b["quantity"]) == want for b in tp.posts), (
            [b["quantity"] for b in tp.posts])
