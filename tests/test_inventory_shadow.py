"""The shadow adapter must map the existing cycle's state faithfully, refuse
cleanly when it cannot, and never acquire a path to an order."""
from decimal import Decimal as D

from src.gemini_execution import inventory_shadow as IVS
from src.gemini_execution.inventory_engine import Config, Limits

GOOD_BOOK = {"present": True, "readable": True,
             "yes": {"bid": "0.50", "bid_qty": "100"},
             "no": {"bid": "0.49", "bid_qty": "80"}}
FLOW = {"n_prints": 6, "buy_prints": 4, "sell_prints": 2}


def _dec(**kw):
    base = dict(symbol="KXBTC15M-T", tte_s=300, presented=GOOD_BOOK, depth=None,
                flow=FLOW, inventory={"yes_qty": "0", "no_qty": "0"}, working=[])
    base.update(kw)
    return IVS.shadow_decision(**base)


def test_book_mapping_uses_our_own_cost_convention():
    b = IVS.book_from_presented(GOOD_BOOK)
    assert b.yes_bid == D("0.50") and b.no_bid == D("0.49")
    assert b.taker_own("UP") == D("0.51")       # lift the YES ask = 1 - no_bid
    assert b.taker_own("DOWN") == D("0.50")     # lift the NO  ask = 1 - yes_bid
    assert b.maker_own("UP") == D("0.50")
    assert b.maker_queue("DOWN") == D(80)


def test_an_unreadable_book_is_a_refusal_not_a_default():
    assert IVS.book_from_presented({"present": False}) is None
    assert IVS.book_from_presented({"present": True, "readable": False}) is None
    assert IVS.book_from_presented({"present": True, "readable": True,
                                    "yes": {"bid": None}, "no": {}}) is None
    r = _dec(presented={"present": False})
    assert (r["action"], r["reason"]) == ("WAIT", "BOOK_UNREADABLE")
    assert r["SHADOW_NOT_SENT"] is True


def test_missing_time_to_expiry_is_a_refusal():
    r = _dec(tte_s=None)
    assert (r["action"], r["reason"]) == ("WAIT", "TTE_UNKNOWN")


def test_inventory_maps_yes_to_up_and_no_to_down():
    inv = IVS.inventory_from_cycle({"yes_qty": "7", "no_qty": "2",
                                    "yes_cost": "3.5", "no_cost": "1.0"})
    assert inv.up_qty == D(7) and inv.dn_qty == D(2)
    assert inv.resid == D(5) and inv.matched == D(2)
    assert inv.up_basis() == D("0.5")
    # a cycle with no basis still produces a usable, conservative inventory
    bare = IVS.inventory_from_cycle({"yes_qty": "3", "no_qty": "0"})
    assert bare.up_cost == 0 and bare.resid == D(3)
    assert IVS.inventory_from_cycle(None).resid == 0


def test_working_orders_map_to_resting_orders_with_the_live_queue():
    b = IVS.book_from_presented(GOOD_BOOK)
    r = IVS.resting_from_working(
        [{"leg": "YES", "price": "0.50", "qty": "5"},
         {"leg": "NO", "price": "0.49", "count": "3", "filled": "1"},
         {"leg": "GARBAGE", "price": "0.4", "qty": "1"},
         {"price": None, "qty": "1"},
         "not a mapping"], b)
    assert len(r) == 2
    assert r[0].side == "UP" and r[0].queue == D(100)
    assert r[1].side == "DOWN" and r[1].remaining == D(2) and r[1].queue == D(80)


def test_the_shipped_calibration_asserts_no_edge_so_the_engine_waits():
    assert IVS.UNCALIBRATED.n_train == 0
    assert IVS.UNCALIBRATED.resid_sd == 0.5
    r = _dec()
    assert r["action"] == "WAIT"
    assert r["reason"] == "PROB_UNCERTAIN"
    # and it says why in the row itself, rather than leaving a bare WAIT
    assert "NEGATIVE" in r["calibration_note"]


def test_the_row_carries_the_full_decision_chain():
    r = _dec(inventory={"yes_qty": "4", "no_qty": "1", "yes_cost": "2.0",
                        "no_cost": "0.5"})
    for k in ("p_up", "sigma", "prob_source", "inv_up", "inv_dn", "inv_resid",
              "worst_case_before", "yes_bid", "no_bid", "action", "reason",
              "rejected", "SHADOW_NOT_SENT"):
        assert k in r, k
    assert r["inv_resid"] == "3"
    assert len(r["rejected"]) == 4            # both sides x maker/taker
    assert all(c["reason"] for c in r["rejected"])


def test_the_adapter_has_no_order_path():
    """Checks the module's actual call surface, not its prose -- the docstrings
    legitimately discuss submitted orders, and a substring match on them was a
    false positive."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(IVS))
    imported = set()
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
        elif isinstance(node, ast.Call):
            f = node.func
            called.add(f.attr if isinstance(f, ast.Attribute)
                       else getattr(f, "id", ""))
    # no transport library can be reached from here
    assert not imported & {"requests", "httpx", "urllib", "aiohttp", "socket",
                           "websockets", "http"}
    # and nothing that mutates an order is called
    assert not called & {"post", "put", "delete", "create_order", "submit",
                         "submit_order", "cancel_order", "place"}


def test_a_calibrated_signal_would_be_acted_on_proving_the_gate_is_the_blocker():
    """Guards against a dead adapter: with a (hypothetical) skilled calibration
    the same inputs produce a trade, so the WAIT above is the probability gate
    talking and not a broken wiring."""
    from src.gemini_execution import inventory_prob as IP
    skilled = IP.Calibration(b_imb=0.0, b_flow=0.40, intercept=0.0,
                             resid_sd=0.02, n_train=9999, note="HYPOTHETICAL")
    r = IVS.shadow_decision(symbol="T", tte_s=600, presented=GOOD_BOOK,
                            depth=None, flow=FLOW,
                            inventory={"yes_qty": "0", "no_qty": "0"},
                            working=[], cal=skilled)
    assert r["action"] == "BUY_UP"
    assert r["SHADOW_NOT_SENT"] is True
