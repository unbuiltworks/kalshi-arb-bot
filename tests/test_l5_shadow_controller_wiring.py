"""The independent shadow decision source, wired into the REAL runner.

The controller's own tests prove it decides from our inventory and cannot
trade. They cannot prove it is CONNECTED, and a decision source that is
never called is worth nothing. So this file builds an actual `Runner` over
a temp ledger, drives the real `tick`, and asserts on what the process
actually printed.

Each test guards a way this wiring could be quietly useless or quietly
dangerous:

  * emitting nothing (the deliverable absent while the service looks fine);
  * going silent exactly when the money path stops -- a cool-off, a halt, a
    dead L5 feed -- which are the hours we most need the record for;
  * following L5's feed instead of deciding for itself;
  * reaching an order path once an arming variable is set;
  * resetting simulated inventory on restart, which flatters every number
    computed from it;
  * triggering a venue read that moves a money-path gate;
  * settling against an expired book's last price instead of the venue's
    own settlement.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal as D
from unittest import mock

import pytest

from src import l5_pmus_live_runner as R
from src import l5_shadow_controller as SC

SLUG = "btc-updown-15m-2026-10-18-1830z"
PMUS = "cpc-btc-updown-15m-2026-10-18-1830z"
#: T+5:00 into the 1830z window, which is the window `roll` derives from
#: this clock. WS is the venue's own 900s grid boundary, not a round number
#: of our choosing -- an off-grid NOW silently tests the next window.
WS = 1792348200.0
NOW = WS + 300.0


def feed_rows():
    return [
        {"slug": SLUG, "timestamp": 1792348857, "side": "BUY",
         "outcome": "Up", "price": "0.31", "size": "1.449274",
         "transactionHash": "0x" + "a" * 64, "conditionId": "0x" + "b" * 64},
    ]


def build(tmp_path, monkeypatch, *, feed=None, root=None, bid="0.50",
          ask="0.51"):
    monkeypatch.delenv("L5_ORDER_REGISTRY_PORT", raising=False)
    book = mock.MagicMock()
    book.snapshot.return_value = {
        "bid": D(bid), "ask": D(ask), "stream_age_s": 0.02,
        "market_state": "MARKET_STATE_OPEN", "source": "authenticated_ws"}
    book.resub_due.return_value = []
    book.stall_due.return_value = False
    return R.Runner(root=str(root or (tmp_path / "live")), book=book,
                    feed=feed or (lambda: feed_rows()))


def capture(monkeypatch):
    """Everything the process prints, by kind. The claim is about the
    RECORD, so the record is what is asserted on."""
    out = []

    def emit(kind, doc):
        out.append((kind, json.loads(json.dumps(doc, default=str))))
    monkeypatch.setattr(R, "emit", emit)
    return out


def decisions(out):
    return [d for k, d in out if k == "L5_SHADOW_DECISION"]


# ============================================== IT IS ACTUALLY CONNECTED

def test_the_real_tick_emits_an_inventory_driven_decision(tmp_path,
                                                          monkeypatch):
    """THE DELIVERABLE. A real Runner, the real tick, the real v2 model."""
    runner = build(tmp_path, monkeypatch)
    out = capture(monkeypatch)
    runner.tick(NOW)
    recs = decisions(out)
    assert recs, [k for k, _ in out]
    assert recs[0]["provenance"] == "CONTROLLER_GENERATED"
    assert recs[0]["market"] == PMUS
    assert recs[0]["fill_rule"] == "TOUCH_THROUGH_OPTIMISTIC"


def test_the_decision_is_dated_against_the_real_window_clock(tmp_path,
                                                             monkeypatch):
    """The BTC15m defect this adaptation exists for: a 1e9 sentinel tte put
    every state in `tte>10m` for the whole window."""
    runner = build(tmp_path, monkeypatch)
    out = capture(monkeypatch)
    runner.tick(NOW)
    rec = decisions(out)[0]
    assert rec["tte_s"] == pytest.approx(600.0, abs=1.0)
    assert rec["window_source"] == "CLOCK_GRID"


def test_it_prefers_the_venues_own_bounds_when_the_money_path_has_them(
        tmp_path, monkeypatch):
    runner = build(tmp_path, monkeypatch)
    runner.roll(NOW)
    runner.window_bounds[runner.ws] = {
        "source": "VENUE", "start": "2026-10-18T18:30:00+00:00",
        "end": "2026-10-18T18:45:00+00:00"}
    out = capture(monkeypatch)
    runner.tick(NOW)
    assert decisions(out)[0]["window_source"] == "VENUE"


def test_the_shadow_never_triggers_a_venue_window_read(tmp_path, monkeypatch):
    """A venue read from here would CACHE A FAILURE as permanent for the
    window, and the arming boundary then refuses WINDOW_BOUNDS_UNCONFIRMED
    on a read shadow code provoked. Shadow code must not move a money gate.
    """
    runner = build(tmp_path, monkeypatch)
    calls = {"n": 0}

    def boom(*_a, **_kw):
        calls["n"] += 1
        raise AssertionError("the shadow performed a venue window read")
    monkeypatch.setattr(R.OBS, "venue_window_bounds", boom)
    capture(monkeypatch)
    for i in range(4):
        runner.tick(NOW + i * 3.0)
    assert calls["n"] == 0
    assert runner.window_bounds == {} or all(
        v is not None for v in runner.window_bounds.values())


# ====================================== IT DOES NOT GO SILENT WITH THE MONEY

def test_it_still_decides_during_a_venue_cooloff(tmp_path, monkeypatch):
    runner = build(tmp_path, monkeypatch)
    monkeypatch.setattr(runner, "venue_cooling", lambda: 30.0)
    out = capture(monkeypatch)
    runner.tick(NOW)
    assert decisions(out), "the shadow went silent in a cool-off"


def test_it_still_decides_while_halted(tmp_path, monkeypatch):
    """A HALT stops SUBMISSIONS. There is nothing here to submit, and the
    record is the one thing we still want during a halt."""
    runner = build(tmp_path, monkeypatch)
    monkeypatch.setattr(runner, "halted", lambda: True)
    monkeypatch.setattr(R.LX, "deadman", lambda *a, **kw: {})
    out = capture(monkeypatch)
    runner.tick(NOW)
    assert decisions(out), "the shadow went silent under a halt"


def test_it_decides_with_no_l5_feed_at_all(tmp_path, monkeypatch):
    """INDEPENDENCE, behaviourally. The money path is driven by L5's
    activity feed; this must not be."""
    runner = build(tmp_path, monkeypatch, feed=lambda: None)
    out = capture(monkeypatch)
    runner.tick(NOW)
    recs = decisions(out)
    assert recs and runner.stats["feed_errors"] >= 1


def test_a_controller_failure_disables_only_the_controller(tmp_path,
                                                           monkeypatch):
    """A missing model artifact must stop this and nothing else."""
    runner = build(tmp_path, monkeypatch)
    monkeypatch.setattr(SC.pv2, "load_v2",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            FileNotFoundError("artifact")))
    out = capture(monkeypatch)
    runner.tick(NOW)
    assert any(k == "L5_SHADOW_DISABLED" for k, _ in out)
    assert not decisions(out)
    assert runner._shadow_off is True
    # and the tick itself completed: the money path is untouched
    runner.tick(NOW + 3.0)


# ========================================= IT CANNOT REACH AN ORDER PATH

def test_no_arming_variable_makes_the_shadow_submit(tmp_path, monkeypatch):
    """Every arming key at its live value and the shadow lock LIFTED. The
    controller still submits nothing, because it holds nothing to submit
    with -- asserted on the real transport class, since `roll` builds its
    own and an injected recorder would be discarded."""
    for k, v in (("PMUS_Q1_LIVE", "YES"), ("PMUS_Q1_SESSION_ID", "s"),
                 ("PMUS_Q1_ARM", "s"), ("L5_SHADOW_ONLY", "0")):
        monkeypatch.setenv(k, v)
    runner = build(tmp_path, monkeypatch, feed=lambda: [])
    sent = {"n": 0}

    def submit(self, intent):
        sent["n"] += 1
        raise AssertionError("submit reached: %r" % (dict(intent),))
    monkeypatch.setattr(R.T.PmusTransport, "submit", submit, raising=True)
    out = capture(monkeypatch)
    runner.roll(NOW)
    for i in range(5):
        runner.shadow_tick(NOW + i * 3.0)
    assert decisions(out), "no decision was taken, so nothing was proved"
    assert sent["n"] == 0
    c = runner.shadow_controller()
    for attr in SC.FORBIDDEN_ATTRS:
        assert not hasattr(c, attr), attr


def test_the_runner_hands_the_controller_no_transport(tmp_path, monkeypatch):
    """Structural, not behavioural: whatever the controller was given, none
    of it is the transport, the executor or the store."""
    runner = build(tmp_path, monkeypatch)
    runner.tick(NOW)
    c = runner.shadow_controller()
    held = [v for v in vars(c).values()]
    for v in held:
        assert not isinstance(v, R.T.PmusTransport), vars(c)
        assert not isinstance(v, R.LX.InventoryStore), vars(c)


def test_before_any_window_is_rolled_there_is_nothing_to_decide_about(
        tmp_path, monkeypatch):
    """Pinned because it is the shape of a vacuously green test: a
    `shadow_tick` without a rolled window is a no-op, and a test that
    forgets `roll` proves nothing while passing."""
    runner = build(tmp_path, monkeypatch, feed=lambda: [])
    out = capture(monkeypatch)
    assert runner.ws is None
    assert runner.shadow_tick(NOW) is None
    assert not decisions(out)


# ================================================ PERSISTENCE AND RESTART

def test_simulated_inventory_survives_a_restart(tmp_path, monkeypatch):
    """A reset inventory flatters every number computed from it."""
    root = tmp_path / "live"
    runner = build(tmp_path, monkeypatch, root=root)
    capture(monkeypatch)
    runner.tick(NOW)
    c = runner.shadow_controller()
    inv = c.inventory_for(PMUS)
    inv.record(True, D("0.47"))
    c._store_inventory(PMUS, inv)
    c._save()
    again = build(tmp_path, monkeypatch, root=root)
    assert again.shadow_controller().inventory_for(PMUS).snapshot() == {
        "yes": 1, "no": 0, "matched": 0, "residual": 1}


def test_the_state_file_lands_under_the_live_root(tmp_path, monkeypatch):
    """So it is on the mounted volume in production, not in the container's
    ephemeral filesystem."""
    runner = build(tmp_path, monkeypatch)
    capture(monkeypatch)
    runner.tick(NOW)
    path = runner.shadow_controller().state_path
    assert path == os.path.join(runner.root, "shadow_controller.json")
    assert os.path.exists(path)


# ================================================== EMISSION IS CONTINUOUS

def test_repetition_is_throttled_but_every_tick_is_still_evaluated(
        tmp_path, monkeypatch):
    """300 identical lines a window bury the record; a decision count that
    stops moving hides a dead controller. Both are wrong, so repetition is
    suppressed and the COUNT still advances."""
    runner = build(tmp_path, monkeypatch, feed=lambda: [])
    out = capture(monkeypatch)
    runner.roll(NOW)
    for i in range(20):
        runner.shadow_tick(NOW + i * 3.0)
    assert runner.stats["shadow_decisions"] == 20
    assert 1 <= len(decisions(out)) < 20
    assert runner.stats["shadow_repeat_suppressed"] > 0


def test_an_unchanged_decision_reappears_within_the_emit_interval(
        tmp_path, monkeypatch):
    runner = build(tmp_path, monkeypatch, feed=lambda: [])
    out = capture(monkeypatch)
    runner.roll(NOW)
    runner.shadow_tick(NOW)
    runner.shadow_tick(NOW + R.SHADOW_EMIT_S + 1.0)
    assert len(decisions(out)) == 2


def test_the_heartbeat_carries_the_shadow_report(tmp_path, monkeypatch):
    runner = build(tmp_path, monkeypatch, feed=lambda: [])
    capture(monkeypatch)
    runner.tick(NOW)
    out = capture(monkeypatch)
    runner.heartbeat()
    hb = [d for k, d in out if k == "L5_LIVE_HB"]
    assert hb and hb[0]["shadow_controller"]["decisions"] >= 1
    assert hb[0]["shadow_controller"]["provenance"] == "CONTROLLER_GENERATED"


# ===================================================== WINDOW CLOSE

def test_rolling_the_window_reports_what_the_shadow_is_left_holding(
        tmp_path, monkeypatch):
    runner = build(tmp_path, monkeypatch, feed=lambda: [])
    capture(monkeypatch)
    runner.tick(NOW)
    c = runner.shadow_controller()
    inv = c.inventory_for(PMUS)
    inv.record(True, D("0.47"))
    c._store_inventory(PMUS, inv)
    out = capture(monkeypatch)
    runner.tick(NOW + 900.0)              # next window
    close = [d for k, d in out if k == "L5_SHADOW_WINDOW_CLOSE"]
    assert close and close[0]["market"] == PMUS
    assert close[0]["inventory"]["residual"] == 1
    assert "STRANDED" in close[0]["labels"]
    assert c._slot(PMUS)["resting"] is None, \
        "a resting order must not survive expiry"


# ======================================================= SETTLEMENT

def _closed_window(runner, monkeypatch, tmp_path):
    capture(monkeypatch)
    runner.tick(NOW)
    c = runner.shadow_controller()
    inv = c.inventory_for(PMUS)
    inv.record(True, D("0.47"))
    inv.record(False, D("0.49"))
    inv.record(True, D("0.46"))
    c._store_inventory(PMUS, inv)
    c._save()
    return c


def test_settlement_uses_the_venues_own_price_and_nothing_else(
        tmp_path, monkeypatch):
    runner = build(tmp_path, monkeypatch, feed=lambda: [])
    c = _closed_window(runner, monkeypatch, tmp_path)
    monkeypatch.setattr(R.OBS, "pmus_bbo", lambda slug: {
        "state": "MARKET_STATE_EXPIRED", "settlementPx": "1.0000"})
    out = capture(monkeypatch)
    runner.shadow_settle(NOW + 900.0 + R.SHADOW_SETTLE_GRACE_S + 1.0)
    rec = [d for k, d in out if k == "L5_SHADOW_SETTLED"]
    assert rec, [k for k, _ in out]
    assert rec[0]["matched_units"] == 1 and rec[0]["residual_units"] == 1
    # one matched pair redeems $1; the residual YES redeems $1 at px 1.0
    assert D(rec[0]["redemption_total"]) == D(2)
    assert D(rec[0]["cost_total"]) == D("1.42")
    assert "NOT realized" in rec[0]["labels"]


@pytest.mark.parametrize("md", [
    {"state": "MARKET_STATE_OPEN", "settlementPx": "1.0000"},
    {"state": "MARKET_STATE_EXPIRED", "settlementPx": "0.0100"},
    {"state": "MARKET_STATE_EXPIRED"},
    {"state": "MARKET_STATE_EXPIRED", "settlementPx": None},
    {},
])
def test_an_expired_books_last_price_is_never_a_settlement(tmp_path,
                                                           monkeypatch, md):
    """The defect this venue already produced here once: 0.01 -- the price an
    expired book still carried -- reached the ledger as a payout."""
    runner = build(tmp_path, monkeypatch, feed=lambda: [])
    c = _closed_window(runner, monkeypatch, tmp_path)
    monkeypatch.setattr(R.OBS, "pmus_bbo", lambda slug: dict(md))
    out = capture(monkeypatch)
    runner.shadow_settle(NOW + 900.0 + R.SHADOW_SETTLE_GRACE_S + 1.0)
    assert not [d for k, d in out if k == "L5_SHADOW_SETTLED"]
    assert c.inventory_for(PMUS).snapshot()["residual"] == 1
    assert PMUS in c.stranded()


def test_a_settlement_read_failure_strands_rather_than_settles(tmp_path,
                                                               monkeypatch):
    runner = build(tmp_path, monkeypatch, feed=lambda: [])
    c = _closed_window(runner, monkeypatch, tmp_path)

    def boom(_slug):
        raise OSError("network")
    monkeypatch.setattr(R.OBS, "pmus_bbo", boom)
    out = capture(monkeypatch)
    runner.shadow_settle(NOW + 900.0 + R.SHADOW_SETTLE_GRACE_S + 1.0)
    assert not [d for k, d in out if k == "L5_SHADOW_SETTLED"]
    assert runner.stats["shadow_settle_read_error"] >= 1
    assert PMUS in c.stranded()


def test_a_still_open_window_is_not_asked_about_at_all(tmp_path, monkeypatch):
    """One read per closed window after the grace, never a poll of a live
    market from the shadow path."""
    runner = build(tmp_path, monkeypatch, feed=lambda: [])
    c = _closed_window(runner, monkeypatch, tmp_path)
    asked = {"n": 0}

    def bbo(_slug):
        asked["n"] += 1
        return {}
    monkeypatch.setattr(R.OBS, "pmus_bbo", bbo)
    capture(monkeypatch)
    runner.shadow_settle(NOW + 10.0)
    assert asked["n"] == 0, "the shadow asked about a live market"
    runner.shadow_settle(NOW + 900.0 + R.SHADOW_SETTLE_GRACE_S + 1.0)
    assert asked["n"] == 1


# ================================================== COMPARISON AGAINST L5

def _growing_feed():
    """A feed yielding a NEW row each call. `poll` treats its first read as
    history and dedupes, so a repeating feed never reaches `handle` and
    every assertion below would pass for the wrong reason."""
    # the FIRST row is consumed as history by `poll`'s seeding read, so the
    # first comparison is against the SECOND row. Three rows, so a test can
    # see more than one handled action.
    rows = [
        {"slug": SLUG, "timestamp": 1792348857, "side": "BUY",
         "outcome": "Up", "price": "0.31", "size": "1.449274",
         "transactionHash": "0x" + "a" * 64, "conditionId": "0x" + "b" * 64},
        {"slug": SLUG, "timestamp": 1792348870, "side": "BUY",
         "outcome": "Down", "price": "0.60", "size": "20",
         "transactionHash": "0x" + "c" * 64, "conditionId": "0x" + "d" * 64},
        {"slug": SLUG, "timestamp": 1792348880, "side": "BUY",
         "outcome": "Up", "price": "0.42", "size": "11",
         "transactionHash": "0x" + "e" * 64, "conditionId": "0x" + "f" * 64},
    ]
    n = {"i": 0}

    def feed():
        n["i"] += 1
        return rows[:min(n["i"], len(rows))]
    return feed


def test_l5s_action_is_compared_against_the_decision_that_predates_it(
        tmp_path, monkeypatch):
    """The validation standard: at this state, what did L5 do and what would
    we have done. Our decision must PREDATE the action -- a decision taken
    after seeing L5's trade is hindsight."""
    runner = build(tmp_path, monkeypatch, feed=_growing_feed())
    out = capture(monkeypatch)
    runner.tick(NOW)                      # seeds the feed, decides once
    runner.tick(NOW + 3.0)                # acts on the new row
    cmp = [d for k, d in out if k == "L5_SHADOW_COMPARE"]
    assert cmp, [k for k, _ in out]
    c0 = cmp[0]
    assert c0["l5"]["side"] == "NO" and c0["l5"]["price"] == "0.60"
    assert c0["ours"]["decision"] in ("quote", "no_action")
    assert c0["ours"]["decision_age_s"] >= 0.0
    assert c0["same_side"] in (True, False)
    assert "PREDATES" in c0["labels"]
    assert c0["provenance"] == "CONTROLLER_GENERATED_VS_L5_OBSERVED"


def test_a_comparison_failure_never_costs_the_strategy_an_action(
        tmp_path, monkeypatch):
    """A raise in the comparison would abandon the rest of the batch. The
    money path must be untouched by a record-keeping fault."""
    runner = build(tmp_path, monkeypatch, feed=_growing_feed())
    seen = {"n": 0}
    real = runner.handle

    def handle(r, recv):
        seen["n"] += 1
        return real(r, recv)
    runner.handle = handle

    def boom(*_a, **_kw):
        raise RuntimeError("record keeping")
    monkeypatch.setattr(runner, "shadow_compare", boom)
    capture(monkeypatch)
    for i in range(4):
        runner.tick(NOW + i * 3.0)
    assert seen["n"] >= 2, "a comparison fault stole the strategy's actions"
    assert any(k.startswith("shadow_compare_error")
               for k in runner.stats.keys())


def test_no_comparison_is_emitted_before_we_have_decided_anything(
        tmp_path, monkeypatch):
    """A comparison against no decision would be a fabricated NO_ACTION."""
    runner = build(tmp_path, monkeypatch, feed=_growing_feed())
    out = capture(monkeypatch)
    assert runner._shadow_last_rec is None
    runner.shadow_compare({"slug": SLUG, "outcome": "Up", "side": "BUY",
                           "price": "0.31", "size": "1"}, NOW)
    assert not [d for k, d in out if k == "L5_SHADOW_COMPARE"]


def test_the_comparison_never_feeds_l5s_action_back_into_our_decision(
        tmp_path, monkeypatch):
    """INDEPENDENCE. The record pairs the two; it must not join them. The
    decision after a comparison must be identical to the decision a runner
    that never saw the feed would take, from the same book and clock."""
    with_feed = build(tmp_path, monkeypatch, feed=_growing_feed())
    capture(monkeypatch)
    with_feed.tick(NOW)
    with_feed.tick(NOW + 3.0)
    mine = dict(with_feed._shadow_last_rec or {})
    blind = build(tmp_path, monkeypatch, root=tmp_path / "blind",
                  feed=lambda: [])
    blind.tick(NOW)
    blind.tick(NOW + 3.0)
    theirs = dict(blind._shadow_last_rec or {})
    for k in ("decision", "relation", "side", "target_price", "state",
              "tte_s", "inventory"):
        assert mine.get(k) == theirs.get(k), (k, mine.get(k), theirs.get(k))
