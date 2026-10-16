"""The continuous shadow policy evaluator. SHADOW ONLY: nothing submits."""
import os
from decimal import Decimal as D

import pytest

from src import l5_shadow_policy_eval as E
from src import l5_two_sided_probe as TS

TICK = D("0.01"); MIN_QTY = D("0.01")
LIM = dict(tick=TICK, min_qty=MIN_QTY, depth_ticks=1,
           cash_envelope=D("2.00"), max_worst_case_loss=D("2.00"))


@pytest.fixture(autouse=True)
def _authorized(monkeypatch):
    monkeypatch.setenv(TS.EXPERIMENT_ENV, "shadow-policy-eval-test")
    monkeypatch.setenv(TS.MARKET_ENV, "cpc-btc-updown-15m-*")


def snap(t, bid, ask, bids=(), asks=()):
    return {"t": t, "bid": bid, "ask": ask,
            "bids": [[p, q] for p, q in bids],
            "asks": [[p, q] for p, q in asks]}


def trade(t, px, qty):
    return {"t": t, "px": px, "qty": qty, "trade_time": "x%s" % t}


SLUG = "cpc-btc-updown-15m-2026-10-16-2030z"
T0 = E.window_start_epoch(SLUG) + 1.0


def base_ladder():
    # touch 0.61/0.62 -> our UP bid 0.60, our DOWN buy rests as ask at 0.63
    lv = snap(T0, "0.6100", "0.6200",
              bids=(("0.6100", "100"), ("0.6000", "10"), ("0.5900", "50")),
              asks=(("0.6200", "100"), ("0.6300", "10"), ("0.6400", "50")))
    return [lv, dict(lv, t=T0 + 30.0), dict(lv, t=T0 + 60.0)]


# ------------------------------------------------------------------ plumbing

def test_window_start_epoch_reads_the_slug_start():
    assert E.window_start_epoch(SLUG) == 1792182600.0
    assert E.window_start_epoch("not-a-slug") is None


def test_rung_depth_returns_none_for_a_price_that_is_not_a_rung():
    s = base_ladder()[0]
    assert E.rung_depth(s, "bids", D("0.6000")) == D("10")
    assert E.rung_depth(s, "bids", D("0.5500")) is None


def test_queue_fill_never_fills_on_an_unknown_queue():
    out = E.queue_fill([trade(T0 + 1, "0.60", "9999")], t_join=T0,
                       px=D("0.6000"), side="bid", queue_ahead=None)
    assert out["filled"] is False
    assert out["reason"] == "QUEUE_AHEAD_UNREADABLE"
    assert out["modelled"] is True


def test_queue_fill_ignores_prints_before_the_join():
    trs = [trade(T0 - 5, "0.60", "9999"), trade(T0 + 5, "0.60", "5")]
    out = E.queue_fill(trs, t_join=T0, px=D("0.6000"), side="bid",
                       queue_ahead=D("10"))
    assert out["filled"] is False and out["prints_counted"] == 1
    out2 = E.queue_fill(trs + [trade(T0 + 6, "0.59", "6")], t_join=T0,
                        px=D("0.6000"), side="bid", queue_ahead=D("10"))
    assert out2["filled"] is True and out2["t"] == T0 + 6


def test_queue_fill_side_direction():
    # an ASK at 0.63 fills on prints AT OR ABOVE 0.63, not below it
    low = E.queue_fill([trade(T0 + 1, "0.62", "99")], t_join=T0,
                       px=D("0.6300"), side="ask", queue_ahead=D("10"))
    assert low["filled"] is False
    hi = E.queue_fill([trade(T0 + 1, "0.63", "99")], t_join=T0,
                      px=D("0.6300"), side="ask", queue_ahead=D("10"))
    assert hi["filled"] is True


# ---------------------------------------------------------------- settlement

def test_settlement_net_matched_pair_is_outcome_independent():
    for yv in (D(0), D(1), None):
        out = E.settlement_net(qty_up=D(1), qty_dn=D(1), cash_up=D("0.60"),
                               cash_dn=D("0.37"), yes_value=yv)
        assert out["settled"] is True
        assert out["net_usd"] == "0.03"


def test_settlement_net_leaves_an_unsettled_residual_unresolved():
    out = E.settlement_net(qty_up=D(1), qty_dn=D(0), cash_up=D("0.60"),
                           cash_dn=D(0), yes_value=None)
    assert out["settled"] is False and out["pending_residual"] is True
    assert out["net_usd"] is None
    assert out["reason"] == "RESIDUAL_UNSETTLED"


def test_settlement_net_prices_a_residual_only_from_a_yes_value():
    won = E.settlement_net(qty_up=D(1), qty_dn=D(0), cash_up=D("0.60"),
                           cash_dn=D(0), yes_value=D(1))
    lost = E.settlement_net(qty_up=D(1), qty_dn=D(0), cash_up=D("0.60"),
                            cash_dn=D(0), yes_value=D(0))
    assert won["net_usd"] == "0.40" and lost["net_usd"] == "-0.60"


# --------------------------------------------------------------- eligibility

def test_eligibility_and_size_come_from_the_existing_builder():
    rec = E.evaluate_window(SLUG, base_ladder(), [], **LIM)
    assert rec["eligibility"]["ok"] is True
    plan = TS.build_orders(market=SLUG, bid="0.6100", ask="0.6200",
                           bid_depth="100", ask_depth="100",
                           book_age_s=D("0.5"), **LIM)
    assert rec["quote"]["qty"] == plan["qty_per_leg"]
    assert rec["quote"]["up_px"] == plan["legs"][0]["price"]


def test_an_unauthorized_market_is_not_scored(monkeypatch):
    monkeypatch.setenv(TS.MARKET_ENV, "cpc-eth-updown-15m-*")
    rec = E.evaluate_window(SLUG, base_ladder(), [], **LIM)
    assert rec["ok"] is False and rec["reason"] == "NOT_ELIGIBLE"
    assert rec["detail"] == "NOT_THE_AUTHORIZED_MARKET"


def test_risk_limits_are_respected_not_widened():
    rec = E.evaluate_window(SLUG, base_ladder(), [],
                            **dict(LIM, max_worst_case_loss=D("0.10")))
    qty = D(rec["quote"]["qty"])
    dear = max(D(rec["quote"]["up_px"]), D(rec["quote"]["dn_px"]))
    assert qty * dear + TS.worst_fee(qty, dear) <= D("0.10")


def test_no_ladder_evidence_is_a_refusal_not_an_empty_result():
    rec = E.evaluate_window(SLUG, [], [], **LIM)
    assert rec["ok"] is False and rec["reason"] == "NO_LADDER_EVIDENCE"


# ------------------------------------------------------------------ outcomes

def test_neither_leg_filling_is_reported_as_neither():
    rec = E.evaluate_window(SLUG, base_ladder(), [], **LIM)
    assert rec["outcome"] == "NEITHER"
    assert rec["selected"] == "HOLD"
    assert rec["decisions"] == []


def test_both_legs_filling_reports_the_one_sided_interval():
    trs = [trade(T0 + 5, "0.63", "20"),      # clears our DOWN ask (queue 10)
           trade(T0 + 40, "0.60", "20")]     # clears our UP bid (queue 10)
    rec = E.evaluate_window(SLUG, base_ladder(), trs, **LIM)
    assert rec["outcome"] == "BOTH"
    assert rec["one_sided_s"] == pytest.approx(35.0, abs=0.01)
    assert rec["inventory_after_acquisition"]["held_side"] == "DOWN"
    assert rec["inventory_after_acquisition"]["deficient_side"] == "UP"


def test_one_sided_outcome_is_named_and_the_residual_left_unsettled():
    trs = [trade(T0 + 5, "0.63", "20")]      # only the DOWN leg clears
    rec = E.evaluate_window(SLUG, base_ladder(), trs, **LIM)
    assert rec["outcome"] == "DOWN_ONLY"
    assert rec["one_sided_s"] is None
    assert rec["net"]["HOLD"]["net_usd"] is None
    assert rec["net"]["HOLD"]["pending_residual"] is True


# ------------------------------------------------------------- the three bars

def test_the_guard_blocks_the_cross_the_raw_decision_proposes():
    trs = [trade(T0 + 5, "0.63", "20"), trade(T0 + 40, "0.60", "20")]
    rec = E.evaluate_window(SLUG, base_ladder(), trs, **LIM)
    rows = rec["decisions"]
    assert rows, "a one-sided interval must produce decision rows"
    assert any(r["cross_raw"]["ok"] for r in rows)
    assert not any(r["cross_guarded"]["ok"] for r in rows)
    assert rec["selected"] == "HOLD"
    assert rec["net"]["HOLD"]["net_usd"] == rec["net"]["CROSS"]["net_usd"]


def test_every_decision_row_records_the_evidence_it_was_made_on():
    trs = [trade(T0 + 5, "0.63", "20"), trade(T0 + 40, "0.60", "20")]
    rec = E.evaluate_window(SLUG, base_ladder(), trs, **LIM)
    r = rec["decisions"][0]
    for k in ("t", "t_rel_s", "phase", "bid", "ask", "reprice",
              "cross_raw", "cross_guarded"):
        assert k in r
    for k in ("filled", "queue_ahead", "cum_qualifying", "prints_counted",
              "modelled"):
        assert k in rec["resting"]["up"]


def test_the_fee_model_is_the_shared_one_for_every_policy():
    trs = [trade(T0 + 5, "0.63", "20"), trade(T0 + 40, "0.60", "20")]
    rec = E.evaluate_window(SLUG, base_ladder(), trs, **LIM)
    want = "maker=%s taker=%s" % (TS.MAKER_THETA, TS.TAKER_THETA)
    for p in E.POLICIES:
        assert rec["net"][p]["fee_model"] == want
    # and the module declares no fee table of its own
    src = open("src/l5_shadow_policy_eval.py").read()
    assert "0.0695" not in src and "0.0125" not in src


def test_nothing_in_a_record_claims_a_fill_or_a_submission():
    trs = [trade(T0 + 5, "0.63", "20"), trade(T0 + 40, "0.60", "20")]
    rec = E.evaluate_window(SLUG, base_ladder(), trs, **LIM)
    assert rec["shadow_only"] is True
    assert rec["submitted"] is False
    assert rec["fills_are_modelled"] is True


# ------------------------------------------------------------------ summarize

def test_summarize_excludes_unsettled_residuals_instead_of_scoring_them_zero():
    both = E.evaluate_window(SLUG, base_ladder(),
                             [trade(T0 + 5, "0.63", "20"),
                              trade(T0 + 40, "0.60", "20")], **LIM)
    one = E.evaluate_window(SLUG, base_ladder(),
                            [trade(T0 + 5, "0.63", "20")], **LIM)
    s = E.summarize([both, one])
    assert s["windows"] == 2 and s["eligible"] == 2
    assert s["settled"] == 1 and s["pending_residual"] == 1
    assert s["outcomes"] == {"BOTH": 1, "DOWN_ONLY": 1}
    assert D(s["net_usd"]["HOLD"]) == D(both["net"]["HOLD"]["net_usd"])
    assert all(v == "0" or D(v) == 0 for v in s["vs_blind_usd"].values())
    assert "none of these figures is realized profit" in s["labels"]


# ------------------------------- the incremental accumulator (service-ready)

def feed(acc, ladder, trades):
    """Deliver books and prints in the order the recorder would see them."""
    ev = [("b", r["t"], r) for r in ladder] + [("t", r["t"], r) for r in trades]
    ev.sort(key=lambda x: (x[1], 0 if x[0] == "b" else 1))
    for kind, _t, r in ev:
        if kind == "b":
            acc.on_book(r)
        else:
            acc.on_trade(r.get("t"), r.get("px"), r.get("qty"))
    return acc


IGNORE = {"incremental", "books_seen", "prints_seen",
          "out_of_order_dropped", "decisions"}


@pytest.mark.parametrize("trs", [
    [],                                                        # NEITHER
    [{"t": T0 + 5, "px": "0.63", "qty": "20", "trade_time": "a"}],  # one-sided
    [{"t": T0 + 5, "px": "0.63", "qty": "20", "trade_time": "a"},
     {"t": T0 + 40, "px": "0.60", "qty": "20", "trade_time": "b"}],  # BOTH
])
def test_incremental_matches_the_batch_evaluation(trs):
    want = E.evaluate_window(SLUG, base_ladder(), trs, **LIM)
    got = feed(E.WindowAccumulator(SLUG, **LIM), base_ladder(), trs).finish()
    for k in sorted(set(want) | set(got)):
        if k in IGNORE:
            continue
        assert got.get(k) == want.get(k), k


def test_the_accumulator_holds_no_per_snapshot_history():
    acc = feed(E.WindowAccumulator(SLUG, **LIM), base_ladder(),
               [{"t": T0 + 5, "px": "0.63", "qty": "20", "trade_time": "a"}])
    for name, v in vars(acc).items():
        assert not isinstance(v, list), (
            "%s is a list: the accumulator must stay O(1) in window length"
            % name)
    rec = acc.finish()
    assert "decisions" not in rec


def test_a_restart_mid_window_fails_closed_instead_of_scoring_a_partial():
    late = [dict(s, t=T0 + 400.0) for s in base_ladder()]
    acc = feed(E.WindowAccumulator(SLUG, **LIM), late,
               [{"t": T0 + 405, "px": "0.63", "qty": "20",
                 "trade_time": "a"}])
    rec = acc.finish()
    assert rec["ok"] is False
    assert rec["reason"] == "MISSED_WINDOW_OPEN"
    assert "net" not in rec and "outcome" not in rec


def test_out_of_order_events_are_dropped_and_counted_not_guessed_at():
    lad = base_ladder()
    acc = E.WindowAccumulator(SLUG, **LIM)
    acc.on_book(lad[0])
    acc.on_trade(T0 + 10, "0.63", "20")
    acc.on_trade(T0 + 5, "0.63", "9999")       # earlier than what we counted
    rec = acc.finish()
    assert rec["out_of_order_dropped"] == 1
    assert rec["resting"]["dn"]["cum_qualifying"] == "20"


def test_an_unreadable_queue_never_models_a_fill():
    thin = snap(T0, "0.6100", "0.6200",
                bids=(("0.6100", "100"),), asks=(("0.6200", "100"),))
    acc = feed(E.WindowAccumulator(SLUG, **LIM), [thin],
               [{"t": T0 + 5, "px": "0.60", "qty": "99999",
                 "trade_time": "a"}])
    rec = acc.finish()
    assert rec["outcome"] == "NEITHER"
    assert rec["resting"]["up"]["reason"] == "QUEUE_AHEAD_UNREADABLE"


# ----------------------------------------------- the log: rollover + restarts

def _log(tmp_path, **kw):
    return E.ShadowPolicyLog(path=str(tmp_path / "store.jsonl"),
                             **dict(LIM, **kw))


def test_rollover_finishes_a_window_when_the_recorder_drops_its_ladder(tmp_path):
    log = _log(tmp_path)
    lad = base_ladder()
    # interleaved, as the recorder sees them
    log.observe_book(SLUG, lad[0])
    log.observe_trade(SLUG, T0 + 5, "0.63", "20")
    log.observe_book(SLUG, lad[1])
    log.observe_trade(SLUG, T0 + 40, "0.60", "20")
    log.observe_book(SLUG, lad[2])
    assert log.rollover(keep=[SLUG]) == []          # still live: not scored
    out = log.rollover(keep=[])                     # ladder dropped: score it
    assert len(out) == 1 and out[0]["outcome"] == "BOTH"
    assert out[0]["ok"] is True


def test_the_store_makes_a_rescored_window_impossible(tmp_path):
    log = _log(tmp_path)
    for s in base_ladder():
        log.observe_book(SLUG, s)
    log.rollover(keep=[])
    # a fresh log, as after a redeploy, reads the store and skips the slug
    again = _log(tmp_path)
    assert SLUG in again.done
    for s in base_ladder():
        again.observe_book(SLUG, s)
    assert again.live == {}
    assert again.rollover(keep=[]) == []
    assert sum(1 for _ in open(tmp_path / "store.jsonl")) == 1


def test_an_unwritable_store_is_counted_not_crashed(tmp_path):
    log = E.ShadowPolicyLog(path=str(tmp_path / "nope") + "\0bad",
                            **LIM)
    for s in base_ladder():
        log.observe_book(SLUG, s)
    out = log.rollover(keep=[])
    assert len(out) == 1
    assert log.errors >= 1


def test_the_log_is_off_unless_explicitly_enabled(monkeypatch):
    monkeypatch.delenv(E.ENABLE_ENV, raising=False)
    assert E.enabled() is False
    monkeypatch.setenv(E.ENABLE_ENV, "yes")
    assert E.enabled() is True
    monkeypatch.setenv(E.ENABLE_ENV, "1")
    assert E.enabled() is False


def test_the_emitted_document_stays_small_and_claims_nothing(tmp_path):
    import json
    log = _log(tmp_path)
    lad = base_ladder()
    log.observe_book(SLUG, lad[0])
    log.observe_trade(SLUG, T0 + 5, "0.63", "20")
    log.observe_book(SLUG, lad[1])
    log.observe_trade(SLUG, T0 + 40, "0.60", "20")
    log.observe_book(SLUG, lad[2])
    doc = log.emit_document(log.rollover(keep=[]))
    blob = json.dumps(doc)
    assert len(blob) < 1200, "one window must not cost a large log line"
    assert "realized profit" in doc["labels"]
    assert doc["windows"][0]["net_usd"]["HOLD"] is not None


# ------------------------------------------------ the deployment safety check

def test_the_module_can_reach_no_network_crypto_or_subprocess_module():
    """The closure of what this module imports, proved rather than grepped."""
    import ast
    import os
    seen, stack, ext = set(), ["src/l5_shadow_policy_eval.py"], set()
    while stack:
        f = stack.pop()
        if f in seen or not os.path.exists(f):
            continue
        seen.add(f)
        for n in ast.walk(ast.parse(open(f).read())):
            if isinstance(n, ast.Import):
                for a in n.names:
                    ext.add(a.name.split(".")[0])
            elif isinstance(n, ast.ImportFrom):
                if n.level:
                    for a in n.names:
                        stack.append("src/%s.py" % a.name)
                elif n.module:
                    ext.add(n.module.split(".")[0])
    assert seen == {"src/l5_shadow_policy_eval.py",
                    "src/l5_two_sided_probe.py"}
    forbidden = {"socket", "ssl", "http", "urllib", "requests", "httpx",
                 "aiohttp", "websockets", "subprocess", "hmac", "hashlib",
                 "base64", "cryptography", "ftplib", "smtplib", "pickle"}
    assert not (ext & forbidden), sorted(ext & forbidden)


def test_the_module_reads_only_its_own_two_environment_variables():
    import ast
    names = set()
    for f in ("src/l5_shadow_policy_eval.py",):
        for n in ast.walk(ast.parse(open(f).read())):
            if (isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "getenv"):
                a = n.args[0]
                names.add(a.id if isinstance(a, ast.Name) else getattr(
                    a, "value", None))
    assert names == {"ENABLE_ENV", "STORE_ENV"}
    assert E.ENABLE_ENV == "L5_SHADOW_POLICY_EVAL"
    assert E.STORE_ENV == "L5_SHADOW_POLICY_STORE"


def test_no_environment_value_can_reach_the_record_the_log_or_the_store(
        tmp_path, monkeypatch):
    """Secrets in the environment must not appear in anything this emits."""
    import json
    canary = "CANARY-SECRET-b7f3e1"
    for var in ("PMUS_API_KEY_ID", "PMUS_API_SECRET_KEY", "SOME_TOKEN"):
        monkeypatch.setenv(var, canary)
    log = _log(tmp_path)
    lad = base_ladder()
    log.observe_book(SLUG, lad[0])
    log.observe_trade(SLUG, T0 + 5, "0.63", "20")
    log.observe_book(SLUG, lad[1])
    log.observe_trade(SLUG, T0 + 40, "0.60", "20")
    log.observe_book(SLUG, lad[2])
    scored = log.rollover(keep=[])
    assert scored and scored[0]["ok"] is True
    blobs = [json.dumps(scored[0], default=str),
             json.dumps(log.emit_document(scored), default=str),
             open(tmp_path / "store.jsonl").read()]
    for b in blobs:
        assert canary not in b
        for var in ("PMUS_API_KEY_ID", "PMUS_API_SECRET_KEY"):
            assert var not in b


def test_a_failing_accumulator_cannot_interrupt_the_recorder(tmp_path):
    """Every hook swallows and counts. The capture must never stop."""
    log = _log(tmp_path)
    log.observe_book(SLUG, base_ladder()[0])

    class Boom:
        def on_book(self, *a, **k):
            raise RuntimeError("boom")

        def on_trade(self, *a, **k):
            raise RuntimeError("boom")

        def finish(self, **k):
            raise RuntimeError("boom")

    log.live[SLUG] = Boom()
    log.observe_book(SLUG, base_ladder()[1])          # must not raise
    log.observe_trade(SLUG, T0 + 5, "0.63", "20")     # must not raise
    assert log.rollover(keep=[]) == []                # must not raise
    assert log.errors >= 3


def test_an_unknown_slug_trade_is_ignored_rather_than_opening_a_window(
        tmp_path):
    log = _log(tmp_path)
    log.observe_trade("cpc-btc-updown-15m-2026-10-16-9999z", T0, "0.5", "1")
    assert log.live == {}
    assert log.errors == 0


# ------------------------------- modelled execution vs venue-confirmed outcome

BOTH_TRADES = [{"t": T0 + 5, "px": "0.63", "qty": "20", "trade_time": "a"},
               {"t": T0 + 40, "px": "0.60", "qty": "20", "trade_time": "b"}]
ONE_TRADE = [{"t": T0 + 5, "px": "0.63", "qty": "20", "trade_time": "a"}]
GOOD_EV = {"resolution_source": "VENUE_SETTLEMENT_PX", "yes_value": "1",
           "observed_at": "2026-10-16T21:05:01Z"}


def test_a_matched_pair_is_outcome_independent_not_venue_confirmed():
    rec = E.evaluate_window(SLUG, base_ladder(), BOTH_TRADES, **LIM)
    assert E.settlement_basis(rec) == E.BASIS_MODELLED_MATCHED
    assert rec["fills_are_modelled"] is True
    assert "settlement_evidence" not in rec


def test_an_unmatched_residual_stays_pending_with_no_net():
    rec = E.evaluate_window(SLUG, base_ladder(), ONE_TRADE, **LIM)
    assert E.settlement_basis(rec) == E.BASIS_PENDING
    for p in E.POLICIES:
        assert rec["net"][p]["net_usd"] is None
        assert rec["net"][p]["pending_residual"] is True


def test_reconciliation_prices_a_pending_residual_from_positive_evidence():
    rec = E.evaluate_window(SLUG, base_ladder(), ONE_TRADE, **LIM)
    out = E.reconcile_settlement(rec, GOOD_EV)
    assert out["reconciled"] is True
    assert E.settlement_basis(out) == E.BASIS_VENUE_SETTLED
    assert out["net"]["HOLD"]["net_usd"] is not None
    assert out["net"]["HOLD"]["pending_residual"] is False
    # the fills are still modelled; settlement confirmed the OUTCOME only
    assert out["fills_are_modelled"] is True
    assert "remain MODELLED" in out["settlement_evidence"]["labels"]


def test_reconciliation_is_idempotent_and_cannot_double_count():
    rec = E.evaluate_window(SLUG, base_ladder(), ONE_TRADE, **LIM)
    once = E.reconcile_settlement(rec, GOOD_EV)
    twice = E.reconcile_settlement(once, GOOD_EV)
    assert twice["reconciled"] is False
    assert twice["reconcile_reason"] == "ALREADY_RECONCILED"
    assert twice["net"]["HOLD"]["net_usd"] == once["net"]["HOLD"]["net_usd"]
    # and the original record was not mutated
    assert rec["net"]["HOLD"]["net_usd"] is None


@pytest.mark.parametrize("ev,reason", [
    (None, "NO_POSITIVE_SETTLEMENT_EVIDENCE"),
    ({"resolution_source": "BOOK_PRICE_AT_EXPIRY", "yes_value": "1"},
     "NO_POSITIVE_SETTLEMENT_EVIDENCE"),
    ({"resolution_source": "LAST_TRADE", "yes_value": "1"},
     "NO_POSITIVE_SETTLEMENT_EVIDENCE"),
    ({"resolution_source": "VENUE_SETTLEMENT_PX", "yes_value": "0.99"},
     "YES_VALUE_NOT_BINARY"),
    ({"resolution_source": "VENUE_SETTLEMENT_PX", "yes_value": None},
     "YES_VALUE_NOT_BINARY"),
])
def test_weak_evidence_leaves_the_outcome_unresolved(ev, reason):
    rec = E.evaluate_window(SLUG, base_ladder(), ONE_TRADE, **LIM)
    out = E.reconcile_settlement(rec, ev)
    assert out["reconciled"] is False and out["reconcile_reason"] == reason
    assert out["net"]["HOLD"]["net_usd"] is None
    assert E.settlement_basis(out) == E.BASIS_PENDING


def test_reconciling_a_matched_window_changes_nothing():
    rec = E.evaluate_window(SLUG, base_ladder(), BOTH_TRADES, **LIM)
    out = E.reconcile_settlement(rec, GOOD_EV)
    assert out["reconciled"] is False
    assert out["reconcile_reason"] == "NOTHING_PENDING"
    assert out["net"]["HOLD"]["net_usd"] == rec["net"]["HOLD"]["net_usd"]


def test_a_reconciled_residual_prices_the_winning_side_correctly():
    rec = E.evaluate_window(SLUG, base_ladder(), ONE_TRADE, **LIM)
    assert rec["inventory_after_acquisition"]["held_side"] == "DOWN"
    won = E.reconcile_settlement(rec, dict(GOOD_EV, yes_value="0"))   # DOWN
    lost = E.reconcile_settlement(rec, dict(GOOD_EV, yes_value="1"))  # UP
    assert D(won["net"]["HOLD"]["net_usd"]) > D(lost["net"]["HOLD"]["net_usd"])
