"""Intl fair-value guard on OPENING quotes (default off).

The bot never lacked fills (1,004 contracts); the losses were the lone legs
an informed taker picks off while the price is already moving, and
Polymarket intl moves first. The guard posts and keeps an opening bid only
while intl says it is still cheap, pulls it the moment intl moves, never
touches a completion, and fails closed without a fresh intl quote."""
import os
import time
from decimal import Decimal as D

os.environ["PMUS_TWO_SIDED_ENABLED"] = "YES"
from src import pmus_sports_two_sided as M  # noqa: E402
from src import pmus_sports_xv as X  # noqa: E402
from tests.test_pmus_sports_two_sided import FakeVenue  # noqa: E402

T = D("0.01")


def guard(mid=None, slug="aec-x", require=True, age=0.0):
    t = [1000.0]
    g = X.IntlGuard(lambda p: {}, lambda p: [], require=require)
    g.intl = X.IntlBook(clock=lambda: t[0])
    g.resolved[slug] = "TOK"
    if mid is not None:
        g.intl.ingest({"event_type": "book", "asset_id": "TOK",
                       "bids": [{"price": str(round(mid - 0.005, 3)),
                                 "size": "5"}],
                       "asks": [{"price": str(round(mid + 0.005, 3)),
                                 "size": "5"}]})
    t[0] += age
    g._t = t
    return g


def move(g, mid):
    g.intl.ingest({"event_type": "book", "asset_id": "TOK",
                   "bids": [{"price": str(round(mid - 0.005, 3)), "size": "5"}],
                   "asks": [{"price": str(round(mid + 0.005, 3)), "size": "5"}]})


# ------------------------------------------------------------- the verdict
def test_safe_when_intl_agrees_with_pmus():
    v = guard(0.45).verdict("aec-x", {"YES": D("0.42"), "NO": D("0.52")}, T)
    assert v["ok"] and v["why"] == "intl_confirms"


def test_unsafe_yes_bid_when_intl_has_moved_down():
    # PMUS still 0.44/0.46 (our YES bid 0.42) but intl already at 0.40
    v = guard(0.40).verdict("aec-x", {"YES": D("0.42"), "NO": D("0.52")}, T)
    assert not v["ok"] and set(v["unsafe"]) == {"YES"}


def test_unsafe_no_bid_when_intl_has_moved_up():
    v = guard(0.50).verdict("aec-x", {"YES": D("0.42"), "NO": D("0.52")}, T)
    assert not v["ok"] and set(v["unsafe"]) == {"NO"}


def test_fails_closed_without_fresh_intl():
    assert not guard(None).verdict("aec-x", {"YES": D("0.42")}, T)["ok"]
    assert not guard(0.45, age=60).verdict("aec-x", {"YES": D("0.42")}, T)["ok"]
    g = guard(0.45)
    assert not g.verdict("aec-unmatched", {"YES": D("0.42")}, T)["ok"]
    # require off: an unpriceable market falls back to today's behaviour
    assert guard(None, require=False).verdict("aec-x", {"YES": D("0.42")},
                                              T)["ok"]


# ------------------------------------------------------------- engine path
def eng(tmp_path, g):
    v = FakeVenue()
    v.guard = g
    e = M.Engine(v, M.Book(tmp_path / "s.json"))
    return e, v


def test_opening_is_placed_only_when_intl_confirms(tmp_path):
    e, v = eng(tmp_path, guard(0.45))
    e.select(v.account())
    e.step_market("aec-x")
    sides = sorted(o["side"] for o in v.orders.values() if o["open"])
    assert sides == ["NO", "YES"]


def test_opening_withheld_and_market_kept_while_intl_disagrees(tmp_path):
    g = guard(0.40)
    e, v = eng(tmp_path, g)
    g.resolved["aec-x"] = "TOK"
    e.select(v.account())
    e.step_market("aec-x")
    assert not any(o["open"] for o in v.orders.values())
    assert g.stats["blocked"] >= 1
    mk = e.b.m("aec-x")
    mk["idle_since"] = time.time() - 999          # long past the 120s idle
    e.step_market("aec-x")
    assert not mk.get("done")                     # held, not released
    move(g, 0.45)                                 # intl agrees again
    e.step_market("aec-x")
    assert sorted(o["side"] for o in v.orders.values() if o["open"]) == \
        ["NO", "YES"]


def test_intl_move_pulls_resting_opening_pair_before_pmus_moves(tmp_path):
    g = guard(0.45)
    e, v = eng(tmp_path, g)
    e.select(v.account())
    e.step_market("aec-x")
    assert sum(o["open"] for o in v.orders.values()) == 2
    move(g, 0.39)                                 # PMUS book unchanged
    e._on_intl_change("aec-x")
    e.drain_intl_events()
    assert not any(o["open"] for o in v.orders.values())
    assert g.stats["pulled"] == 1


def test_completion_leg_is_never_pulled_by_the_guard(tmp_path):
    g = guard(0.45)
    e, v = eng(tmp_path, g)
    e.select(v.account())
    e.step_market("aec-x")
    yes = next(k for k, o in v.orders.items() if o["side"] == "YES")
    v.orders[yes]["filled"], v.orders[yes]["open"] = D("1"), False
    e.step_market("aec-x")                        # now long YES: completing
    comp = [o for o in v.orders.values() if o["open"]]
    assert comp and all(o["side"] == "NO" for o in comp)
    move(g, 0.30)
    e._on_intl_change("aec-x")
    e.drain_intl_events()
    assert any(o["open"] for o in v.orders.values())


def test_select_opens_only_intl_matched_markets(tmp_path):
    g = guard(0.45)
    g.resolved.pop("aec-x")                       # not yet resolved
    e, v = eng(tmp_path, g)
    e.select(v.account())
    assert "aec-x" not in e.b.d["markets"]
    assert "aec-x" in g._pending                  # queued for matching
    g.resolved["aec-x"] = "TOK"
    e.select(v.account())
    assert "aec-x" in e.b.d["markets"]


def test_default_off(monkeypatch):
    monkeypatch.delenv(M.INTL_GUARD_ENV, raising=False)
    assert M.start_intl_guard(FakeVenue()) is None


def test_guard_object_has_no_order_path():
    g = X.IntlGuard(lambda p: {}, lambda p: [])
    for name in ("place", "cancel", "cancel_fast", "v", "venue", "t"):
        assert not hasattr(g, name)


def test_track_queues_existing_markets_for_matching():
    # live 23:43Z: markets chosen before the guard started were never matched
    g = X.IntlGuard(lambda p: {}, lambda p: [])
    g.track(["aec-old-1", "aec-old-2"])
    assert g._pending == ["aec-old-1", "aec-old-2"]


def test_pending_market_is_held_unpriceable_market_is_released(tmp_path):
    g = X.IntlGuard(lambda p: {}, lambda p: [])
    assert g.verdict("aec-x", {"YES": D("0.42")}, T)["matched"] is True
    g.resolved["aec-x"] = None                     # intl does not list it
    assert g.verdict("aec-x", {"YES": D("0.42")}, T)["matched"] is False
    e, v = eng(tmp_path, g)
    g.resolved["aec-x"] = "TOK"
    e.select(v.account())                          # selected while matched
    g.resolved["aec-x"] = None                     # later: unpriceable
    mk = e.b.m("aec-x")
    e.step_market("aec-x")
    mk["idle_since"] = time.time() - 999
    e.step_market("aec-x")
    assert mk.get("done")                          # slot freed


# ------------------------------------------------- RN1 replica path (real guard)
def _rn1(tmp_path, monkeypatch, g):
    from src import pmus_rn1_replica as R
    monkeypatch.setattr(R, "ON", True)
    monkeypatch.setattr(R, "RESIDUAL_CAP_USD", D("10"))
    monkeypatch.setattr(R, "PER_MARKET_USD", D("20"))
    monkeypatch.setattr(M, "TOTAL_CAP", D("20"))
    e, v = eng(tmp_path, g)
    v.bid, v.ask = D("0.48"), D("0.50")
    e.b.m("aec-x")
    return e, v


def _live(v):
    return {o["side"]: o["px"] for o in v.orders.values() if o["open"]}


def test_rn1_opening_needs_intl_to_confirm(tmp_path, monkeypatch):
    e, v = _rn1(tmp_path, monkeypatch, guard(0.485))
    e.step_market("aec-x")
    assert _live(v) == {"YES": D("0.47"), "NO": D("0.49")}


def test_rn1_unfavourable_intl_blocks_that_opening(tmp_path, monkeypatch):
    # intl already at 0.44: our YES 0.47 is not cheap; NO 0.49 still is
    e, v = _rn1(tmp_path, monkeypatch, guard(0.44))
    e.step_market("aec-x")
    assert _live(v) == {"NO": D("0.49")}


def test_rn1_stale_intl_blocks_openings(tmp_path, monkeypatch):
    e, v = _rn1(tmp_path, monkeypatch, guard(0.485, age=60))
    e.step_market("aec-x")
    assert v.orders == {}


def test_rn1_unmatched_market_blocks_openings(tmp_path, monkeypatch):
    g = guard(0.485)
    g.resolved["aec-x"] = None                       # no intl listing
    e, v = _rn1(tmp_path, monkeypatch, g)
    e.step_market("aec-x")
    assert v.orders == {}


def test_rn1_profitable_completion_ignores_the_guard(tmp_path, monkeypatch):
    e, v = _rn1(tmp_path, monkeypatch, guard(None))   # no intl at all
    mk = e.b.m("aec-x")
    mk["pos"], mk["cost"] = {"YES": "5", "NO": "0"}, {"YES": "2.35", "NO": "0"}
    mk["last_fill_t"] = {"YES": 9e12}                # extension cooling down
    e.step_market("aec-x")
    assert _live(v) == {"NO": D("0.49")}             # 0.47 + 0.49 <= 0.97
