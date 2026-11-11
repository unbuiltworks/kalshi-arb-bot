"""Regressions for the sequential L5 sports decision layer (issue #167
comment 5535231759): auditable next actions under the >=1c-after-fees
hard bound, freshness semantics, causal fill confirmation, DISARMED
purity, and heartbeat integration that can never break the collector."""
from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone
from decimal import Decimal as D

from src import pmus_sports_l5_decision as l5d
from src import pmus_status_report as st

SLUG = "aec-setkameua-kulhry-sokyev-2026-09-04"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _obs(ts, bid="0.48", ask="0.50", cum="100", last=None):
    return {"kind": "bbo_book", "t": _iso(ts), "slug": SLUG,
            "league": "setkameua", "bid": bid, "ask": ask,
            "bid_touch_qty": "500", "ask_touch_qty": "400",
            "ask_book_missing": False, "last_trade_px": last,
            "shares_traded_cum": cum}


def _model():
    """Tiny frozen-model stand-in: open/extend then complete."""
    full = {}
    for band in ("p45-55c",):
        for imb in ("flat", "imb1-10"):
            stt = f"{band}|tte>10m|{imb}"
            full[f"{stt}|open|q<5"] = {"complete|q<5": 8, "extend|q<5": 2}
            full[f"{stt}|extend|q<5"] = {"complete|q<5": 8}
            full[f"{stt}|complete|q<5"] = {"complete|q<5": 8}
    return {"full": full, "base": {}, "marginal": {"complete|q<5": 5,
                                                   "extend|q<5": 3,
                                                   "open|q<5": 2},
            "cadence_modal": {}, "_sha256_16": "sportsdecision00"}


def _qualifying_obs(base: float):
    """~15 minutes of 35s-spaced rows: sells at the bid and buys at the
    ask in two separate 300s slots, so the fresh window can genuinely
    reach Q1_GATE_CLEARED under the existing gate."""
    rows, cum = [], 100
    for i in range(26):
        t = base + i * 35
        o = _obs(t, cum=str(cum))
        if i in (2, 11, 19):            # sells at bid
            cum += 10
            o = _obs(t, cum=str(cum), last="0.48")
        if i in (5, 14, 22):            # buys at ask
            cum += 10
            o = _obs(t, cum=str(cum), last="0.50")
        rows.append(o)
    return rows


def test_no_order_path_or_credentials_structurally():
    src = inspect.getsource(l5d)
    for banned in ("urllib", "websockets", "requests", "http_get",
                   "Transport", "/v1/orders", "X-PM-", "SECRET",
                   "pmus_q1_executor", "pmus_micro_executor",
                   "pmus_spring_l5_session"):
        assert banned not in src, banned
    assert '"order_entry": False' in src
    assert "DISARMED" in l5d.LABELS


def test_hard_bound_always_wins_over_observation_cap(monkeypatch):
    """The >=1c-after-known-fees bound participates in every emitted
    complement target: even a (hypothetically) loosened observation cap
    cannot admit a <1c-net pair."""
    px, econ = l5d.complement_target(D("0.48"), deficit_bid=D("0.60"))
    assert px == D("0.50")                       # 0.98 cap binds
    assert econ["max_pair_cost"] == "0.98"
    monkeypatch.setattr(l5d, "PAIR_OBSERVATION_CAP", D("1.05"))
    px2, econ2 = l5d.complement_target(D("0.48"), deficit_bid=D("0.60"))
    assert px2 == D("0.51")                      # hard 0.99 bound binds
    assert D(econ2["max_pair_cost"]) == D("0.99")


def test_fresh_qualifier_emits_auditable_next_action():
    base = 1_000_000.0
    obs = _qualifying_obs(base)
    now = base + 25 * 35 + 30                    # ~30s after last row
    row = l5d.decide_market(SLUG, obs, now, _model())
    assert row["fresh_15m_status"] == "Q1_GATE_CLEARED"
    assert row["possible_fills"] >= 2            # open + complete replayed
    assert row["pair_costs_theoretical"]         # a pair completed
    assert all(D(c) <= D("0.98") for c in row["pair_costs_theoretical"])
    na = row["next_action"]
    assert na["decision"] == "quote"
    assert na["relation"] in ("open", "extend", "complete")
    assert na["side"] in ("YES", "NO") and na["q1_size"] == 1
    assert "inventory_effect" in na and "reason" in na
    assert D(na["target_price"]) > 0
    if "max_pair_cost" in na:
        assert D(na["max_pair_cost"]) <= D("0.98")
    assert row["executable_now"] is True
    assert row["order_entry"] is False and row["realized_pnl"] is None


def test_stale_1h_qualifier_is_never_currently_executable():
    base = 1_000_000.0
    obs = _qualifying_obs(base)
    now = base + 26 * 35 + 2400                  # fresh 15m window empty
    row = l5d.decide_market(SLUG, obs, now, _model())
    assert row["window_status"] == "Q1_GATE_CLEARED"
    assert row["fresh_15m_status"] != "Q1_GATE_CLEARED"
    assert row["executable_now"] is False
    # FRESHNESS is what refuses this, and it is the honest reason: the
    # book really is ~40 minutes old. The old message blamed the missing
    # 15m pair-completion status, which was the Q1 veto talking.
    assert row["stale_reason"].startswith("book_stale:")
    assert row["next_action"]["decision"] == "no_action"   # book stale too


def test_fill_confirmation_is_strictly_causal():
    base = 1_000_000.0
    # a sell print BEFORE the first decision instant must not fill it
    obs = [_obs(base, cum="100"),
           _obs(base + 5, cum="110", last="0.48"),   # early sell
           _obs(base + 40, cum="110"),
           _obs(base + 80, cum="110")]
    row = l5d.decide_market(SLUG, obs, base + 100, _model())
    quoted = [a for a in row["actions_replayed"]
              if a.get("relation") == "open"]
    # the open quoted after the early print sees no LATER sell -> no_fill
    assert quoted and all(a["fill_class"] == "no_fill" for a in quoted)


def test_off_distribution_takes_no_action():
    model = {"full": {}, "base": {}, "marginal": {"open|q<5": 1.0},
             "cadence_modal": {}, "_sha256_16": "offdist000000000"}
    base = 1_000_000.0
    obs = _qualifying_obs(base)
    row = l5d.decide_market(SLUG, obs, base + 900, model)
    assert row["next_action"]["decision"] == "no_action"
    assert row["next_action"]["reason"] == "v2_off_distribution"
    assert row["possible_fills"] == 0


def test_slate_names_preflight_candidate_and_existing_executor_path():
    base = 1_000_000.0
    by_slug = {SLUG: _qualifying_obs(base)}
    out = l5d.slate_decisions(by_slug, now=base + 25 * 35 + 30,
                              model=_model())
    assert out["kind"] == "PMUS_SPORTS_L5_DECISION"
    pf = out["preflight_candidate"]
    assert pf is not None and pf["slug"] == SLUG
    assert pf["next_action"]["decision"] == "quote"
    assert "pmus_spring_l5_probe_entry" in pf["execution_path_if_approved"]
    assert "NOT invoked" in pf["execution_path_if_approved"]
    assert out["order_entry"] is False and out["realized_pnl"] is None


def test_emit_writes_heartbeat_line(capsys):
    base = 1_000_000.0
    l5d.emit_decisions({SLUG: _qualifying_obs(base)},
                       now=base + 25 * 35 + 30)
    line = [ln for ln in capsys.readouterr().out.splitlines()
            if ln.startswith("PMUS_SPORTS_L5_DECISION ")]
    assert len(line) == 1
    payload = json.loads(line[0].split(" ", 1)[1])
    assert payload["candidates_evaluated"] >= 1
    # slim heartbeat: full replays stay in the report, not the log line
    assert all("actions_replayed" not in r for r in payload["rows"])


def test_decision_layer_errors_never_break_collector_heartbeat(
        tmp_path, monkeypatch, capsys):
    raw = tmp_path / "raw"
    raw.mkdir()
    day = datetime.now(timezone.utc)
    rows = [_obs(day.timestamp() - 60), _obs(day.timestamp() - 30)]
    (raw / f"pmus_{day:%Y%m%d}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n")

    def boom(*a, **k):
        raise RuntimeError("decision layer exploded")
    monkeypatch.setattr(l5d, "emit_decisions", boom)
    rep = st.emit(tmp_path)
    assert rep["l5_sports_decisions"]["error"].startswith("RuntimeError")
    out = capsys.readouterr().out
    assert "PMUS_STATUS " in out                 # heartbeat survived


# ---- OPEN admission (owner P0: pair economics are not an entry gate) ----

def _fresh(**over):
    row = {"observations": 40, "collector_gaps": 0, "q1_depth_support": True,
           "ask_side_served_pct": 100.0, "status": "NOT_READY"}
    row.update(over)
    return row


def _quote(**over):
    plan = {"decision": "quote", "relation": "open", "side": "YES",
            "target_price": "0.4500"}
    plan.update(over)
    return plan


def test_open_admits_without_any_pair_completion_evidence():
    """The core of the bug. A market that has never completed a pair --
    status NOT_READY, zero matched slots -- must still admit a first
    OPEN, because requiring past completions before a first entry is
    circular: nothing completes if nothing may open."""
    assert l5d.open_admission(_fresh(status="NOT_READY"), 3.0,
                              _quote()) is None


def test_q1_gate_cleared_is_not_required_and_not_consulted():
    for status in ("NOT_READY", "SHADOW_CANDIDATE", "Q1_GATE_CLEARED"):
        assert l5d.open_admission(_fresh(status=status), 3.0,
                                  _quote()) is None


def test_freshness_still_refuses():
    r = l5d.open_admission(_fresh(), 10_000.0, _quote())
    assert r and r.startswith("book_stale:")
    assert l5d.open_admission(_fresh(), None, _quote()).startswith(
        "book_stale:")


def test_cadence_gaps_still_refuse():
    assert l5d.open_admission(_fresh(collector_gaps=2), 3.0,
                              _quote()) == "cadence_gap:2"


def test_liquidity_still_refuses_a_book_we_cannot_rest_against():
    assert l5d.open_admission(_fresh(q1_depth_support=False), 3.0,
                              _quote()) == "no_q1_touch_depth"
    assert l5d.open_admission(_fresh(ask_side_served_pct=0.0), 3.0,
                              _quote()) == "one_sided_book"


def test_an_unobserved_market_is_refused_not_assumed_good():
    assert l5d.open_admission(_fresh(observations=0), 3.0,
                              _quote()) == "no_observations_in_window"


def test_off_distribution_state_still_refuses():
    """Distribution and the model itself are the entry conditions that
    remain -- an action v2 never took is not an L5 action."""
    r = l5d.open_admission(
        _fresh(), 3.0,
        {"decision": "no_action", "reason": "v2_off_distribution"})
    assert r == "model_no_open:v2_off_distribution"


def test_the_model_declining_still_refuses():
    r = l5d.open_admission(_fresh(), 3.0,
                           {"decision": "no_action", "reason": "one_sided_book"})
    assert r == "model_no_open:one_sided_book"


def test_slate_ranking_no_longer_excludes_a_market_without_pair_evidence():
    """The second veto: NOT_READY markets were dropped before any
    decision row was built, so they could never be evaluated for OPEN."""
    base = 1_000_000.0
    obs = _qualifying_obs(base)
    now = base + 26 * 35
    out = l5d.slate_decisions({SLUG: obs}, now=now, model=_model())
    assert out["candidates_evaluated"] >= 1


def test_preflight_candidate_no_longer_requires_q1_gate_cleared():
    base = 1_000_000.0
    obs = _qualifying_obs(base)
    now = base + 26 * 35
    out = l5d.slate_decisions({SLUG: obs}, now=now, model=_model())
    rows = out["rows"]
    executable = [r for r in rows if r["executable_now"]]
    if executable:
        assert out["preflight_candidate"] is not None
        assert out["preflight_candidate"]["slug"] == executable[0]["slug"]


def test_completion_economics_are_untouched_by_this_change():
    """The <=0.98 cap and the >=1c hard bound still price a COMPLETE --
    they were only ever wrong as an ENTRY condition."""
    inv = l5d.Inventory()
    inv.record(True, l5d.D("0.24"))
    px, econ = l5d.complement_target(inv.avg(True), l5d.D("0.80"))
    assert px == l5d.D("0.74")                       # 0.98 cap, the tighter
    assert econ["max_pair_cost"] == "0.98"
