"""Fail-closed tests for the DISARMED-by-default PMUS q=1 executor."""
from __future__ import annotations

import base64
import inspect
import json
from decimal import Decimal as D

import pytest

from src import pmus_q1_executor as ex


SLUG = "aec-mlb-bal-col-2026-09-02"
SECRET = base64.b64encode(b"\x07" * 32).decode()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    for var in (ex.LIVE_ENV, ex.SID_ENV, ex.ARM_ENV, ex.MARKET_ENV,
                ex.GATE_ENV, ex.KEY_ENV, ex.SECRET_ENV,
                ex.RESIDUAL_ACTION_ENV):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv(ex.STATE_ENV, str(tmp_path / "q1"))
    return monkeypatch


def arm(monkeypatch, sid="probe-01"):
    monkeypatch.setenv(ex.LIVE_ENV, "YES")
    monkeypatch.setenv(ex.SID_ENV, sid)
    monkeypatch.setenv(ex.ARM_ENV, sid)
    monkeypatch.setenv(ex.KEY_ENV, "kid-test-abc")
    monkeypatch.setenv(ex.SECRET_ENV, SECRET)
    monkeypatch.setenv(ex.MARKET_ENV, SLUG)
    monkeypatch.setenv(ex.GATE_ENV, SLUG)


class FakeTransport:
    """Scripted transport; records every call; NEVER used unless armed."""

    def __init__(self, script):
        self.script = dict(script)
        self.calls = []

    def request(self, method, path, body=None, base=ex.API):
        self.calls.append((method, path, body))
        key = f"{method} {path}"
        val = self.script.get(key)
        if callable(val):
            return val(self.calls)
        return val if val is not None else {}


def _happy_script(first_price="0.4500", comp_price="0.5400"):
    polls = {"n": 0}

    def first_order(_calls):
        polls["n"] += 1
        if polls["n"] >= 2:
            return {"order": {"status": "ORDER_STATUS_FILLED",
                              "filledQuantity": "1",
                              "avgPx": {"value": first_price}}}
        return {"order": {"status": "ORDER_STATUS_OPEN",
                          "filledQuantity": "0"}}

    return {
        "GET /v1/account/balances": {"buyingPower": {"value": "39.50"}},
        "GET /v1/portfolio/positions": {"positions": []},
        "GET /v1/orders/open": {"orders": []},
        f"GET /v1/markets/{SLUG}/bbo": {"marketData": {
            "bestBid": {"value": "0.4500"},
            "bestAsk": {"value": "0.4550"}}},
        "POST /v1/order/preview": {},
        "POST /v1/orders": {"orderId": "ord-1"},
        "GET /v1/order/ord-1": first_order,
        "GET /v1/order/": {},
        "POST /v1/orders/open/cancel": {},
    }


def _fast(report_env=None):
    clock_state = {"t": 0.0}

    def clock():
        clock_state["t"] += 5.0
        return clock_state["t"]

    return clock, lambda s: None


# ---------------- fail-closed paths ----------------

def test_disarmed_by_default_makes_zero_network_calls(env):
    t = FakeTransport({})
    rep = ex.run_campaign(t)
    assert rep["status"] == "DISARMED" and t.calls == []


def test_arm_mismatch_and_missing_pieces_fail_closed(env):
    t = FakeTransport({})
    env.setenv(ex.LIVE_ENV, "YES")
    env.setenv(ex.SID_ENV, "sid-a")
    env.setenv(ex.ARM_ENV, "sid-DIFFERENT")
    assert ex.run_campaign(t)["status"] == "DISARMED"
    env.setenv(ex.ARM_ENV, "sid-a")           # armed but no creds
    assert ex.run_campaign(t)["status"] == "NO_CREDENTIALS"
    env.setenv(ex.KEY_ENV, "k")
    env.setenv(ex.SECRET_ENV, SECRET)
    env.setenv(ex.MARKET_ENV, SLUG)           # gate evidence absent
    assert ex.run_campaign(t)["status"] == "GATE_NOT_CLEARED"
    env.setenv(ex.GATE_ENV, "some-other-market")
    assert ex.run_campaign(t)["status"] == "GATE_NOT_CLEARED"
    assert t.calls == []                      # still zero network activity


def test_daily_loss_stop_blocks_before_any_call(env):
    arm(env)
    ex.ledger_append({"kind": "realized", "realized_usd": "-1.00"})
    t = FakeTransport({})
    rep = ex.run_campaign(t)
    assert rep["status"] == "DAILY_LOSS_STOP" and t.calls == []


@pytest.mark.parametrize("mutate,reason", [
    ({"GET /v1/portfolio/positions":
      {"positions": [{"marketSlug": SLUG, "quantity": "2"}]}},
     "existing_position_in_target_market"),
    ({"GET /v1/orders/open": {"orders": [{"id": "x"}]}},
     "open_orders_present"),
    ({"GET /v1/account/balances": {"buyingPower": {"value": "0.50"}}},
     "buying_power"),
    ({f"GET /v1/markets/{SLUG}/bbo":
      {"marketData": {"bestBid": {"value": "0.8300"},
                      "bestAsk": {"value": "0.8350"}}}},
     "entry_out_of_band"),
    ({f"GET /v1/markets/{SLUG}/bbo":
      {"marketData": {"bestBid": {"value": "0.4500"}}}},
     "bbo_not_two_sided"),
])
def test_preflight_failures_place_no_order(env, mutate, reason):
    arm(env)
    script = _happy_script()
    script.update(mutate)
    t = FakeTransport(script)
    clock, slp = _fast()
    rep = ex.run_campaign(t, clock=clock, sleep=slp)
    assert rep["status"] == "PREFLIGHT_FAILED"
    assert reason.split(":")[0] in rep["reason"]
    assert not any(p == "/v1/orders" and m == "POST"
                   for m, p, _ in t.calls)


def test_preview_rejection_halts_without_create(env):
    arm(env)
    script = _happy_script()
    script["POST /v1/order/preview"] = {"warnings": ["would cross"]}
    t = FakeTransport(script)
    clock, slp = _fast()
    rep = ex.run_campaign(t, clock=clock, sleep=slp)
    assert rep["status"] == "HALT" and "PREVIEW_REJECTED" in rep["reason"]
    assert not any(p == "/v1/orders" and m == "POST" for m, p, _ in t.calls)
    # deadman still fired
    assert any(p == "/v1/orders/open/cancel" for _, p, _ in t.calls)


def test_ambiguous_fill_price_is_a_halt(env):
    arm(env)
    script = _happy_script()
    script["GET /v1/order/ord-1"] = {"order": {
        "status": "ORDER_STATUS_FILLED", "filledQuantity": "1"}}  # no price
    t = FakeTransport(script)
    clock, slp = _fast()
    rep = ex.run_campaign(t, clock=clock, sleep=slp)
    assert rep["status"] == "HALT" and "AMBIGUOUS_FILL_PRICE" in rep["reason"]
    assert any(p == "/v1/orders/open/cancel" for _, p, _ in t.calls)


# ---------------- economics ----------------

def test_complement_bound_and_taker_rule():
    assert ex.complement_bound(D("0.45")) == D("0.540")
    assert ex.entry_ok(D("0.80")) and not ex.entry_ok(D("0.805"))
    assert ex.complement_bound(D("0.79")) == D("0.20")   # exactly at band
    assert ex.complement_bound(D("0.80")) is None        # <0.20 -> no band
    assert ex.taker_completion_ok(D("0.45"), D("0.52")) is True
    assert ex.taker_completion_ok(D("0.45"), D("0.55")) is False


# ---------------- lifecycle ----------------

def test_happy_path_q1_lifecycle(env, tmp_path):
    arm(env)
    script = _happy_script()
    comp_polls = {"n": 0}

    def orders_post(calls):
        n = sum(1 for m, p, _ in calls if m == "POST" and p == "/v1/orders")
        return {"orderId": f"ord-{n}"}

    def comp_order(_calls):
        comp_polls["n"] += 1
        if comp_polls["n"] >= 2:
            return {"order": {"status": "ORDER_STATUS_FILLED",
                              "filledQuantity": "1",
                              "outcomeSide": "OUTCOME_SIDE_NO",
                              "avgPx": {"value": "0.4600"}}}   # long-side px
        return {"order": {"status": "ORDER_STATUS_OPEN",
                          "filledQuantity": "0"}}
    script["POST /v1/orders"] = orders_post
    script["GET /v1/order/ord-2"] = comp_order
    t = FakeTransport(script)
    clock, slp = _fast()
    rep = ex.run_campaign(t, clock=clock, sleep=slp)
    assert rep["status"] == "MATCHED"
    assert rep["pair_cost"] == "0.9900"
    assert rep["locked_edge_pre_rebate"] == "0.0100"
    assert rep["realized_pnl"] is None            # settled != realized
    assert "PENDING" in rep["settlement"]
    creates = [b for m, p, b in t.calls if m == "POST" and p == "/v1/orders"]
    assert len(creates) == 2
    for body in creates:
        assert body["quantity"] == 1
        assert body["participateDontInitiate"] is True     # post-only maker
        assert body["type"] == "ORDER_TYPE_LIMIT"
        assert body["action"] == "ORDER_ACTION_BUY"
    assert creates[0]["outcomeSide"] == "OUTCOME_SIDE_YES"
    assert creates[1]["outcomeSide"] == "OUTCOME_SIDE_NO"
    # NO complement at NO 0.540 is sent as the venue's long-side price 0.460
    assert D(creates[1]["price"]["value"]) == D("0.460")
    # previews happened BEFORE each create
    idx = [i for i, (m, p, _) in enumerate(t.calls)
           if p in ("/v1/order/preview", "/v1/orders") and m == "POST"]
    order = [t.calls[i][1] for i in idx]
    assert order == ["/v1/order/preview", "/v1/orders",
                     "/v1/order/preview", "/v1/orders"]
    # durable intent persisted before placement, deadman fired at exit
    st = json.loads((tmp_path / "q1" / "campaign_state.json").read_text())
    assert st["orders"][0]["stage"] == "first_leg"
    assert any(p == "/v1/orders/open/cancel" for _, p, _ in t.calls)


def test_no_fill_cancels_exact_and_verifies(env):
    arm(env)
    script = _happy_script()
    script["GET /v1/order/ord-1"] = {"order": {
        "status": "ORDER_STATUS_OPEN", "filledQuantity": "0"}}
    script["POST /v1/order/ord-1/cancel"] = {}
    t = FakeTransport(script)
    clock, slp = _fast()
    env.setenv(ex.FIRST_WAIT_ENV, "20")
    rep = ex.run_campaign(t, clock=clock, sleep=slp)
    assert rep["status"] == "NO_FILL" and rep["cancel_verified"] is True
    assert any(p == "/v1/order/ord-1/cancel" for _, p, _ in t.calls)


def test_one_sided_hold_respects_residual_cap_and_action(env):
    arm(env)
    script = _happy_script()

    def orders_post(calls):
        n = sum(1 for m, p, _ in calls if m == "POST" and p == "/v1/orders")
        return {"orderId": f"ord-{n}"}
    script["POST /v1/orders"] = orders_post
    script["GET /v1/order/ord-2"] = {"order": {
        "status": "ORDER_STATUS_OPEN", "filledQuantity": "0"}}
    script["POST /v1/order/ord-2/cancel"] = {}
    t = FakeTransport(script)
    clock, slp = _fast()
    env.setenv(ex.DEADLINE_ENV, "40")
    rep = ex.run_campaign(t, clock=clock, sleep=slp)
    assert rep["status"] == "ONE_SIDED_HOLD"
    assert rep["residual"] == 1
    assert rep["residual_action"] == "hold"       # default posture
    assert rep["realized_pnl"] is None


def test_no_secret_material_in_report_or_ledger(env, tmp_path, capsys):
    arm(env)
    t = FakeTransport(_happy_script())
    clock, slp = _fast()
    rep = ex.run_campaign(t, clock=clock, sleep=slp)
    blob = json.dumps(rep)
    ledger = (tmp_path / "q1" / "ledger.jsonl").read_text()
    for secret in ("kid-test-abc", SECRET):
        assert secret not in blob and secret not in ledger


def test_signed_headers_match_documented_scheme(env):
    arm(env)
    from cryptography.hazmat.primitives.asymmetric import ed25519
    h = ex.signed_headers("POST", "/v1/orders", now_ms=1700000000000)
    pub = ed25519.Ed25519PrivateKey.from_private_bytes(b"\x07" * 32) \
        .public_key()
    pub.verify(base64.b64decode(h["X-PM-Signature"]),
               b"1700000000000POST/v1/orders")


def test_isolation_from_read_only_workers():
    from src import pmus_ws_evidence_recorder as rec
    from src import pmus_sports_collector as col
    for mod in (rec, col):
        src = inspect.getsource(mod)
        assert "pmus_q1_executor" not in src
    # and the executor imports neither worker
    src = inspect.getsource(ex)
    assert "pmus_ws_evidence_recorder" not in src
    assert "pmus_sports_collector" not in src


# ------- current live REST schema + first-probe hardening (5518048857/-178633) -------

def _current_schema_script(first_price="0.4500"):
    """Fixtures in the CURRENT documented/observed live shapes."""
    polls = {"n": 0}

    def first_order(_calls):
        polls["n"] += 1
        if polls["n"] >= 2:
            return {"order": {"state": "ORDER_STATE_FILLED",
                              "cumQuantity": "1", "avgPx": first_price}}
        return {"order": {"state": "ORDER_STATE_OPEN", "cumQuantity": "0"}}

    return {
        "GET /v1/account/balances": {           # LIST shape
            "balances": [{"currency": "USD",
                          "buyingPower": {"value": "39.50"}}]},
        "GET /v1/portfolio/positions": {"positions": {}},   # MAP shape
        "GET /v1/orders/open": {"orders": []},
        f"GET /v1/markets/{SLUG}/bbo": {"marketData": {
            "bestBid": {"value": "0.4500"},
            "bestAsk": {"value": "0.4550"}}},
        "POST /v1/order/preview": {},
        "POST /v1/orders": {"id": "C8N87XC00MVV"},          # top-level id
        "GET /v1/order/C8N87XC00MVV": first_order,
        "POST /v1/orders/open/cancel": {},
    }


def test_current_schema_shapes_parse_end_to_end(env):
    arm(env)
    script = _current_schema_script()

    def orders_post(calls):
        n = sum(1 for m, p, _ in calls if m == "POST" and p == "/v1/orders")
        return {"id": "C8N87XC00MVV"} if n == 1 else {"id": "ord-2"}
    comp_polls = {"n": 0}

    def comp_order(_calls):
        comp_polls["n"] += 1
        if comp_polls["n"] >= 2:
            return {"order": {"state": "ORDER_STATE_FILLED",
                              "cumQuantity": "1", "avgPx": "0.5400"}}
        return {"order": {"state": "ORDER_STATE_OPEN", "cumQuantity": "0"}}
    script["POST /v1/orders"] = orders_post
    script["GET /v1/order/ord-2"] = comp_order
    t = FakeTransport(script)
    clock, slp = _fast()
    rep = ex.run_campaign(t, clock=clock, sleep=slp)
    assert rep["status"] == "MATCHED" and rep["pair_cost"] == "0.9900"


def test_positions_map_keyed_by_slug_blocks_preflight(env):
    arm(env)
    script = _current_schema_script()
    script["GET /v1/portfolio/positions"] = {
        "positions": {SLUG: {"quantity": "2"}}}
    t = FakeTransport(script)
    clock, slp = _fast()
    rep = ex.run_campaign(t, clock=clock, sleep=slp)
    assert rep["status"] == "PREFLIGHT_FAILED"
    assert rep["reason"] == "existing_position_in_target_market"


def test_rejected_zero_fill_is_terminal_not_halt(env):
    """First live probe shape: ORDER_STATE_REJECTED, cumQuantity=0,
    avgPx=0.0000 -> REJECTED_NO_FILL terminal, exit-clean, no replay."""
    arm(env)
    script = _current_schema_script()
    script["GET /v1/order/C8N87XC00MVV"] = {"order": {
        "state": "ORDER_STATE_REJECTED", "cumQuantity": "0",
        "avgPx": "0.0000"}}
    t = FakeTransport(script)
    clock, slp = _fast()
    rep = ex.run_campaign(t, clock=clock, sleep=slp)
    assert rep["status"] == "REJECTED_NO_FILL" and rep["stage"] == "first_leg"
    posts = [p for m, p, _ in t.calls if m == "POST" and p == "/v1/orders"]
    assert len(posts) == 1                     # mutation NEVER replayed


def test_fresh_ask_check_refuses_would_cross_before_post(env):
    """Post-only race guard: BBO moves so the join meets the ask between
    preview and POST -> local refusal, zero mutations."""
    arm(env)
    script = _current_schema_script()
    bbo_calls = {"n": 0}

    def moving_bbo(_calls):
        bbo_calls["n"] += 1
        if bbo_calls["n"] <= 2:                # preflight + entry reads
            return {"marketData": {"bestBid": {"value": "0.4750"},
                                   "bestAsk": {"value": "0.4800"}}}
        return {"marketData": {"bestBid": {"value": "0.4700"},
                               "bestAsk": {"value": "0.4750"}}}  # ask==join
    script[f"GET /v1/markets/{SLUG}/bbo"] = moving_bbo
    t = FakeTransport(script)
    clock, slp = _fast()
    rep = ex.run_campaign(t, clock=clock, sleep=slp)
    assert rep["status"] == "STALE_PRICE_REFUSED"
    assert not any(p == "/v1/orders" and m == "POST" for m, p, _ in t.calls)


def test_transient_404_on_status_retried_get_only(env):
    """Eventual consistency after an accepted id: bounded GET retries,
    never a POST replay."""
    arm(env)
    script = _current_schema_script()
    polls = {"n": 0}

    class E404(Exception):
        code = 404

    def flaky_status(_calls):
        polls["n"] += 1
        if polls["n"] <= 2:
            raise E404("transient")
        return {"order": {"state": "ORDER_STATE_FILLED",
                          "cumQuantity": "1", "avgPx": "0.4500"}}
    script["GET /v1/order/C8N87XC00MVV"] = flaky_status
    script["POST /v1/orders"] = {"id": "C8N87XC00MVV"}
    t = FakeTransport(script)
    clock, slp = _fast()
    rep = ex.run_campaign(t, clock=clock, sleep=slp)
    # first leg filled after retries; complement path proceeds (hold ok)
    assert rep["status"] in ("MATCHED", "ONE_SIDED_HOLD")
    posts = [p for m, p, _ in t.calls if m == "POST" and p == "/v1/orders"]
    assert len(posts) <= 2                     # one per leg, no replays
    assert polls["n"] >= 3                     # the GET actually retried


def test_cancel_bodies_use_current_schema_keys(env):
    arm(env)
    script = _current_schema_script()
    script["GET /v1/order/C8N87XC00MVV"] = {"order": {
        "state": "ORDER_STATE_OPEN", "cumQuantity": "0"}}
    script["POST /v1/order/C8N87XC00MVV/cancel"] = {}
    t = FakeTransport(script)
    clock, slp = _fast()
    env.setenv(ex.FIRST_WAIT_ENV, "20")
    rep = ex.run_campaign(t, clock=clock, sleep=slp)
    assert rep["status"] == "NO_FILL"
    exact = [b for m, p, b in t.calls
             if p == "/v1/order/C8N87XC00MVV/cancel"]
    assert exact and exact[0] == {"marketSlug": SLUG}
    deadman = [b for m, p, b in t.calls if p == "/v1/orders/open/cancel"]
    assert deadman and deadman[-1] == {"slugs": [SLUG]}
