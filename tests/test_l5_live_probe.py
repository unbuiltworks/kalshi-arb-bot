"""The live-probe harness must fail closed and must not be able to trade."""
import json
import os
import subprocess
import sys

import pytest

from src.l5_live_probe import accounting as A
from src.l5_live_probe import envelope as E
from src.l5_live_probe import incentive_join as J
from src.l5_live_probe import preflight as P
from src.l5_live_probe import reconcile as R

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "src", "l5_live_probe")


# ---------------------------------------------------------------- closure

def test_the_package_contains_no_order_verb_anywhere():
    """The one property that makes everything else safe to prepare."""
    for fn in sorted(os.listdir(PKG)):
        if not fn.endswith(".py"):
            continue
        src = open(os.path.join(PKG, fn)).read()
        for verb in P.ORDER_VERBS:
            assert f"def {verb}" not in src, f"{fn} defines {verb}"
            assert f".{verb}(" not in src, f"{fn} calls {verb}"


def test_importing_the_package_pulls_in_no_pmus_module():
    """Asserted in a SUBPROCESS: an import already done by the test session
    would make this pass for the wrong reason."""
    code = (
        "import sys;"
        "import src.l5_live_probe.preflight, src.l5_live_probe.reconcile,"
        "src.l5_live_probe.accounting, src.l5_live_probe.incentive_join;"
        "bad=[m for m in sys.modules if 'pmus' in m.lower()];"
        "print(json.dumps(bad))" .replace("json.dumps", "__import__('json').dumps")
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, cwd=os.path.dirname(PKG) + "/..")
    assert out.returncode == 0, out.stderr[-600:]
    assert json.loads(out.stdout.strip()) == []


def test_pmus_separation_check_reports_the_state_root_it_avoids():
    g = P.pmus_separation()
    assert g["state"] == "PASS"
    assert g["detail"]["state_root"] not in E.PMUS_STATE_ROOTS


# ---------------------------------------------------------------- fail closed

def test_an_unconfigured_preflight_blocks_and_never_passes():
    v = P.verdict()
    assert v["ok"] is False and v["verdict"] == "BLOCKED"
    assert v["n_blockers"] > 0


def test_undetermined_is_a_blocker_not_a_pass():
    """'we could not check' must never read the same as 'it is fine'."""
    g = P.gate("X", None, "could not establish")
    assert g["state"] == "UNDETERMINED"
    v = P.verdict()
    states = {c["state"] for c in v["checks"]}
    assert "UNDETERMINED" in states
    assert all(c["state"] != "PASS"
               for c in v["checks"] if c["blocker"] and c["state"] != "PASS")


def test_the_envelope_refuses_to_default():
    for k in E.ENV.values():
        os.environ.pop(k, None)
    with pytest.raises(E.EnvelopeNotChosen) as e:
        E.shell()
    for name in E.ENV.values():
        assert name in str(e.value)


def test_a_chosen_envelope_produces_a_shell_with_every_bound_set(monkeypatch):
    for key, name in E.ENV.items():
        monkeypatch.setenv(name, {"daily_loss_stop": "-5.00",
                                  "max_inventory_shares": "10",
                                  "max_market_spend": "5.00",
                                  "max_concurrent_markets": "1",
                                  "deadman_s": "300"}[key])
    sh = E.shell()
    assert sh.daily_loss_stop == "-5.00" and sh.max_concurrent_markets == 1


def test_order_size_below_the_venue_minimum_is_refused():
    g = P.order_size_constraints({"qty": 1}, {"qty": 5})
    assert g["state"] == "FAIL" and "below venue minimum" in g["blocker"]
    assert P.order_size_constraints({"qty": 5}, {"qty": 5})["state"] == "PASS"


def test_credentials_are_checked_for_presence_and_never_read(monkeypatch):
    for i, name in enumerate(P.CREDENTIAL_ENV):
        monkeypatch.setenv(name, f"super-secret-value-{i}")
    g = P.credentials_present()
    assert g["state"] == "PASS"
    blob = json.dumps(g)
    assert "super-secret-value" not in blob
    # the NAMES are reported (they must be, to say what is missing); the
    # VALUES never are
    for name in P.CREDENTIAL_ENV:
        assert name in blob


# ---------------------------------------------------------------- accounting

def test_nothing_is_realized_before_settlement():
    c = A.chain(market_key="m", venue="PMUS",
                fills=[{"side": "Up", "qty": "10", "cost": "4.80"}],
                resolution=None)
    assert c["realized_net"] is None and c["matched_pnl"] is None
    assert c["stage_reached"] == "fill"


def test_realized_net_is_trading_less_fees_plus_only_credited_incentives():
    c = A.chain(
        market_key="m", venue="PMUS",
        fills=[{"side": "Up", "qty": "10", "cost": "4.80"},
               {"side": "Down", "qty": "10", "cost": "4.90"}],
        rebates=[{"rebate_credited_usd": "0.02", "fee_charged_usd": "0.01"}],
        rewards=[{"credited": True, "credited_usd": "0.40", "amount_usd": "0.40"},
                 {"credited": False, "credited_usd": "0", "amount_usd": "9.99"}],
        resolution={"resolved": True, "winning_outcome": "Up"})
    # matched 10 * (1 - 0.97) = 0.30; fees 0.01; rebate 0.02; reward 0.40 PAID
    # the 9.99 accrual must NOT appear anywhere in the realized line
    assert c["realized_net"] == "0.710000"
    assert c["reward"]["accrued_not_credited_usd"] == "9.99"


def test_an_unsettled_market_contributes_nothing_to_the_roll_up():
    open_ = A.chain(market_key="a", venue="PMUS",
                    fills=[{"side": "Up", "qty": "10", "cost": "5"}])
    done = A.chain(market_key="b", venue="PMUS",
                   fills=[{"side": "Up", "qty": "10", "cost": "4.80"},
                          {"side": "Down", "qty": "10", "cost": "4.90"}],
                   resolution={"resolved": True, "winning_outcome": "Up"})
    r = A.roll_up([open_, done])
    assert r["settled"] == 1 and r["unsettled"] == 1
    assert r["identity_holds"] is True


# ---------------------------------------------------------------- incentives

def test_a_maker_rebate_joins_to_the_exact_fill():
    rows = J.rebate_rows([{
        "id": "t1", "marketSlug": "m", "orderId": "o1", "isAggressor": False,
        "qtyDecimal": "10",
        "passiveExecution": {"commissionNotionalCollected": {"value": "-0.02"}},
        "aggressorExecution": {"commissionNotionalCollected": {"value": "0.07"}}}])
    assert rows[0]["resolution"] == J.FILL_RESOLVED
    assert rows[0]["trade_id"] == "t1" and rows[0]["rebate_credited_usd"] == "0.02"
    # the counterparty's 0.07 is never ours
    assert rows[0]["fee_charged_usd"] == "0"


def test_our_side_is_chosen_by_isAggressor_not_by_summing_both():
    taker = J.rebate_rows([{
        "id": "t", "marketSlug": "m", "isAggressor": True, "qtyDecimal": "5",
        "passiveExecution": {"commissionNotionalCollected": {"value": "-0.01"}},
        "aggressorExecution": {"commissionNotionalCollected": {"value": "0.03"}}}])
    assert taker[0]["fee_charged_usd"] == "0.03"
    assert taker[0]["rebate_credited_usd"] == "0"


def test_a_reward_never_claims_fill_resolution():
    rows = J.reward_rows([{"marketSlug": "m", "programType": "LIQUIDITY",
                           "status": "PAID", "reward": "0.40"}])
    assert rows[0]["resolution"] == J.MARKET_RESOLVED
    assert rows[0]["fill_resolution"] == J.NOT_IDENTIFIABLE
    cov = J.coverage([], rows)
    assert cov["rewards"]["joined_to_a_fill_usd"] is None


def test_an_accrued_reward_is_not_credited():
    rows = J.reward_rows([{"marketSlug": "m", "status": "PENDING",
                           "reward": "5.00"}])
    assert rows[0]["credited"] is False and rows[0]["credited_usd"] == "0"


def test_rebate_rows_are_retained_in_full_never_truncated():
    """The existing audit keeps 20; a credit whose fill we dropped is a credit
    we cannot attribute."""
    many = [{"id": f"t{i}", "marketSlug": "m", "isAggressor": False,
             "qtyDecimal": "5",
             "passiveExecution": {"commissionNotionalCollected": {"value": "-0.01"}}}
            for i in range(250)]
    rows = J.rebate_rows(many)
    assert len(rows) == 250
    assert J.coverage(rows, [])["rebates"]["fully_attributable"] is True


# ---------------------------------------------------------------- reconcile

def test_unknown_state_stops_rather_than_continues():
    d = R.decide()
    assert d["action"] == R.STOP and d["latched"] is True


def test_a_stop_always_cancels_resting_orders():
    d = R.decide(combined_loss="-5.00", daily_stop="-5.00")
    assert d["action"] == R.STOP and d["cancel_resting_orders"] is True


def test_the_daily_stop_is_inclusive_at_the_boundary():
    assert R.decide(combined_loss="-5.00", daily_stop="-5.00")["action"] == R.STOP
    assert R.decide(combined_loss="-4.99", daily_stop="-5.00",
                    heartbeat_age_s=1, deadman_s=300, inventory={"ok": True},
                    orders={"ok": True}, settlement_age_s=1,
                    settlement_max_age_s=60, serving_sha="a", expected_sha="a",
                    accounting_ok=True)["action"] == R.CONTINUE


def test_no_input_combination_yields_an_action_that_starts_anything():
    """Exhaustive over the decision inputs, not a grep for a word: `decide` may
    return STOP, CANCEL_ALL or CONTINUE and may never authorise a resume."""
    import itertools
    safe = {R.STOP, R.CANCEL, R.CONTINUE}
    seen = set()
    grid = itertools.product(
        [None, "0", "-5.00"],            # combined_loss
        [None, "-5.00"],                 # daily_stop
        [None, 1.0, 9999.0],             # heartbeat_age_s
        [None, 300.0],                   # deadman_s
        [None, {"ok": True}, {"ok": False, "diffs": [1]}],
        [None, {"ok": True}, {"ok": False, "orphans_at_venue": ["x"]}],
        [None, True, False],             # accounting_ok
    )
    for cl, ds, hb, dm, inv, orders, acc in grid:
        d = R.decide(combined_loss=cl, daily_stop=ds, heartbeat_age_s=hb,
                     deadman_s=dm, inventory=inv, orders=orders,
                     accounting_ok=acc)
        assert d["action"] in safe, d
        assert d["may_auto_resume"] is False
        # a stop must always cancel: orders left resting after a stop are not
        # stopped, they are unsupervised
        if d["action"] == R.STOP:
            assert d["cancel_resting_orders"] is True
        seen.add(d["action"])
    assert seen == safe, f"the grid never exercised {safe - seen}"


def test_the_venue_wins_an_inventory_disagreement():
    m = R.inventory_matches({"up": "10"}, {"up": "12"})
    assert m["ok"] is False and m["diffs"][0]["venue"] == "12"
    assert R.decide(combined_loss="0", daily_stop="-5",
                    inventory=m)["action"] == R.STOP


def test_an_orphan_order_at_the_venue_forces_a_cancel():
    o = R.orphan_orders(ours=[], venue_open=["x1"])
    assert o["orphans_at_venue"] == ["x1"]
    assert R.decide(combined_loss="0", daily_stop="-5",
                    orders=o)["action"] == R.CANCEL
