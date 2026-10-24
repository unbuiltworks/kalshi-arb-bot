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
    # STRINGS: `AuthBook.snapshot` returns str prices, and a mock that
    # returns Decimals is a mock that cannot reproduce production. It did
    # not, and production raised TypeError on every tick of the first
    # covered-band window.
    book.snapshot.return_value = {
        "bid": str(bid), "ask": str(ask), "stream_age_s": 0.02,
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


def decisions(out, variant=None, scenario=SC.HEADLINE_SCENARIO):
    """Decision records, filtered to ONE scenario by default.

    Every tick emits one record per scenario, so an unfiltered count
    trebles. The headline scenario is the one promotion is judged on, so it
    is the default unit of "a decision" here.

    FILTERED BY ARM, NOT BY POLICY. Two arms now share the policy ARGMAX --
    the touch arm and the placement arm -- so a policy filter would merge
    them and compare the comparison against itself.
    """
    recs = [d for k, d in out if k == "L5_SHADOW_DECISION"]
    return [d for d in recs
            if (variant is None or d.get("variant") == variant)
            and (scenario is None or d.get("scenario") == scenario)]


def ctl(runner, policy=SC.BASELINE_POLICY):
    """One named controller. There is one PER POLICY now, and the baseline
    is the one the money path would have used."""
    return runner.shadow_controller()[policy]


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
    assert recs[0]["fill_rule"] == "QUEUE_AWARE_THREE_SCENARIO"


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


def test_a_controller_failure_disables_every_policy_and_nothing_else(
        tmp_path, monkeypatch):
    """A missing model artifact must stop this and nothing else."""
    runner = build(tmp_path, monkeypatch)
    monkeypatch.setattr(SC.pv2, "load_v2",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            FileNotFoundError("artifact")))
    out = capture(monkeypatch)
    runner.tick(NOW)
    assert any(k == "L5_SHADOW_DISABLED" for k, _ in out)
    assert not decisions(out)
    assert runner._shadow_off is True and runner._shadow == {}
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
    c = ctl(runner)
    for attr in SC.FORBIDDEN_ATTRS:
        assert not hasattr(c, attr), attr


def test_the_runner_hands_the_controller_no_transport(tmp_path, monkeypatch):
    """Structural, not behavioural: whatever the controller was given, none
    of it is the transport, the executor or the store."""
    runner = build(tmp_path, monkeypatch)
    runner.tick(NOW)
    c = ctl(runner)
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
    c = ctl(runner)
    for scen in SC.SCENARIOS:
        inv = c.inventory_for(PMUS, scen)
        inv.record(True, D("0.47"))
        c._store_inventory(PMUS, inv, scen)
    c._save()
    again = build(tmp_path, monkeypatch, root=root)
    assert ctl(again).inventory_for(PMUS).snapshot() == {
        "yes": 1, "no": 0, "matched": 0, "residual": 1}


def test_the_state_file_lands_under_the_live_root(tmp_path, monkeypatch):
    """So it is on the mounted volume in production, not in the container's
    ephemeral filesystem."""
    runner = build(tmp_path, monkeypatch)
    capture(monkeypatch)
    runner.tick(NOW)
    for name, c in runner.shadow_controller().items():
        want = os.path.join(runner.root, SC.variant_state_filename(name))
        assert c.state_path == want, name
        assert os.path.exists(want), name


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
    for name in SC.POLICIES:
        assert runner.stats["shadow_decisions:%s" % name] == 20, name
        assert 1 <= len(decisions(out, name)) < 20, name
        # the throttle is keyed per (policy, scenario) so neither a policy
        # nor a scenario can mute another
        sup = [k for k in runner.stats.keys()
               if k.startswith("shadow_repeat_suppressed:%s|" % name)]
        assert sup, (name, sorted(runner.stats.keys()))


def test_an_unchanged_decision_reappears_within_the_emit_interval(
        tmp_path, monkeypatch):
    runner = build(tmp_path, monkeypatch, feed=lambda: [])
    out = capture(monkeypatch)
    runner.roll(NOW)
    runner.shadow_tick(NOW)
    runner.shadow_tick(NOW + R.SHADOW_EMIT_S + 1.0)
    for name in SC.POLICIES:
        assert len(decisions(out, name)) == 2, name


def test_the_heartbeat_carries_the_shadow_report(tmp_path, monkeypatch):
    runner = build(tmp_path, monkeypatch, feed=lambda: [])
    capture(monkeypatch)
    runner.tick(NOW)
    out = capture(monkeypatch)
    runner.heartbeat()
    hb = [d for k, d in out if k == "L5_LIVE_HB"]
    assert hb
    block = hb[0]["shadow_controller"]
    assert set(block) == {a[0] for a in SC.SHADOW_VARIANTS}, block
    for name in (a[0] for a in SC.SHADOW_VARIANTS):
        assert block[name]["decisions"] >= 1, name
        assert block[name]["scenarios"] == list(SC.SCENARIOS)
        assert block[name]["headline_scenario"] == SC.HEADLINE_SCENARIO
        assert block[name]["provenance"] == "CONTROLLER_GENERATED"
        assert block[name]["variant"] == name


# ===================================================== WINDOW CLOSE

def test_rolling_the_window_reports_what_the_shadow_is_left_holding(
        tmp_path, monkeypatch):
    runner = build(tmp_path, monkeypatch, feed=lambda: [])
    capture(monkeypatch)
    runner.tick(NOW)
    c = ctl(runner)
    for scen in SC.SCENARIOS:
        inv = c.inventory_for(PMUS, scen)
        inv.record(True, D("0.47"))
        c._store_inventory(PMUS, inv, scen)
    out = capture(monkeypatch)
    runner.tick(NOW + 900.0)              # next window
    close = [d for k, d in out if k == "L5_SHADOW_WINDOW_CLOSE"]
    assert {d["policy"] for d in close} == set(SC.POLICIES), close
    mine = [d for d in close if d["policy"] == SC.BASELINE_POLICY]
    assert mine and mine[0]["market"] == PMUS
    line = mine[0]["scenarios"][SC.HEADLINE_SCENARIO]
    assert line["inventory"]["residual"] == 1
    assert "STRANDED" in mine[0]["labels"]
    for scen in SC.SCENARIOS:
        assert c._scen(PMUS, scen)["resting"] is None, \
            "a resting order must not survive expiry"


# ======================================================= SETTLEMENT

def _closed_window(runner, monkeypatch, tmp_path):
    capture(monkeypatch)
    runner.tick(NOW)
    c = ctl(runner)
    for scen in SC.SCENARIOS:
        inv = c.inventory_for(PMUS, scen)
        inv.record(True, D("0.47"))
        inv.record(False, D("0.49"))
        inv.record(True, D("0.46"))
        c._store_inventory(PMUS, inv, scen)
    c._slot(PMUS)["window_end_ts"] = WS + 900.0
    c._save()
    return c


def test_settlement_uses_the_venues_own_price_and_nothing_else(
        tmp_path, monkeypatch):
    runner = build(tmp_path, monkeypatch, feed=lambda: [])
    c = _closed_window(runner, monkeypatch, tmp_path)
    monkeypatch.setattr(R.OBS, "pmus_settlement", lambda slug: {
        "state": "MARKET_STATE_EXPIRED", "settlementPx": "1.0000"})
    out = capture(monkeypatch)
    runner.shadow_settle(NOW + 900.0 + R.SHADOW_SETTLE_GRACE_S + 1.0)
    rec = [d for k, d in out if k == "L5_SHADOW_SETTLED"
           and d["policy"] == SC.BASELINE_POLICY]
    assert rec, [k for k, _ in out]
    line = rec[0]["scenarios"][SC.HEADLINE_SCENARIO]
    assert line["matched_units"] == 1 and line["residual_units"] == 1
    # one matched pair redeems $1; the residual YES redeems $1 at px 1.0
    assert D(line["redemption_total"]) == D(2)
    assert D(line["cost_total"]) == D("1.42")
    assert "NOT realized" in rec[0]["labels"]
    # fees and rebates are reported with their provenance, not modelled
    assert rec[0]["maker_fee_usd"] == "0.0000"
    assert "CONFIRMED" in rec[0]["maker_fee_provenance"]
    assert rec[0]["maker_rebate_usd"] == "0.0000"
    assert "UNCREDITED" in rec[0]["maker_rebate_provenance"]


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
    monkeypatch.setattr(R.OBS, "pmus_settlement", lambda slug: dict(md))
    out = capture(monkeypatch)
    runner.shadow_settle(NOW + 900.0 + R.SHADOW_SETTLE_GRACE_S + 1.0)
    assert not [d for k, d in out if k == "L5_SHADOW_SETTLED"
                and d["policy"] == SC.BASELINE_POLICY]
    assert c.inventory_for(PMUS).snapshot()["residual"] == 1
    assert PMUS in c.stranded()


def test_a_settlement_read_failure_strands_rather_than_settles(tmp_path,
                                                               monkeypatch):
    runner = build(tmp_path, monkeypatch, feed=lambda: [])
    c = _closed_window(runner, monkeypatch, tmp_path)

    def boom(_slug):
        raise OSError("network")
    monkeypatch.setattr(R.OBS, "pmus_settlement", boom)
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
    monkeypatch.setattr(R.OBS, "pmus_settlement", bbo)
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
    assert not runner._shadow
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


def test_the_runner_produces_a_real_quote_not_a_controller_error(
        tmp_path, monkeypatch):
    """THE PRODUCTION REGRESSION. With the venue's own string-valued book
    and a price band the model covers, the runner's shadow tick must emit a
    QUOTE -- not the `controller_error:TypeError` it emitted in production
    on the 1545z window."""
    runner = build(tmp_path, monkeypatch, feed=lambda: [], bid="0.48",
                   ask="0.50")
    out = capture(monkeypatch)
    runner.tick(NOW)
    recs = decisions(out)
    assert recs, [k for k, _ in out]
    assert recs[0]["decision"] == "quote", recs[0]
    assert recs[0]["relation"] == "open" and recs[0]["side"] == "YES"
    for r in recs:
        assert not str(r.get("reason") or "").startswith("controller_error"), r
    assert not [k for k in runner.stats.keys() if k.startswith("shadow_error")]


# ===================================== TWO POLICIES, ONE BOOK, ONE INSTANT

def test_both_policies_run_with_separate_inventories(tmp_path, monkeypatch):
    """THE P1 DELIVERABLE. The baseline keeps running beside the candidate,
    each over its OWN simulated inventory in its OWN file, so a candidate
    can never be credited with the baseline's fills."""
    runner = build(tmp_path, monkeypatch, feed=lambda: [], bid="0.48",
                   ask="0.50")
    out = capture(monkeypatch)
    runner.tick(NOW)
    cs = runner.shadow_controller()
    assert set(cs) == {a[0] for a in SC.SHADOW_VARIANTS}
    assert SC.BASELINE_POLICY in cs
    # the two offset arms are present, both on the baseline policy, both
    # one tick behind the touch, and they differ ONLY in two-sidedness
    deep = sorted(lbl for lbl, c in cs.items() if c.placement != SC.AT_TOUCH)
    assert deep == sorted([
        "%s@%s" % (SC.BASELINE_POLICY, SC.ONE_TICK_BEHIND),
        "%s@%s" % (SC.BASELINE_POLICY, SC.TWO_SIDED)]), deep
    for lbl in deep:
        assert cs[lbl].policy == SC.BASELINE_POLICY
        assert cs[lbl].offset_ticks == 1
    one = cs["%s@%s" % (SC.BASELINE_POLICY, SC.ONE_TICK_BEHIND)]
    two = cs["%s@%s" % (SC.BASELINE_POLICY, SC.TWO_SIDED)]
    assert one.two_sided is False and two.two_sided is True
    paths = {c.state_path for c in cs.values()}
    assert len(paths) == len(cs), paths
    for name in (a[0] for a in SC.SHADOW_VARIANTS):
        assert decisions(out, name), name
    # distinct objects, distinct state dicts
    objs = list(cs.values())
    assert objs[0].state is not objs[1].state


def test_every_policy_decides_on_the_same_book_and_instant(tmp_path,
                                                           monkeypatch):
    """A comparison across policies means nothing if they are fed different
    books, so the snapshot is taken ONCE per tick."""
    runner = build(tmp_path, monkeypatch, feed=lambda: [])
    out = capture(monkeypatch)
    runner.tick(NOW)
    recs = {(d["policy"], d.get("placement")): d for d in decisions(out)}
    assert len(recs) == len(SC.SHADOW_VARIANTS)
    ttes = {d["tte_s"] for d in recs.values()}
    ends = {d.get("window_end_ts") for d in recs.values()}
    assert len(ttes) == 1 and len(ends) == 1, (ttes, ends)
    # and the book was read once, not once per policy
    assert runner.book.snapshot.call_count <= 3


def test_one_policys_chatter_cannot_mute_the_other(tmp_path, monkeypatch):
    """The emit throttle is keyed per policy. Shared, whichever decided
    first would suppress the other's identical-looking record and the pair
    would stop being comparable."""
    runner = build(tmp_path, monkeypatch, feed=lambda: [])
    out = capture(monkeypatch)
    runner.roll(NOW)
    for i in range(6):
        runner.shadow_tick(NOW + i * 3.0)
    for name in (a[0] for a in SC.SHADOW_VARIANTS):
        assert decisions(out, name), name


def test_the_record_carries_the_model_argmax_beside_the_policys_choice(
        tmp_path, monkeypatch):
    """So a reader can see where a policy DEPARTED from the baseline rather
    than having to infer it."""
    runner = build(tmp_path, monkeypatch, feed=lambda: [], bid="0.48",
                   ask="0.50")
    out = capture(monkeypatch)
    runner.tick(NOW)
    for d in decisions(out):
        if d.get("decision") == "quote":
            assert "model_argmax" in d, d
    base = decisions(out, SC.BASELINE_POLICY)
    assert base and base[0]["relation"] == base[0]["model_argmax"], \
        "the baseline IS the argmax; if these differ it is not the baseline"


def test_one_venue_read_settles_every_policys_copy(tmp_path, monkeypatch):
    """Both policies hold the same window; settling it must cost ONE public
    read, not one per policy."""
    runner = build(tmp_path, monkeypatch, feed=lambda: [])
    capture(monkeypatch)
    runner.tick(NOW)
    for name, c in runner.shadow_controller().items():
        for scen in SC.SCENARIOS:
            inv = c.inventory_for(PMUS, scen)
            inv.record(True, D("0.47"))
            inv.record(False, D("0.49"))
            c._store_inventory(PMUS, inv, scen)
        c._slot(PMUS)["window_end_ts"] = WS + 900.0
        c._save()
    reads = {"n": 0}

    def bbo(_slug):
        reads["n"] += 1
        return {"state": "MARKET_STATE_EXPIRED", "settlementPx": "1.0000"}
    monkeypatch.setattr(R.OBS, "pmus_settlement", bbo)
    out = capture(monkeypatch)
    runner.shadow_settle(NOW + 900.0 + R.SHADOW_SETTLE_GRACE_S + 1.0)
    settled = [d for k, d in out if k == "L5_SHADOW_SETTLED"]
    assert {d["policy"] for d in settled} == set(SC.POLICIES), settled
    assert reads["n"] == 1, reads


def test_l5_is_compared_against_the_baseline_not_the_candidate(tmp_path,
                                                               monkeypatch):
    """The question is what the DEPLOYED policy would have done at L5's
    moment. Comparing against a candidate would flatter the candidate."""
    runner = build(tmp_path, monkeypatch, feed=_growing_feed())
    out = capture(monkeypatch)
    runner.tick(NOW)
    runner.tick(NOW + 3.0)
    cmp = [d for k, d in out if k == "L5_SHADOW_COMPARE"]
    assert cmp
    assert cmp[0]["compared_policy"] == SC.BASELINE_POLICY, cmp[0]


# ================================== THE OBSERVER FAN-OUT (money path)

def test_the_money_paths_observer_is_called_first_and_unwrapped():
    """ITS BEHAVIOUR MUST BE BYTE-FOR-BYTE WHAT IT WAS when it owned the
    slot alone. So it is called FIRST, and its exceptions propagate to
    `AuthBook` exactly as before -- which is what keeps `observer_errors`
    counting what it always counted."""
    order = []
    fan = R.FrameFanout(lambda m, t: order.append("primary"))
    fan.add(lambda m, t: order.append("shadow"))
    fan({}, 1.0)
    assert order == ["primary", "shadow"]

    def boom(m, t):
        raise RuntimeError("money path")
    fan2 = R.FrameFanout(boom)
    fan2.add(lambda m, t: order.append("should not matter"))
    with pytest.raises(RuntimeError):
        fan2({}, 1.0)


def test_a_shadow_observer_raising_cannot_reach_the_money_path():
    """One shadow observer failing must neither stop the others nor touch
    the primary. Counted, not swallowed silently."""
    seen = []
    fan = R.FrameFanout(lambda m, t: seen.append("primary"))

    def boom(m, t):
        raise ValueError("shadow")
    fan.add(boom)
    fan.add(lambda m, t: seen.append("second shadow"))
    fan({}, 1.0)
    assert seen == ["primary", "second shadow"]
    assert fan.errors == {"ValueError": 1}
    assert fan.status()["extra_errors"] == {"ValueError": 1}


def test_a_fanout_with_no_observers_is_inert():
    R.FrameFanout(None)({}, 1.0)          # must not raise


def test_the_roll_hands_the_ladder_to_the_shadow_and_keeps_the_primary(
        tmp_path, monkeypatch):
    """The real wiring: after a roll the book's observer is the fanout, the
    executor's own model session is its primary, and the shadow policies'
    ladder is attached beside it."""
    runner = build(tmp_path, monkeypatch, feed=lambda: [])
    capture(monkeypatch)
    runner.tick(NOW)
    fan = runner.fanout
    assert fan is not None
    assert runner.book.frame_observer is fan
    assert runner.shadow_ladder is not None
    assert fan.extra, "the shadow ladder was never attached"
    # model_fills() is on by default, so the executor's session is primary
    assert fan.primary is not None
    assert runner.model is not None


def test_closing_the_model_session_detaches_only_its_own_observer(
        tmp_path, monkeypatch):
    """This used to null the whole slot, which was right when the slot held
    exactly that session. With the fanout it would silently detach the
    shadow's ladder too."""
    runner = build(tmp_path, monkeypatch, feed=lambda: [])
    capture(monkeypatch)
    runner.tick(NOW)
    fan, lad = runner.fanout, runner.shadow_ladder
    runner.model_close()
    assert fan.primary is None, "the closed session still receives frames"
    assert runner.book.frame_observer is fan, "the shadow was detached too"
    assert runner.shadow_ladder is lad


def test_the_ladder_actually_receives_authenticated_frames(tmp_path,
                                                           monkeypatch):
    """End to end: a frame pushed through the fanout reaches the shadow's
    ladder and produces a queue position, which is the whole reason the
    fan-out exists."""
    runner = build(tmp_path, monkeypatch, feed=lambda: [])
    capture(monkeypatch)
    runner.tick(NOW)
    runner.fanout({"market_events": [
        {"marketSlug": PMUS,
         "bids": [{"px": "0.48", "qty": "250"}],
         "asks": [{"px": "0.50", "qty": "120"}]}]}, NOW)
    lad = runner.shadow_ladder
    assert lad.status()["slugs"] == 1, lad.status()
    assert lad.ahead_of_us(PMUS, "BID", D("0.48")) == D("250")


def test_the_shadow_quote_gets_a_real_queue_position_in_the_runner(
        tmp_path, monkeypatch):
    """The payoff: a quote taken through the runner carries queue_ahead
    from the authenticated ladder instead of the None that made
    conservative and base unmeasurable."""
    runner = build(tmp_path, monkeypatch, feed=lambda: [], bid="0.48",
                   ask="0.50")
    capture(monkeypatch)
    # ROLL FIRST, then feed the ladder, then decide -- all at the SAME
    # instant. Decide on a later tick and the tte bucket moves, which on
    # the real frozen model can land off-distribution and prove nothing.
    runner.roll(NOW)
    runner.fanout({"market_events": [
        {"marketSlug": PMUS,
         "bids": [{"px": "0.48", "qty": "7"}],
         "asks": [{"px": "0.50", "qty": "9"}]}]}, NOW)
    out = capture(monkeypatch)
    runner.shadow_tick(NOW)
    quoted = [d for d in decisions(out) if d.get("decision") == "quote"]
    assert quoted, [d.get("reason") for d in decisions(out)]
    assert quoted[0]["queue_ahead_at_placement"] is not None, quoted[0]
    assert quoted[0]["ticks_behind_touch"] is not None, quoted[0]


def test_a_restart_drops_resting_orders_rather_than_resuming_a_queue(
        tmp_path, monkeypatch):
    """A HypoOrder's value is its queue position AS CAPTURED AT PLACEMENT,
    and that cannot be reconstructed after a restart. Resuming one would
    credit a fill against a queue we can no longer see, so the order is
    dropped -- which can only ever lose a fill, never invent one."""
    root = tmp_path / "live"
    runner = build(tmp_path, monkeypatch, root=root, bid="0.48", ask="0.50",
                   feed=lambda: [])
    capture(monkeypatch)
    runner.tick(NOW)
    c = ctl(runner)
    resting = [scen for scen in SC.SCENARIOS
               if c._scen(PMUS, scen).get("resting")]
    assert resting, "nothing rested, so the restart proves nothing"
    again = build(tmp_path, monkeypatch, root=root, feed=lambda: [])
    c2 = ctl(again)
    assert c2._orders == {}, c2._orders
    # the persisted description survives for the record, but no live order
    # object does, so no fill can be credited against it
    assert (c2._orders.get(PMUS) or {}).get(SC.HEADLINE_SCENARIO) is None


def test_the_state_discontinuity_is_reported_not_absorbed(tmp_path):
    """v2 inventory was credited by the touch-through upper bound. Carrying
    it into v3 would overstate the conservative column promotion is judged
    on, so it is discarded -- and SAID so, on every heartbeat."""
    import json as _json
    path = tmp_path / "shadow_controller_argmax.json"
    path.write_text(_json.dumps({
        "v": 2, "markets": {PMUS: {"yes": 3, "no": 1, "yes_cost": "1.11",
                                   "no_cost": "0.49", "settled": None}},
        "carry": {"windows": 7}}))
    c = SC.ShadowController(state_path=str(path), policy="ARGMAX",
                            model={"full": {}, "base": {}, "marginal": {},
                                   "cadence_modal": {}})
    d = c.discontinuity()
    assert d and d["from_version"] == 2 and d["to_version"] == 3
    assert d["markets_discarded"] == 1
    assert d["prior_state"][PMUS]["yes"] == 3
    assert d["carry_discarded"] == {"windows": 7}
    assert "overstate" in d["reason"]
    for scen in SC.SCENARIOS:
        assert c.inventory_for(PMUS, scen).snapshot()["residual"] == 0, scen
    assert c.report()["state_discontinuity"]["from_version"] == 2


# --------------------------------------------------------------------------
# The read itself, against the document shape the venue actually serves.
#
# EVERY TEST ABOVE MONKEYPATCHES THE READ, SO NOT ONE OF THEM COULD CATCH
# THE DEFECT THAT RAN IN PRODUCTION FOR 201 POLLS: `shadow_settle` called
# `pmus_bbo`, and the fixtures handed it a dict with `settlementPx` in it --
# a document the real `pmus_bbo` cannot return for an expired market,
# because a null top of book sends it to /book, which has no settlementPx
# at all. The fixture was kinder than the venue. These tests use the venue's
# own document instead.
EXPIRED_BBO = {                     # verbatim shape, 2026-10-19 1700z
    "marketData": {
        "marketSlug": "cpc-btc-updown-15m-2026-10-19-1700z",
        "state": "MARKET_STATE_EXPIRED",
        "settlementPx": {"value": "0.0000", "currency": "USD"},
        "bestBid": None, "bestAsk": None,
        "lastTradePx": {"value": "0.0100", "currency": "USD"},
        "sharesTraded": "618747.5900",
    }
}
EXPIRED_BOOK = {"marketData": {                 # /book after expiry: empty
    "marketSlug": "cpc-btc-updown-15m-2026-10-19-1700z",
    "state": "MARKET_STATE_EXPIRED", "bids": [], "offers": []}}


def _routes(monkeypatch, bbo, book):
    seen = []

    def _get(url, timeout=20.0):
        seen.append(url)
        if url.endswith("/bbo"):
            if isinstance(bbo, BaseException):
                raise bbo
            return bbo
        if url.endswith("/book"):
            if isinstance(book, BaseException):
                raise book
            return book
        raise AssertionError("unexpected route %s" % url)
    monkeypatch.setattr(R.OBS, "_get", _get)
    return seen


def test_the_settlement_read_returns_the_venues_price_for_a_null_bbo(
        monkeypatch):
    seen = _routes(monkeypatch, EXPIRED_BBO, EXPIRED_BOOK)
    md = R.OBS.pmus_settlement("cpc-btc-updown-15m-2026-10-19-1700z")
    assert md["state"] == "MARKET_STATE_EXPIRED"
    assert R.T.settlement_decimal(md["settlementPx"]) == D(0)
    # and it does not go near the book route, which cannot answer this
    assert all(u.endswith("/bbo") for u in seen), seen


def test_the_book_reader_discards_that_price_which_is_why_they_are_separate(
        monkeypatch):
    """The live defect, pinned. If this ever starts carrying settlementPx,
    the two reads can be merged -- until then, merging them loses it."""
    _routes(monkeypatch, EXPIRED_BBO, EXPIRED_BOOK)
    md = R.OBS.pmus_bbo("cpc-btc-updown-15m-2026-10-19-1700z") or {}
    assert R.T.settlement_decimal(md.get("settlementPx")) is None
    assert md.get("_source") == "empty_both"


def test_shadow_settle_reads_the_settlement_route_not_the_book_route(
        tmp_path, monkeypatch):
    """End to end over the REAL observer: a closed window with the venue's
    own expired document settles, and the book route is never consulted."""
    runner = build(tmp_path, monkeypatch, feed=lambda: [])
    c = _closed_window(runner, monkeypatch, tmp_path)
    md = json.loads(json.dumps(EXPIRED_BBO))
    md["marketData"]["marketSlug"] = PMUS
    md["marketData"]["settlementPx"] = {"value": "1.0000", "currency": "USD"}
    seen = _routes(monkeypatch, md, EXPIRED_BOOK)
    out = capture(monkeypatch)
    runner.shadow_settle(NOW + 900.0 + R.SHADOW_SETTLE_GRACE_S + 1.0)
    rec = [d for k, d in out if k == "L5_SHADOW_SETTLED"
           and d["policy"] == SC.BASELINE_POLICY]
    assert rec, [k for k, _ in out]
    assert rec[0]["yes_value"] == "1.0000"
    assert not [u for u in seen if u.endswith("/book")], seen
    assert PMUS not in c.stranded()
    assert c.economics()["per_scenario"][
        SC.HEADLINE_SCENARIO]["windows_settled"] == 1


def test_a_failed_settlement_read_is_not_an_unsettled_market(monkeypatch):
    _routes(monkeypatch, OSError("network"), EXPIRED_BOOK)
    assert R.OBS.pmus_settlement("x") is None


def test_the_quoting_book_reader_is_unchanged_by_the_new_read(monkeypatch):
    """`pmus_bbo` is on the money path. Its behaviour on a live market must
    be exactly what it was: /bbo when populated, /book when not."""
    live = {"marketData": {"marketSlug": "m", "state": "MARKET_STATE_OPEN",
                           "bestBid": {"value": "0.45"},
                           "bestAsk": {"value": "0.46"}}}
    seen = _routes(monkeypatch, live, EXPIRED_BOOK)
    md = R.OBS.pmus_bbo("m")
    assert md["_source"] == "bbo"
    assert md["bestBid"]["value"] == "0.45"
    assert all(u.endswith("/bbo") for u in seen), seen

    book = {"marketData": {"marketSlug": "m", "state": "MARKET_STATE_OPEN",
                           "bids": [{"px": {"value": "0.45"}, "qty": "10"}],
                           "offers": [{"px": {"value": "0.46"},
                                       "qty": "12"}]}}
    seen2 = _routes(monkeypatch, {"marketData": {"state": "x"}}, book)
    md2 = R.OBS.pmus_bbo("m")
    assert md2["_source"] == "book_levels"
    assert md2["_fallback_from"] == "bbo_empty"
    assert any(u.endswith("/book") for u in seen2), seen2


# --------------------------------------------------------------------------
# The compact comparison line.
#
# The arm reports already ride in the heartbeat, but that document reached a
# quarter of a megabyte in production and the log transport splits it into
# four fragments -- the numbers that answer the question were present and
# unreadable. These guard the short line that replaces reading them, and
# guard it the way the rest of this file guards wiring: by driving the real
# runner and asserting on what the process actually printed.
# --------------------------------------------------------------------------
def test_the_heartbeat_emits_the_two_sided_comparison_as_its_own_line(
        tmp_path, monkeypatch):
    out = capture(monkeypatch)
    r = build(tmp_path, monkeypatch)
    r.shadow_controller()                      # build the arms
    r.heartbeat()
    lines = [d for k, d in out if k == "L5_SHADOW_TWO_SIDED"]
    assert len(lines) == 1, [k for k, _ in out]
    doc = lines[0]
    assert doc["control"] == SC.TWO_SIDED_CONTROL
    assert doc["treatment"] == SC.TWO_SIDED_TREATMENT
    assert doc["arms_missing"] == []
    assert doc["controlled"] is True
    # and it must actually be SHORT -- that is the entire reason it exists
    assert len(json.dumps(doc)) < 20000, len(json.dumps(doc))


def test_the_comparison_line_is_absent_rather_than_empty_with_no_arms(
        tmp_path, monkeypatch):
    """A shadow that failed to build must not print a comparison of
    nothing, which would read as a measured result of zero."""
    out = capture(monkeypatch)
    r = build(tmp_path, monkeypatch)
    r._shadow, r._shadow_off = {}, True
    r.heartbeat()
    assert not [k for k, _ in out if k == "L5_SHADOW_TWO_SIDED"]


def test_a_failure_in_the_comparison_cannot_cost_us_the_heartbeat(
        tmp_path, monkeypatch):
    """It is reporting on reporting. It gates nothing, and it must not be
    able to take down the line an operator actually reads."""
    out = capture(monkeypatch)
    r = build(tmp_path, monkeypatch)
    r.shadow_controller()

    def boom(_reports):
        raise ValueError("synthetic")
    monkeypatch.setattr(SC, "compare_two_sided", boom)
    r.heartbeat()                              # must not raise
    assert [k for k, _ in out if k == "L5_LIVE_HB"]
    assert not [k for k, _ in out if k == "L5_SHADOW_TWO_SIDED"]
    assert r.stats["two_sided_compare_error:ValueError"] == 1


def test_the_comparison_agrees_with_an_offline_run_of_the_same_reports(
        tmp_path, monkeypatch):
    """The offline wrapper must not be able to disagree with production:
    both call the same function, and this is what pins that."""
    out = capture(monkeypatch)
    r = build(tmp_path, monkeypatch)
    arms = r.shadow_controller()
    r.heartbeat()
    live = [d for k, d in out if k == "L5_SHADOW_TWO_SIDED"][0]
    offline = json.loads(json.dumps(
        SC.compare_two_sided({n: c.report() for n, c in arms.items()}),
        default=str))
    assert live == offline
