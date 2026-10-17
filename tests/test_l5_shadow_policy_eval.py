"""The continuous shadow policy evaluator. SHADOW ONLY: nothing submits."""
import os
from decimal import Decimal as D

import json
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


#: `process` is reported only by the incremental path -- it describes quote
#: generations, which the one-shot batch evaluation does not model.
IGNORE = {"incremental", "books_seen", "prints_seen",
          "out_of_order_dropped", "decisions", "process"}


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
        if name == "generations":
            # bounded by max_generations, NOT by window length
            assert len(v) <= acc.max_generations
            continue
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


# ---------------------------------------------------------------- dashboard

def test_the_dashboard_keeps_the_four_kinds_of_number_apart():
    matched = E.evaluate_window(SLUG, base_ladder(), BOTH_TRADES, **LIM)
    pending = E.evaluate_window(SLUG, base_ladder(), ONE_TRADE, **LIM)
    settled = E.reconcile_settlement(pending, GOOD_EV)
    d = E.dashboard([matched, pending, settled],
                    live={"submitted": 0, "filled": 0,
                          "realized_usd": "-8.39480000"})
    assert d["modelled_matched_pair"]["windows"] == 1
    assert d["pending_residual"]["windows"] == 1
    assert d["venue_settled_modelled"]["windows"] == 1
    # a pending residual has NO net, only exposure
    assert d["pending_residual"]["net_usd"] is None
    assert D(d["pending_residual"]["exposure_usd"]) > 0
    assert D(d["pending_residual"]["residual_qty"]) > 0
    # and the modelled buckets never absorb it
    assert D(d["modelled_matched_pair"]["net_usd"]["HOLD"]) == D(
        matched["net"]["HOLD"]["net_usd"])
    assert d["actual_trading"]["realized_usd"] == "-8.39480000"
    assert d["actual_trading"]["submitted"] == 0
    assert "NOT summed" in d["labels"]


def test_the_dashboard_counts_unscored_windows_by_reason():
    bad = E.evaluate_window(SLUG, [], [], **LIM)
    d = E.dashboard([bad])
    assert d["not_scored"]["windows"] == 1
    assert d["not_scored"]["reasons"]["NO_LADDER_EVIDENCE"] == 1
    assert d["modelled_matched_pair"]["windows"] == 0


def test_the_dashboard_never_reports_a_combined_net():
    matched = E.evaluate_window(SLUG, base_ladder(), BOTH_TRADES, **LIM)
    d = E.dashboard([matched])
    assert "total_net_usd" not in d
    assert "net_usd" not in d
    assert d["actual_trading"]["realized_usd"] is None


# ------------------------------------------------- the image must be complete

def test_pmus_ws_recorder_image_covers_its_import_closure():
    """Every local module the recorder imports must be COPYed into its image.

    THE DEFECT THIS CATCHES, which it did not catch the first time. The
    recorder Dockerfile copies file by file. `src/l5_shadow_policy_eval.py`
    was not among them, so the first shadow-policy deploy came up healthy,
    logged nothing, and scored no window: the import raised, the broken latch
    set, and every hook went inert. Nothing was wrong at runtime -- the
    module simply was not there.
    """
    import ast
    import os
    df = open("deploy/pmus_ws_recorder/Dockerfile").read()
    seen, stack = set(), ["src/pmus_ws_evidence_recorder.py",
                          "src/pmus_ws_evidence_recorder_enriched.py"]
    while stack:
        f = stack.pop()
        if f in seen or not os.path.exists(f):
            continue
        seen.add(f)
        for n in ast.walk(ast.parse(open(f).read())):
            if isinstance(n, ast.ImportFrom) and n.level:
                for a in n.names:
                    stack.append("src/%s.py" % a.name)
            elif isinstance(n, ast.Import):
                for a in n.names:
                    if a.name.startswith("src."):
                        stack.append("src/%s.py" % a.name.split(".", 1)[1])
    missing = sorted(f for f in seen if f not in df)
    assert not missing, (
        "these modules are imported by the recorder but not COPYed into its "
        "image, so they would raise ImportError in production: %s" % missing)


# ------------------------------- venue settlement evidence (pure predicate)

def test_terminal_states_match_the_transport_exactly():
    from src import l5_pmus_transport as T
    assert E.TERMINAL_MARKET_STATES == T.TERMINAL_MARKET_STATES


def _bbo(state="MARKET_STATE_EXPIRED", px="1.0000", slug=SLUG):
    return {"marketData": {"state": state, "settlementPx": px, "slug": slug}}


def test_settlement_from_bbo_accepts_only_a_binary_terminal_settlement():
    ok = E.settlement_from_bbo(_bbo(), SLUG)
    assert ok["ok"] is True
    assert ok["resolution_source"] == "VENUE_SETTLEMENT_PX"
    assert D(ok["yes_value"]) == D(1)
    assert E.settlement_from_bbo(_bbo(px="0.0000"), SLUG)["ok"] is True


@pytest.mark.parametrize("doc,reason", [
    (_bbo(state="MARKET_STATE_OPEN"), "STATE_NOT_TERMINAL"),
    (_bbo(px=None), "SETTLEMENT_ABSENT"),
    (_bbo(px="0.0100"), "SETTLEMENT_NOT_BINARY"),   # the expired-book trap
    (_bbo(px="0.9900"), "SETTLEMENT_NOT_BINARY"),
    (_bbo(slug="some-other-market"), "RESOLUTION_MARKET_MISMATCH"),
    ({}, "STATE_NOT_TERMINAL"),
    (None, "STATE_NOT_TERMINAL"),
])
def test_settlement_from_bbo_refuses_everything_else(doc, reason):
    out = E.settlement_from_bbo(doc, SLUG)
    assert out["ok"] is False and out["reason"] == reason


def test_the_predicate_performs_no_request():
    import ast
    src = open("src/l5_shadow_policy_eval.py").read()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef)
              and n.name == "settlement_from_bbo")
    calls = {getattr(c.func, "id", getattr(c.func, "attr", ""))
             for c in ast.walk(fn) if isinstance(c, ast.Call)}
    assert not (calls & {"urlopen", "urlretrieve", "post", "request",
                         "http_get_json", "Request"})


# ----------------------------------- bounded, idempotent reconciliation loop

#: just inside the retry budget for this slug's window
NOW_OK = E.window_start_epoch(SLUG) + 900 + 60


def _pending_log(tmp_path):
    log = _log(tmp_path)
    lad = base_ladder()
    log.observe_book(SLUG, lad[0])
    log.observe_trade(SLUG, T0 + 5, "0.63", "20")     # DOWN only
    log.observe_book(SLUG, lad[1])
    log.rollover(keep=[])
    return log


def test_a_pending_window_is_reconciled_from_venue_evidence(tmp_path):
    log = _pending_log(tmp_path)
    assert log.pending_markets() == [SLUG]
    out = log.reconcile_pending(lambda path: _bbo(), now=NOW_OK)
    assert len(out) == 1 and out[0]["reconciled"] is True
    assert out[0]["settlement_evidence"]["resolution_source"] == \
        "VENUE_SETTLEMENT_PX"
    assert out[0]["net"]["HOLD"]["net_usd"] is not None
    assert log.pending_markets() == []


def test_reconciliation_appends_and_never_overwrites(tmp_path):
    import json
    log = _pending_log(tmp_path)
    before = open(tmp_path / "store.jsonl").read().splitlines()
    log.reconcile_pending(lambda path: _bbo(), now=NOW_OK)
    after = open(tmp_path / "store.jsonl").read().splitlines()
    assert len(after) == len(before) + 1
    assert after[0] == before[0], "the original record must be untouched"
    first = json.loads(after[0])
    assert first["net"]["HOLD"]["net_usd"] is None   # still the scoring record
    assert json.loads(after[-1])["supersedes"]


def test_reconciliation_does_not_double_count_across_a_restart(tmp_path):
    log = _pending_log(tmp_path)
    log.reconcile_pending(lambda path: _bbo(), now=NOW_OK)
    lines = len(open(tmp_path / "store.jsonl").read().splitlines())
    again = _log(tmp_path)                       # as after a redeploy
    assert SLUG in again.settled
    assert again.pending_markets() == []
    assert again.reconcile_pending(lambda path: _bbo(), now=NOW_OK) == []
    assert len(open(tmp_path / "store.jsonl").read().splitlines()) == lines


def test_an_unsettled_market_stays_pending_and_is_retried(tmp_path):
    log = _pending_log(tmp_path)
    for _ in range(3):
        assert log.reconcile_pending(
            lambda path: _bbo(state="MARKET_STATE_OPEN"), now=NOW_OK) == []
    assert log.pending_markets() == [SLUG]
    assert log.attempts[SLUG] == 3
    out = log.reconcile_pending(lambda path: _bbo(), now=NOW_OK)   # settles
    assert out[0]["reconciled"] is True


def test_the_retry_budget_is_bounded_and_closes_unresolved(tmp_path):
    log = _pending_log(tmp_path)
    for _ in range(E.RECONCILE_MAX_ATTEMPTS):
        log.reconcile_pending(lambda path: _bbo(state="MARKET_STATE_OPEN"),
                              now=NOW_OK)
    out = log.reconcile_pending(lambda path: _bbo(state="MARKET_STATE_OPEN"),
                                now=NOW_OK)
    assert len(out) == 1
    assert out[0]["reconcile_closed"] is True
    assert out[0]["reconcile_reason"] == "RETRY_BUDGET_EXHAUSTED"
    assert out[0]["net"]["HOLD"]["net_usd"] is None    # never priced
    assert log.pending_markets() == []


def test_a_lapsed_settlement_window_closes_unresolved(tmp_path):
    log = _pending_log(tmp_path)
    late = E.window_start_epoch(SLUG) + 900 + E.RECONCILE_MAX_AGE_S + 1
    out = log.reconcile_pending(lambda path: _bbo(state="MARKET_STATE_OPEN"),
                                now=late)
    assert out[0]["reconcile_closed"] is True
    assert out[0]["reconcile_reason"] == "SETTLEMENT_WINDOW_LAPSED"
    assert out[0]["net"]["HOLD"]["net_usd"] is None


def test_a_broken_settlement_reader_fails_open_for_capture(tmp_path):
    log = _pending_log(tmp_path)

    def boom(path):
        raise RuntimeError("reader down")

    assert log.reconcile_pending(boom, now=NOW_OK) == []   # must not raise
    assert log.reconcile_errors >= 1
    assert log.pending_markets() == [SLUG]       # still pending, not closed
    # and capture is unaffected
    log.observe_book("cpc-btc-updown-15m-2026-10-16-2215z", base_ladder()[0])
    assert log.errors == 0


def test_a_matched_window_is_never_reconciled(tmp_path):
    log = _log(tmp_path)
    lad = base_ladder()
    log.observe_book(SLUG, lad[0])
    log.observe_trade(SLUG, T0 + 5, "0.63", "20")
    log.observe_book(SLUG, lad[1])
    log.observe_trade(SLUG, T0 + 40, "0.60", "20")
    log.observe_book(SLUG, lad[2])
    log.rollover(keep=[])
    calls = []
    assert log.reconcile_pending(lambda p: calls.append(p), now=NOW_OK) == []
    assert calls == [], "a matched pair needs no settlement read"


# ------------------------------------------ the bounded read-only projection

def test_the_view_is_bounded_in_records_and_fields(tmp_path):
    log = _pending_log(tmp_path)
    v = log.records_view(limit=10_000)
    assert v["returned"] <= E.VIEW_MAX_RECORDS
    assert v["pending"] == 1
    row = v["records"][0]
    assert set(row) <= set(E.VIEW_FIELDS)
    # the fields the audit actually needs are present
    for k in ("quote", "inventory_after_acquisition", "resting", "net"):
        assert k in row
    assert "dashboard" in v
    assert "realized profit" in v["labels"]


def test_the_view_serves_no_field_outside_the_allow_list(tmp_path):
    log = _pending_log(tmp_path)
    log.latest[SLUG] = dict(log.latest[SLUG], secret_field="CANARY")
    blob = json.dumps(log.records_view())
    assert "CANARY" not in blob and "secret_field" not in blob


# ------------------------------ UNRESOLVED_EXPIRED is explicit and unpriced

def test_retry_exhaustion_yields_an_explicit_unresolved_expired_state(tmp_path):
    log = _pending_log(tmp_path)
    for _ in range(E.RECONCILE_MAX_ATTEMPTS):
        log.reconcile_pending(lambda path: _bbo(state="MARKET_STATE_OPEN"),
                              now=NOW_OK)
    out = log.reconcile_pending(lambda path: _bbo(state="MARKET_STATE_OPEN"),
                                now=NOW_OK)
    rec = out[0]
    assert E.settlement_basis(rec) == E.BASIS_UNRESOLVED_EXPIRED
    assert rec["settlement_basis"] == "UNRESOLVED_EXPIRED"
    assert rec["reconciled"] is False
    # no false settlement attribution, and nothing priced
    assert "settlement_evidence" not in rec
    for p in E.POLICIES:
        assert rec["net"][p]["net_usd"] is None
    assert rec["net"]["HOLD"]["pending_residual"] is True


def test_the_dashboard_counts_unresolved_expired_separately(tmp_path):
    log = _pending_log(tmp_path)
    lapsed = E.window_start_epoch(SLUG) + 900 + E.RECONCILE_MAX_AGE_S + 1
    out = log.reconcile_pending(lambda p: _bbo(state="MARKET_STATE_OPEN"),
                                now=lapsed)
    d = E.dashboard(out)
    assert d["unresolved_expired"]["windows"] == 1
    assert d["unresolved_expired"]["net_usd"] is None
    assert d["pending_residual"]["windows"] == 0
    assert d["modelled_matched_pair"]["windows"] == 0
    assert d["venue_settled_modelled"]["windows"] == 0


def test_a_pass_is_bounded_in_how_many_markets_it_reads(tmp_path):
    log = _log(tmp_path)
    seen = []
    # windows near NOW_OK, so none of them is already past the retry budget
    for hhmm in ("2030z", "2045z", "2100z", "2115z", "2130z", "2145z",
                 "2200z")[:E.RECONCILE_MAX_PER_PASS + 3]:
        slug = "cpc-btc-updown-15m-2026-10-16-%s" % hhmm
        log.latest[slug] = {"market": slug, "ok": True,
                            "net": {p: {"net_usd": None,
                                        "pending_residual": True}
                                    for p in E.POLICIES}}
    log.reconcile_pending(lambda path: seen.append(path) or
                          _bbo(state="MARKET_STATE_OPEN"), now=NOW_OK)
    assert len(seen) == E.RECONCILE_MAX_PER_PASS


def test_the_view_reports_the_settlement_basis_for_every_record(tmp_path):
    log = _pending_log(tmp_path)
    row = log.records_view()["records"][0]
    assert row["settlement_basis"] == E.BASIS_PENDING


# --------------------------- the recorder patch must not block the loop

def test_the_recorder_patch_runs_settlement_off_the_event_loop():
    """A synchronous GET inside `async def run` would stall the websocket.

    The collector's default timeout is 15s; four pending windows back to
    back is a minute of lost capture. The patch therefore schedules the
    reconciliation onto the SAME side worker, under the SAME busy flag,
    that schedule_status and schedule_shadow_tick already use.
    """
    import ast
    diff = open("deploy/shadow_policy/RECORDER_PATCH_2.diff").read()
    added = "\n".join(l[1:] for l in diff.splitlines()
                      if l.startswith("+") and not l.startswith("+++"))
    assert "run_in_executor(worker, _settle)" in added
    assert 'side_busy["settle"] = True' in added
    assert "SETTLEMENT_HTTP_TIMEOUT_S" in added
    # and the reconciliation must NOT be called straight from the loop body
    assert "_spl.reconcile_pending(" not in added
    assert "schedule_settlement(_spl)" in added


def test_the_settlement_timeout_is_far_below_the_collector_default():
    from src import pmus_sports_collector as col
    import inspect
    default = inspect.signature(col.http_get_json).parameters["timeout"].default
    assert E.SETTLEMENT_HTTP_TIMEOUT_S <= 5.0
    assert E.SETTLEMENT_HTTP_TIMEOUT_S < default


def test_the_recorder_patch_adds_no_write_verb_or_order_path():
    diff = open("deploy/shadow_policy/RECORDER_PATCH_2.diff").read()
    added = "\n".join(l[1:] for l in diff.splitlines()
                      if l.startswith("+") and not l.startswith("+++"))
    for bad in ("requests.post", "urlopen(", ".post(", "place_order",
                "create_order", "cancel_order", "do_POST", "do_PUT",
                "PMUS_API_SECRET", "PMUS_API_KEY"):
        assert bad not in added, bad


def test_the_token_is_never_logged_or_echoed():
    diff = open("deploy/shadow_policy/RECORDER_PATCH_2.diff").read()
    added = "\n".join(l[1:] for l in diff.splitlines()
                      if l.startswith("+") and not l.startswith("+++"))
    assert "compare_digest" in added
    # the token must never reach a print or a response body
    for line in added.splitlines():
        if "print(" in line or "wfile.write" in line:
            assert "token" not in line.lower()


# ------------------------------------------- replenishment (OFF by default)

def test_replenishment_is_off_by_default_and_changes_nothing():
    acc = feed(E.WindowAccumulator(SLUG, **LIM), base_ladder(), BOTH_TRADES)
    assert acc.replenish is False and acc.max_generations == 1
    rec = acc.finish()
    assert rec["process"]["replenish"] is False
    assert rec["process"]["generations"] == 1
    # identical to the batch evaluation on everything it also reports
    want = E.evaluate_window(SLUG, base_ladder(), BOTH_TRADES, **LIM)
    for k in sorted(set(want)):
        if k in IGNORE:
            continue
        assert rec.get(k) == want.get(k), k


def test_replenishment_opens_a_new_generation_after_both_legs_fill():
    lad = base_ladder() + [dict(base_ladder()[0], t=T0 + 90.0),
                           dict(base_ladder()[0], t=T0 + 120.0)]
    trs = BOTH_TRADES + [
        {"t": T0 + 95, "px": "0.63", "qty": "20", "trade_time": "c"},
        {"t": T0 + 125, "px": "0.60", "qty": "20", "trade_time": "d"}]
    one = feed(E.WindowAccumulator(SLUG, **LIM), lad, trs).finish()
    many = feed(E.WindowAccumulator(SLUG, replenish=True, max_generations=4,
                                    **LIM), lad, trs).finish()
    assert one["process"]["generations"] == 1
    assert many["process"]["generations"] > one["process"]["generations"]
    assert (many["process"]["modelled_legs_filled"]
            > one["process"]["modelled_legs_filled"])
    assert D(many["process"]["matched_qty"]) > D(one["process"]["matched_qty"])


def test_replenishment_is_bounded_and_reports_cumulative_cash():
    lad = base_ladder() + [dict(base_ladder()[0], t=T0 + 60.0 * k)
                           for k in range(2, 12)]
    trs = []
    for k in range(12):
        trs += [{"t": T0 + 60 * k + 5, "px": "0.63", "qty": "20",
                 "trade_time": "a%d" % k},
                {"t": T0 + 60 * k + 10, "px": "0.60", "qty": "20",
                 "trade_time": "b%d" % k}]
    rec = feed(E.WindowAccumulator(SLUG, replenish=True, max_generations=3,
                                   **LIM), lad, trs).finish()
    assert rec["process"]["generations"] <= 3
    # each generation redeploys the envelope: the cash number must grow
    assert D(rec["process"]["cash_deployed_usd"]) > D("0")
    assert "must be read beside the turnover" in rec["process"]["labels"]


def test_replenishment_never_quotes_past_the_cancel_cutoff():
    cut = TS.cancel_cutoff_from_start(E.window_start_epoch(SLUG))
    late = dict(base_ladder()[0], t=cut + 5.0)
    acc = E.WindowAccumulator(SLUG, replenish=True, max_generations=5, **LIM)
    feed(acc, base_ladder(), BOTH_TRADES)
    before = len(acc.generations)
    acc.on_book(late)
    assert len(acc.generations) == before


# ---------------- regression: the two defects found during the low-capital run

def test_a_refused_replenishment_does_not_void_the_window():
    """DEFECT 1. After replenishment was refused, `on_book` fell back into
    the WINDOW-open path, re-applied the 30s grace check mid-window and
    reported MISSED_WINDOW_OPEN for a window that had quoted and filled.
    That corrupted the first low-capital run."""
    lad = base_ladder() + [dict(base_ladder()[0], t=T0 + 90.0),
                           dict(base_ladder()[0], t=T0 + 150.0)]
    trs = BOTH_TRADES
    # $3.00 admits the first generation (~$2.00) and refuses the second
    acc = E.WindowAccumulator(SLUG, replenish=True, max_generations=8,
                              capital_ceiling=D("3.00"), **LIM)
    rec = feed(acc, lad, trs).finish()
    assert acc.done_quoting is True
    assert rec["ok"] is True, rec.get("reason")
    assert rec.get("reason") != "MISSED_WINDOW_OPEN"
    assert rec["process"]["generations"] >= 1
    assert rec["process"]["stopped_because"] == "CAPITAL_CEILING"


def test_the_capital_ceiling_is_charged_and_never_released():
    acc = E.WindowAccumulator(SLUG, replenish=True, max_generations=8,
                              capital_ceiling=D("3.00"), **LIM)
    rec = feed(acc, base_ladder(), BOTH_TRADES).finish()
    c = D(rec["process"]["committed_capital_usd"])
    assert D("0") < c <= D("3.00")
    # a completed pair must NOT give capital back
    assert rec["process"]["generations"] >= 1
    assert c >= D(rec["process"]["matched_qty"]) * D("0.9")


def test_single_generation_net_field_is_not_the_window_pnl():
    """DEFECT 2. rec['net'] prices ONE generation. generations_pnl accounts
    for all of them; reading the record's own field under replenishment
    reports the last generation as if it were the window."""
    lad = base_ladder() + [dict(base_ladder()[0], t=T0 + 90.0),
                           dict(base_ladder()[0], t=T0 + 120.0)]
    trs = BOTH_TRADES + [
        {"t": T0 + 95, "px": "0.63", "qty": "20", "trade_time": "c"},
        {"t": T0 + 125, "px": "0.60", "qty": "20", "trade_time": "d"}]
    rec = feed(E.WindowAccumulator(SLUG, replenish=True, max_generations=4,
                                   **LIM), lad, trs).finish()
    assert rec["process"]["generations"] >= 2
    agg = E.generations_pnl(rec)
    assert agg["generations"] == rec["process"]["generations"]
    one = D(rec["net"]["HOLD"]["net_usd"])
    assert D(agg["net_usd"]) > one, (
        "multi-generation P&L must exceed the single-generation field")
    assert "invalid for a replenished run" in agg["labels"]


def test_generations_pnl_leaves_an_unmatched_leg_unpriced():
    rec = feed(E.WindowAccumulator(SLUG, replenish=True, max_generations=4,
                                   **LIM), base_ladder(), ONE_TRADE).finish()
    agg = E.generations_pnl(rec)
    assert agg["priced"] is False and agg["net_usd"] is None
    assert D(agg["unmatched_qty"]) > 0
    assert D(agg["worst_case_residual_loss_usd"]) > 0
    won = E.generations_pnl(rec, yes_value=D(0))     # DOWN won
    lost = E.generations_pnl(rec, yes_value=D(1))
    assert won["priced"] is True
    assert D(won["net_usd"]) > D(lost["net_usd"])


def test_the_worst_case_residual_cap_counts_cost_already_sunk():
    """A second clip bought cheaper must not reset the bound. 5@0.39 then
    5@0.25 costs $3.20, not 10 x 0.25 = $2.50, and a $2.50 cap must refuse
    the second clip."""
    sim = E.IndependentReplenishSim(
        SLUG, tick=D("0.01"), min_qty=D("0.01"), depth_ticks=1,
        cash_envelope=D(10), max_worst_case_loss=D(10), clip=D(5),
        capital_ceiling=D(10), max_unmatched_qty=D(10),
        max_residual_loss_usd=D("2.50"))
    sim.up_qty = D(5); sim.up_cost = D("1.95")        # 5 @ 0.39 already held
    assert sim._residual_cost() == D("1.95")
    before = dict(sim.refusals)
    sim._place("UP", snap(T0, "0.2600", "0.2700",
                          bids=(("0.2600", "100"), ("0.2500", "10")),
                          asks=(("0.2700", "100"), ("0.2800", "10"))))
    assert sim.resting["UP"] is None, "the second clip must be refused"
    assert sim.refusals.get("WORST_CASE_RESIDUAL_CAP", 0) > \
        before.get("WORST_CASE_RESIDUAL_CAP", 0)


def test_per_leg_capital_is_not_charged_the_whole_pair_obligation():
    """Charging each side qty*(1+up_px) double-counts: the DOWN leg owes a
    dollar a share, the UP leg owes its own price."""
    sim = E.IndependentReplenishSim(
        SLUG, tick=D("0.01"), min_qty=D("0.01"), depth_ticks=1,
        cash_envelope=D(50), max_worst_case_loss=D(50), clip=D(5),
        capital_ceiling=D(50), max_unmatched_qty=D(50),
        max_residual_loss_usd=D(50))
    sim._place("UP", base_ladder()[0])
    up_only = sim.committed
    assert up_only == D(5) * D("0.60"), up_only      # qty * up_px
    sim._place("DOWN", base_ladder()[0])
    # a resting DOWN leg is a BUY_SHORT and owes a DOLLAR a share, which is
    # the engine's own conservative cash basis -- not its limit price.
    assert sim.committed - up_only == D(5) * D(1)
    # and the pair together is qty*(1+up_px), charged ONCE, not twice
    assert sim.committed == D(5) * (D(1) + D("0.60"))


def test_a_resting_order_never_reuses_volume_from_before_it_joined():
    sim = E.IndependentReplenishSim(
        SLUG, tick=D("0.01"), min_qty=D("0.01"), depth_ticks=1,
        cash_envelope=D(50), max_worst_case_loss=D(50), clip=D(5),
        capital_ceiling=D(50), max_unmatched_qty=D(50),
        max_residual_loss_usd=D(50))
    sim.on_book(base_ladder()[0])
    sim.on_trade(T0, "0.60", "9999")        # same instant as the join
    assert sim.resting["UP"] is not None, "volume at the join must not count"
    sim.on_trade(T0 + 1, "0.60", "9999")
    assert sim.resting["UP"] is None        # strictly later volume fills it


# ------------- accounting across multiple same-side clips, pending, settlement

def _sim(**kw):
    base = dict(tick=D("0.01"), min_qty=D("0.01"), depth_ticks=1,
                cash_envelope=D(50), max_worst_case_loss=D(50), clip=D(5),
                capital_ceiling=D(50), max_unmatched_qty=D(50),
                max_residual_loss_usd=D(50))
    return E.IndependentReplenishSim(SLUG, **dict(base, **kw))


def test_multiple_same_side_clips_at_different_prices_sum_their_cost():
    s = _sim()
    s.up_qty = D(5); s.up_cost = D("1.95")          # 5 @ 0.39
    s.up_qty += D(5); s.up_cost += D("1.25")        # 5 @ 0.25
    assert s.up_qty == D(10)
    assert s._residual_cost() == D("3.20")          # NOT 10 x 0.25
    r = s.result(yes_value=D(0))                    # UP lost
    assert D(r["up_avg_px"]) == D("0.32")           # blended, not last
    assert D(r["worst_case_residual_loss_usd"]) == D("3.20")
    assert D(r["net_usd"]) == D("-3.20")            # whole cost lost
    won = s.result(yes_value=D(1))                  # UP won
    assert D(won["net_usd"]) == D(10) - D("3.20")


def test_partial_offset_reduces_the_residual_at_blended_cost():
    s = _sim()
    s.up_qty = D(10); s.up_cost = D("3.20")         # blended 0.32
    s.dn_qty = D(4);  s.dn_cost = D("2.40")         # 4 @ 0.60
    assert s._residual_cost() == D("1.92")          # 6 unmatched x 0.32
    r = s.result(yes_value=D(1))
    # 4 matched pairs pay $1 each; 6 residual UP pay $1 each; cost is both legs
    assert D(r["matched_qty"]) == D(4)
    assert D(r["residual_qty"]) == D(6)
    assert D(r["net_usd"]) == (D(4) + D(6)) - (D("3.20") + D("2.40"))


def test_a_pending_order_commits_capital_but_is_not_inventory():
    s = _sim()
    s.on_book(base_ladder()[0])
    assert s.resting["UP"] is not None and s.resting["DOWN"] is not None
    r = s.result(yes_value=D(1))
    assert D(r["max_committed_capital_usd"]) > 0      # capital is committed
    assert r["fills"] == 0                            # but nothing is owned
    assert D(r["up_qty"]) == 0 and D(r["dn_qty"]) == 0
    assert D(r["worst_case_residual_loss_usd"]) == 0
    assert D(r["net_usd"]) == 0


def test_an_unsettled_residual_is_never_priced_as_zero():
    s = _sim()
    s.up_qty = D(5); s.up_cost = D("1.95")
    r = s.result(yes_value=None)
    assert r["priced"] is False and r["net_usd"] is None
    assert D(r["worst_case_residual_loss_usd"]) == D("1.95")


def test_a_fully_matched_book_is_outcome_independent():
    s = _sim()
    s.up_qty = D(5); s.up_cost = D("3.00")
    s.dn_qty = D(5); s.dn_cost = D("1.85")
    a = s.result(yes_value=D(0)); b = s.result(yes_value=D(1))
    c = s.result(yes_value=None)
    assert a["net_usd"] == b["net_usd"] == c["net_usd"]
    assert c["priced"] is True                       # needs no settlement
    assert D(a["net_usd"]) == D(5) - D("4.85")


def test_policy_c_skips_the_long_side_and_keeps_quoting_the_deficient_one():
    s = _sim(inventory_aware=True)
    s.up_qty = D(5)                      # long UP by one clip
    s._place("UP", base_ladder()[0])
    assert s.resting["UP"] is None
    assert s.refusals.get("INVENTORY_AWARE_SKIP_LONG_SIDE") == 1
    s._place("DOWN", base_ladder()[0])
    assert s.resting["DOWN"] is not None, "the deficient side must still quote"


def test_policy_c_is_off_by_default():
    s = _sim()
    assert s.inventory_aware is False
    s.up_qty = D(5)          # the exact state policy C would refuse
    s._place("UP", base_ladder()[0])
    assert s.resting["UP"] is not None
    assert "INVENTORY_AWARE_SKIP_LONG_SIDE" not in s.refusals
