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
    assert out == {"checked": 1, "reconciled": 1, "unresolved": 0}
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
