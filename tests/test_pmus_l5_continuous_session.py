"""Continuous L5 campaign (OWNER 5552036210 / 5552039820): select from
fresh authenticated evidence, run the canonical sequential session on
ONE market, reselect after a session without inventory, hold after one
with inventory. DISARMED it is a shadow selector with no venue network."""
from __future__ import annotations

import json

from src import pmus_l5_continuous_session as cont
from src import pmus_spring_l5_probe_entry as entry
from src import pmus_spring_l5_session as base
from src import pmus_l5_persistence_v2 as pv2

q1 = base.q1


def _row(slug, bid="0.50", ask="0.51", touch="500", gaps=0,
         status="MARKET_STATUS_OPEN", side="YES", **extra):
    row = {
        "slug": slug, "bid": bid, "ask": ask, "trades_1h": 25,
        "two_sided_trades": True, "off_distribution": False,
        "both_sides_served": True, "bid_touch_qty": touch,
        "collector_gaps": gaps,
        "spring_pair_target": "0.98", "spring_matched_slots": 0,
        "spring_repeated_completion": False, "spring_category": "atp",
        "spring_category_evidence": {"completions_60m": 0,
                                     "distinct_markets_60m": 0,
                                     "markets": [], "bar_met": False},
        "spring_category_bar_met": False, "spring_fee_safe_completions": 0,
        "probe_evidence": {"status": "INSUFFICIENT_EVIDENCE",
                           "reasons": ["none_yet"],
                           "min_completions_required": 2},
        "market_state": {"active": True, "archived": False, "closed": False,
                         "status": status,
                         "endDate": "2026-09-16T09:30:00Z"},
        "probe_entry_action": {"decision": "quote", "relation": "open",
                               "side": side, "target_price": bid},
    }
    row.update(extra)
    return row


def _status(rows, t=1000.0):
    return {"t": t, "spring_watch": rows}


def _model():
    b = "p45-55c|tte>10m|flat"
    return {"full": {f"{b}|open|q<5": {"open|q<5": 30, "extend|q<5": 5}},
            "base": {b: {"open|q<5": 30, "extend|q<5": 5}},
            "marginal": {"open|q<5": 0.8, "complete|q<5": 0.2},
            "cadence_modal": {}, "_sha256_16": "conttest00000000",
            "n_transitions": 35, "n_full_states": 1}


def _write_status(tmp_path, status):
    p = tmp_path / "status.jsonl"
    p.write_text(json.dumps(status) + "\n", encoding="utf-8")
    return p


def _disarm(monkeypatch):
    for k in (base.SPRING_LIVE_ENV, base.SPRING_SID_ENV,
              base.SPRING_ARM_ENV, base.SPRING_MARKET_ENV,
              base.SPRING_GATE_ENV):
        monkeypatch.delenv(k, raising=False)


def _arm(monkeypatch, tmp_path):
    monkeypatch.setenv(base.SPRING_LIVE_ENV, "YES")
    monkeypatch.setenv(base.SPRING_SID_ENV, "c1")
    monkeypatch.setenv(base.SPRING_ARM_ENV, "c1")
    monkeypatch.delenv(base.SPRING_MARKET_ENV, raising=False)
    monkeypatch.delenv(base.SPRING_GATE_ENV, raising=False)
    monkeypatch.setenv(q1.base.KEY_ENV, "k")
    monkeypatch.setenv(q1.base.SECRET_ENV, "s")
    monkeypatch.setenv(q1.base.STATE_ENV, str(tmp_path))
    monkeypatch.setattr(entry, "durable_state_reason", lambda *a, **k: None)
    from tests.test_pmus_spring_l5_session import _complement_proven
    _complement_proven(tmp_path)


# ---- selection: every row gated, admitted rows ranked, refusals named ----

def test_rank_gates_every_row_and_names_each_refusal():
    now = 1005.0
    st = _status([
        _row("deep", touch="9000"),
        _row("stale", gaps=1),
        _row("resolved", status="MARKET_STATUS_RESOLVED",
             market_state={"active": False, "archived": True,
                           "closed": True,
                           "status": "MARKET_STATUS_RESOLVED",
                           "endDate": "2026-09-05T08:00:00Z"}),
        _row("thin", touch="0.1"),
        _row("shallow", touch="20"),
    ])
    r = cont.rank_candidates(st, now, _model())
    # OWNER 5552475548 #2: a recorder cadence gap ranks LAST among equals
    # but no longer vetoes a fresh in-distribution state
    assert [x["slug"] for x in r["admitted"]] == ["deep", "shallow", "stale"]
    assert r["admitted"][0]["rank"] == 1
    # model probability and rank travel with every admitted candidate
    assert r["admitted"][0]["state_n"] == 35
    assert r["admitted"][0]["p_buy"] > 0.9
    assert r["admitted"][0]["relation_ranked"][0]["relation"] == "open"
    reasons = {x["slug"]: x["reason"] for x in r["refused"]}
    assert reasons["resolved"].startswith("strict_evidence_market")
    assert reasons["thin"].startswith("strict_evidence_no_q1_touch_depth")


def test_in_distribution_outranks_deeper_off_distribution():
    """The best CURRENT STATE, not the deepest book: a state the model
    never saw ranks below one it knows, whatever the depth."""
    now = 1005.0
    st = _status([_row("known", bid="0.50", ask="0.51", touch="100"),
                  _row("unseen", bid="0.36", ask="0.37", touch="50000")])
    r = cont.rank_candidates(st, now, _model())
    assert [x["slug"] for x in r["admitted"]] == ["known", "unseen"]
    assert r["admitted"][1]["off_distribution"] is True


def test_cooldown_excludes_a_market_that_just_refused():
    now = 1005.0
    st = _status([_row("a", touch="900"), _row("b", touch="100")])
    r = cont.rank_candidates(st, now, _model(), exclude={"a": now + 60})
    assert [x["slug"] for x in r["admitted"]] == ["b"]
    assert any(x["slug"] == "a" and x["reason"].startswith("cooldown")
               for x in r["refused"])


# ---- campaign: disarmed = shadow; armed = session then reselect/hold ----

def test_disarmed_campaign_is_shadow_only(monkeypatch, tmp_path):
    _disarm(monkeypatch)
    p = _write_status(tmp_path, _status([_row("mkt")]))
    monkeypatch.setenv(entry.EVIDENCE_STATUS_PATH_ENV, str(p))
    monkeypatch.setattr(pv2, "load_v2", lambda *a, **k: _model())
    calls = []
    monkeypatch.setattr(base, "run_session",
                        lambda *a, **k: calls.append(k) or {})
    ticks = iter([0.0, 0.0, 100.0, 100.0, 100.0])
    rep = cont.run_campaign(clock=lambda: next(ticks), sleep=lambda s: None,
                            wall=lambda: 1005.0, campaign_s=50.0)
    assert rep["mode"] == "SHADOW"
    assert rep["status"] == "SHADOW_SELECTED"
    assert rep["last_shadow"]["chosen"]["slug"] == "mkt"
    assert calls == []                       # never reached the session
    assert rep["realized_pnl"] is None


def test_stale_candidate_reselects_instead_of_terminating(monkeypatch,
                                                          tmp_path):
    """OWNER 5552039820: a transient cadence miss must not end the
    campaign. The first market refuses; the next admitted one is chosen
    on the very next cycle."""
    _arm(monkeypatch, tmp_path)
    monkeypatch.setattr(pv2, "load_v2", lambda *a, **k: _model())
    statuses = iter([
        _status([_row("first", touch="900", gaps=1),
                 _row("second", touch="100")]),
    ])
    last = {}

    def fake_load(path=None):
        try:
            last["s"] = next(statuses)
        except StopIteration:
            pass
        return last["s"], "status_url:test"
    monkeypatch.setattr(entry, "load_status_document", fake_load)
    ran = []

    def fake_session(t, clock, sleep, wall, slug, gate_ok, deadline_s):
        ran.append((slug, gate_ok, deadline_s))
        return {"status": "SPRING_NO_FILL", "slug": slug,
                "inventory": {"up": "0", "dn": "0"}, "actions": []}
    ticks = iter([0.0, 0.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0,
                  1000.0, 1000.0, 1000.0])
    rep = cont.run_campaign(clock=lambda: next(ticks), sleep=lambda s: None,
                            wall=lambda: 1005.0, campaign_s=500.0,
                            session_runner=fake_session)
    assert ran and ran[0][0] == "second"
    assert ran[0][1] is True                   # strict guard IS the gate
    assert ran[0][2] <= cont.SESSION_DEADLINE_S
    assert rep["status"] == "CAMPAIGN_RESELECTING"


def test_inventory_holds_the_campaign_to_one_market(monkeypatch, tmp_path):
    _arm(monkeypatch, tmp_path)
    monkeypatch.setattr(pv2, "load_v2", lambda *a, **k: _model())
    st = _status([_row("a", touch="900"), _row("b", touch="800")])
    monkeypatch.setattr(entry, "load_status_document",
                        lambda path=None: (st, "status_url:test"))
    ran = []

    def fake_session(t, clock, sleep, wall, slug, gate_ok, deadline_s):
        ran.append(slug)
        return {"status": "SPRING_RESIDUAL_HOLD", "slug": slug,
                "inventory": {"up": "0.01", "dn": "0"},
                "actions": [{"relation": "open"}]}
    ticks = iter([0.0] + [1.0] * 20)
    rep = cont.run_campaign(clock=lambda: next(ticks), sleep=lambda s: None,
                            wall=lambda: 1005.0, campaign_s=500.0,
                            session_runner=fake_session)
    assert ran == ["a"]                        # one market, then hold
    assert rep["status"] == "CAMPAIGN_HOLD:SPRING_RESIDUAL_HOLD"
    assert rep["sessions"][0]["selected"]["rank"] == 1


def test_no_candidate_keeps_polling_without_mutation(monkeypatch, tmp_path):
    _arm(monkeypatch, tmp_path)
    monkeypatch.setattr(pv2, "load_v2", lambda *a, **k: _model())
    st = _status([_row("x", touch="0.1")])       # below q1 touch depth
    monkeypatch.setattr(entry, "load_status_document",
                        lambda path=None: (st, "status_url:test"))
    ran = []
    ticks = iter([0.0, 1.0, 2.0, 3.0, 100.0, 100.0])
    rep = cont.run_campaign(clock=lambda: next(ticks), sleep=lambda s: None,
                            wall=lambda: 1005.0, campaign_s=50.0,
                            session_runner=lambda *a, **k: ran.append(k))
    assert ran == []
    assert rep["status"] == "CAMPAIGN_NO_CANDIDATE"


def test_campaign_starts_no_fresh_session_until_complement_proven(
        monkeypatch, tmp_path):
    """OWNER 5556038561 #3: a candidate is selected, the campaign is
    ARMED, but no opposite-side order has ever been venue-accepted -> no
    first leg, no per-slug allowance spent, WAITING status with the
    proof state logged."""
    _arm(monkeypatch, tmp_path)
    monkeypatch.setattr(pv2, "load_v2", lambda *a, **k: _model())
    (tmp_path / base.COMPLEMENT_PROOF_FILE).unlink(missing_ok=True)
    st = _status([_row("a", touch="900")])
    monkeypatch.setattr(entry, "load_status_document",
                        lambda path=None: (st, "status_url:test"))
    ran = []
    ticks = iter([0.0, 1.0, 2.0, 3.0, 100.0, 100.0])
    rep = cont.run_campaign(clock=lambda: next(ticks), sleep=lambda s: None,
                            wall=lambda: 1005.0, campaign_s=50.0,
                            session_runner=lambda *a, **k: ran.append(k))
    assert ran == []
    assert rep["status"] == "CAMPAIGN_WAITING:COMPLEMENT_UNPROVEN"
    assert rep["complement_gate"]["proven"] is False
    assert rep["complement_gate"]["reason"] == "never_attempted"
    assert rep["selections"] >= 2


def test_armed_campaign_fails_closed_without_durable_state(monkeypatch,
                                                           tmp_path):
    _arm(monkeypatch, tmp_path)
    monkeypatch.setattr(entry, "durable_state_reason",
                        lambda *a, **k: "spring_state_dir_missing:/x")
    rep = cont.run_campaign(clock=lambda: 0.0, sleep=lambda s: None,
                            wall=lambda: 1005.0, campaign_s=1.0)
    assert rep["status"] == "DURABLE_STATE_REQUIRED"


def test_base_session_logs_model_rank_and_holds_off_distribution(
        monkeypatch, tmp_path):
    """OWNER 5552036210 item 4: probability/rank on every decision; an
    off-distribution state mid-session is a logged HOLD, not a
    substituted rule and not a flatten."""
    from tests.test_pmus_spring_l5_session import Fake, _ticking
    monkeypatch.setenv(base.SPRING_LIVE_ENV, "YES")
    monkeypatch.setenv(base.SPRING_SID_ENV, "s1")
    monkeypatch.setenv(base.SPRING_ARM_ENV, "s1")
    monkeypatch.setenv(base.STATE_DURABLE_ENV, "1")
    monkeypatch.setenv(q1.base.KEY_ENV, "k")
    monkeypatch.setenv(q1.base.SECRET_ENV, "s")
    monkeypatch.setenv(q1.base.STATE_ENV, str(tmp_path))
    __import__("tests.test_pmus_spring_l5_session", fromlist=["x"])._complement_proven(tmp_path)
    monkeypatch.setattr(pv2, "load_v2", lambda *a, **k: _model())
    seq = iter([False, True, True, True, True, True])

    def fake_proposal(model, base_state, last_rel, last_size, residual,
                      clone_avg_cost=None):
        off = next(seq)
        return {"recommended_relation": "extend", "persist_extend_p": 0.6,
                "switch_complete_p": 0.3, "next_reevaluation_s": 2.0,
                "relation_ranked": [{"relation": "extend", "p": 0.6}],
                "state_n": 0 if off else 35, "off_distribution": off}
    monkeypatch.setattr(pv2, "proposal", fake_proposal)
    fake = Fake()
    rep = base.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                           wall=lambda: 1000.0, slug="mkt-x", gate_ok=True)
    assert rep["status"] == "SPRING_RESIDUAL_HOLD", rep
    assert len(rep["actions"]) == 1                   # the open only
    decs = rep["decisions"]
    assert decs[0]["relation_ranked"] and decs[0]["state_n"] == 35
    assert decs[1]["acted"] == "hold"
    assert decs[1]["refusal"] == "off_distribution_hold"
    # no sell / flatten exists; residual is simply held
    assert not any(p == "/v1/orders" and b.get("side") == "SELL"
                   for p, b in fake.posts)


# ---- owner acceptance items (5552240713 follow-up) ----

def test_full_universe_is_scanned_including_cfb():
    """League-agnostic: a CFB row is gated and ranked like any other."""
    now = 1005.0
    st = _status([_row("aec-atp-x-y-2026-09-05", touch="300"),
                  _row("atc-cfb-osu-tex-2026-09-05-osu", touch="5000"),
                  _row("aec-lol-a-b-2026-09-05", touch="10")])
    r = cont.rank_candidates(st, now, _model())
    slugs = [x["slug"] for x in r["admitted"]]
    assert "atc-cfb-osu-tex-2026-09-05-osu" in slugs
    assert len(r["admitted"]) + len(r["refused"]) == 3


def test_open_needs_no_preformed_pair_or_completion_history():
    """OWNER 5551236878 / 5552036210: OPEN admission is fresh evidence +
    in-distribution state. Zero completions, no <=0.99 pair -> admitted."""
    now = 1005.0
    st = _status([_row("fresh", spring_matched_slots=0,
                       spring_repeated_completion=False,
                       spring_fee_safe_completions=0,
                       spring_category_bar_met=False,
                       bid="0.50", ask="0.53")])   # pair 1.03: no pair
    r = cont.rank_candidates(st, now, _model())
    assert [x["slug"] for x in r["admitted"]] == ["fresh"]


def test_fill_updates_inventory_and_requeries_model_before_next_action(
        monkeypatch, tmp_path):
    """After an authoritative fill the session must recompute the state
    from REAL inventory and ask the canonical model again; the second
    query sees the residual, the third sees the extended residual."""
    from tests.test_pmus_spring_l5_session import Fake, _ticking
    monkeypatch.setenv(base.SPRING_LIVE_ENV, "YES")
    monkeypatch.setenv(base.SPRING_SID_ENV, "s1")
    monkeypatch.setenv(base.SPRING_ARM_ENV, "s1")
    monkeypatch.setenv(base.STATE_DURABLE_ENV, "1")
    monkeypatch.setenv(q1.base.KEY_ENV, "k")
    monkeypatch.setenv(q1.base.SECRET_ENV, "s")
    monkeypatch.setenv(q1.base.STATE_ENV, str(tmp_path))
    __import__("tests.test_pmus_spring_l5_session", fromlist=["x"])._complement_proven(tmp_path)
    monkeypatch.setattr(pv2, "load_v2", lambda *a, **k: _model())
    import itertools
    seen = []
    rels = itertools.chain(["open", "extend", "complete"],
                           itertools.repeat("complete"))

    def fake_proposal(model, base_state, last_rel, last_size, residual,
                      clone_avg_cost=None):
        seen.append((base_state, last_rel, residual))
        return {"recommended_relation": next(rels), "persist_extend_p": 0.5,
                "switch_complete_p": 0.4, "next_reevaluation_s": 2.0,
                "relation_ranked": [], "state_n": 35,
                "off_distribution": False}
    monkeypatch.setattr(pv2, "proposal", fake_proposal)
    fake = Fake()
    rep = base.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                           wall=lambda: 1000.0, slug="mkt-x", gate_ok=True)
    assert rep["status"] == "SPRING_MATCHED", (rep.get("reason"), seen)
    assert [a["relation"] for a in rep["actions"]] == \
        ["open", "extend", "complete", "complete"]
    # model queried once per action, with the REAL inventory of that
    # moment (NO opened first: cheaper touch, so the residual is negative)
    assert [s[2] for s in seen[:4]] == [0.0, -1.0, -2.0, -1.0]
    assert seen[1][1] == "open" and seen[2][1] == "extend"
    assert seen[1][0].endswith("imb1-10") and seen[0][0].endswith("flat")
    assert rep["inventory"]["residual"] == "0.00"
    # every SUBMITTED order is reported with its authoritative outcome
    assert [o["outcome"] for o in rep["orders"]] == ["filled"] * 4
    assert rep["orders"][0]["order_id"] == "SPR1"
    # completion economics carried the two bounds; the open carried none
    assert rep["pair_economics"]["hard_bound_px"] is not None
    assert all(b["participateDontInitiate"] is True
               for p, b in fake.posts if p == "/v1/orders")


def test_spent_cap_market_is_not_reselected_this_campaign(monkeypatch,
                                                          tmp_path):
    """A market whose durable per-market cap is spent cannot change inside
    the campaign; reselecting it every cooldown just burns cycles."""
    _arm(monkeypatch, tmp_path)
    monkeypatch.setattr(pv2, "load_v2", lambda *a, **k: _model())
    st = _status([_row("a", touch="900"), _row("b", touch="100")])
    monkeypatch.setattr(entry, "load_status_document",
                        lambda path=None: (st, "status_url:test"))
    ran = []

    def fake_session(t, clock, sleep, wall, slug, gate_ok, deadline_s):
        ran.append(slug)
        return {"status": "SPRING_NO_FILL", "slug": slug,
                "stop_reason": "spend_cap_reached",
                "inventory": {"up": "0", "dn": "0"}, "actions": []}
    import itertools
    ticks = itertools.chain([0.0] + [1.0] * 8, itertools.repeat(1000.0))
    rep = cont.run_campaign(clock=lambda: next(ticks), sleep=lambda s: None,
                            wall=lambda: 1005.0, campaign_s=500.0,
                            session_runner=fake_session)
    assert ran[:2] == ["a", "b"]
    assert "a" not in ran[1:]
    assert rep["status"] == "CAMPAIGN_RESELECTING"


# ---- OWNER 5552475548: rest, cadence-as-quality, filled-basis cap, CFB ----

def _armed_session_env(monkeypatch, tmp_path):
    monkeypatch.setenv(base.SPRING_LIVE_ENV, "YES")
    monkeypatch.setenv(base.SPRING_SID_ENV, "s1")
    monkeypatch.setenv(base.SPRING_ARM_ENV, "s1")
    monkeypatch.setenv(base.STATE_DURABLE_ENV, "1")
    monkeypatch.setenv(q1.base.KEY_ENV, "k")
    monkeypatch.setenv(q1.base.SECRET_ENV, "s")
    monkeypatch.setenv(q1.base.STATE_ENV, str(tmp_path))
    monkeypatch.setattr(pv2, "load_v2", lambda *a, **k: _model())
    from tests.test_pmus_spring_l5_session import _complement_proven
    _complement_proven(tmp_path)


def _open_proposal(monkeypatch):
    def fake_proposal(model, base_state, last_rel, last_size, residual,
                      clone_avg_cost=None):
        return {"recommended_relation": "open", "persist_extend_p": 0.1,
                "switch_complete_p": 0.1, "next_reevaluation_s": 2.0,
                "relation_ranked": [{"relation": "open", "p": 0.9}],
                "state_n": 35, "off_distribution": False}
    monkeypatch.setattr(pv2, "proposal", fake_proposal)


def test_cadence_gap_ranks_but_no_longer_vetoes_a_fresh_state():
    now = 1005.0
    st = _status([_row("gappy", gaps=1, touch="900"),
                  _row("clean", gaps=0, touch="900"),
                  _row("unknown", gaps=None, touch="900"),
                  _row("stale", gaps=0)])
    st["spring_watch"][3]["slug"] = "stale"
    r = cont.rank_candidates(st, now, _model())
    slugs = [x["slug"] for x in r["admitted"]]
    assert "gappy" in slugs and "unknown" in slugs
    assert slugs[0] == "clean"                 # quality feature, not a veto
    # a stale SNAPSHOT still fails closed
    r2 = cont.rank_candidates(_status([_row("x")], t=now - 600), now,
                              _model())
    assert r2["admitted"] == [] and \
        r2["refused"][0]["reason"].startswith("strict_evidence_stale")


def test_maker_order_rests_up_to_30s_then_cancels(monkeypatch, tmp_path):
    """No 2s blind cancel: the order rests until the deadline (30s) when
    nothing moves, and the rest time is logged for fill-hazard work."""
    from tests.test_pmus_spring_l5_session import Fake
    _armed_session_env(monkeypatch, tmp_path)
    _open_proposal(monkeypatch)
    monkeypatch.setenv(base.MAX_ACTIONS_ENV, "1")
    fake = Fake(fill_all=False)
    t = {"v": 0.0}

    def clock():
        t["v"] += 1.0
        return t["v"]
    rep = base.run_session(fake, clock=clock, sleep=lambda s: None,
                           wall=lambda: 1000.0, slug="mkt-x", gate_ok=True)
    o = rep["orders"][0]
    assert o["outcome"] == "no_fill_cancelled"
    assert o["end_reason"] == "deadline"
    assert 25.0 <= o["rest_s"] <= 35.0
    assert rep["spring_gates"]["maker_rest_max_s"] == 30.0
    # cancelled exactly, then the deadman
    assert any(p.endswith("/cancel") and p.startswith("/v1/order/")
               for p, _ in fake.posts)


def test_resting_order_is_repriced_when_the_touch_moves(monkeypatch,
                                                        tmp_path):
    """Evidence-driven early cancel: the book moves so the resting quote is
    no longer the current L5 action -> cancel well before 30s."""
    from tests.test_pmus_spring_l5_session import Fake
    _armed_session_env(monkeypatch, tmp_path)
    _open_proposal(monkeypatch)
    monkeypatch.setenv(base.MAX_ACTIONS_ENV, "1")
    fake = Fake(fill_all=False)
    polls = {"n": 0}
    real = fake.request

    def moving(method, path, body=None, **kw):
        if path.endswith("/bbo"):
            polls["n"] += 1
            if polls["n"] > 6:                 # after a few polls, move
                fake.bid, fake.ask = "0.5200", "0.5250"
        return real(method, path, body)
    fake.request = moving
    t = {"v": 0.0}

    def clock():
        t["v"] += 1.0
        return t["v"]
    rep = base.run_session(fake, clock=clock, sleep=lambda s: None,
                           wall=lambda: 1000.0, slug="mkt-x", gate_ok=True)
    assert "orders" in rep, (rep.get("status"), rep.get("reason"))
    o = rep["orders"][0]
    # the book moved up: our resting NO @ 0.495 is now at/through the NO
    # ask (1-0.52 = 0.48), so post-only safety fires first; either way
    # the quote is no longer the current L5 action and is pulled early
    assert o["end_reason"].startswith("repriced:")
    assert o["end_reason"].split(":")[1] in ("would_cross", "touch_moved")
    assert o["rest_s"] < 20.0
    assert rep["inventory"]["up"] == "0" and rep["inventory"]["dn"] == "0"


def test_cap_counts_filled_notional_not_cancelled_submissions(monkeypatch,
                                                              tmp_path):
    monkeypatch.setenv(q1.base.STATE_ENV, str(tmp_path))
    led = tmp_path / "ledger.jsonl"
    rows = [
        {"kind": "submitted", "stage": "spring_open_1", "slug": "m",
         "order_id": "A", "price": "0.46", "qty": "0.01"},
        {"kind": "order_terminal", "slug": "m", "order_id": "A",
         "filled_qty": "0", "price": "0.46"},
        {"kind": "submitted", "stage": "spring_open_2", "slug": "m",
         "order_id": "B", "price": "0.46", "qty": "0.01"},
        {"kind": "order_terminal", "slug": "m", "order_id": "B",
         "filled_qty": "0.004", "price": "0.46"},       # partial counts
        {"kind": "submitted", "stage": "spring_open_3", "slug": "m",
         "order_id": "C", "price": "0.50", "qty": "0.01"},  # unconfirmed
    ]
    led.write_text("".join(json.dumps(r) + "\n" for r in rows))
    cum = base.spring_cumulative_notional("m")
    from decimal import Decimal as D
    assert cum == D("0.46") * D("0.004") + D("0.50") * D("0.01")


def test_reconcile_gives_old_zero_fill_submissions_a_terminal_row(
        monkeypatch, tmp_path):
    """Migration for today's ledger: cancelled attempts from the earlier
    build no longer exhaust the market."""
    from tests.test_pmus_spring_l5_session import Fake
    monkeypatch.setenv(q1.base.STATE_ENV, str(tmp_path))
    led = tmp_path / "ledger.jsonl"
    led.write_text(json.dumps({"kind": "submitted", "stage": "spring_open_1",
                               "slug": "m", "order_id": "OLD1",
                               "price": "0.46", "qty": "0.01"}) + "\n")
    fake = Fake()
    fake.orders["OLD1"] = {"state": "ORDER_STATE_CANCELED", "cumQuantity": "0",
                           "avgPx": "0.46", "marketSlug": "m"}
    from decimal import Decimal as D
    assert base.spring_cumulative_notional("m") == D("0.0046")
    out = base.reconcile_submissions(fake, "m")
    assert (out["checked"], out["reconciled"], out["unresolved"]) == (1, 1, 0)
    assert base.spring_cumulative_notional("m") == D("0")


def test_live_open_state_is_rechecked_before_submission(monkeypatch,
                                                        tmp_path):
    from tests.test_pmus_spring_l5_session import Fake
    _armed_session_env(monkeypatch, tmp_path)
    _open_proposal(monkeypatch)
    fake = Fake()
    real = fake.request

    def closed(method, path, body=None, **kw):
        out = real(method, path, body)
        if path.startswith("/v1/market/slug/"):
            out["market"]["status"] = "MARKET_STATUS_RESOLVED"
            out["market"]["closed"] = True
        return out
    fake.request = closed
    rep = base.run_session(fake, clock=lambda: 0.0, sleep=lambda s: None,
                           wall=lambda: 1000.0, slug="mkt-x", gate_ok=True)
    assert rep["status"] == "SPRING_SAFE_REFUSAL"
    assert rep["reason"].startswith("live_market")
    assert not any(p == "/v1/orders" for p, _ in fake.posts)


def test_cfb_is_preferred_unless_another_market_is_materially_better():
    now = 1005.0
    cfb = _row("atc-cfb-osu-tex-2026-09-05-osu", touch="500")
    st = _status([_row("aec-atp-a-b-2026-09-05", touch="600"), cfb])
    r = cont.rank_candidates(st, now, _model())
    assert r["admitted"][0]["slug"].startswith("atc-cfb")
    assert r["admitted"][0]["cfb"] is True
    # materially better: twice the model evidence AND at least the depth
    from src.polymarket_l5.action_model import state_key
    k = state_key(0.40, 1e9, 0.0)             # the model's own bucket name
    big = _model()
    big["full"][f"{k}|open|q<5"] = {"open|q<5": 80}
    big["base"][k] = {"open|q<5": 80}
    st2 = _status([_row("aec-atp-a-b-2026-09-05", bid="0.40", ask="0.41",
                        touch="900"), cfb])
    r2 = cont.rank_candidates(st2, now, big)
    assert r2["admitted"][0]["slug"].startswith("aec-atp")


def test_attempts_per_slug_are_capped_separately_from_exposure(monkeypatch,
                                                               tmp_path):
    _arm(monkeypatch, tmp_path)
    monkeypatch.setattr(pv2, "load_v2", lambda *a, **k: _model())
    import itertools
    walls = itertools.count(1005.0, 200.0)      # every cycle clears cooldown
    last_wall = {"v": 1005.0}

    def wall():
        last_wall["v"] = next(walls)
        return last_wall["v"]
    # the recorder snapshot stays FRESH relative to the advancing clock
    monkeypatch.setattr(
        entry, "load_status_document",
        lambda path=None: (_status([_row("a", touch="900")],
                                   t=last_wall["v"]), "status_url:test"))
    ran = []

    def fake_session(t, clock, sleep, wall, slug, gate_ok, deadline_s):
        ran.append(slug)
        return {"status": "SPRING_NO_FILL", "slug": slug,
                "inventory": {"up": "0", "dn": "0"}, "actions": []}
    ticks = itertools.chain([0.0] + [1.0] * 30, itertools.repeat(1000.0))
    cont.run_campaign(clock=lambda: next(ticks), sleep=lambda s: None,
                      wall=wall, campaign_s=500.0,
                      session_runner=fake_session)
    assert ran.count("a") == cont.MAX_SESSIONS_PER_SLUG


def test_held_position_holds_the_campaign_instead_of_spinning(monkeypatch,
                                                              tmp_path):
    """An account-wide residual from an earlier session refuses every new
    market at preflight; the campaign holds at once (one market at a
    time) rather than burning its session allowance on the same refusal."""
    _arm(monkeypatch, tmp_path)
    monkeypatch.setattr(pv2, "load_v2", lambda *a, **k: _model())
    st = _status([_row("a", touch="900"), _row("b", touch="800")])
    monkeypatch.setattr(entry, "load_status_document",
                        lambda path=None: (st, "status_url:test"))
    ran = []

    def fake_session(t, clock, sleep, wall, slug, gate_ok, deadline_s):
        ran.append(slug)
        return {"status": "PREFLIGHT_FAILED", "slug": slug,
                "reason": "nonzero_positions:held-mkt=0.0200"}
    import itertools
    ticks = itertools.chain([0.0] + [1.0] * 20, itertools.repeat(1000.0))
    rep = cont.run_campaign(clock=lambda: next(ticks), sleep=lambda s: None,
                            wall=lambda: 1005.0, campaign_s=500.0,
                            session_runner=fake_session)
    assert ran == ["a"]
    assert rep["status"] == "CAMPAIGN_HOLD:POSITION_HELD"


# ---- OWNER 5552607804: resume the held market ----

def _ledger_with_fill(tmp_path, slug="held", side="OUTCOME_SIDE_YES",
                      px="0.43", qty="0.02"):
    rows = [
        {"kind": "submitted", "stage": "spring_open_1", "slug": slug,
         "order_id": "F1", "side": side, "price": px, "qty": qty},
        {"kind": "submitted", "stage": "spring_open_2", "slug": slug,
         "order_id": "F2", "side": side, "price": px, "qty": qty},
        {"kind": "order_terminal", "slug": slug, "order_id": "F1",
         "filled_qty": "0", "price": px},
        {"kind": "order_terminal", "slug": slug, "order_id": "F2",
         "filled_qty": qty, "price": px},           # the real fill
    ]
    (tmp_path / "ledger.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows))


def test_held_inventory_is_rebuilt_from_the_ledger(monkeypatch, tmp_path):
    monkeypatch.setenv(q1.base.STATE_ENV, str(tmp_path))
    _ledger_with_fill(tmp_path)
    inv = base.held_inventory_from_ledger("held")
    assert inv.snapshot()["up"] == "0.02" and inv.snapshot()["dn"] == "0"
    assert inv.avg_up() == base.D("0.43")
    assert inv.last_rel == "open" and inv.unit == base.D("0.02")
    assert inv.behavioral_residual() == 1.0
    assert base.held_inventory_from_ledger("other").snapshot()["up"] == "0"


def test_resume_continues_the_held_market_with_real_inventory(monkeypatch,
                                                              tmp_path):
    """The engine recognises 'I already own +0.02 YES here', rebuilds
    that inventory, asks the model in the post-fill state and acts on its
    answer (EXTEND here); the cap counts only NEW spend."""
    from tests.test_pmus_spring_l5_session import Fake, _ticking
    _armed_session_env(monkeypatch, tmp_path)
    _ledger_with_fill(tmp_path, slug="held")
    seen = []

    def fake_proposal(model, base_state, last_rel, last_size, residual,
                      clone_avg_cost=None):
        seen.append((base_state, last_rel, residual))
        return {"recommended_relation": "extend", "persist_extend_p": 0.7,
                "switch_complete_p": 0.3, "next_reevaluation_s": 2.0,
                "relation_ranked": [{"relation": "extend", "p": 0.7}],
                "state_n": 20, "off_distribution": False}
    monkeypatch.setattr(pv2, "proposal", fake_proposal)
    monkeypatch.setenv(base.MAX_ACTIONS_ENV, "1")
    fake = Fake(bid="0.4300", ask="0.4350")
    fake.positions["held"] = {"netPositionDecimal": "0.02"}
    rep = base.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                           wall=lambda: 1000.0, slug="held", gate_ok=True,
                           resume=True)
    assert rep["status"] == "SPRING_RESIDUAL_HOLD", rep
    assert rep["resumed_inventory"]["up"] == "0.02"
    assert seen[0][1] == "open" and seen[0][2] == 1.0
    assert seen[0][0].endswith("imb1-10")            # not 'flat'
    assert rep["decisions"][0]["acted"] == "extend"
    assert rep["orders"][0]["relation"] == "extend"
    assert rep["orders"][0]["side"] == "YES"          # same side
    assert rep["inventory"]["up"] == "0.04"
    # the resumed 0.0086 is already in the durable cap; not double counted
    assert rep["spring_gates"]["prior_cumulative_notional"] == "0.0086"


def test_resume_refuses_when_ledger_and_venue_disagree(monkeypatch,
                                                       tmp_path):
    from tests.test_pmus_spring_l5_session import Fake, _ticking
    _armed_session_env(monkeypatch, tmp_path)
    _ledger_with_fill(tmp_path, slug="held")
    fake = Fake()
    fake.positions["held"] = {"netPositionDecimal": "0.05"}
    rep = base.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                           wall=lambda: 1000.0, slug="held", gate_ok=True,
                           resume=True)
    assert rep["status"] == "SPRING_SAFE_REFUSAL"
    assert rep["reason"].startswith("inventory_mismatch")
    assert not any(p == "/v1/orders" for p, _ in fake.posts)


def test_resume_still_refuses_a_position_in_another_market(monkeypatch,
                                                           tmp_path):
    from tests.test_pmus_spring_l5_session import Fake, _ticking
    _armed_session_env(monkeypatch, tmp_path)
    _ledger_with_fill(tmp_path, slug="held")
    fake = Fake()
    fake.positions["held"] = {"netPositionDecimal": "0.02"}
    fake.positions["elsewhere"] = {"netPositionDecimal": "0.01"}
    rep = base.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                           wall=lambda: 1000.0, slug="held", gate_ok=True,
                           resume=True)
    assert rep["status"] == "PREFLIGHT_FAILED"
    assert rep["reason"].startswith("nonzero_positions_elsewhere:elsewhere")


def test_campaign_resumes_the_held_market_and_starts_no_other(monkeypatch,
                                                              tmp_path):
    from tests.test_pmus_spring_l5_session import Fake
    _arm(monkeypatch, tmp_path)
    monkeypatch.setattr(pv2, "load_v2", lambda *a, **k: _model())
    st = _status([_row("fresh-candidate", touch="9000")])
    monkeypatch.setattr(entry, "load_status_document",
                        lambda path=None: (st, "status_url:test"))
    fake = Fake()
    fake.positions["held"] = {"netPositionDecimal": "0.02"}
    ran = []

    def fake_session(t, clock, sleep, wall, slug, gate_ok, deadline_s,
                     **kw):
        ran.append((slug, kw.get("resume")))
        return {"status": "SPRING_RESIDUAL_HOLD", "slug": slug,
                "inventory": {"up": "0.02", "dn": "0"},
                "actions": []}
    import itertools
    ticks = itertools.chain([0.0] + [1.0] * 8, itertools.repeat(1000.0))
    rep = cont.run_campaign(fake, clock=lambda: next(ticks),
                            sleep=lambda s: None, wall=lambda: 1005.0,
                            campaign_s=500.0, session_runner=fake_session)
    assert ran and all(s == "held" and r is True for s, r in ran)
    assert rep["status"].startswith("CAMPAIGN_RESUMING")
    assert not any(s == "fresh-candidate" for s, _ in ran)


def test_resume_reconciles_an_unterminated_fill_before_rebuilding(
        monkeypatch, tmp_path):
    """Today's live fill (CAASE550EMVF) was written by a build that
    recorded only the submission. Resume must reconcile it against the
    venue first, or the ledger says flat while the venue says +0.02 and
    the position can never be continued."""
    from tests.test_pmus_spring_l5_session import Fake, _ticking
    _armed_session_env(monkeypatch, tmp_path)
    (tmp_path / "ledger.jsonl").write_text(json.dumps(
        {"kind": "submitted", "stage": "spring_open_3", "slug": "held",
         "order_id": "LIVE3", "side": "OUTCOME_SIDE_YES",
         "price": "0.43", "qty": "0.02"}) + "\n")
    monkeypatch.setattr(pv2, "proposal", lambda *a, **k: {
        "recommended_relation": "extend", "persist_extend_p": 0.7,
        "switch_complete_p": 0.3, "next_reevaluation_s": 2.0,
        "relation_ranked": [], "state_n": 20, "off_distribution": False})
    monkeypatch.setenv(base.MAX_ACTIONS_ENV, "1")
    fake = Fake(bid="0.4300", ask="0.4350")
    fake.positions["held"] = {"netPositionDecimal": "0.02"}
    fake.orders["LIVE3"] = {"state": "ORDER_STATE_FILLED",
                            "cumQuantity": "0.02", "avgPx": "0.43",
                            "marketSlug": "held"}
    rep = base.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                           wall=lambda: 1000.0, slug="held", gate_ok=True,
                           resume=True)
    assert rep["status"] == "SPRING_RESIDUAL_HOLD", rep
    assert rep["reconcile"]["reconciled"] == 1
    assert rep["resumed_inventory"]["up"] == "0.02"
    assert base.D(rep["resumed_inventory"]["avg_up"]) == base.D("0.43")
    assert rep["inventory"]["up"] == "0.04"       # extend filled on top


def test_held_inventory_counts_a_live_fill_once(monkeypatch, tmp_path):
    """The live session writes order_terminal BEFORE filled for the same
    order; a single pass trusting whichever came first counted one fill
    twice (ledger 0.04 vs venue 0.03 at 15:15Z). Filled rows win."""
    monkeypatch.setenv(q1.base.STATE_ENV, str(tmp_path))
    rows = [
        {"kind": "submitted", "stage": "spring_open_3", "slug": "m",
         "order_id": "OLD", "side": "OUTCOME_SIDE_YES", "price": "0.43",
         "qty": "0.02"},
        {"kind": "order_terminal", "slug": "m", "order_id": "OLD",
         "filled_qty": "0.02", "price": "0.43"},        # old build: no filled
        {"kind": "submitted", "stage": "spring_extend_1", "slug": "m",
         "order_id": "NEW", "side": "OUTCOME_SIDE_YES", "price": "0.62",
         "qty": "0.01"},
        {"kind": "order_terminal", "slug": "m", "order_id": "NEW",
         "filled_qty": "0.01", "price": "0.62"},        # new build: both
        {"kind": "filled", "stage": "spring_extend_1", "slug": "m",
         "order_id": "NEW", "side": "YES", "price": "0.62", "qty": "0.01"},
    ]
    (tmp_path / "ledger.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows))
    inv = base.held_inventory_from_ledger("m")
    assert inv.snapshot()["up"] == "0.03"
    assert inv.spent() == base.D("0.43") * base.D("0.02") + \
        base.D("0.62") * base.D("0.01")
    assert inv.last_rel == "extend"


# ---- settlement of the held market: venue-authoritative, never guessed ----

def _held_fake():
    from tests.test_pmus_spring_l5_session import Fake
    fake = Fake()
    fake.positions["held"] = {"netPositionDecimal": "0.03"}
    return fake


def _gone_session(t, clock, sleep, wall, slug, gate_ok, deadline_s, **kw):
    return {"status": "SPRING_SKIP_NOT_QUALIFYING", "slug": slug,
            "reason": "resume:bbo:Halt"}


def test_resolved_held_market_is_settled_from_venue_activities(monkeypatch,
                                                               tmp_path):
    _arm(monkeypatch, tmp_path)
    monkeypatch.setattr(cont, "status_has_trades", lambda: True)
    monkeypatch.setattr(pv2, "load_v2", lambda *a, **k: _model())
    from src import pmus_settlement_auditor as auditor
    monkeypatch.setattr(auditor, "audit_market", lambda slug, t: {
        "market": slug, "settled": True, "status": "REALIZED",
        "settlement_key": "res-1", "realized_confirmed": True,
        "realized_usd": "0.0152", "position_net": "0",
        "resolution": {"side": "YES", "tradeId": "res-1",
                       "updateTime": "2026-09-05T15:16:00Z"}})
    import itertools
    ticks = itertools.chain([0.0] + [1.0] * 8, itertools.repeat(1000.0))
    rep = cont.run_campaign(_held_fake(), clock=lambda: next(ticks),
                            sleep=lambda s: None, wall=lambda: 1005.0,
                            campaign_s=500.0, session_runner=_gone_session)
    assert rep["status"] == "CAMPAIGN_SETTLED:REALIZED"
    assert rep["realized_pnl"] == "0.0152"           # venue's number, verbatim
    assert len(rep["settlements"]) == 1              # audited once, not looped
    assert rep["settlements"][0]["ledger_rows_written"] == \
        ["settled", "realized"]
    rows = [json.loads(l) for l in
            (tmp_path / "ledger.jsonl").read_text().splitlines()]
    assert [r["kind"] for r in rows if r.get("slug") == "held"] == \
        ["settled", "realized", "settlement_breakdown"]
    assert base.q1.base.realized_today() == base.D("0.0152")


def test_unresolved_held_market_keeps_resuming_with_realized_null(
        monkeypatch, tmp_path):
    _arm(monkeypatch, tmp_path)
    monkeypatch.setattr(cont, "status_has_trades", lambda: True)
    monkeypatch.setattr(pv2, "load_v2", lambda *a, **k: _model())
    from src import pmus_settlement_auditor as auditor
    monkeypatch.setattr(auditor, "audit_market", lambda slug, t: {
        "market": slug, "settled": False, "status": "PENDING_SETTLEMENT",
        "realized_confirmed": False, "realized_usd": None,
        "position_net": "0.03", "resolution": None})
    import itertools
    ticks = itertools.chain([0.0] + [1.0] * 6, itertools.repeat(1000.0))
    rep = cont.run_campaign(_held_fake(), clock=lambda: next(ticks),
                            sleep=lambda s: None, wall=lambda: 1005.0,
                            campaign_s=500.0, session_runner=_gone_session)
    assert rep["status"].startswith("CAMPAIGN_RESUMING")
    assert rep["realized_pnl"] is None
    assert not (tmp_path / "ledger.jsonl").exists() or \
        "realized" not in (tmp_path / "ledger.jsonl").read_text()


def test_flat_campaign_settles_markets_the_ledger_filled_in(monkeypatch,
                                                            tmp_path):
    """The venue drops a resolved market from positions, so the campaign
    is flat again -- but the ledger remembers the fills. Those markets
    are audited and their realized result recorded, once."""
    from tests.test_pmus_spring_l5_session import Fake
    _arm(monkeypatch, tmp_path)
    monkeypatch.setattr(pv2, "load_v2", lambda *a, **k: _model())
    _ledger_with_fill(tmp_path, slug="gone")
    assert cont.unsettled_slugs() == ["gone"]
    st = _status([_row("x", touch="0.1")])           # nothing admitted
    monkeypatch.setattr(entry, "load_status_document",
                        lambda path=None: (st, "status_url:test"))
    from src import pmus_settlement_auditor as auditor
    calls = []

    def fake_audit(slug, t):
        calls.append(slug)
        return {"market": slug, "settled": True, "status": "REALIZED",
                "settlement_key": "res-9", "realized_confirmed": True,
                "realized_usd": "0.0152", "position_net": "0",
                "resolution": {"side": "YES", "tradeId": "res-9"}}
    monkeypatch.setattr(auditor, "audit_market", fake_audit)
    import itertools
    ticks = itertools.chain([0.0] + [1.0] * 12, itertools.repeat(1000.0))
    rep = cont.run_campaign(Fake(), clock=lambda: next(ticks),
                            sleep=lambda s: None, wall=lambda: 1005.0,
                            campaign_s=500.0,
                            session_runner=lambda *a, **k: {})
    assert calls == ["gone"]                          # audited exactly once
    assert rep["settlements"][0]["status"] == "REALIZED"
    assert cont.unsettled_slugs() == []              # ledger now carries it
    assert base.q1.base.realized_today() == base.D("0.0152")


# ---- OWNER 5552841170: continuation band, state-change wait, funnel ----

def test_continuation_extend_is_not_blocked_by_the_open_band(monkeypatch,
                                                              tmp_path):
    """The 0.35-0.65 band is OPEN admission. A held position whose model
    state at the CURRENT price (0.66) is in-distribution and says EXTEND
    extends; the band no longer freezes a valid continuation."""
    from tests.test_pmus_spring_l5_session import Fake, _ticking
    _armed_session_env(monkeypatch, tmp_path)
    _ledger_with_fill(tmp_path, slug="held")
    monkeypatch.setattr(pv2, "proposal", lambda *a, **k: {
        "recommended_relation": "extend", "persist_extend_p": 0.7,
        "switch_complete_p": 0.3, "next_reevaluation_s": 2.0,
        "relation_ranked": [], "state_n": 16, "off_distribution": False})
    monkeypatch.setenv(base.MAX_ACTIONS_ENV, "1")
    fake = Fake(bid="0.6600", ask="0.6700")
    fake.positions["held"] = {"netPositionDecimal": "0.02"}
    rep = base.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                           wall=lambda: 1000.0, slug="held", gate_ok=True,
                           resume=True)
    assert rep.get("stop_reason") is None, rep
    assert rep["orders"][0]["relation"] == "extend"
    assert base.D(rep["orders"][0]["px"]) == base.D("0.66")
    assert base.D(rep["inventory"]["up"]) > base.D("0.02")   # extended


def test_open_admission_band_still_applies_to_a_fresh_market(monkeypatch,
                                                              tmp_path):
    from tests.test_pmus_spring_l5_session import Fake, _ticking
    _armed_session_env(monkeypatch, tmp_path)
    _open_proposal(monkeypatch)
    fake = Fake(bid="0.6600", ask="0.6700")
    rep = base.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                           wall=lambda: 1000.0, slug="fresh", gate_ok=True)
    assert rep["status"] == "SPRING_SKIP_NOT_QUALIFYING"
    assert rep["reason"].startswith("outside_spring_band")


def test_off_distribution_continuation_holds(monkeypatch, tmp_path):
    """Do not extrapolate L5 beyond observed states: an off-distribution
    current state on a held position is a logged HOLD, no order."""
    from tests.test_pmus_spring_l5_session import Fake, _ticking
    _armed_session_env(monkeypatch, tmp_path)
    _ledger_with_fill(tmp_path, slug="held")
    monkeypatch.setattr(pv2, "proposal", lambda *a, **k: {
        "recommended_relation": "extend", "persist_extend_p": 0.7,
        "switch_complete_p": 0.3, "next_reevaluation_s": 2.0,
        "relation_ranked": [], "state_n": 0, "off_distribution": True})
    monkeypatch.setenv(base.MAX_ACTIONS_ENV, "2")
    fake = Fake(bid="0.7800", ask="0.7900")
    fake.positions["held"] = {"netPositionDecimal": "0.02"}
    rep = base.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                           wall=lambda: 1000.0, slug="held", gate_ok=True,
                           resume=True)
    assert rep["status"] == "SPRING_RESIDUAL_HOLD"
    assert not rep.get("orders")
    assert all(d["refusal"] == "off_distribution_hold"
               for d in rep["decisions"])


def test_await_state_change_wakes_on_book_move_else_heartbeat():
    from tests.test_pmus_spring_l5_session import Fake
    fake = Fake()
    t = {"v": 0.0}

    def clock():
        return t["v"]

    def sleep(s):
        t["v"] += s
        if t["v"] >= 15:
            fake.bid = "0.5100"
    out = cont.await_state_change(fake, "m", clock, sleep, max_wait_s=120,
                                  poll_s=5)
    assert out["reason"] == "book_changed" and out["waited_s"] <= 20
    quiet = Fake()
    t["v"] = 0.0
    out2 = cont.await_state_change(quiet, "m", clock,
                                   lambda s: t.__setitem__("v", t["v"] + s),
                                   max_wait_s=120, poll_s=5)
    assert out2["reason"] == "heartbeat" and out2["waited_s"] >= 120


def test_funnel_counts_every_cycle_by_exact_reason(monkeypatch, tmp_path):
    _arm(monkeypatch, tmp_path)
    monkeypatch.setattr(pv2, "load_v2", lambda *a, **k: _model())
    st = _status([_row("thin", touch="0.1"), _row("gone", gaps=0,
                  status="MARKET_STATUS_RESOLVED",
                  market_state={"active": False, "archived": True,
                                "closed": True,
                                "status": "MARKET_STATUS_RESOLVED",
                                "endDate": "2026-09-05T08:00:00Z"})])
    monkeypatch.setattr(entry, "load_status_document",
                        lambda path=None: (st, "status_url:test"))
    ticks = iter([0.0, 1.0, 2.0, 3.0, 100.0, 100.0])
    rep = cont.run_campaign(clock=lambda: next(ticks), sleep=lambda s: None,
                            wall=lambda: 1005.0, campaign_s=50.0,
                            session_runner=lambda *a, **k: {})
    f = rep["funnel"]
    assert f["cycles"] >= 1 and f["universe_rows"] == 2 * f["cycles"]
    assert f["admitted"] == 0 and f["submitted"] == 0 and f["filled"] == 0
    assert set(f["refusals"]) == {"strict_evidence_no_q1_touch_depth",
                                  "strict_evidence_market_active"}


# ---- orphan open orders left by a redeploy mid-rest ----

def _fake_with_open_orders(orders):
    from tests.test_pmus_spring_l5_session import Fake
    fake = Fake()
    real = fake.request

    def req(method, path, body=None, **kw):
        if path == "/v1/orders/open":
            live = [o for o in orders
                    if fake.orders.get(o["id"], {}).get("state")
                    not in ("ORDER_STATE_CANCELED",)]
            return {"orders": live}
        return real(method, path, body)
    fake.request = req
    for o in orders:
        fake.orders[o["id"]] = {"state": "ORDER_STATE_OPEN", "cumQuantity": "0",
                                "avgPx": "0.43", "marketSlug": o["marketSlug"]}
    return fake


def test_orphan_order_in_our_market_is_cancelled_at_campaign_start(
        monkeypatch, tmp_path):
    _arm(monkeypatch, tmp_path)
    monkeypatch.setattr(pv2, "load_v2", lambda *a, **k: _model())
    (tmp_path / "ledger.jsonl").write_text(json.dumps(
        {"kind": "submitted", "stage": "spring_open_1", "slug": "ours",
         "order_id": "ORPH", "price": "0.43", "qty": "0.02"}) + "\n")
    fake = _fake_with_open_orders([{"id": "ORPH", "marketSlug": "ours"}])
    st = _status([_row("x", touch="0.1")])
    monkeypatch.setattr(entry, "load_status_document",
                        lambda path=None: (st, "status_url:test"))
    import itertools
    ticks = itertools.chain([0.0, 1.0, 2.0], itertools.repeat(100.0))
    rep = cont.run_campaign(fake, clock=lambda: next(ticks),
                            sleep=lambda s: None, wall=lambda: 1005.0,
                            campaign_s=50.0, session_runner=lambda *a, **k: {})
    assert rep["orphan_sweep"]["cancelled"][0]["order_id"] == "ORPH"
    assert rep["orphan_sweep"]["foreign"] == []
    assert fake.orders["ORPH"]["state"] == "ORDER_STATE_CANCELED"
    assert "orphan_cancelled" in (tmp_path / "ledger.jsonl").read_text()


def test_open_order_in_a_market_we_never_touched_stops_the_campaign(
        monkeypatch, tmp_path):
    _arm(monkeypatch, tmp_path)
    monkeypatch.setattr(pv2, "load_v2", lambda *a, **k: _model())
    fake = _fake_with_open_orders([{"id": "X1", "marketSlug": "not-ours"}])
    rep = cont.run_campaign(fake, clock=lambda: 0.0, sleep=lambda s: None,
                            wall=lambda: 1005.0, campaign_s=50.0,
                            session_runner=lambda *a, **k: {})
    assert rep["status"] == "CAMPAIGN_STOP:FOREIGN_OPEN_ORDERS"
    assert fake.orders["X1"]["state"] == "ORDER_STATE_OPEN"     # untouched


def test_extend_stops_at_the_residual_cap_but_complete_may_continue(
        monkeypatch, tmp_path):
    """Bounded imbalance: at 5 behavioral units of residual the model's
    EXTEND is a logged HOLD (residual_cap), nothing is flattened, and a
    COMPLETE still prices through the pair bounds."""
    from tests.test_pmus_spring_l5_session import Fake, _ticking
    _armed_session_env(monkeypatch, tmp_path)
    rows = []
    for i in range(5):                          # five 0.02 YES fills
        rows.append({"kind": "submitted", "stage": f"spring_extend_{i}",
                     "slug": "held", "order_id": f"F{i}",
                     "side": "OUTCOME_SIDE_YES", "price": "0.41",
                     "qty": "0.02"})
        rows.append({"kind": "order_terminal", "slug": "held",
                     "order_id": f"F{i}", "filled_qty": "0.02",
                     "price": "0.41"})
    (tmp_path / "ledger.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows))
    rels = iter(["extend", "complete"])
    monkeypatch.setattr(pv2, "proposal", lambda *a, **k: {
        "recommended_relation": next(rels), "persist_extend_p": 0.6,
        "switch_complete_p": 0.4, "next_reevaluation_s": 2.0,
        "relation_ranked": [], "state_n": 49, "off_distribution": False})
    monkeypatch.setenv(base.MAX_ACTIONS_ENV, "2")
    fake = Fake(bid="0.4100", ask="0.4200")
    fake.positions["held"] = {"netPositionDecimal": "0.10"}
    rep = base.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                           wall=lambda: 1000.0, slug="held", gate_ok=True,
                           resume=True)
    assert rep["decisions"][0]["acted"] == "hold"
    assert rep["decisions"][0]["refusal"].startswith("residual_cap:5")
    assert rep["decisions"][1]["acted"] == "complete"
    assert rep["orders"][0]["relation"] == "complete"
    assert rep["orders"][0]["side"] == "NO"
    assert not any(b.get("action") == "ORDER_ACTION_SELL"
                   for p, b in fake.posts if p == "/v1/orders")


def test_settlement_breakdown_separates_matched_from_residual(monkeypatch,
                                                              tmp_path):
    """FILLED -> SETTLED -> REALIZED with the cash decomposed: matched
    pairs are arithmetic, the residual contribution is what the venue's
    realized number leaves after them; fees are reported unreported,
    never assumed."""
    monkeypatch.setenv(q1.base.STATE_ENV, str(tmp_path))
    rows = [
        {"kind": "submitted", "stage": "spring_open_1", "slug": "m",
         "order_id": "A", "side": "OUTCOME_SIDE_YES", "price": "0.43",
         "qty": "0.02"},
        {"kind": "filled", "stage": "spring_open_1", "slug": "m",
         "order_id": "A", "side": "YES", "price": "0.43", "qty": "0.02"},
        {"kind": "submitted", "stage": "spring_extend_2", "slug": "m",
         "order_id": "B", "side": "OUTCOME_SIDE_YES", "price": "0.62",
         "qty": "0.01"},
        {"kind": "filled", "stage": "spring_extend_2", "slug": "m",
         "order_id": "B", "side": "YES", "price": "0.62", "qty": "0.01"},
        {"kind": "submitted", "stage": "spring_complete_3", "slug": "m",
         "order_id": "C", "side": "OUTCOME_SIDE_NO", "price": "0.50",
         "qty": "0.01"},
        {"kind": "filled", "stage": "spring_complete_3", "slug": "m",
         "order_id": "C", "side": "NO", "price": "0.50", "qty": "0.01"},
    ]
    (tmp_path / "ledger.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows))
    audit = {"settled": True, "status": "REALIZED", "realized_usd": "0.0152",
             "resolution": {"side": "POSITION_RESOLUTION_SIDE_LONG"},
             "trade_summary": {"trades": [{"isAggressor": False}] * 3}}
    b = cont.settlement_breakdown("m", audit)
    D = base.D
    assert b["bought_yes"] == "0.03" and b["bought_no"] == "0.01"
    assert b["matched_pairs"] == "0.01" and b["directional_residual"] == "0.02"
    # matched pair cost = avg_yes (0.4933..) + avg_no (0.50)
    assert abs(D(b["matched_pair_cost"]) - D("0.9933")) < D("0.001")
    assert D(b["matched_contribution_usd"]) == D("0.01") * (
        D("1") - D(b["matched_pair_cost"]))
    assert D(b["residual_contribution_usd"]) == D("0.0152") - D(
        b["matched_contribution_usd"])
    assert b["maker_fills"] == 3 and b["taker_fills"] == 0
    assert b["fees_usd"] == "venue_unreported"
    assert b["realized_usd_venue"] == "0.0152"


# ---- OWNER 5553189142: the throughput rung and its advance gate ----

def test_rung_works_three_sequential_markets_then_stops_at_the_gate(
        monkeypatch, tmp_path):
    """Up to N filled markets, each after the prior is settled,
    concurrency 1, then CAMPAIGN_RUNG_COMPLETE with a reconciliation
    report; worst case is N x the per-market cap."""
    from tests.test_pmus_spring_l5_session import Fake
    _arm(monkeypatch, tmp_path)
    monkeypatch.setenv(cont.RUNG_MARKETS_ENV, "3")
    monkeypatch.setattr(pv2, "load_v2", lambda *a, **k: _model())
    fake = Fake()
    order = iter(["m1", "m2", "m3"])
    last_wall = {"v": 1005.0}
    st = {"t": 1005.0, "spring_watch": [_row("m1", touch="900"),
                                        _row("m2", touch="800"),
                                        _row("m3", touch="700")]}

    def load(path=None):
        st["t"] = last_wall["v"]                # snapshot stays fresh
        return st, "status_url:test"
    monkeypatch.setattr(entry, "load_status_document", load)
    from src import pmus_settlement_auditor as auditor
    monkeypatch.setattr(auditor, "audit_market", lambda slug, t: {
        "market": slug, "settled": True, "status": "REALIZED",
        "settlement_key": f"res-{slug}", "realized_confirmed": True,
        "realized_usd": "0.0100", "position_net": "0", "resolution": {}})

    def fake_session(t, clock, sleep, wall, slug, gate_ok, deadline_s,
                     **kw):
        if kw.get("resume"):
            return {"status": "SPRING_SKIP_NOT_QUALIFYING", "slug": slug,
                    "reason": "resume:bbo:Halt"}
        fake.positions[slug] = {"netPositionDecimal": "0.02"}
        return {"status": "SPRING_RESIDUAL_HOLD", "slug": slug,
                "inventory": {"up": "0.02", "dn": "0"},
                "actions": [{"relation": "open"}], "reconcile": {"unresolved": 0}}
    # settlement drops the position from the venue
    real_settle = cont.settle_held

    def settle(t, slug):
        out = real_settle(t, slug)
        fake.positions.pop(slug, None)
        return out
    monkeypatch.setattr(cont, "settle_held", settle)
    import itertools
    ticks = itertools.chain([0.0] + [1.0] * 600, itertools.repeat(1000.0))
    walls = itertools.count(1005.0, 130.0)

    def wall():
        last_wall["v"] = next(walls)
        return last_wall["v"]
    rep = cont.run_campaign(fake, clock=lambda: next(ticks),
                            sleep=lambda s: None, wall=wall,
                            campaign_s=500.0, session_runner=fake_session)
    assert rep["status"] == "CAMPAIGN_RUNG_COMPLETE", rep["status"]
    assert rep["rung"]["markets_allowed"] == 3
    assert rep["rung"]["worst_case_usd"] == "0.15"
    assert rep["advance_gate"]["filled_markets"] == ["m1", "m2", "m3"]
    assert rep["advance_gate"]["all_clean"] is True
    assert rep["realized_pnl"] == "0.0300"


def test_funnel_counts_only_fills_made_in_this_session():
    """Live funnel read `filled: 90` for six real fills: a resumed market
    re-carries its whole fill history in `actions` every cycle. Only the
    session's own reported orders with a positive filled quantity count."""
    rep = {"actions": [{}] * 6,
           "orders": [{"filled_qty": "0.02"}, {"filled_qty": "0"},
                      {"filled_qty": "0.01"}, {"filled_qty": None},
                      {"filled_qty": "junk"}]}
    assert cont._new_fills(rep) == 2
    assert cont._new_fills({"actions": [{}] * 6}) == 0
    assert cont._new_fills({}) == 0


# ---- LIVE 17:40:52Z: a venue 500 between sessions must not kill the campaign ----

def test_transient_venue_error_between_sessions_is_waited_out(monkeypatch,
                                                              tmp_path):
    """held_market() raised Halt(GET_RETRIES_EXHAUSTED:/v1/orders/open:
    HTTP Error 500) and the process died with +0.06 YES held. A transient
    venue read error is a logged, bounded wait; the campaign goes on."""
    from tests.test_pmus_spring_l5_session import Fake
    _arm(monkeypatch, tmp_path)
    monkeypatch.setattr(pv2, "load_v2", lambda *a, **k: _model())
    st = _status([_row("x", touch="0.1")])           # nothing admitted
    monkeypatch.setattr(entry, "load_status_document",
                        lambda path=None: (st, "status_url:test"))
    calls = {"n": 0}

    def flaky_held(t):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise q1.Halt("GET_RETRIES_EXHAUSTED:/v1/orders/open:HTTP Error 500")
        return None
    monkeypatch.setattr(cont, "held_market", flaky_held)
    slept = []
    import itertools
    ticks = itertools.chain([0.0] + [1.0] * 12, itertools.repeat(1000.0))
    rep = cont.run_campaign(Fake(), clock=lambda: next(ticks),
                            sleep=lambda s: slept.append(s),
                            wall=lambda: 1005.0, campaign_s=500.0,
                            session_runner=lambda *a, **k: {})
    assert calls["n"] >= 3                            # survived both errors
    assert rep["status"] == "CAMPAIGN_NO_CANDIDATE"   # ...and kept selecting
    assert rep["selections"] >= 1
    assert slept[:2] == [cont.ITERATION_ERROR_BACKOFF_S] * 2


def test_sustained_venue_failure_waits_closed_until_campaign_expiry(
        monkeypatch, tmp_path):
    """Waiting is the fail-closed state: no selection, no order, the
    backoff slows to 60s after 10 errors, and the campaign ends only at
    its own expiry -- never as an exited process with a position held."""
    from tests.test_pmus_spring_l5_session import Fake
    _arm(monkeypatch, tmp_path)
    monkeypatch.setattr(pv2, "load_v2", lambda *a, **k: _model())
    monkeypatch.setattr(entry, "load_status_document",
                        lambda path=None: (_status([]), "status_url:test"))

    def dead(t):
        raise q1.Halt("GET_RETRIES_EXHAUSTED:/v1/orders/open:HTTP Error 500")
    monkeypatch.setattr(cont, "held_market", dead)
    slept = []
    import itertools
    ticks = itertools.chain([0.0] + [1.0] * 200, itertools.repeat(1000.0))
    rep = cont.run_campaign(Fake(), clock=lambda: next(ticks),
                            sleep=lambda s: slept.append(s),
                            wall=lambda: 1005.0,
                            campaign_s=500.0, session_runner=lambda *a, **k: {})
    assert rep["status"] == "CAMPAIGN_WAITING:VENUE_UNAVAILABLE"
    assert rep["selections"] == 0                     # never traded through it
    assert slept[:10] == [cont.ITERATION_ERROR_BACKOFF_S] * 10
    assert slept[10:12] == [cont.ITERATION_ERROR_SLOW_BACKOFF_S] * 2


def test_sigterm_still_propagates_through_the_guard(monkeypatch, tmp_path):
    from tests.test_pmus_spring_l5_session import Fake
    _arm(monkeypatch, tmp_path)
    monkeypatch.setattr(pv2, "load_v2", lambda *a, **k: _model())

    def term(t):
        raise KeyboardInterrupt("SIGTERM")
    monkeypatch.setattr(cont, "held_market", term)
    import pytest
    with pytest.raises(KeyboardInterrupt):
        cont.run_campaign(Fake(), clock=lambda: 0.0, sleep=lambda s: None,
                          wall=lambda: 1005.0, campaign_s=500.0,
                          session_runner=lambda *a, **k: {})


def test_first_read_after_an_outage_is_reported_and_resweeps_open_orders(
        monkeypatch, tmp_path):
    """OWNER 5553997094 #2: the first successful account read after a
    venue outage is logged as a recovery and open orders are reconciled
    again before anything can be submitted."""
    from tests.test_pmus_spring_l5_session import Fake
    _arm(monkeypatch, tmp_path)
    monkeypatch.setattr(pv2, "load_v2", lambda *a, **k: _model())
    monkeypatch.setattr(entry, "load_status_document",
                        lambda path=None: (_status([_row("x", touch="0.1")]),
                                           "status_url:test"))
    calls = {"n": 0}

    def flaky_held(t):
        calls["n"] += 1
        if calls["n"] <= 3:
            raise q1.Halt("GET_RETRIES_EXHAUSTED:/v1/orders/open:HTTP Error 500")
        return None
    monkeypatch.setattr(cont, "held_market", flaky_held)
    sweeps = []
    real_sweep = cont.sweep_orphan_orders
    monkeypatch.setattr(cont, "sweep_orphan_orders",
                        lambda t: sweeps.append(1) or real_sweep(t))
    import itertools
    ticks = itertools.chain([0.0] + [1.0] * 14, itertools.repeat(1000.0))
    rep = cont.run_campaign(Fake(), clock=lambda: next(ticks),
                            sleep=lambda s: None, wall=lambda: 1005.0,
                            campaign_s=500.0, session_runner=lambda *a, **k: {})
    assert len(sweeps) == 2                     # boot + recovery
    assert len(rep["recoveries"]) == 1
    rec = rep["recoveries"][0]
    assert rec["errors"] == 3 and rec["held"] is None
    assert rec["sweep"]["foreign"] == [] and rec["outage_s"] >= 0
    assert rep["status"] == "CAMPAIGN_NO_CANDIDATE"


# ---- OWNER 5554127702 #1/#5: recovery gate, postmortem stamps, burst ----

def test_recovery_gate_waits_for_public_prints_then_resumes_and_reports_burst(
        monkeypatch, tmp_path):
    """Account reads healthy is not enough: no session runs until public
    prints move (sharesTraded up or the recorder tape shows trades).
    Outage start / account recovery / trades resumed are stamped, and the
    30-minute recovery burst is reported."""
    from tests.test_pmus_spring_l5_session import Fake
    _arm(monkeypatch, tmp_path)
    monkeypatch.setattr(pv2, "load_v2", lambda *a, **k: _model())
    tape = {"trades": 0}
    tick = {"v": 0.0}
    # the status doc ages with the wall (never from the future: the clock
    # ticks between `now` and the status read); freshness is not the
    # subject of this test
    monkeypatch.setenv(entry.STRICT_EVIDENCE_MAX_AGE_ENV, "1000000")
    monkeypatch.setattr(
        entry, "load_status_document",
        lambda path=None: (_status([_row("x", touch="900",
                                         trades_1h=tape["trades"])],
                                   t=tick["v"] - 2000.0),
                           "status_url:test"))
    fake = Fake()
    shares = {"v": 100.0}
    real = fake.request

    def req(method, path, body=None, **kw):
        out = real(method, path, body)
        if path.endswith("/bbo"):
            out["marketData"]["sharesTraded"] = str(shares["v"])
        return out
    fake.request = req
    calls = {"n": 0}

    def flaky_held(t):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise q1.Halt("GET_RETRIES_EXHAUSTED:/v1/orders/open:HTTP Error 500")
        if calls["n"] == 6:
            shares["v"] = 101.0                 # a print lands...
            tape["trades"] = 25                 # ...and the row's flow returns
        return None
    monkeypatch.setattr(cont, "held_market", flaky_held)
    ran = []

    def fake_session(t, clock, sleep, wall, slug, gate_ok, deadline_s, **kw):
        ran.append(slug)
        return {"status": "SPRING_NO_FILL", "slug": slug,
                "inventory": {"up": "0", "dn": "0"}, "actions": [],
                "orders": [{"outcome": "no_fill_cancelled",
                            "end_reason": "deadline", "filled_qty": "0"}],
                "decisions": [{"acted": "open",
                               "shadow_differs_from_executed": False}]}
    import itertools
    # each tick is 5 minutes of clock so the 30-minute milestone is reached
    ticks = itertools.chain([0.0], (60.0 * i for i in range(1, 700)),
                            itertools.repeat(100000.0))

    def clock():
        tick["v"] = next(ticks)
        return tick["v"]
    rep = cont.run_campaign(fake, clock=clock,
                            sleep=lambda s: None, wall=lambda: 1005.0 + tick["v"],
                            campaign_s=40000.0, session_runner=fake_session)
    assert rep["outages"] and rep["outages"][0]["errors"] == 2
    o = rep["outages"][0]
    assert o["start_utc"] and o["account_recovered_utc"] and o["trades_resumed_utc"]
    assert o["trades_resumed_by"] == {"markets_moved": ["x"]}
    # no session ran while the gate was pending (held calls 3..5)
    assert ran and calls["n"] >= 6
    assert rep["recovery_bursts"] and rep["recovery_bursts"][0]["minutes"] == 30
    rb = rep["recovery_bursts"][0]
    assert rb["sessions"] >= 1 and rb["submitted"] >= 1 and rb["filled"] == 0
    assert rb["decisions_by_action"]["open"] >= 1


def test_recovery_gate_accepts_the_recorder_tape_as_prints(monkeypatch,
                                                            tmp_path):
    from tests.test_pmus_spring_l5_session import Fake
    _arm(monkeypatch, tmp_path)
    monkeypatch.setattr(pv2, "load_v2", lambda *a, **k: _model())
    monkeypatch.setattr(entry, "load_status_document",
                        lambda path=None: (_status([_row("x", touch="0.1")]),
                                           "status_url:test"))   # trades_1h 25
    calls = {"n": 0}

    def flaky_held(t):
        calls["n"] += 1
        if calls["n"] == 1:
            raise q1.Halt("GET_RETRIES_EXHAUSTED:/v1/orders/open:HTTP Error 500")
        return None
    monkeypatch.setattr(cont, "held_market", flaky_held)
    import itertools
    ticks = itertools.chain([0.0] + [1.0] * 10, itertools.repeat(1000.0))
    rep = cont.run_campaign(Fake(), clock=lambda: next(ticks),
                            sleep=lambda s: None, wall=lambda: 1005.0,
                            campaign_s=500.0, session_runner=lambda *a, **k: {})
    assert rep["outages"][0]["trades_resumed_by"] == {"recorder_tape": True}
    assert rep["status"] == "CAMPAIGN_NO_CANDIDATE"
    assert rep["rung"]["ladder_rung"] == "R1" and rep["ramp_ladder"][0]["id"] == "R1"


def test_capped_extend_falls_to_complete_only_when_the_flag_is_on(monkeypatch,
                                                                   tmp_path):
    """OWNER 5554106892 next-mismatch candidate, prebuilt OFF: at the
    residual cap the modal policy HOLDs 100% while L5 COMPLETEs 43-48% in
    those states. With PMUS_SPRING_CAP_FALLBACK_COMPLETE=1 a capped
    EXTEND becomes the second-ranked COMPLETE (mass >= 0.20), priced by
    the unchanged <=0.98 / >=1c bounds; without the flag nothing moves."""
    from tests.test_pmus_spring_l5_session import Fake, _ticking
    _armed_session_env(monkeypatch, tmp_path)
    rows = []
    for i in range(3):                          # three 0.02 YES fills = cap
        rows.append({"kind": "submitted", "stage": f"spring_extend_{i}",
                     "slug": "held", "order_id": f"F{i}",
                     "side": "OUTCOME_SIDE_YES", "price": "0.49",
                     "qty": "0.02"})
        rows.append({"kind": "order_terminal", "slug": "held",
                     "order_id": f"F{i}", "filled_qty": "0.02",
                     "price": "0.49"})
    (tmp_path / "ledger.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows))
    monkeypatch.setattr(pv2, "proposal", lambda *a, **k: {
        "recommended_relation": "extend", "persist_extend_p": 0.756,
        "switch_complete_p": 0.244, "next_reevaluation_s": 2.0,
        "relation_ranked": [{"relation": "extend", "p": 0.756},
                            {"relation": "complete", "p": 0.244}],
        "state_n": 16, "off_distribution": False})
    monkeypatch.setenv(base.MAX_ACTIONS_ENV, "1")
    # flag OFF: hold at the cap, no order
    monkeypatch.delenv(base.CAP_FALLBACK_ENV, raising=False)
    fake = Fake(bid="0.6900", ask="0.7400")     # NO ask 0.31: pair 0.80
    fake.positions["held"] = {"netPositionDecimal": "0.06"}
    rep = base.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                           wall=lambda: 1000.0, slug="held", gate_ok=True,
                           resume=True)
    assert rep["decisions"][0]["acted"] == "hold"
    assert rep["decisions"][0]["refusal"].startswith("residual_cap:3.0")
    assert not rep.get("orders")
    # flag ON: the capped extend becomes a fee-safe COMPLETE on the NO side
    monkeypatch.setenv(base.CAP_FALLBACK_ENV, "1")
    fake = Fake(bid="0.6900", ask="0.7400")
    fake.positions["held"] = {"netPositionDecimal": "0.06"}
    rep = base.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                           wall=lambda: 1000.0, slug="held", gate_ok=True,
                           resume=True)
    d = rep["decisions"][0]
    assert d["acted"] == "complete"
    assert d["refusal"].startswith("residual_cap:fallback_complete:p=0.244")
    o = rep["orders"][0]
    assert o["relation"] == "complete" and o["side"] == "NO"
    assert base.D(o["px"]) <= base.D("0.49")          # <= 0.98 - avg 0.49
    assert rep["pair_economics"]["regime"] in ("spring_target", "hard_bound_only")
    assert not any(b.get("action") == "ORDER_ACTION_SELL"
                   for p, b in fake.posts if p == "/v1/orders")


# ---- OWNER 5554553793 A: restart persistence, boot gate, recovery sim ----

def _two_run_harness(monkeypatch, tmp_path, shares, tape):
    from tests.test_pmus_spring_l5_session import Fake
    _arm(monkeypatch, tmp_path)
    monkeypatch.setattr(pv2, "load_v2", lambda *a, **k: _model())
    monkeypatch.setattr(
        entry, "load_status_document",
        lambda path=None: (_status([_row("x", touch="900", trades_1h=25)]),
                           "status_url:test"))
    fake = Fake()
    real = fake.request

    def req(method, path, body=None, **kw):
        out = real(method, path, body)
        if path.endswith("/bbo"):
            out["marketData"]["sharesTraded"] = str(shares["v"])
        return out
    fake.request = req
    return fake


def test_outage_baseline_and_gate_survive_a_restart(monkeypatch, tmp_path):
    """Run 1 enters an outage (baseline sampled), the account recovers,
    the gate is pending when the process dies. Run 2 (same durable
    state dir) restores the baseline and the pending gate: no session
    runs until prints move past the ORIGINAL baseline."""
    shares, tape = {"v": 100.0}, {"trades": 0}
    fake = _two_run_harness(monkeypatch, tmp_path, shares, tape)
    calls = {"n": 0}

    def flaky_held(t):
        calls["n"] += 1
        if calls["n"] in (2, 3):
            raise q1.Halt("GET_RETRIES_EXHAUSTED:/v1/orders/open:HTTP Error 500")
        return None
    monkeypatch.setattr(cont, "held_market", flaky_held)
    monkeypatch.setattr(cont, "status_has_trades", lambda: False)
    ran = []
    fake_session = lambda t, clock, sleep, wall, slug, **kw: ran.append(slug) or {}
    import itertools
    ticks = itertools.chain([0.0] + [1.0] * 60, itertools.repeat(1000.0))
    rep1 = cont.run_campaign(fake, clock=lambda: next(ticks),
                             sleep=lambda s: None, wall=lambda: 1005.0,
                             campaign_s=500.0, session_runner=fake_session)
    assert rep1["status"] == "CAMPAIGN_WAITING:VENUE_PRINTS" and not ran
    saved = json.loads((tmp_path / "l5_campaign_state.json").read_text())
    assert saved["recovery_gate"]["pending"] is True
    assert saved["liveness_baseline"] == {"x": 100.0}
    assert saved["outage"]["first_error"] == "boot_without_public_prints" \
        or saved["outage"]["first_error"].startswith("Halt")
    # ---- restart: account healthy from the first read, tape still frozen
    monkeypatch.setattr(cont, "held_market", lambda t: None)
    ticks2 = itertools.chain([0.0] + [1.0] * 60, itertools.repeat(1000.0))
    rep2 = cont.run_campaign(fake, clock=lambda: next(ticks2),
                             sleep=lambda s: None, wall=lambda: 1005.0,
                             campaign_s=500.0, session_runner=fake_session)
    assert rep2["restored_state"]["recovery_gate"]["pending"] is True
    assert rep2["status"] == "CAMPAIGN_WAITING:VENUE_PRINTS" and not ran
    # ---- restart again: a print lands above the ORIGINAL baseline
    shares["v"] = 100.5
    ticks3 = itertools.chain([0.0] + [1.0] * 60, itertools.repeat(1000.0))
    rep3 = cont.run_campaign(fake, clock=lambda: next(ticks3),
                             sleep=lambda s: None, wall=lambda: 1005.0,
                             campaign_s=500.0, session_runner=fake_session)
    assert rep3["outages"][0]["trades_resumed_by"] == {"markets_moved": ["x"]}
    assert ran == ["x"]
    saved3 = json.loads((tmp_path / "l5_campaign_state.json").read_text())
    assert saved3["recovery_gate"] is None and saved3["outage"] is None
    assert saved3["burst"]["start_wall"] == 1005.0


def test_boot_without_public_prints_waits_before_any_session(monkeypatch,
                                                              tmp_path):
    """A fresh boot (no persisted state) while the recorder tape shows no
    trades is treated as inside an outage: baseline sampled, gate
    pending, no session -- until a print lands."""
    shares, tape = {"v": 50.0}, {"trades": 0}
    fake = _two_run_harness(monkeypatch, tmp_path, shares, tape)
    monkeypatch.setattr(cont, "held_market", lambda t: None)
    calls = {"n": 0}

    def status_trades():
        calls["n"] += 1
        if calls["n"] == 4:
            tape["trades"] = 3                    # tape moves on 4th check
        return tape["trades"] > 0
    monkeypatch.setattr(cont, "status_has_trades", status_trades)
    ran = []
    import itertools
    ticks = itertools.chain([0.0] + [1.0] * 60, itertools.repeat(1000.0))
    rep = cont.run_campaign(fake, clock=lambda: next(ticks),
                            sleep=lambda s: None, wall=lambda: 1005.0,
                            campaign_s=500.0,
                            session_runner=lambda t, clock, sleep, wall, slug,
                            **kw: ran.append(slug) or {})
    o = rep["outages"][0]
    assert o["first_error"] == "boot_without_public_prints"
    assert o["trades_resumed_by"] == {"recorder_tape": True}
    assert ran == ["x"]


def test_recovery_burst_milestones_are_not_double_counted_after_restart(
        monkeypatch, tmp_path):
    shares, tape = {"v": 100.0}, {"trades": 5}
    fake = _two_run_harness(monkeypatch, tmp_path, shares, tape)
    monkeypatch.setattr(cont, "held_market", lambda t: None)
    monkeypatch.setattr(cont, "status_has_trades", lambda: True)
    # a burst already reported the 30-minute milestone before the restart
    cont.save_campaign_state({
        "saved_utc": "x", "rung": "R1", "filled_markets": ["m1"],
        "settled_here": ["m1"], "outage": None, "liveness_baseline": {},
        "recovery_gate": None,
        "burst": {"start_wall": 1000.0, "funnel0": {}, "sessions0": 0,
                  "settlements0": 0, "milestones": [30, 60, 120],
                  "done": [30]}})
    import itertools
    ticks = itertools.chain([0.0] + [1.0] * 60, itertools.repeat(1000.0))
    rep = cont.run_campaign(fake, clock=lambda: next(ticks),
                            sleep=lambda s: None,
                            wall=lambda: 1000.0 + 61 * 60,    # 61 min later
                            campaign_s=500.0,
                            session_runner=lambda *a, **k: {})
    assert [b["minutes"] for b in rep["recovery_bursts"]] == [60]
    assert rep["recovery_bursts"][0]["labels"].startswith("after a restart")
    assert rep["restored_state"]["filled_markets"] == ["m1"]
    saved = json.loads((tmp_path / "l5_campaign_state.json").read_text())
    assert saved["burst"]["done"] == [30, 60]


def test_rung_fills_survive_a_restart(monkeypatch, tmp_path):
    """Two markets filled and settled before the restart count toward
    the 3-market R1 rung; the third completes it."""
    shares, tape = {"v": 100.0}, {"trades": 5}
    fake = _two_run_harness(monkeypatch, tmp_path, shares, tape)
    monkeypatch.setattr(cont, "status_has_trades", lambda: True)
    cont.save_campaign_state({
        "saved_utc": "x", "rung": "R1", "filled_markets": ["m1", "m2"],
        "settled_here": ["m1", "m2"], "outage": None,
        "liveness_baseline": {}, "recovery_gate": None, "burst": None})
    fake.positions["x"] = {"netPositionDecimal": "0.03"}
    from src import pmus_settlement_auditor as auditor
    monkeypatch.setattr(auditor, "audit_market", lambda slug, t: {
        "market": slug, "settled": True, "status": "REALIZED",
        "settlement_key": "r", "realized_confirmed": True,
        "realized_usd": "0.01", "position_net": "0",
        "resolution": {"side": "YES", "tradeId": "r", "updateTime": "t"}})
    import itertools
    ticks = itertools.chain([0.0] + [1.0] * 60, itertools.repeat(1000.0))
    rep = cont.run_campaign(fake, clock=lambda: next(ticks),
                            sleep=lambda s: None, wall=lambda: 1005.0,
                            campaign_s=500.0, session_runner=_gone_session)
    assert rep["status"] == "CAMPAIGN_RUNG_COMPLETE"
    assert rep["advance_gate"]["filled_markets"] == ["m1", "m2", "x"]


def test_deterministic_recovery_simulation(monkeypatch, tmp_path):
    """OWNER 5554553793 A, end to end on one held market: account
    endpoints recover before the tape -> no trade; tape advances ->
    re-sweep/reconcile -> held-state decision (resume) -> campaign
    resumes; then an ambiguous account (two markets held) -> fail closed."""
    shares, tape = {"v": 100.0}, {"trades": 0}
    fake = _two_run_harness(monkeypatch, tmp_path, shares, tape)
    fake.positions["held"] = {"netPositionDecimal": "0.03"}
    monkeypatch.setattr(cont, "status_has_trades", lambda: tape["trades"] > 0)
    calls = {"n": 0}
    real_held = cont.held_market

    def flaky_held(t):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise q1.Halt("GET_RETRIES_EXHAUSTED:/v1/orders/open:HTTP Error 500")
        if calls["n"] == 5:
            shares["v"] = 101.0                  # tape advances
        if calls["n"] == 8:
            fake.positions["other"] = {"netPositionDecimal": "0.02"}
        return real_held(t)
    monkeypatch.setattr(cont, "held_market", flaky_held)
    sweeps = []
    real_sweep = cont.sweep_orphan_orders
    monkeypatch.setattr(cont, "sweep_orphan_orders",
                        lambda t: sweeps.append(1) or real_sweep(t))
    resumed = []

    def session(t, clock, sleep, wall, slug, gate_ok, deadline_s, **kw):
        resumed.append((slug, kw.get("resume")))
        return {"status": "SPRING_RESIDUAL_HOLD", "slug": slug,
                "inventory": {"up": "0.03", "dn": "0"}, "orders": [],
                "decisions": [{"acted": "hold"}]}
    import itertools
    ticks = itertools.count(0.0, 0.05)        # ~10k calls inside the campaign
    rep = cont.run_campaign(fake, clock=lambda: next(ticks),
                            sleep=lambda s: None, wall=lambda: 1005.0,
                            campaign_s=500.0, session_runner=session)
    # account recovered at call 3 while the tape was frozen: no session
    o = rep["outages"][0]
    assert o["account_recovered_utc"]
    assert "x" in o["trades_resumed_by"]["markets_moved"]
    assert len(sweeps) == 2                       # boot + recovery re-sweep
    assert resumed and all(r == ("held", True) for r in resumed)
    # then the ambiguous account stops the campaign, fail closed
    assert rep["status"] == "CAMPAIGN_STOP:MULTIPLE_POSITIONS"


def test_l5_campaign_state_file_does_not_collide_with_the_q1_file(monkeypatch,
                                                                   tmp_path):
    """LIVE 20:36Z: a truthy foreign campaign_state.json (the q1 risk
    file) was read as restored L5 state and skipped the boot gate."""
    monkeypatch.setenv(q1.base.STATE_ENV, str(tmp_path))
    (tmp_path / "campaign_state.json").write_text('{"risk": "q1"}')
    assert cont.load_campaign_state() == {}
    assert cont.campaign_state_path().name == "l5_campaign_state.json"
