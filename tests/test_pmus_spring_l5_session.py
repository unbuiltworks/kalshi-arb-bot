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
        self.positions: dict[str, dict] = {}   # slug -> venue position row

    def request(self, method, path, body=None, base=None):
        if path == "/v1/orders/open":
            return {"orders": []}
        if path == "/v1/portfolio/positions":
            return {"positions": dict(self.positions)}
        if path == "/v1/account/balances":
            return {"balances": [{"currency": "USD",
                                  "buyingPower": {"value": "100"}}]}
        if path.endswith("/bbo"):
            return {"marketData": {"bestBid": {"value": self.bid},
                                   "bestAsk": {"value": self.ask}}}
        if path.startswith("/v1/market/slug/"):
            return {"market": {"minimumTradeQty": self.meta[0],
                               "orderPriceMinTickSize": self.meta[1],
                               # live open-state recheck (5552475548 #2)
                               "active": True, "archived": False,
                               "closed": False,
                               "status": "MARKET_STATUS_OPEN",
                               "endDate": "2099-01-01T00:00:00Z"}}
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
                                "outcomeSide": body.get("outcomeSide"),
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
    # P0-D/P0-E (issue #167 comments 5526683554/5526791808): operator
    # asserts strict repeated completion + durable state for these
    # already-armed fixtures; dedicated tests below cover the fail-closed
    # paths when either assertion is absent.
    monkeypatch.setenv(spring.SPRING_STRICT_ENV, slug)
    monkeypatch.setenv(spring.STATE_DURABLE_ENV, "1")
    monkeypatch.setenv(q1.base.KEY_ENV, "k")
    monkeypatch.setenv(q1.base.SECRET_ENV, "s")
    monkeypatch.setenv(q1.base.STATE_ENV, str(tmp_path))
    _complement_proven(tmp_path)


def _complement_proven(tmp_path, proven=True):
    """OWNER 5556038561 #3: a NEW first leg needs a durable venue proof
    that an opposite-side order built by this code was accepted. The
    armed fixtures carry one; the gate itself is tested separately."""
    import json as _json
    (tmp_path / spring.COMPLEMENT_PROOF_FILE).write_text(_json.dumps(
        {"proven": proven, "slug": "fixture", "order_id": "FIX",
         "end_reason": "resting:ORDER_STATE_OPEN",
         "proven_utc": "2026-09-06T00:00:00+00:00"}))


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
    # OPEN side from the SHARED adapter (cheaper touch): bid 0.5000 vs
    # NO join 1-0.5050=0.4950 -> NO first, then extend NO, complete YES
    assert [a["side"] for a in acts] == ["NO", "NO", "YES", "YES"]
    # exact Decimal inventory after every action
    assert acts[0]["inventory_after"]["dn"] == "0.02"
    assert acts[1]["inventory_after"]["dn"] == "0.04"
    a2 = acts[2]["inventory_after"]
    assert (a2["up"], a2["matched"], a2["residual"]) == \
        ("0.02", "0.02", "-0.02")
    a3 = acts[3]["inventory_after"]
    assert (a3["up"], a3["matched"], a3["residual"]) == \
        ("0.04", "0.04", "0.00")
    # spring pair economics: complement at 0.48 -> pair exactly the
    # 0.98 observation cap (OWNER DECISION 5533476555); the >=1c hard
    # bound (0.49 here) still participates in the min()
    assert D(rep["pair_unit_cost"]) == D("0.98")
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
    # OWNER DECISION 5551236878: the CATEGORY evidence bar is COMPLETION
    # evidence and no longer refuses an OPEN. Requiring proof of past
    # completions before a first entry is circular.
    assert spring.spring_candidate_gate(row) is None
    row["spring_category_bar_met"] = True
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
    # canonical lookup v3: after fills the imbalance is L5's model-scale
    # shares for the action made (never the penny unit), never 'flat'
    assert all(not s.endswith("|flat") for s in states[1:3])
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
    assert px == D("0.480")                      # 0.98 - 0.50 (5533476555)
    assert econ["regime"] == "spring_target"
    assert D(econ["hard_bound_px"]) == D("0.49")
    assert D(econ["spring_bound_px"]) == D("0.48")
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


# ---- P0-D: strict replay wired into admission, fail closed (5526683554) ----

def _flowing_row(**extra):
    return {"slug": "x", "bid": "0.50", "skip_reason": None,
            "both_sides_served": True, "trades_1h": 20,
            "two_sided_trades": True, **extra}


def test_loose_q1_matched_history_no_longer_blocks_an_open():
    """OWNER 5551236878. The row still carries the unmet bar for
    reporting; it simply no longer refuses the entry."""
    row = _flowing_row(slot_counts_theoretical={"MATCHED": 2},
                       spring_category_bar_met=False)
    assert spring.spring_candidate_gate(row) is None
    assert row["spring_category_bar_met"] is False


def test_isolated_market_is_no_longer_blocked_from_opening():
    """OWNER 5551236878 supersedes 5533476555 for ENTRY only: an
    isolated market may OPEN. Its thin category evidence is still
    recorded on the row and surfaced by the readiness layer."""
    row = _flowing_row(
        spring_repeated_completion=True,          # old per-market label
        spring_matched_slots=2,                   # 2 own completions
        spring_slot_counts_theoretical={"SPRING_MATCHED": 2},
        spring_category="mlb",
        spring_category_evidence={"completions_60m": 2,
                                  "distinct_markets_60m": 1,
                                  "markets": ["x"], "bar_met": False},
        spring_category_bar_met=False)
    assert spring.spring_candidate_gate(row) is None
    assert row["spring_category_evidence"]["distinct_markets_60m"] == 1


def test_two_distinct_same_category_markets_meet_bar():
    row = _flowing_row(
        spring_category="mlb",
        spring_category_evidence={"completions_60m": 2,
                                  "distinct_markets_60m": 2,
                                  "markets": ["x", "y"], "bar_met": True},
        spring_category_bar_met=True)
    assert spring.spring_candidate_gate(row) is None


def test_missing_category_field_no_longer_fails_the_open():
    """It fails closed for COMPLETION evidence, not for entry."""
    assert spring.spring_candidate_gate(_flowing_row()) is None


def test_owner_decision_observation_constants():
    """OWNER DECISION 5533476555 item 1: observation regime is 35-65
    open / <=0.98 completion; the live >=1c-after-fees bound is
    q1.complement_bound and stays in every complement min()."""
    assert (spring.SPRING_BAND_LO, spring.SPRING_BAND_HI) == \
        (D("0.35"), D("0.65"))
    assert spring.SPRING_PAIR_TARGET == D("0.98")
    # widened cap never admits <1c net: at avg 0.50 the chosen px obeys
    # the hard bound even if the observation cap were looser
    px, econ = spring.spring_complement_price(D("0.50"), D("0.99"),
                                              D("0.005"))
    assert px is not None and px <= D(econ["hard_bound_px"])


def test_rank_carries_strict_fields_from_status_rows(monkeypatch):
    cat_ev = {"completions_60m": 2, "distinct_markets_60m": 2,
              "markets": ["a", "b"], "bar_met": True}
    status = {"spring_watch": [
        {"slug": "with-evidence", "trades_1h": 20, "two_sided_trades": True,
         "both_sides_served": True, "spring_repeated_completion": True,
         "spring_matched_slots": 2, "spring_pair_target": "0.98",
         "spring_slot_counts_theoretical": {"SPRING_MATCHED": 2},
         "spring_category": "mlb", "spring_category_evidence": cat_ev,
         "spring_category_bar_met": True},
        {"slug": "no-evidence", "trades_1h": 30, "two_sided_trades": True,
         "both_sides_served": True},
    ]}
    monkeypatch.setattr(spring.micro, "micro_eligible",
                        lambda t, slug: {"slug": slug, "bid": "0.5000",
                                         "skip_reason": None})
    monkeypatch.setattr(spring.pv2, "load_v2", lambda: None)
    rows = {r["slug"]: r for r in spring.rank_spring_candidates(None, status)}
    good = rows["with-evidence"]
    assert good["spring_category_bar_met"] is True
    assert good["spring_category"] == "mlb"
    assert good["spring_category_evidence"] == cat_ev
    assert good["spring_matched_slots"] == 2
    assert good["spring_pair_target"] == "0.98"
    assert good["spring_skip"] is None
    bad = rows["no-evidence"]
    assert "spring_category_bar_met" not in bad          # stayed missing
    # OWNER 5551236878: missing completion evidence no longer skips the
    # candidate. The field stays absent -- never defaulted to True --
    # so the readiness layer can still report it honestly.
    assert bad["spring_skip"] is None


def test_live_session_opens_without_a_completion_assertion(monkeypatch,
                                                           tmp_path):
    """This is the veto that actually sat on the money path. Without
    PMUS_SPRING_REPEATED_COMPLETION the armed session used to refuse
    every market with spring_category_bar_not_met -- proof of past
    completions demanded before a first OPEN (OWNER 5551236878)."""
    _arm(monkeypatch, tmp_path)
    monkeypatch.delenv(spring.SPRING_STRICT_ENV, raising=False)
    monkeypatch.setattr(pv2, "load_v2", lambda *a, **k: _tiny_model())
    fake = Fake()
    rep = spring.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                             wall=lambda: 0.0)
    assert rep["status"] != "SPRING_SKIP_NOT_QUALIFYING"
    assert rep.get("reason") != "spring_category_bar_not_met"


# ---- P0-E: durable-state arming preflight (5526791808) ----

def test_armed_session_refuses_ephemeral_state(monkeypatch, tmp_path):
    _arm(monkeypatch, tmp_path)
    monkeypatch.delenv(spring.STATE_DURABLE_ENV, raising=False)
    monkeypatch.setattr(spring.os.path, "ismount", lambda p: False)
    fake = Fake()
    rep = spring.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                             wall=lambda: 0.0)
    assert rep["status"] == "STATE_NOT_DURABLE"
    assert rep["reason"] == "state_dir_not_durable_no_mount"
    assert "Railway volume" in rep["required_action"]
    assert fake.posts == []


def test_mounted_state_dir_passes_durability(monkeypatch, tmp_path):
    monkeypatch.setenv(q1.base.STATE_ENV, str(tmp_path))
    monkeypatch.delenv(spring.STATE_DURABLE_ENV, raising=False)
    mounted = {str(tmp_path.resolve())}
    monkeypatch.setattr(spring.os.path, "ismount",
                        lambda p: str(p) in mounted)
    assert spring.state_dir_block_reason() is None


def test_unwritable_state_dir_fails_closed(monkeypatch, tmp_path):
    # a FILE where a directory must be: mkdir/write genuinely fails even
    # for root (chmod-based denial is a no-op under uid 0)
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    monkeypatch.setenv(q1.base.STATE_ENV, str(blocker / "state"))
    monkeypatch.setenv(spring.STATE_DURABLE_ENV, "1")
    reason = spring.state_dir_block_reason()
    assert reason and reason.startswith("state_dir_not_writable")


def test_every_decision_carries_the_owner_required_fields(monkeypatch,
                                                          tmp_path):
    """OWNER 5553997094 #4: state, executed safe action, shadow action
    with probability/rank, L5 target size/aggressiveness, planned size,
    inventory before, and whether the shadow draw differed."""
    from tests.test_pmus_l5_continuous_session import (_armed_session_env,
                                                        _open_proposal)
    _armed_session_env(monkeypatch, tmp_path)
    _open_proposal(monkeypatch)
    monkeypatch.setenv(spring.MAX_ACTIONS_ENV, "1")
    rep = spring.run_session(Fake(), clock=_ticking(), sleep=lambda s: None,
                           wall=lambda: 1000.0, slug="mkt-x", gate_ok=True)
    d = rep["decisions"][0]
    assert d["acted"] == "open" and d["inventory_before"]["up"] == "0"
    assert d["planned_safe_size"]["contracts"] == "0.02"
    sh = d["shadow"]
    assert sh["stochastic"]["sampled"] == "open"
    assert sh["stochastic"]["rank"] == 1 and sh["stochastic"]["p"] == 1.0
    assert "l5_target_size" in sh and "l5_target_aggressiveness" in sh
    assert d["shadow_differs_from_executed"] is False
    o = rep["orders"][0]
    assert o["executed_safe_size"]["contracts"] == "0.02"
    assert "l5_target_size" in o
    # OWNER 5556038561 #7: the divergence is NAMED per decision
    assert d["divergence"] == "none"
    assert d["size_divergence"] == "venue_minimum_vs_l5_target"
    # OWNER 5556038561 #4: the first leg carried its complement preflight
    pf = d["complement_preflight"]
    first_side = o["side"]                                # cheaper touch
    assert pf["side"].endswith("NO" if first_side == "YES" else "YES")
    assert pf["preview_ok"] is True
    assert D(pf["px"]) + D(o["px"]) <= D("0.98")          # fee-safe pair
    expect_wire = (D("1") - D(str(pf["px"])) if pf["side"].endswith("NO")
                   else D(str(pf["px"])))
    assert D(pf["wire_px"]) == expect_wire


# ---------------- OWNER 5556038561: complement acceptance gate ----------------

def test_new_first_leg_fails_closed_until_complement_acceptance_proven(
        monkeypatch, tmp_path):
    from tests.test_pmus_l5_continuous_session import (_armed_session_env,
                                                        _open_proposal)
    _armed_session_env(monkeypatch, tmp_path)
    _open_proposal(monkeypatch)
    (tmp_path / spring.COMPLEMENT_PROOF_FILE).unlink()
    fake = Fake()
    rep = spring.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                             wall=lambda: 1000.0, slug="mkt-x", gate_ok=True)
    assert rep["status"] == "SPRING_SAFE_REFUSAL"
    assert rep["reason"] == "complement_acceptance_unproven"
    assert rep["complement_proof"]["reason"] == "never_attempted"
    assert not any(p == "/v1/orders" for p, _ in fake.posts)
    # a proof that says NOT proven (revoked) blocks the same way
    _complement_proven(tmp_path, proven=False)
    rep = spring.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                             wall=lambda: 1000.0, slug="mkt-x", gate_ok=True)
    assert rep["reason"] == "complement_acceptance_unproven"


def test_resume_proves_complement_acceptance_with_a_resting_opposite_order(
        monkeypatch, tmp_path):
    """Held +0.02 YES @0.43 on a 0.43/0.435 book: the probe is the exact
    fee-safe complement (NO 0.55 -> wire 0.45, venue-minimum 0.01,
    post-only); the venue RESTS it -> proof saved with the evidence, the
    order is cancelled, and a fresh first leg is allowed afterwards."""
    from tests.test_pmus_l5_continuous_session import (_armed_session_env,
                                                        _ledger_with_fill)
    _armed_session_env(monkeypatch, tmp_path)
    _ledger_with_fill(tmp_path, slug="held")
    (tmp_path / spring.COMPLEMENT_PROOF_FILE).unlink()

    def hold_proposal(model, base_state, last_rel, last_size, residual,
                      clone_avg_cost=None):
        return {"recommended_relation": "extend", "persist_extend_p": 0.7,
                "switch_complete_p": 0.3, "next_reevaluation_s": 2.0,
                "relation_ranked": [{"relation": "extend", "p": 0.7}],
                "state_n": 20, "off_distribution": False}
    monkeypatch.setattr(pv2, "proposal", hold_proposal)
    monkeypatch.setenv(spring.MAX_ACTIONS_ENV, "1")
    fake = Fake(bid="0.4300", ask="0.4350", fill_all=False)
    fake.positions["held"] = {"netPositionDecimal": "0.02"}
    rep = spring.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                             wall=lambda: 1000.0, slug="held", gate_ok=True,
                             resume=True)
    acc = rep["complement_acceptance"]
    assert acc["proven"] is True, acc
    assert acc["end_reason"] == "resting:ORDER_STATE_OPEN"
    assert acc["submitted"] == {"side": "OUTCOME_SIDE_NO", "px": "0.55",
                                "wire_px": "0.45", "qty": "0.01",
                                "post_only": True}
    probe = [b for p, b in fake.posts if p == "/v1/orders"][0]
    assert probe["outcomeSide"] == "OUTCOME_SIDE_NO"
    assert probe["price"]["value"] == "0.45" and probe["quantity"] == 0.01
    assert probe["participateDontInitiate"] is True
    assert fake.orders["SPR1"]["state"] == "ORDER_STATE_CANCELED"
    # the probe is a SUBMITTED order in the ladder, unfilled
    assert rep["orders"][0]["stage"] == "complement_acceptance"
    assert rep["orders"][0]["outcome"] == "no_fill_cancelled"
    assert rep["inventory"]["dn"] == "0"
    assert spring.complement_acceptance_proven()
    assert spring.load_complement_proof()["order_id"] == "SPR1"


def test_resume_probe_rejection_keeps_first_legs_blocked(monkeypatch,
                                                         tmp_path):
    from tests.test_pmus_l5_continuous_session import (_armed_session_env,
                                                        _ledger_with_fill)
    _armed_session_env(monkeypatch, tmp_path)
    _ledger_with_fill(tmp_path, slug="held")
    (tmp_path / spring.COMPLEMENT_PROOF_FILE).unlink()
    _script_proposals(monkeypatch, ["extend"])
    monkeypatch.setenv(spring.MAX_ACTIONS_ENV, "1")

    class Rejecting(Fake):
        def request(self, method, path, body=None, base=None):
            out = super().request(method, path, body, base)
            if path == "/v1/orders":
                self.orders[out["id"]]["state"] = "ORDER_STATE_REJECTED"
                self.open_order = None
            return out
    fake = Rejecting(bid="0.4300", ask="0.4350", fill_all=False)
    fake.positions["held"] = {"netPositionDecimal": "0.02"}
    rep = spring.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                             wall=lambda: 1000.0, slug="held", gate_ok=True,
                             resume=True)
    acc = rep["complement_acceptance"]
    assert acc["proven"] is False
    assert acc["end_reason"].startswith("rejected:ORDER_REJECTED_ZERO_FILL")
    assert not spring.complement_acceptance_proven()
    assert spring.load_complement_proof()["reason"].startswith("rejected")
    # bounded: the next resume inside the retry interval does NOT submit
    # another probe order; after the interval it does
    n_orders = sum(1 for p, _ in fake.posts if p == "/v1/orders")
    rep2 = spring.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                              wall=lambda: 1100.0, slug="held", gate_ok=True,
                              resume=True)
    assert "complement_acceptance" not in rep2
    rep3 = spring.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                              wall=lambda: 1000.0 + spring.COMPLEMENT_PROOF_RETRY_S,
                              slug="held", gate_ok=True, resume=True)
    assert rep3["complement_acceptance"]["proven"] is False
    assert sum(1 for p, _ in fake.posts if p == "/v1/orders") >= n_orders + 1


def test_complement_rejection_revokes_the_proof(monkeypatch, tmp_path):
    from tests.test_pmus_l5_continuous_session import (_armed_session_env,
                                                        _ledger_with_fill)
    _armed_session_env(monkeypatch, tmp_path)
    _ledger_with_fill(tmp_path, slug="held")
    _script_proposals(monkeypatch, ["complete"])
    monkeypatch.setenv(spring.MAX_ACTIONS_ENV, "1")

    class Rejecting(Fake):
        def request(self, method, path, body=None, base=None):
            out = super().request(method, path, body, base)
            if path == "/v1/orders":
                self.orders[out["id"]]["state"] = "ORDER_STATE_REJECTED"
                self.open_order = None
            return out
    fake = Rejecting(bid="0.4300", ask="0.4350", fill_all=False)
    fake.positions["held"] = {"netPositionDecimal": "0.02"}
    assert spring.complement_acceptance_proven()            # fixture proof
    rep = spring.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                             wall=lambda: 1000.0, slug="held", gate_ok=True,
                             resume=True)
    assert rep["orders"][0]["relation"] == "complete"
    assert rep["orders"][0]["end_reason"].startswith("rejected")
    assert rep["orders"][0]["rejection_class"].startswith(
        "implementation bug candidate")
    proof = spring.load_complement_proof()
    assert proof["proven"] is False
    assert proof["reason"].startswith("revoked:complete_rejected")
    assert proof["revoked_order"]["order_id"] == "SPR1"


def test_complement_rejected_after_book_moved_through_price_is_a_venue_adaptation(
        monkeypatch, tmp_path):
    """LIVE 02:10Z: rejections right after a fill at our price are the
    book moving through it -- classified from the FRESH book, no proof
    revocation, wait for the next move."""
    from tests.test_pmus_l5_continuous_session import (_armed_session_env,
                                                        _ledger_with_fill)
    _armed_session_env(monkeypatch, tmp_path)
    _ledger_with_fill(tmp_path, slug="held")
    _script_proposals(monkeypatch, ["complete"])
    monkeypatch.setenv(spring.MAX_ACTIONS_ENV, "1")

    class MovedThrough(Fake):
        def request(self, method, path, body=None, base=None):
            out = super().request(method, path, body, base)
            if path == "/v1/orders":
                self.orders[out["id"]]["state"] = "ORDER_STATE_REJECTED"
                self.open_order = None
                # NO complete at 0.55 <-> YES bid jumps to 0.45: NO ask
                # is now 0.55 -> our price crossed
                self.bid = "0.4500"
            return out
    fake = MovedThrough(bid="0.4300", ask="0.4350", fill_all=False)
    fake.positions["held"] = {"netPositionDecimal": "0.02"}
    rep = spring.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                             wall=lambda: 1000.0, slug="held", gate_ok=True,
                             resume=True)
    o = rep["orders"][0]
    assert o["end_reason"].startswith("rejected")
    assert o["rejection_class"].startswith("venue/execution adaptation")
    assert o["fresh_opp_ask"] == "0.5500"
    assert spring.complement_acceptance_proven()          # NOT revoked


def test_rejection_row_carries_the_venue_market_status(monkeypatch, tmp_path):
    """R5F 2026-09-07 (5577507821): 36 rejections sat 2-12 ticks off the
    fresh ask with no venue reason. The classifier now reads the venue's
    own market flags at rejection time and names a non-OPEN status on
    the class -- observability only: the crossed/not-crossed decision
    and the proof revocation are unchanged."""
    from tests.test_pmus_l5_continuous_session import (_armed_session_env,
                                                        _ledger_with_fill)
    _armed_session_env(monkeypatch, tmp_path)
    _ledger_with_fill(tmp_path, slug="held")
    _script_proposals(monkeypatch, ["complete"])
    monkeypatch.setenv(spring.MAX_ACTIONS_ENV, "1")

    class Halted(Fake):
        def request(self, method, path, body=None, base=None):
            out = super().request(method, path, body, base)
            if path == "/v1/orders":
                self.orders[out["id"]]["state"] = "ORDER_STATE_REJECTED"
                self.open_order = None
                self.halted = True
            if path.startswith("/v1/market/slug/") and getattr(self, "halted", False):
                out["market"]["status"] = "MARKET_STATUS_HALTED"
                out["market"]["tradingHalted"] = True
            return out
    fake = Halted(bid="0.4300", ask="0.4350", fill_all=False)
    fake.positions["held"] = {"netPositionDecimal": "0.02"}
    rep = spring.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                             wall=lambda: 1000.0, slug="held", gate_ok=True,
                             resume=True)
    o = rep["orders"][0]
    assert o["end_reason"].startswith("rejected")
    assert o["rejection_class"].startswith("implementation bug candidate")
    assert o["rejection_class"].endswith(";venue_market_status=MARKET_STATUS_HALTED")
    assert o["venue_market"]["status"] == "MARKET_STATUS_HALTED"
    assert o["venue_market"]["tradingHalted"] is True      # carried verbatim
    assert o["venue_market"]["closed"] is False
    # the consumer of `crossed` is unchanged: a non-crossing rejection
    # still revokes the proof exactly as before
    assert spring.load_complement_proof()["proven"] is False


def test_rejection_row_carries_the_authenticated_recorder_book(monkeypatch, tmp_path):
    """R5F 2026-09-08 01:09Z: NO completes at 0.71 filled twice on
    malden-stoole and were then rejected four times while the public
    gateway BBO read the opposite ask at 0.73 (two ticks of room) and the
    venue reported MARKET_STATUS_OPEN. The rejection row now carries the
    recorder's AUTHENTICATED top of book beside the public one, and says
    so on the class when the two disagree. Read-only: the crossed
    decision and the proof revocation still follow the gateway book."""
    from tests.test_pmus_l5_continuous_session import (_armed_session_env,
                                                        _ledger_with_fill)
    from src import pmus_spring_l5_probe_entry as entry
    _armed_session_env(monkeypatch, tmp_path)
    _ledger_with_fill(tmp_path, slug="held")
    _script_proposals(monkeypatch, ["complete"])
    monkeypatch.setenv(spring.MAX_ACTIONS_ENV, "1")
    # the authenticated book is TIGHTER than the public one: a NO buy at
    # 0.55 does not cross the public NO ask (1 - 0.43 = 0.57) but does
    # cross the authenticated NO ask (1 - 0.45 = 0.55)
    monkeypatch.setattr(entry, "load_status_document", lambda path=None: (
        {"t": "2026-09-08T01:09:49Z",
         "spring_watch": [{"slug": "held", "bid": "0.4500", "ask": "0.4550",
                           "last_authenticated_update_age_s": 0.4,
                           "both_sides_served": True}]}, "status_url:test"))

    class Rejecting(Fake):
        def request(self, method, path, body=None, base=None):
            out = super().request(method, path, body, base)
            if path == "/v1/orders":
                self.orders[out["id"]]["state"] = "ORDER_STATE_REJECTED"
                self.open_order = None
            return out
    fake = Rejecting(bid="0.4300", ask="0.4350", fill_all=False)
    fake.positions["held"] = {"netPositionDecimal": "0.02"}
    rep = spring.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                             wall=lambda: 1000.0, slug="held", gate_ok=True,
                             resume=True)
    o = rep["orders"][0]
    assert o["end_reason"].startswith("rejected")
    book = o["recorder_book"]
    assert book["yes_bid"] == "0.4500" and book["authenticated_age_s"] == 0.4
    assert book["opp_ask_authenticated"] == "0.5500"
    assert book["disagrees_with_gateway"] is True
    assert o["rejection_class"].endswith(";authenticated_book_crossed")
    # a status document that cannot be read never blocks or raises
    monkeypatch.setattr(entry, "load_status_document",
                        lambda path=None: (_ for _ in ()).throw(RuntimeError("no status")))
    rep2 = spring.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                              wall=lambda: 1000.0, slug="held", gate_ok=True,
                              resume=True)
    rejected = [x for x in rep2["orders"] if x.get("rejection_class")]
    assert rejected, rep2["orders"]
    assert rejected[0]["recorder_book"]["unreadable"].startswith("RuntimeError")
    assert ";authenticated_book_crossed" not in rejected[0]["rejection_class"]


def test_first_leg_refused_when_its_complement_preview_is_rejected(
        monkeypatch, tmp_path):
    from tests.test_pmus_l5_continuous_session import (_armed_session_env,
                                                        _open_proposal)
    _armed_session_env(monkeypatch, tmp_path)
    _open_proposal(monkeypatch)
    monkeypatch.setenv(spring.MAX_ACTIONS_ENV, "1")

    class NoPreview(Fake):
        # the complement preflight is the FIRST preview of the session
        # (it precedes the first leg's own preview); refuse exactly it
        previews = 0

        def request(self, method, path, body=None, base=None):
            if path == "/v1/order/preview":
                self.previews += 1
                if self.previews == 1:
                    return {"error": "complement preview refused"}
            return super().request(method, path, body, base)
    fake = NoPreview()
    rep = spring.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                             wall=lambda: 1000.0, slug="mkt-x", gate_ok=True)
    assert rep["stop_reason"] == "complement_preview_rejected"
    assert rep["decisions"][0]["divergence"] == \
        "safety_adaptation:complement_preview_rejected"
    assert not any(p == "/v1/orders" for p, _ in fake.posts)


def test_divergence_reason_separates_safety_from_behavior():
    dr = spring.divergence_reason
    assert dr("residual_cap:3.0>=3", "extend", "hold", "complete") == \
        "safety_adaptation:residual_cap"
    assert dr("off_distribution_hold", "extend", "hold", None) == \
        "safety_adaptation:off_distribution_hold"
    assert dr("residual_cap:fallback_complete:p=0.336", "extend",
              "complete", "extend") == "policy_flag:cap_fallback_complete"
    assert dr(None, "extend", "extend", "complete") == \
        "behavioral:modal_argmax_vs_stochastic_draw"
    assert dr(None, "extend", "extend", "extend") == "none"
    assert dr(None, "open", "open", None) == "none"


def test_stochastic_selection_executes_the_seeded_draw(monkeypatch, tmp_path):
    """Phase 1 largest mismatch: with PMUS_SPRING_ACTION_SELECTION=
    stochastic the executed relation IS the seeded draw the shadow log
    carries (same seed, same masses); modal stays the default."""
    from tests.test_pmus_l5_continuous_session import (_armed_session_env,
                                                        _ledger_with_fill)
    _armed_session_env(monkeypatch, tmp_path)
    _ledger_with_fill(tmp_path, slug="held")
    monkeypatch.setattr(pv2, "load_v2", lambda *a, **k: {
        **_tiny_model(), "_sha256_16": "abcdef0123456789"})

    def prop(model, base_state, last_rel, last_size, residual,
             clone_avg_cost=None):
        return {"recommended_relation": "extend", "persist_extend_p": 0.55,
                "switch_complete_p": 0.45, "next_reevaluation_s": 2.0,
                "relation_ranked": [{"relation": "extend", "p": 0.55},
                                    {"relation": "complete", "p": 0.45}],
                "state_n": 40, "off_distribution": False}
    monkeypatch.setattr(pv2, "proposal", prop)
    monkeypatch.setenv(spring.MAX_ACTIONS_ENV, "4")
    monkeypatch.setenv(spring.ACTION_SELECTION_ENV, "stochastic")
    fake = Fake(bid="0.4300", ask="0.4350", fill_all=False)
    fake.positions["held"] = {"netPositionDecimal": "0.02"}
    rep = spring.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                             wall=lambda: 1000.0, slug="held", gate_ok=True,
                             resume=True)
    decs = rep["decisions"]
    assert decs and all(d["action_selection"] == "stochastic" for d in decs)
    for d in decs:
        assert d["acted"] == d["shadow"]["stochastic"]["sampled"]
        assert d["shadow_differs_from_executed"] is False
        assert d["divergence"] in ("none", "none:stochastic_draw_executed")
        assert d["divergence_class"] == "no disagreement"
    # the draw is seeded per (slug, idx, model sha): both relations occur
    assert {d["acted"] for d in decs} <= {"extend", "complete"}
    # default (modal) still executes the argmax
    monkeypatch.delenv(spring.ACTION_SELECTION_ENV)
    fake2 = Fake(bid="0.4300", ask="0.4350", fill_all=False)
    fake2.positions["held"] = {"netPositionDecimal": "0.02"}
    rep2 = spring.run_session(fake2, clock=_ticking(), sleep=lambda s: None,
                              wall=lambda: 1000.0, slug="held", gate_ok=True,
                              resume=True)
    assert all(d["acted"] == "extend" and d["action_selection"] == "modal"
               for d in rep2["decisions"])


def test_divergence_class_covers_the_five_owner_classes():
    dc = spring.divergence_class
    assert dc("safety_adaptation:residual_cap") == "safety-cap override"
    assert dc("safety_adaptation:spend_cap->complete") == "safety-cap override"
    assert dc("safety_adaptation:off_distribution_hold") == \
        "genuinely off-distribution state"
    assert dc("safety_adaptation:complement_preview_rejected") == \
        "venue/execution adaptation"
    assert dc("behavioral:modal_argmax_vs_stochastic_draw").startswith(
        "model deficiency")
    assert dc("none") == "no disagreement"
    assert dc("weird:thing").startswith("implementation bug")


def test_complement_plan_prices_the_opposite_side_fee_safe_and_maps_wire():
    plan = spring.complement_plan(True, D("0.36"),
                                  {"bid": "0.35", "ask": "0.355"}, D("0.01"))
    assert plan["side"] == "OUTCOME_SIDE_NO"
    assert plan["px"] == D("0.62") and plan["wire_px"] == "0.38"
    assert plan["opp_ask"] == "0.65"                     # 1 - yes bid
    plan = spring.complement_plan(False, D("0.60"),
                                  {"bid": "0.39", "ask": "0.40"}, D("0.01"))
    assert plan["side"] == "OUTCOME_SIDE_YES"
    assert plan["px"] == D("0.38") and plan["wire_px"] == "0.38"


# ---- OWNER GO (#167, reply to 5600883240): the entry band must not end a
# ---- session that is holding inventory. It still refuses the OPEN/EXTEND.

class DriftingFake(Fake):
    """A market whose bid collapses out of the band after the first order.

    This reproduces the R5F failure: we act in band, the bid runs away, and
    every later cycle meets the entry band. A flat market that is ALREADY
    out of band never reaches the loop -- it is refused at admission as
    SPRING_SKIP_NOT_QUALIFYING -- so the band's in-loop branches are only
    reachable by drifting, which is what this fixture does.
    """

    def __init__(self, drift_after=1, drifted_bid="0.1000",
                 drifted_ask="0.1050", **kw):
        super().__init__(**kw)
        self.drift_after = drift_after
        self.drifted = (drifted_bid, drifted_ask)
        self.posted = 0

    def request(self, method, path, body=None, base=None):
        out = super().request(method, path, body, base)
        if path == "/v1/orders":
            self.posted += 1
            if self.posted >= self.drift_after:
                self.bid, self.ask = self.drifted
        return out


def _entry_band_rows(rep):
    return [d for d in rep.get("decisions", []) if d.get("entry_band_left")]


def test_entry_band_on_held_inventory_no_longer_ends_the_session(
        monkeypatch, tmp_path):
    """The measured R5F defect: the band fired 42 times, 39 while HOLDING
    unmatched inventory, and each one ended the session -- taking the
    residual's completion work with it. The session must now survive."""
    _arm(monkeypatch, tmp_path)
    _script_proposals(monkeypatch, ["open", "extend", "extend", "complete"])
    fake = DriftingFake(drift_after=1)
    rep = spring.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                             wall=lambda: 1000.0)
    rows = _entry_band_rows(rep)
    assert rows, "the band never fired; fixture does not reproduce the case"
    assert rows[0]["entry_band_left"]["outcome"] == "completion_only"
    assert not str(rep.get("stop_reason") or "").startswith("entry_band_left")
    # it kept working the market instead of dying on the first refusal
    assert len(rep["decisions"]) > 3


def test_entry_band_still_refuses_the_first_side_action_it_fired_on(
        monkeypatch, tmp_path):
    """NOT permission to average into a collapsing held side: no OPEN or
    EXTEND may be priced off an out-of-band bid."""
    _arm(monkeypatch, tmp_path)
    _script_proposals(monkeypatch, ["open", "extend", "extend", "complete"])
    fake = DriftingFake(drift_after=1)
    rep = spring.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                             wall=lambda: 1000.0)
    dec = _entry_band_rows(rep)[0]
    assert dec["acted_draw"] == "extend"
    assert dec["entry_band_left"]["outcome"] == "completion_only"
    assert "entry_band_left" in dec["cap_binding"]
    # the band routed it away from the first side; `acted` then reads what
    # actually happened -- here `hold`, because the second gate
    # (no_legal_maker_price) refused the completion the band routed to.
    assert dec["acted"] not in ("open", "extend")
    # exactly ONE first-side action ever priced: the in-band open
    first_side = [a for a in rep["actions"]
                  if a["relation"] in ("open", "extend")]
    assert len(first_side) == 1 and first_side[0]["relation"] == "open"


def test_entry_band_while_flat_still_stops_the_session(monkeypatch, tmp_path):
    """Unchanged behaviour: with no inventory there is nothing to complete,
    so the band still ends the session and names itself in the log."""
    _arm(monkeypatch, tmp_path)
    _script_proposals(monkeypatch, ["open", "open"])
    # nothing fills, so the book drifts out of band while we are still flat
    fake = DriftingFake(drift_after=1, fill_all=False)
    rep = spring.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                             wall=lambda: 1000.0)
    assert str(rep.get("stop_reason") or "").startswith("entry_band_left")
    rows = _entry_band_rows(rep)
    assert rows[-1]["entry_band_left"]["outcome"] == "session_stopped_flat"
    assert rows[-1]["entry_band_left"]["held_inventory"] == "0"


def test_band_conversion_records_the_draw_it_overrode(monkeypatch, tmp_path):
    """The executed relation is what the record says was acted; the draw the
    band overrode is kept beside it, as the spend cap already does."""
    _arm(monkeypatch, tmp_path)
    _script_proposals(monkeypatch, ["open", "extend", "complete"])
    fake = DriftingFake(drift_after=1)
    rep = spring.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                             wall=lambda: 1000.0)
    dec = _entry_band_rows(rep)[0]
    assert dec["would_act_without_safety_cap"] == dec["acted_draw"]
    assert dec["entry_band_left"]["held_inventory"] != "0"
    assert dec["entry_band_left"]["in_observation_band"] is False
    assert dec["divergence_class"] == "safety-cap override"


def test_surviving_the_band_does_not_by_itself_price_a_completion(
        monkeypatch, tmp_path):
    """The honest limit of this change, pinned so it cannot be forgotten.

    Surviving the band is necessary but not always sufficient. How far the
    bid ran decides whether the completion the session survives to attempt
    is priceable at all: at a moderate drift the complement's own fee-safe
    branch finds a legal price and the leg gets paired (see
    tests/test_l5_evidence_integrity.py), but at the deep drift used here --
    bid 0.10, so the complement sits above 0.80 -- a SECOND gate,
    `complement_not_priceable:no_legal_maker_price`, refuses it.

    That second gate is the staged price-band change (#167 task 91) and the
    owner has NOT given it. So this change recovers the shallow-drift
    strandings on its own and leaves the deep-drift ones for that separate
    decision. If this test starts failing because completes are priced at
    this depth, the decision was taken -- deliberately or not -- and must be
    re-examined.
    """
    _arm(monkeypatch, tmp_path)
    _script_proposals(monkeypatch, ["open", "extend", "complete", "complete"])
    fake = DriftingFake(drift_after=1)
    rep = spring.run_session(fake, clock=_ticking(), sleep=lambda s: None,
                             wall=lambda: 1000.0)
    refusals = {d.get("refusal") for d in rep["decisions"]}
    assert "complement_not_priceable:no_legal_maker_price" in refusals
    assert not [a for a in rep["actions"] if a["relation"] == "complete"]
