from decimal import Decimal as D
import inspect

import pytest

from src import pmus_micro_executor as m
from src import pmus_q1_executor_v4 as q1


class MetaFake:
    def __init__(self, minq="0.01", tick="0.005"):
        self.minq = minq
        self.tick = tick

    def request(self, method, path, body=None, base=q1.API):
        assert method == "GET"
        assert path.startswith("/v1/market/slug/")
        assert base == q1.GATEWAY
        return {"market": {"minimumTradeQty": self.minq,
                           "orderPriceMinTickSize": self.tick}}


def test_micro_quantity_targets_penny_and_caps_expensive_complement():
    assert m.choose_micro_quantity(D("0.575"), D("0.01")) == D("0.01")
    # A naive 1c entry target at 29c would choose q=.03, but the 71c
    # complement would cost 2.13c. MICRO caps q=.02 so either leg <=2c.
    q = m.choose_micro_quantity(D("0.29"), D("0.01"))
    assert q == D("0.02")
    assert q * D("0.29") <= D("0.02")
    assert q * D("0.71") <= D("0.02")


def test_very_cheap_entry_cannot_imply_large_complement():
    q = m.choose_micro_quantity(D("0.01"), D("0.01"))
    assert q == D("0.02")
    assert q * D("0.99") <= D("0.02")


def test_micro_requires_fractional_venue_support():
    with pytest.raises(m.Halt, match="MICRO_FRACTIONAL_QTY_UNSUPPORTED"):
        m.choose_micro_quantity(D("0.50"), D("1"))


def test_market_spec_uses_live_min_qty_and_tick(monkeypatch):
    monkeypatch.delenv(m.TARGET_NOTIONAL_ENV, raising=False)
    monkeypatch.delenv(m.MAX_LEG_NOTIONAL_ENV, raising=False)
    spec = m.market_micro_spec(MetaFake(), "m", D("0.575"))
    assert spec["minimum_trade_qty"] == D("0.01")
    assert spec["tick"] == D("0.005")
    assert spec["qty"] == D("0.01")
    assert spec["entry_notional"] == D("0.00575")


def test_the_venue_tick_is_authoritative_not_a_constant(monkeypatch):
    """PMUS sports serve orderPriceMinTickSize=0.01 while q1.TICK is
    0.005. A hardcoded equality check refused EVERY sports market with
    MICRO_TICK_UNSUPPORTED:0.01 -- the live path could not have placed a
    sports order at all."""
    monkeypatch.delenv(m.TARGET_NOTIONAL_ENV, raising=False)
    monkeypatch.delenv(m.MAX_LEG_NOTIONAL_ENV, raising=False)
    spec = m.market_micro_spec(MetaFake(tick="0.01"), "m", D("0.50"))
    assert spec["tick"] == D("0.01")


def test_a_price_off_the_venue_grid_fails_closed(monkeypatch):
    """0.4950 is legal on a 0.005 grid and ILLEGAL on a 0.01 grid. This
    is where the suspicious $0.00495 notional came from -- an off-grid
    price, not a sizing error."""
    monkeypatch.delenv(m.TARGET_NOTIONAL_ENV, raising=False)
    monkeypatch.delenv(m.MAX_LEG_NOTIONAL_ENV, raising=False)
    with pytest.raises(m.Halt, match="MICRO_PRICE_OFF_TICK"):
        m.market_micro_spec(MetaFake(tick="0.01"), "m", D("0.495"))
    # the same price is fine on the grid that admits it
    assert m.market_micro_spec(MetaFake(tick="0.005"), "m",
                               D("0.495"))["tick"] == D("0.005")


def test_an_implausible_tick_still_fails_closed(monkeypatch):
    monkeypatch.delenv(m.TARGET_NOTIONAL_ENV, raising=False)
    monkeypatch.delenv(m.MAX_LEG_NOTIONAL_ENV, raising=False)
    for bad in ("0", "-0.01", "1", "2"):
        with pytest.raises(m.Halt, match="MICRO_TICK_IMPLAUSIBLE"):
            m.market_micro_spec(MetaFake(tick=bad), "m", D("0.50"))


def test_body_emits_decimal_quantity_without_touching_q1():
    before = q1.QTY
    body = m._body("m", "OUTCOME_SIDE_YES", D("0.575"), D("0.01"), True)
    assert body["quantity"] == 0.01
    assert body["participateDontInitiate"] is True
    assert q1.QTY == before == 1
    # Dedicated module must never mutate the shared q1 quantity global.
    assert "q1.QTY =" not in inspect.getsource(m)


def test_authoritative_fill_threshold_is_requested_decimal_qty():
    partial = {"state": "ORDER_STATE_OPEN", "cumQuantity": "0.0100",
               "avgPx": {"value": "0.5750"}}
    full = {"state": "ORDER_STATE_FILLED", "cumQuantity": "0.0200",
            "avgPx": {"value": "0.5750"}}
    assert m.authoritative_fill(partial, D("0.02")) is None
    p = m.authoritative_fill(partial, D("0.02"), accept_partial=True)
    assert p["qty"] == D("0.0100")
    assert p["partial"] is True
    f = m.authoritative_fill(full, D("0.02"))
    assert f["qty"] == D("0.0200")
    assert f["partial"] is False


def test_partially_filled_state_string_never_promotes_partial_qty():
    # Regression: substring "FILLED" appears in PARTIALLY_FILLED. Quantity,
    # not the state string, decides whether the requested MICRO order is full.
    row = {"state": "ORDER_STATE_PARTIALLY_FILLED",
           "cumQuantity": "0.0100", "avgPx": {"value": "0.5750"}}
    assert m.authoritative_fill(row, D("0.02")) is None
    p = m.authoritative_fill(row, D("0.02"), accept_partial=True)
    assert p["partial"] is True
    assert p["qty"] == D("0.0100")


def test_fill_above_requested_qty_fails_loudly():
    row = {"state": "ORDER_STATE_FILLED", "cumQuantity": "0.0300",
           "avgPx": {"value": "0.5750"}}
    with pytest.raises(m.Halt, match="MICRO_FILL_QTY_EXCEEDS_REQUESTED"):
        m.authoritative_fill(row, D("0.02"))


def test_account_gate_is_self_contained_and_preserves_decimal_position(monkeypatch):
    calls = []

    def fake_get(t, path, *args, **kwargs):
        calls.append(path)
        if path == "/v1/orders/open":
            return {"orders": []}
        if path == "/v1/portfolio/positions":
            return {"positions": {"m": {"netPositionDecimal": "0.0100"}}}
        raise AssertionError(path)

    monkeypatch.setattr(q1.base, "_get_with_retry", fake_get)
    reason = m.account_block_reason(object())
    assert reason == "nonzero_positions:m=0.0100"
    assert calls == ["/v1/orders/open", "/v1/portfolio/positions"]
    assert "pmus_aws_supervisor" not in inspect.getsource(m)


def test_account_gate_all_flat_passes(monkeypatch):
    def fake_get(t, path, *args, **kwargs):
        if path == "/v1/orders/open":
            return {"orders": []}
        if path == "/v1/portfolio/positions":
            return {"positions": [{"marketSlug": "m", "netPositionDecimal": "0.0000"}]}
        raise AssertionError(path)

    monkeypatch.setattr(q1.base, "_get_with_retry", fake_get)
    assert m.account_block_reason(object()) is None


def test_residual_report_preserves_point_zero_one_not_zero_or_one(monkeypatch):
    monkeypatch.setattr(q1, "_position_for_slug", lambda *a, **k: D("0.0100"))
    report = m._residual_report(
        {"campaign": "x"}, "COMPLEMENT_UNFILLED_RESIDUAL_HOLD",
        D("0.42"), D("0.01"), "deadline", object(), "m")
    assert report["residual"] == "0.0100"
    assert report["residual_cap_qty"] == "0.01"
    assert report["residual_cap_breached"] is False
    assert report["status"] == "COMPLEMENT_UNFILLED_RESIDUAL_HOLD"


def test_residual_above_actual_first_fill_fails_loudly(monkeypatch):
    monkeypatch.setattr(q1, "_position_for_slug", lambda *a, **k: D("0.0200"))
    report = m._residual_report(
        {}, "HALT_WITH_RESIDUAL_HOLD", D("0.42"), D("0.01"),
        "unexpected", object(), "m")
    assert report["residual"] == "0.0200"
    assert report["residual_cap_breached"] is True
    assert report["status"] == "MICRO_RESIDUAL_CAP_BREACH"


def test_arming_is_fully_isolated_in_both_directions(monkeypatch):
    """F1 (issue #167 comment 5520359624): q1 arming never arms micro,
    micro arming never arms q1/v4."""
    for env in (m.MICRO_LIVE_ENV, m.MICRO_SID_ENV, m.MICRO_ARM_ENV,
                q1.LIVE_ENV, q1.SID_ENV, q1.ARM_ENV):
        monkeypatch.delenv(env, raising=False)
    # full q1 dual-key alone must NOT arm micro
    monkeypatch.setenv(q1.LIVE_ENV, "YES")
    monkeypatch.setenv(q1.SID_ENV, "qsid")
    monkeypatch.setenv(q1.ARM_ENV, "qsid")
    assert m.micro_armed() is False
    # micro dual-key alone must NOT arm q1
    for env in (q1.LIVE_ENV, q1.SID_ENV, q1.ARM_ENV):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv(m.MICRO_LIVE_ENV, "YES")
    monkeypatch.setenv(m.MICRO_SID_ENV, "msid")
    monkeypatch.setenv(m.MICRO_ARM_ENV, "msid")
    assert m.micro_armed() is True
    assert q1.armed() is False
    # mismatched micro session/arm fails closed
    monkeypatch.setenv(m.MICRO_ARM_ENV, "other")
    assert m.micro_armed() is False


def _arm_micro(monkeypatch, tmp_path, slug="aec-mlb-nyy-laa-2026-09-02"):
    monkeypatch.setenv(m.MICRO_LIVE_ENV, "YES")
    monkeypatch.setenv(m.MICRO_SID_ENV, "msid")
    monkeypatch.setenv(m.MICRO_ARM_ENV, "msid")
    monkeypatch.setenv(m.MICRO_MARKET_ENV, slug)
    monkeypatch.setenv(m.MICRO_GATE_ENV, slug)
    monkeypatch.setenv(q1.base.KEY_ENV, "k")
    monkeypatch.setenv(q1.base.SECRET_ENV, "s")
    monkeypatch.setenv(q1.base.STATE_ENV, str(tmp_path))
    return slug


class CampaignFake:
    """GET-only through preflight; scripted metadata; counts mutations."""

    def __init__(self, meta=("0.01", "0.005"), bbo=("0.4800", "0.4850")):
        self.meta = meta
        self.bbo = bbo
        self.posts = 0

    def request(self, method, path, body=None, base=None):
        if method == "POST":
            self.posts += 1
            raise AssertionError("mutation attempted in a SKIP scenario")
        if path == "/v1/orders/open":
            return {"orders": []}
        if path == "/v1/portfolio/positions":
            return {"positions": {}}
        if path == "/v1/account/balances":
            return {"balances": [{"currency": "USD",
                                  "buyingPower": {"value": "100.57"}}]}
        if path.endswith("/bbo"):
            return {"marketData": {"bestBid": {"value": self.bbo[0]},
                                   "bestAsk": {"value": self.bbo[1]}}}
        if path.startswith("/v1/market/slug/"):
            if self.meta is None:
                return {"market": {}}
            return {"market": {"minimumTradeQty": self.meta[0],
                               "orderPriceMinTickSize": self.meta[1]}}
        raise AssertionError((method, path))


def test_f2_pre_mutation_failures_skip_with_exit_zero(monkeypatch, tmp_path):
    _arm_micro(monkeypatch, tmp_path)
    # implausible tick -> SKIP (not HALT), zero mutations
    fake = CampaignFake(meta=("0.01", "0"))
    rep = m.run_campaign(fake, clock=lambda: 0.0, sleep=lambda s: None)
    assert rep["status"] == "MICRO_SKIP_SAFE_REFUSAL"
    assert "MICRO_TICK_IMPLAUSIBLE" in rep["reason"]
    assert fake.posts == 0
    # whole-contract minimum -> SKIP too
    fake2 = CampaignFake(meta=("1", "0.005"))
    rep2 = m.run_campaign(fake2, clock=lambda: 0.0, sleep=lambda s: None)
    assert rep2["status"] == "MICRO_SKIP_SAFE_REFUSAL"
    assert "MICRO_FRACTIONAL_QTY_UNSUPPORTED" in rep2["reason"]
    # metadata missing -> SKIP
    fake3 = CampaignFake(meta=None)
    rep3 = m.run_campaign(fake3, clock=lambda: 0.0, sleep=lambda s: None)
    assert rep3["status"] == "MICRO_SKIP_SAFE_REFUSAL"


def test_g1_cumulative_first_leg_cap_is_durable(monkeypatch, tmp_path):
    import json as _json
    slug = _arm_micro(monkeypatch, tmp_path)
    ledger = tmp_path / "ledger.jsonl"
    rows = [{"kind": "submitted", "stage": "micro_first_leg", "micro": True,
             "slug": slug, "price": "0.48", "qty": "0.02"}] * 5   # 5 x 0.0096
    ledger.write_text("\n".join(_json.dumps(r) for r in rows) + "\n")
    assert m.cumulative_first_leg_notional(slug) == D("0.0480")
    fake = CampaignFake()                       # next 0.0096 would breach
    rep = m.run_campaign(fake, clock=lambda: 0.0, sleep=lambda s: None)
    assert rep["status"] == "MICRO_SKIP_MARKET_CAP", rep
    assert fake.posts == 0
    # an unparseable ledger row counts as the full cap — fail closed
    ledger.write_text('{"kind": "submitted", "stage": "micro_first_leg", '
                      f'"micro": true, "slug": "{slug}", "price": "x", '
                      '"qty": "0.02"}\n')
    assert m.cumulative_first_leg_notional(slug) == m.PER_MARKET_FIRST_LEG_CAP


def test_g2_micro_eligible_is_get_only(monkeypatch, tmp_path):
    fake = CampaignFake(bbo=("0.6400", "0.6700"))
    row = m.micro_eligible(fake, "tsc-mlb-stl-lad-2026-09-02-8pt5")
    assert row["skip_reason"] is None
    assert D(row["qty"]) == D("0.01")
    assert D(row["intended_notional"]) == D("0.0064")
    assert fake.posts == 0
    bad = m.micro_eligible(CampaignFake(meta=("1", "0.005")), "x")
    assert "MICRO_FRACTIONAL_QTY_UNSUPPORTED" in bad["skip_reason"]


def test_spring_discipline_is_encoded_structurally():
    """Issue #167 comment 5520416913: no directional winner-prediction
    input, account-wide one-market gate, flat constants, >=1c pre-rebate
    edge, rebate-era lockout documented."""
    src = inspect.getsource(m)
    assert "settlement_outcome" not in src        # no winner prediction
    assert "account_block_reason" in src          # one market at a time
    assert m.DEFAULT_TARGET_NOTIONAL == D("0.01")
    assert m.DEFAULT_MAX_LEG_NOTIONAL == D("0.02")
    assert m.PER_MARKET_FIRST_LEG_CAP == D("0.05")
    assert q1.MIN_EDGE >= D("0.01")               # pre-rebate locked edge
    assert "SPRING DISCIPLINE" in src and "LOCKED OUT" in src


def test_campaign_reads_isolated_market_and_gate_envs(monkeypatch, tmp_path):
    _arm_micro(monkeypatch, tmp_path, slug="aec-x")
    monkeypatch.setenv(m.MICRO_GATE_ENV, "aec-DIFFERENT")
    rep = m.run_campaign(object())
    assert rep["status"] == "GATE_NOT_CLEARED"
    # q1 market/gate envs must be irrelevant to micro
    monkeypatch.setenv(q1.MARKET_ENV, "aec-x")
    monkeypatch.setenv(q1.GATE_ENV, "aec-x")
    rep2 = m.run_campaign(object())
    assert rep2["status"] == "GATE_NOT_CLEARED"


def test_no_side_orders_use_the_venue_long_side_price_convention():
    """docs.polymarket.us: 'To trade the NO side at any price X, set
    price.value = 1.00 - X.' LIVE: 24/24 NO post-only orders priced in NO
    terms were rejected as crossing."""
    from decimal import Decimal as D
    from src import pmus_micro_executor as micro
    body = micro._body("m", "OUTCOME_SIDE_NO", D("0.39"), D("0.01"), True)
    assert body["price"]["value"] == "0.61" and body["outcomeSide"] == "OUTCOME_SIDE_NO"
    body_y = micro._body("m", "OUTCOME_SIDE_YES", D("0.39"), D("0.01"), True)
    assert body_y["price"]["value"] == "0.39"
    # the fill record comes back in long-side terms; we hand back NO terms
    fill = micro.authoritative_fill({"cumQuantity": "0.01", "avgPx": "0.61",
                                     "outcomeSide": "OUTCOME_SIDE_NO"}, D("0.01"))
    assert fill["price"] == D("0.39")
    fill_y = micro.authoritative_fill({"cumQuantity": "0.01", "avgPx": "0.61",
                                       "outcomeSide": "OUTCOME_SIDE_YES"}, D("0.01"))
    assert fill_y["price"] == D("0.61")
    assert micro.venue_price("OUTCOME_SIDE_NO", D("0.4")) == D("0.6")


def test_intent_only_short_records_are_normalized_like_no_side():
    # the venue Order carries both fields; a record with only intent set
    # still maps back to NO terms
    fill = m.authoritative_fill(
        {"state": "ORDER_STATE_FILLED", "cumQuantity": "0.0200",
         "avgPx": {"value": "0.61"}, "intent": "ORDER_INTENT_BUY_SHORT"},
        D("0.02"))
    assert fill["price"] == D("0.39")
    assert m.is_no_order({"intent": "ORDER_INTENT_SELL_SHORT"})
    assert not m.is_no_order({"intent": "ORDER_INTENT_BUY_LONG"})
