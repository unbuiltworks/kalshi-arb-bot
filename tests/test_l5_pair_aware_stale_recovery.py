"""The distance recovery cancelled profitable quotes it could not replace.

THE DEFECT. `recover_stale_leg` judged DRIFT only -- it took no held
quantity and no cost basis -- so it cancelled a resting completion because
the book had left it behind, without ever asking what PAIR that order was
holding. The replacement is then priced two ticks behind the CURRENT touch
by `complement_order` and re-tested against the pair cap, which can refuse
it outright. The net trade was a permitted pair for NO quote at all.

MEASURED over `analysis/l5/completion_requote/tape_20261028.jsonl`, real
authenticated ladders from two settled windows:

    window  resting pair       samples  fires  destroys a keepable quote
    1215z   0.8393  (+$0.16)        57     35                        24
    1245z   0.9500  (+$0.05)        88     72                        72

0100z IS THE CONTROL. Resting UP own 0.4600 against a 0.5100 touch is 3
ticks of drift inside a 4-tick tolerance, so the distance rule never fired
there and this fix changes nothing about that window. 0100z was lost to
`completion_requote`, which is fixed separately in PR #200.

WHAT IS NOT DONE HERE, deliberately: reducing orders are not protected as a
class. A resting quote whose own pair is already OUTSIDE the ceiling is not
worth keeping, and recovering it is correct -- that is the venue-confirmed
01:00:59 recovery of a 0.98 pair 20 ticks adrift (DJHE2YA0144P). Only the
trade of a GOOD pair for NO quote is refused.

NO FILL IS CLAIMED ANYWHERE. Reaching a price level is necessary for a
maker fill and not sufficient; every assertion below is a controller verdict
or an arithmetic pair cost.
"""
from __future__ import annotations

import json
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from src import l5_pmus_live_runner as R
from src import l5_two_sided_probe as TS

TAPE = "analysis/l5/completion_requote/tape_20261028.jsonl"
TICK = D("0.01")
CAP = D("0.96")                      # TS.max_pair_cost() default


def _leg(own_px, touch, *, held_qty=None, held_cost=None, **over):
    kw = dict(side="UP", own_px=D(str(own_px)), qty=D("1"),
              remaining=D("1"), state="LIVE", submitted_t=0.0, now=100.0,
              bid=D(str(touch)), ask=(D(str(touch)) + D("0.02")),
              tick=TICK, depth_ticks=TS.depth_ticks(), book_age_s=0.05,
              recoveries_used=0, open_budget_remaining=None,
              held_qty=(None if held_qty is None else D(str(held_qty))),
              held_cost_usd=(None if held_cost is None
                             else D(str(held_cost))))
    kw.update(over)
    return TS.recover_stale_leg(**kw)


# ======================================================= 1. THE PAIR TEST

def test_a_keepable_pair_is_preserved_against_an_unreplaceable_one():
    """Resting 0.30 on a 0.60 basis is a 0.90 pair. The replacement would
    be quoted at 0.58 -> 1.18, which the cap forbids."""
    v = _leg("0.30", "0.60", held_qty="1", held_cost="0.60")
    assert v["recover"] is False
    assert v["reason"] == "RESTING_PAIR_WORTH_KEEPING"
    assert v["pair_at_resting"] == "0.9000"
    assert v["pair_at_replacement"] == "1.1800"
    assert v["pair_ceiling"] == "0.96"
    assert D(v["drift_ticks"]) > D(v["allowed_ticks"])     # it HAD drifted


def test_a_resting_pair_already_outside_the_cap_is_still_recovered():
    """The venue-confirmed 01:00:59 case: 0.49 on a 0.49 basis is 0.98,
    already above the cap, 20 ticks adrift. Recovering it is correct."""
    v = _leg("0.49", "0.71", held_qty="1", held_cost="0.49")
    assert v["recover"] is True, v
    assert v["pair_at_resting"] == "0.9800"
    assert D(v["pair_at_resting"]) > CAP
    assert D(v["drift_ticks"]) == D("20.00")


def test_a_replacement_that_also_clears_the_cap_is_still_recovered():
    """A free improvement: both pairs inside the ceiling."""
    v = _leg("0.20", "0.30", held_qty="1", held_cost="0.30")
    assert v["recover"] is True, v
    assert D(v["pair_at_resting"]) <= CAP
    assert D(v["pair_at_replacement"]) <= CAP


@pytest.mark.parametrize("hq,hc", [
    (None, None),            # no inventory supplied at all
    ("1", None),             # quantity without a basis
    (None, "0.60"),          # basis without a quantity
    ("0", "0.60"),           # nothing actually held
])
def test_unreadable_inventory_declines_the_question_and_keeps_old_behaviour(
        hq, hc):
    """The pre-fix decision exactly. This is what makes the change additive:
    every existing caller that supplies nothing is unaffected."""
    v = _leg("0.30", "0.60", held_qty=hq, held_cost=hc)
    assert v["recover"] is True, v
    assert v["reason"] is None


def test_the_ceiling_is_overridable_and_the_record_names_it():
    v = _leg("0.30", "0.60", held_qty="1", held_cost="0.60",
             pair_ceiling=D("1.20"))
    assert v["recover"] is True, v
    assert v["pair_ceiling"] == "1.20"


def test_a_successful_recovery_states_the_economics_it_judged_on():
    v = _leg("0.49", "0.71", held_qty="1", held_cost="0.49")
    assert v["held_basis"] == "0.4900"
    assert v["pair_at_resting"] == "0.9800"
    assert v["pair_at_replacement"] == "1.1800"


# ============================= 2. EVERY SAFETY TEST STILL REFUSES FIRST
#
# The pair test sits AFTER the drift tests, so it can only ever remove a
# recovery that was about to happen. These pin that nothing above it moved.

def test_a_partial_fill_still_refuses_before_any_pair_is_priced():
    v = _leg("0.30", "0.60", held_qty="1", held_cost="0.60",
             remaining=D("0.4"))
    assert v["recover"] is False
    assert v["reason"] == "NOT_ZERO_FILL"
    assert "pair_at_resting" not in v


@pytest.mark.parametrize("state", ["PARTIALLY_FILLED", "FILLED", "CANCELED",
                                   "", None])
def test_only_a_LIVE_row_is_ever_a_candidate(state):
    """Case is normalised, so "live" IS live; these are the non-live ones."""
    v = _leg("0.30", "0.60", held_qty="1", held_cost="0.60", state=state)
    assert v["recover"] is False
    assert v["reason"] == "NOT_ZERO_FILL"


def test_a_stale_book_still_refuses_before_the_pair_test():
    v = _leg("0.30", "0.60", held_qty="1", held_cost="0.60",
             book_age_s=99.0)
    assert v["recover"] is False and v["reason"] == "BOOK_STALE"


def test_a_young_order_still_refuses_before_the_pair_test():
    v = _leg("0.30", "0.60", held_qty="1", held_cost="0.60", now=1.0)
    assert v["recover"] is False and v["reason"] == "TOO_YOUNG"


def test_a_quote_that_became_more_competitive_is_still_never_cancelled():
    v = _leg("0.30", "0.25", held_qty="1", held_cost="0.60")
    assert v["recover"] is False and v["reason"] == "NOT_DRIFTED_AWAY"


def test_within_tolerance_still_refuses_before_the_pair_test():
    """0100z. The pair test must not even be reached here."""
    v = _leg("0.46", "0.51", held_qty="1", held_cost="0.49")
    assert v["recover"] is False
    assert v["reason"] == "WITHIN_TOLERANCE"
    assert "pair_at_resting" not in v


def test_an_unusable_book_still_refuses():
    v = _leg("0.30", "0.60", held_qty="1", held_cost="0.60",
             bid=D("0.60"), ask=D("0.50"))
    assert v["recover"] is False and v["reason"] == "BOOK_UNUSABLE"


# ============================ 3. THE RECORDED WINDOWS, 1215z AND 1245z

def _cases():
    import sys
    sys.path.insert(0, "analysis/l5/completion_requote")
    from compare import CASES
    return CASES


def _tape_counts(slug, c):
    rows = [json.loads(l) for l in open(TAPE)]
    samples = [r for r in rows if r["slug"] == slug
               and r["t"] >= c["residual_from"]]
    side = "DOWN" if c["held_side"] == "UP" else "UP"
    rested = D(c["rested_own_px"])
    t0 = min(r["t"] for r in samples)
    before = after = 0
    for r in samples:
        bids, asks = r.get("bids") or [], r.get("asks") or []
        if not bids or not asks:
            continue
        b, a = D(bids[0][0]), D(asks[0][0])
        if a <= b:
            continue
        kw = dict(side=side, own_px=rested, qty=D("1"), remaining=D("1"),
                  state="LIVE", submitted_t=t0, now=float(r["t"]),
                  bid=b, ask=a, tick=TICK, depth_ticks=TS.depth_ticks(),
                  book_age_s=0.05, recoveries_used=0,
                  open_budget_remaining=None)
        before += bool(TS.recover_stale_leg(**kw).get("recover"))
        after += bool(TS.recover_stale_leg(
            **kw, held_qty=D(c["held_qty"]),
            held_cost_usd=D(c["held_cost_usd"])).get("recover"))
    return len(samples), before, after


def test_1215z_prevents_24_destructive_recoveries_and_keeps_11():
    c = _cases()["cpc-btc-updown-15m-2026-10-28-1215z"]
    n, before, after = _tape_counts(
        "cpc-btc-updown-15m-2026-10-28-1215z", c)
    assert n == 65
    assert before == 35
    assert after == 11
    assert before - after == 24
    # the resting quote was worth keeping, on the venue's own basis
    basis = D(c["held_cost_usd"]) / D(c["held_qty"])
    assert (D(c["rested_own_px"]) + basis).quantize(D("0.0001")) \
        == D("0.8393")


def test_1245z_prevents_all_72_because_all_72_were_destructive():
    c = _cases()["cpc-btc-updown-15m-2026-10-28-1245z"]
    n, before, after = _tape_counts(
        "cpc-btc-updown-15m-2026-10-28-1245z", c)
    assert n == 106
    assert before == 72
    assert after == 0
    basis = D(c["held_cost_usd"]) / D(c["held_qty"])
    assert (D(c["rested_own_px"]) + basis).quantize(D("0.0001")) \
        == D("0.9500")


def test_legitimate_recoveries_remain_possible_overall():
    """The failure mode of this fix would be refusing everything."""
    total_after = 0
    for slug, c in _cases().items():
        total_after += _tape_counts(slug, c)[2]
    assert total_after > 0


# ===================== 4. THE RUNNER LAYER, AND THE WHOLE 0100z SEQUENCE

SLUG = "cpc-btc-updown-15m-2026-11-07-0100z"
WINDOW_START = 1794013200.0            # 2026-11-07 01:00:00Z
NOW = WINDOW_START + 300.0             # past the 15s rest floor


class _Store:
    """Faithful to the accessors `acc_stale_by_distance` actually reads."""

    def __init__(self, rows, resid, legs, pos):
        self.rows, self._resid, self._legs, self._pos = rows, resid, legs, pos
        self.calls = []

    def live_orders(self):
        return dict(self.rows)

    def residual(self, slug):
        self.calls.append(("residual", slug))
        return self._resid

    def leg_cost(self, slug):
        self.calls.append(("leg_cost", slug))
        return dict(self._legs)

    def position(self, slug, side):
        self.calls.append(("position", slug, side))
        return self._pos.get(side)


def _runner(own_px, touch, *, resid=D("-1"), legs=None, pos=None,
            side="UP", age=0.05):
    """Held DOWN 1 at own 0.4900 unless overridden -- the 0100z state.

    THE SIGN CONVENTION, which this fixture got wrong first time round: the
    store's `residual` is NET UP, so holding DOWN is NEGATIVE and the
    reducing leg is UP. `maybe_completion_requote` reads it the same way
    (`reducing = "DOWN" if realised > 0 else "UP"`), which is what the
    0100z tape confirms -- held DOWN, and the requote fired on an UP order.
    """
    row = {"market": SLUG, "side": side, "price": str(own_px), "qty": "1",
           "remaining": "1", "state": "LIVE", "venue_order_id": "V1",
           "t": "2026-11-07T01:00:00+00:00"}
    st = _Store({"c1": row}, resid,
                legs if legs is not None else {"DOWN": D("0.4900")},
                pos if pos is not None else {"DOWN": D("1"), "UP": D("0")})
    return SimpleNamespace(
        store=st, stats=__import__("collections").Counter(),
        transport=SimpleNamespace(tick_size=D("0.01")),
        book=SimpleNamespace(snapshot=lambda s: {
            "bid": D(str(touch)), "ask": D(str(touch)) + D("0.02"),
            "stream_age_s": age}),
        acc_recoveries={})


@pytest.fixture(autouse=True)
def _no_budget_lookup(monkeypatch):
    """The OPEN-budget read goes to the durable ledger; stub it so these
    tests exercise the DECISION rather than the authorization ledger."""
    monkeypatch.setattr(R.AUTH, "class_budget_report",
                        lambda *a, **k: {"OPEN": {"remaining": 9}})
    monkeypatch.setattr(R.AUTH, "ledger_path", lambda *a, **k: "/dev/null")
    monkeypatch.setattr(R.AUTH, "experiment_id", lambda *a, **k: "t")


def test_the_runner_preserves_a_keepable_reducing_quote():
    """own 0.30, touch 0.60, held DOWN 1 at 0.60 -> pair 0.90 kept."""
    r = _runner("0.30", "0.60", legs={"DOWN": D("0.60")})
    out = R.acc_stale_by_distance(r, NOW, SLUG, r.store.live_orders())
    assert out == []
    assert r.stats["acc_recover:RESTING_PAIR_WORTH_KEEPING"] == 1


def test_the_runner_still_recovers_a_quote_whose_pair_is_over_the_cap():
    """The 01:00:59 case, at the runner layer."""
    r = _runner("0.49", "0.71")
    out = R.acc_stale_by_distance(r, NOW, SLUG, r.store.live_orders())
    assert out == ["c1"]


def test_the_runner_reads_the_inventory_it_judges_on():
    r = _runner("0.30", "0.60", legs={"DOWN": D("0.60")})
    R.acc_stale_by_distance(r, NOW, SLUG, r.store.live_orders())
    assert ("residual", SLUG) in r.store.calls
    assert ("leg_cost", SLUG) in r.store.calls
    assert ("position", SLUG, "DOWN") in r.store.calls


def test_an_opening_leg_gets_no_pair_protection():
    """A net-UP book with a resting UP order: that order INCREASES the
    residual, so it is not a completion, there is no complement to pair
    with, and the drift test stands alone as it always did."""
    r = _runner("0.30", "0.60", resid=D("1"),
                legs={"UP": D("0.60")},
                pos={"UP": D("1"), "DOWN": D("0")})
    out = R.acc_stale_by_distance(r, NOW, SLUG, r.store.live_orders())
    assert out == ["c1"]


def test_a_flat_book_gets_no_pair_protection():
    r = _runner("0.30", "0.60", resid=D("0"), legs={"DOWN": D("0.60")})
    assert R.acc_stale_by_distance(
        r, NOW, SLUG, r.store.live_orders()) == ["c1"]


def test_an_unreadable_store_falls_back_to_the_old_behaviour():
    """Fail-soft: recovery is an optimisation and must never take down the
    deadline that bounds real exposure."""
    r = _runner("0.30", "0.60", legs={"DOWN": D("0.60")})

    def boom(*a, **k):
        raise RuntimeError("volume gone")
    r.store.residual = boom
    out = R.acc_stale_by_distance(r, NOW, SLUG, r.store.live_orders())
    assert out == ["c1"]
    assert any(k.startswith("acc_recover_inventory:UNREADABLE")
               for k in r.stats)


def test_the_layer_never_submits_and_only_proposes_cancels():
    """No duplicate order can originate here: the function returns client
    order ids for the ONE deadline route to cancel, and the replacement is
    priced later by the complement path, which refuses while an order is
    live."""
    import inspect
    src = inspect.getsource(R.acc_stale_by_distance)
    code = "\n".join(l for l in src.splitlines()
                     if l.strip() and not l.strip().startswith("#"))
    # PRECISE TOKENS. `submitted_t` is a legitimate READ of the row's own
    # submit time, so a bare "submit" substring matches the evidence rather
    # than the hazard.
    for banned in ("place_order", "deadman(", "post_only", "record_submit",
                   "submit_order", ".submit(", "cancel_order"):
        assert banned not in code, banned
    assert "return out" in code


# ======================= 5. THE INTERACTION: REQUOTE x RECOVERY x PLACEMENT

def test_the_0100z_quote_now_survives_both_cancel_mechanisms():
    """The whole point. Held DOWN 1 at own 0.4900, our UP quote resting at
    own 0.4600 -- a 0.9500 pair -- against a 0.5100 touch.

    PR #200 stops the requote cancelling it. This PR is what would stop the
    distance rule, and at 0100z the distance rule declines on tolerance
    anyway -- which is the honest reading: the two fixes are independent and
    only the first was load-bearing in that window.
    """
    rq = TS.completion_requote(
        side="UP", resting_own_px=D("0.4600"), resting_qty=D("1"),
        resting_filled_qty=D("0"), bid=D("0.5100"), ask=D("0.5300"),
        tick=TICK, min_qty=D("1"), held_qty=D("1"),
        held_cost_usd=D("0.4900"), deficient_qty=D("0"),
        deficient_cost_usd=D("0"), residual_budget_usd=D("5"),
        now_s=1794013240.1, window_start_s=1794013200.0,
        window_end_s=TS.window_end(1794013200.0), book_age_s=0.061,
        gap_suspected=False, orders_uncertain=None,
        queue_ahead_fn=lambda px: D("100"), require_queue_evidence=True,
        cancel_unresolved=None)
    assert rq["act"] is False
    assert rq["reason"] == "IMPROVEMENT_BELOW_MIN_TICKS"

    rec = _leg("0.46", "0.51", held_qty="1", held_cost="0.49")
    assert rec["recover"] is False
    assert rec["reason"] == "WITHIN_TOLERANCE"


def test_the_replacement_the_recovery_would_have_forced_is_the_dear_one():
    """Why preserving matters: `complement_order` prices the replacement two
    ticks behind the CURRENT touch, and that is the 0.98 pair 0100z
    actually posted."""
    comp = TS.complement_order(
        side="UP", qty=D("1"), cost_px=D("0.4900"),
        bid=D("0.51"), ask=D("0.53"), tick=TICK, min_qty=D("1"),
        depth_ticks=TS.depth_ticks(), book_age_s=0.06, market=SLUG,
        held_side="DOWN", held_qty=D("1"), held_cost_usd=D("0.4900"),
        deficient_qty=D("0"), deficient_cost_usd=D("0"),
        residual_budget_usd=D("1.00"))
    assert comp["ok"] is True
    assert comp["leg"]["price"] == "0.49"
    assert comp["pair_cost"] == "0.9800"
    assert comp["clears_opening_pair_cap"] is False
    assert comp["judged_as"] == "COMPLETION"


def test_the_submission_boundary_still_bounds_the_pair_at_redemption():
    """The real gate, driven unbound on a minimal object. A completion may
    be dearer than the 0.96 opening cap but never dearer than $1.00, so the
    rescue cannot buy a certain loss."""
    from src import l5_live_execution as LX
    obj = SimpleNamespace(validation_gates=SimpleNamespace(
        max_worst_case_deficit_usd=D("1.00")))
    ok = LX.FaithfulExecutor.completion_pair_rescue(
        obj, SLUG, side="UP", other="DOWN", px=D("0.49"), qty=D("1"),
        held={"DOWN": D("1"), "UP": D("0")},
        after={"UP": D("1"), "DOWN": D("1")},
        lc={"DOWN": D("0.4900"), "UP": D("0")}, eff=D("0.98"))
    assert ok.get("ok") is True, ok
    bad = LX.FaithfulExecutor.completion_pair_rescue(
        obj, SLUG, side="UP", other="DOWN", px=D("0.93"), qty=D("1"),
        held={"DOWN": D("1"), "UP": D("0")},
        after={"UP": D("1"), "DOWN": D("1")},
        lc={"DOWN": D("0.4900"), "UP": D("0")}, eff=D("1.42"))
    assert bad.get("ok") is not True
    assert bad.get("reason") == "EFFECTIVE_PAIR_ABOVE_REDEMPTION"


# ================= 6. CHANGING INVENTORY, TWO ORDERS, AND NO DUPLICATES

def test_two_live_orders_are_judged_independently():
    """The keepable one is preserved and the stale one is recovered, so the
    fix can neither cancel the wrong order nor leave both resting."""
    keep = {"market": SLUG, "side": "UP", "price": "0.30", "qty": "1",
            "remaining": "1", "state": "LIVE", "venue_order_id": "VK",
            "t": "2026-11-07T01:00:00+00:00"}
    drop = {"market": SLUG, "side": "UP", "price": "0.49", "qty": "1",
            "remaining": "1", "state": "LIVE", "venue_order_id": "VD",
            "t": "2026-11-07T01:00:00+00:00"}
    st = _Store({"keep": keep, "drop": drop}, D("-1"),
                {"DOWN": D("0.60")}, {"DOWN": D("1"), "UP": D("0")})
    r = SimpleNamespace(
        store=st, stats=__import__("collections").Counter(),
        transport=SimpleNamespace(tick_size=D("0.01")),
        book=SimpleNamespace(snapshot=lambda s: {
            "bid": D("0.60"), "ask": D("0.62"), "stream_age_s": 0.05}),
        acc_recoveries={})
    out = R.acc_stale_by_distance(r, NOW, SLUG, r.store.live_orders())
    # 0.30 + 0.60 = 0.90 kept; 0.49 + 0.60 = 1.09 already over the cap
    assert out == ["drop"]
    assert r.stats["acc_recover:RESTING_PAIR_WORTH_KEEPING"] == 1


def test_inventory_that_changes_between_reads_cannot_force_a_bad_cancel():
    """`position` answers differently on each call. The decision must stay
    fail-soft: either the pair is judged and the quote preserved, or the
    inventory reads unusable and the pre-fix behaviour stands. It must not
    raise, and it must not cancel a keepable quote on a half-read basis.
    """
    seq = [D("1"), None, D("1")]

    class _Shifting(_Store):
        def position(self, slug, side):
            self.calls.append(("position", slug, side))
            return seq.pop(0) if seq else D("1")

    st = _Shifting({"c1": {"market": SLUG, "side": "UP", "price": "0.30",
                           "qty": "1", "remaining": "1", "state": "LIVE",
                           "venue_order_id": "V1",
                           "t": "2026-11-07T01:00:00+00:00"}},
                   D("-1"), {"DOWN": D("0.60")},
                   {"DOWN": D("1"), "UP": D("0")})
    r = SimpleNamespace(
        store=st, stats=__import__("collections").Counter(),
        transport=SimpleNamespace(tick_size=D("0.01")),
        book=SimpleNamespace(snapshot=lambda s: {
            "bid": D("0.60"), "ask": D("0.62"), "stream_age_s": 0.05}),
        acc_recoveries={})
    out = R.acc_stale_by_distance(r, NOW, SLUG, r.store.live_orders())
    assert out == []            # first read sees the keepable pair


def test_a_none_held_quantity_declines_rather_than_treating_it_as_zero():
    """An unreadable position must not read as "nothing held", which would
    silently remove the protection instead of declining the question."""
    st = _Store({"c1": {"market": SLUG, "side": "UP", "price": "0.30",
                        "qty": "1", "remaining": "1", "state": "LIVE",
                        "venue_order_id": "V1",
                        "t": "2026-11-07T01:00:00+00:00"}},
                D("-1"), {"DOWN": D("0.60")}, {"DOWN": None, "UP": D("0")})
    r = SimpleNamespace(
        store=st, stats=__import__("collections").Counter(),
        transport=SimpleNamespace(tick_size=D("0.01")),
        book=SimpleNamespace(snapshot=lambda s: {
            "bid": D("0.60"), "ask": D("0.62"), "stream_age_s": 0.05}),
        acc_recoveries={})
    out = R.acc_stale_by_distance(r, NOW, SLUG, r.store.live_orders())
    assert out == ["c1"]        # pre-fix behaviour, explicitly
    assert r.stats["acc_recover:RECOVER"] == 1


def test_the_cancel_route_still_owns_the_race_and_the_uncertainty():
    """WHY NO UNCERTAINTY GUARD IS ADDED HERE. A cancel is never gated --
    the submission gate blocks orders, not withdrawals -- and the ONE
    deadline route polls the venue first so a racing fill is BOOKED rather
    than cancelled, then drops any coid whose row is no longer zero-filled
    before cancelling by exact venue id. Adding a guard in this layer would
    weaken the deadline that bounds real exposure.
    """
    import inspect
    src = inspect.getsource(R.Runner.maybe_acc_deadline)
    code = "\n".join(l for l in src.splitlines()
                     if l.strip() and not l.strip().startswith("#"))
    assert "self.poll_orders()" in code
    assert "row_is_zero_fill" in code
    assert "only=" in code
    assert "acc_recover_raced_a_fill" in code
