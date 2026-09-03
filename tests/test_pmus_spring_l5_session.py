"""SPRING-L5 session layer regressions (issue #167 comment 5520504343):
OPEN->EXTEND->COMPLETE decimal inventory, spring gates, pair-target vs
hard-bound economics, maker-only, one market, isolated arming."""
from __future__ import annotations

import inspect
import itertools
from decimal import Decimal as D

from src import pmus_spring_l5_session as spring
from src import pmus_micro_executor as micro
from src import pmus_l5_persistence_v2 as pv2

q1 = micro.q1
SLUG = "aec-mlb-nyy-laa-2026-09-02"


class Fake:
    """Scripted venue: flat account, one market, every maker order fills
    fully at its limit. Enforces ONE outstanding order at a time."""

    def __init__(self, bid="0.5000", ask="0.5050",
                 meta=("0.01", "0.005"), fill_all=True):
        self.bid, self.ask = bid, ask
        self.meta = meta
        self.fill_all = fill_all
        self.orders: dict[str, dict] = {}
        self.open_order: str | None = None
        self.posts: list[tuple[str, dict]] = []
        self.n = 0

    def request(self, method, path, body=None, base=None):
        if path == "/v1/orders/open":
            return {"orders": []}
        if path == "/v1/portfolio/positions":
            return {"positions": {}}
        if path == "/v1/account/balances":
            return {"balances": [{"currency": "USD",
                                  "buyingPower": {"value": "100"}}]}
        if path.endswith("/bbo"):
            return {"marketData": {"bestBid": {"value": self.bid},
                                   "bestAsk": {"value": self.ask}}}
        if path.startswith("/v1/market/slug/"):
            return {"market": {"minimumTradeQty": self.meta[0],
                               "orderPriceMinTickSize": self.meta[1]}}
        if path == "/v1/order/preview":
            return {"order": dict(body["request"])}
        if path == "/v1/orders":
            assert self.open_order is None, "second outstanding order!"
            self.n += 1
            oid = f"SPR{self.n}"
            state = "ORDER_STATE_FILLED" if self.fill_all \
                else "ORDER_STATE_OPEN"
            cum = body["quantity"] if self.fill_all else 0
            self.orders[oid] = {"state": state, "cumQuantity": str(cum),
                                "avgPx": body["price"]["value"],
                                "marketSlug": body["marketSlug"]}
            self.posts.append((path, dict(body)))
            if not self.fill_all:
                self.open_order = oid
            return {"id": oid}
        if path.endswith("/cancel") and path.startswith("/v1/order/"):
            oid = path.split("/")[3]
            self.orders[oid]["state"] = "ORDER_STATE_CANCELED"
            self.open_order = None
            self.posts.append((path, body or {}))
            return {}
        if path.startswith("/v1/order/"):
            oid = path.split("/")[3]
            return {"order": dict(self.orders[oid])}
        if path == "/v1/orders/open/cancel":
            self.posts.append((path, body or {}))
            return {}
        raise AssertionError((method, path))


def _tiny_model():
    base = "p45-55c|tte>10m|flat"
    return {"full": {f"{base}|open|q<5": {"open|q<5": 10}},
            "base": {base: {"open|q<5": 10}},
            "marginal": {"open|q<5": 1.0},
            "cadence_modal": {}, "_sha256_16": "springtest000000",
            "n_transitions": 10, "n_full_states": 1}


def _arm(monkeypatch, tmp_path, slug=SLUG):
    monkeypatch.setenv(spring.SPRING_LIVE_ENV, "YES")
    monkeypatch.setenv(spring.SPRING_SID_ENV, "s1")
    monkeypatch.setenv(spring.SPRING_ARM_ENV, "s1")
    monkeypatch.setenv(spring.SPRING_MARKET_ENV, slug)
    monkeypatch.setenv(spring.SPRING_GATE_ENV, slug)
    monkeypatch.setenv(q1.base.KEY_ENV, "k")
    monkeypatch.setenv(q1.base.SECRET_ENV, "s")
    monkeypatch.setenv(q1.base.STATE_ENV, str(tmp_path))


def _ticking():
    t = {"v": 0.0}

    def clock():
        t["v"] += 0.5
        return t["v"]
    return clock


def _script_proposals(monkeypatch, relations):
    seq = itertools.chain(relations, itertools.repeat(relations[-1]))
    monkeypatch.setattr(pv2, "load_v2", lambda *a, **k: _tiny_model())

    def fake_proposal(model, base_state, last_rel, last_size, residual,
                      clone_avg_cost=None):
        rel = next(seq)
        return {"recommended_relation": rel, "persist_extend_p": 0.6,
                "switch_complete_p": 0.3, "next_reevaluation_s": 2.0,
                "relation_ranked": [], "off_distribution": False}
    monkeypatch.setattr(pv2, "proposal", fake_proposal)


def test_open_extend_extend_complete_decimal_inventory(monkeypatch,
                                                       tmp_path):
    _arm(monkeypatch, tmp_path)
    # first proposal is consumed by the forced OPEN on empty inventory
    _script_proposals(monkeypatch, ["open", "extend", "complete",
                                    "complete"])
    fake = Fake()
    rep = spring.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                             wall=lambda: 1000.0)
    assert rep["status"] == "SPRING_MATCHED", rep
    acts = rep["actions"]
    assert [a["relation"] for a in acts] == ["open", "extend", "complete",
                                            "complete"]
    assert [a["side"] for a in acts] == ["YES", "YES", "NO", "NO"]
    # exact Decimal inventory after every action
    assert acts[0]["inventory_after"]["up"] == "0.02"
    assert acts[1]["inventory_after"]["up"] == "0.04"
    a2 = acts[2]["inventory_after"]
    assert (a2["dn"], a2["matched"], a2["residual"]) == \
        ("0.02", "0.02", "0.02")
    a3 = acts[3]["inventory_after"]
    assert (a3["dn"], a3["matched"], a3["residual"]) == \
        ("0.04", "0.04", "0.00")
    # spring pair economics: complement at 0.46 -> pair exactly the target
    assert D(rep["pair_unit_cost"]) == D("0.96")
    assert rep["pair_economics"]["regime"] == "spring_target"
    assert rep["pair_economics"]["hard_bound_px"] is not None
    # maker-only bodies
    assert all(b["participateDontInitiate"] is True
               for p, b in fake.posts if p == "/v1/orders")
    assert rep["realized_pnl"] is None


def test_no_taker_path_exists_structurally():
    src = inspect.getsource(spring)
    assert "post_only=False" not in src
    assert "taker_completion_ok" not in src
    assert "settlement_outcome" not in src         # no winner prediction
    assert "MAKER ONLY" in src


def test_arming_is_isolated_from_q1_and_micro(monkeypatch, tmp_path):
    for env in (spring.SPRING_LIVE_ENV, spring.SPRING_SID_ENV,
                spring.SPRING_ARM_ENV):
        monkeypatch.delenv(env, raising=False)
    # fully arm q1 AND micro: spring must stay disarmed, zero calls
    for live, sid, arm in ((q1.LIVE_ENV, q1.SID_ENV, q1.ARM_ENV),
                           (micro.MICRO_LIVE_ENV, micro.MICRO_SID_ENV,
                            micro.MICRO_ARM_ENV)):
        monkeypatch.setenv(live, "YES")
        monkeypatch.setenv(sid, "x")
        monkeypatch.setenv(arm, "x")
    fake = Fake()
    rep = spring.run_session(fake)
    assert rep["status"] == "DISARMED"
    assert fake.posts == [] and fake.n == 0
    # spring keys arm neither q1 nor micro
    monkeypatch.setenv(spring.SPRING_LIVE_ENV, "YES")
    monkeypatch.setenv(spring.SPRING_SID_ENV, "s1")
    monkeypatch.setenv(spring.SPRING_ARM_ENV, "s1")
    for env in (q1.LIVE_ENV, q1.SID_ENV, q1.ARM_ENV, micro.MICRO_LIVE_ENV,
                micro.MICRO_SID_ENV, micro.MICRO_ARM_ENV):
        monkeypatch.delenv(env, raising=False)
    assert spring.armed() is True
    assert q1.armed() is False and micro.micro_armed() is False


def test_out_of_band_market_skips_without_mutation(monkeypatch, tmp_path):
    _arm(monkeypatch, tmp_path)
    monkeypatch.setattr(pv2, "load_v2", lambda *a, **k: _tiny_model())
    fake = Fake(bid="0.7000", ask="0.7050")
    rep = spring.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                             wall=lambda: 0.0)
    assert rep["status"] == "SPRING_SKIP_NOT_QUALIFYING"
    assert "outside_spring_band" in rep["reason"]
    assert fake.posts == []


def test_candidate_gate_thin_flow_and_off_distribution():
    row = {"slug": "x", "bid": "0.50", "skip_reason": None,
           "both_sides_served": True, "trades_1h": 3}
    assert spring.spring_candidate_gate(row).startswith("thin_flow")
    row["trades_1h"] = 50
    row["two_sided_trades"] = False
    assert spring.spring_candidate_gate(row) == "one_directional_flow"
    row["two_sided_trades"] = True
    assert spring.spring_candidate_gate(row) is None
    # off-distribution v2 state blocks
    assert spring.spring_candidate_gate(
        row, {"full": {}, "base": {}, "marginal": {"open|q<5": 1.0}}) == \
        "v2_off_distribution"


def test_cumulative_cap_blocks_new_spend(monkeypatch, tmp_path):
    _arm(monkeypatch, tmp_path)
    _script_proposals(monkeypatch, ["extend"])
    monkeypatch.setattr(spring, "spring_cumulative_notional",
                        lambda slug: D("0.045"))
    fake = Fake()                     # next 0.01 action would breach 0.05
    rep = spring.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                             wall=lambda: 0.0)
    assert rep["status"] == "SPRING_NO_FILL"
    assert [p for p, b in fake.posts if p == "/v1/orders"] == []


def test_stop_before_settlement_prevents_any_action(monkeypatch, tmp_path):
    _arm(monkeypatch, tmp_path)
    _script_proposals(monkeypatch, ["extend"])
    monkeypatch.setenv(spring.SETTLEMENT_ENV, "1000")
    fake = Fake()
    rep = spring.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                             wall=lambda: 950.0)   # inside stop margin
    assert rep["status"] == "SPRING_NO_FILL"
    assert fake.posts == [] or all(p != "/v1/orders"
                                   for p, b in fake.posts)


def test_account_wide_flat_gate_blocks_first_mutation(monkeypatch,
                                                      tmp_path):
    _arm(monkeypatch, tmp_path)
    monkeypatch.setattr(pv2, "load_v2", lambda *a, **k: _tiny_model())
    monkeypatch.setattr(micro, "account_block_reason",
                        lambda t: "nonzero_positions:other=0.0100")
    fake = Fake()
    rep = spring.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                             wall=lambda: 0.0)
    assert rep["status"] == "PREFLIGHT_FAILED"
    assert "nonzero_positions" in rep["reason"]
    assert fake.posts == []


def test_unfilled_orders_cancel_and_session_holds_no_ghost_inventory(
        monkeypatch, tmp_path):
    _arm(monkeypatch, tmp_path)
    _script_proposals(monkeypatch, ["extend"])
    monkeypatch.setenv(spring.MAX_ACTIONS_ENV, "2")
    fake = Fake(fill_all=False)       # nothing ever fills
    rep = spring.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                             wall=lambda: 0.0)
    assert rep["status"] == "SPRING_NO_FILL", rep
    assert rep["inventory"]["up"] == "0" and rep["inventory"]["dn"] == "0"
    cancels = [p for p, b in fake.posts if p.endswith("/cancel")
               and p.startswith("/v1/order/")]
    creates = [p for p, b in fake.posts if p == "/v1/orders"]
    assert len(cancels) == len(creates) >= 1     # every order cancelled
    assert ("/v1/orders/open/cancel", {"slugs": [SLUG]}) in \
        [(p, b) for p, b in fake.posts]          # deadman ran


def test_penny_residual_is_never_flat_in_behavioral_state():
    """Issue #167 comment 5520710312: a 0.01-contract first fill must
    read as +/-1 behavioral unit, EXTEND must increase the behavioral
    imbalance, COMPLETE must reduce it — never int()-truncated to 0."""
    inv = spring.Inventory()
    assert inv.behavioral_residual() == 0.0
    inv.record(True, D("0.50"), D("0.01"), "open")      # first penny fill
    assert inv.behavioral_residual() == 1.0
    key = spring.spring_state_key(inv, D("0.50"), None, 0.0)
    assert key.endswith("imb1-10") and "flat" not in key
    inv.record(True, D("0.50"), D("0.01"), "extend")    # extend -> 2 units
    assert inv.behavioral_residual() == 2.0
    inv.record(False, D("0.46"), D("0.01"), "complete")  # complete -> 1
    assert inv.behavioral_residual() == 1.0
    # partial fills map proportionally, sign preserved
    inv2 = spring.Inventory()
    inv2.record(False, D("0.50"), D("0.02"), "open")
    inv2.record(False, D("0.50"), D("0.01"), "extend")   # NO surplus
    assert inv2.behavioral_residual() == -1.5
    key2 = spring.spring_state_key(inv2, D("0.50"), None, 0.0)
    assert key2.endswith("imb1-10")


def test_sequence_uses_behavioral_states_at_penny_sizing(monkeypatch,
                                                         tmp_path):
    _arm(monkeypatch, tmp_path)
    _script_proposals(monkeypatch, ["open", "extend", "complete",
                                    "complete"])
    fake = Fake()
    rep = spring.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                             wall=lambda: 1000.0)
    assert rep["status"] == "SPRING_MATCHED", rep
    states = [d["state"] for d in rep["decisions"]]
    assert states[0].endswith("flat")             # before any fill
    assert all(s.endswith("imb1-10") for s in states[1:3])  # 1-2 units
    assert "flat" not in states[1] and "flat" not in states[2]


def test_spring_cumulative_counter_counts_open_extend_not_complete(
        monkeypatch, tmp_path):
    import json as _json
    monkeypatch.setenv(q1.base.STATE_ENV, str(tmp_path))
    slug = SLUG
    rows = [
        {"kind": "submitted", "stage": "spring_open_1", "slug": slug,
         "price": "0.50", "qty": "0.02"},
        {"kind": "submitted", "stage": "spring_extend_2", "slug": slug,
         "price": "0.50", "qty": "0.02"},
        {"kind": "submitted", "stage": "spring_complete_3", "slug": slug,
         "price": "0.46", "qty": "0.04"},              # excluded
        {"kind": "submitted", "stage": "micro_first_leg", "slug": slug,
         "price": "0.48", "qty": "0.02", "micro": True},  # shared cap
        {"kind": "submitted", "stage": "spring_open_1", "slug": "other",
         "price": "0.50", "qty": "0.02"},              # other market
    ]
    (tmp_path / "ledger.jsonl").write_text(
        "\n".join(_json.dumps(r) for r in rows) + "\n")
    assert spring.spring_cumulative_notional(slug) == D("0.0296")
    # unparseable row counts as the full cap — fail closed
    (tmp_path / "ledger.jsonl").write_text(
        '{"kind": "submitted", "stage": "spring_open_1", '
        f'"slug": "{slug}", "price": "x", "qty": "0.02"}}\n')
    assert spring.spring_cumulative_notional(slug) == \
        spring.SESSION_NOTIONAL_CAP


def test_spring_complement_price_exposes_both_bounds():
    px, econ = spring.spring_complement_price(D("0.50"), D("0.50"),
                                              D("0.005"))
    assert px == D("0.460")                      # 0.96 - 0.50
    assert econ["regime"] == "spring_target"
    assert D(econ["hard_bound_px"]) == D("0.49")
    assert D(econ["spring_bound_px"]) == D("0.46")
    none_px, econ2 = spring.spring_complement_price(D("0.80"), D("0.21"),
                                                    D("0.005"))
    assert none_px is None                       # 0.16 bound below band


# --- spring_watch selector blind spot (issue #167 comment 5521430953) ---

def _sw_book(slug, bid, ask, qty="200"):
    return {"marketData": {"marketSlug": slug,
                           "bids": [{"px": {"value": bid}, "qty": qty}],
                           "offers": [{"px": {"value": ask}, "qty": qty}]}}


def _sw_trade(slug, price, side):
    return {"trade": {"marketSlug": slug, "price": {"value": price},
                      "quantity": "1", "taker": {"side": side}}}


def _sw_state():
    """Ten out-of-band markets outrank the one qualifying 0.50 market by
    trade count, so it is #11 overall: invisible to the 5-row display and
    not gate-cleared."""
    from src import pmus_ws_evidence_recorder as rec
    st: dict = {}
    base = 1_700_000_000.0
    for i, px in enumerate(["0.85"] * 5 + ["0.70"] * 5):
        slug = f"aec-flow-{i}"
        rec.apply_events(st, _sw_book(slug, f"{px}00", f"{px}50"), base)
        for k in range(30 - i):
            rec.apply_events(st, _sw_trade(slug, f"{px}50", "BUY"),
                             base + k)
    fit = "aec-spring-fit"
    rec.apply_events(st, _sw_book(fit, "0.5000", "0.5200"), base)
    for k in range(6):   # modest genuine two-sided flow
        side = "BUY" if k % 2 else "SELL"
        rec.apply_events(st, _sw_trade(fit, "0.5100", side), base + k * 30)
    return rec, st, base + 400, fit


def test_spring_watch_surfaces_band_market_hidden_by_display():
    rec, st, now, fit = _sw_state()
    status = rec.build_status(st, True, 100, 11, now, l5_alloc=set())
    tops = [r["slug"] for r in status["top_markets"]]
    assert fit not in tops and len(tops) == 5        # display-hidden
    assert not any(r["slug"] == fit
                   for r in status["eligible_markets"])
    watch = status["spring_watch"]
    assert [r["slug"] for r in watch] == [fit]       # band prefilter only
    row = watch[0]
    assert row["two_sided_trades"] is True
    assert row["trades_1h"] == 6
    assert row["gate_status"] in ("NOT_READY", "SHADOW_CANDIDATE")
    assert "not an execution admission" in row["labels"]
    assert len(watch) <= rec.SPRING_WATCH_LIMIT


def test_rank_spring_candidates_consumes_spring_watch(monkeypatch):
    rec, st, now, fit = _sw_state()
    status = rec.build_status(st, True, 100, 11, now, l5_alloc=set())
    seen: list[str] = []

    def fake_eligible(t, slug):
        seen.append(slug)
        return {"slug": slug, "bid": "0.5000", "ask": "0.5200",
                "skip_reason": None}

    monkeypatch.setattr(spring.micro, "micro_eligible", fake_eligible)
    rows = spring.rank_spring_candidates(None, status)
    assert fit in seen                               # selector now sees it
    mine = [r for r in rows if r["slug"] == fit][0]
    assert mine["trades_1h"] == 6                    # watch row facts flow
    assert mine["two_sided_trades"] is True
    # advisory only: the canonical gates still decide admission
    assert "spring_skip" in mine
