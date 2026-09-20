"""A and B as Gemini execution adaptations. Model A is not touched."""
from decimal import Decimal as D

from src.gemini_execution import ab_arms as AB
from src.gemini_execution import tape_replay as TR

BOOK = {"yes": {"bid": "0.40", "ask": "0.44"},
        "no": {"bid": "0.56", "ask": "0.60"}}


def test_A_NEVER_IMPROVES_THROUGH_THE_ASK():
    """Crossing is a taker order wearing a maker's label."""
    assert AB.maker_only_improve(BOOK, "YES", "0.40") == D("0.41")
    assert AB.maker_only_improve(BOOK, "YES", "0.42") == D("0.43")
    assert AB.maker_only_improve(BOOK, "YES", "0.43") is None   # would reach
    assert AB.maker_only_improve(BOOK, "YES", "0.50") is None   # through
    assert AB.maker_only_improve({}, "YES", "0.40") is None     # no ask


def test_A_KEEPS_MODEL_AS_DECISION_AND_ONLY_MOVES_THE_PRICE():
    acts = [{"leg": "YES", "price": "0.40", "qty": "0.01", "action": "OPEN"},
            {"leg": "NO", "price": "0.56", "qty": "0.01", "action": "OPEN"}]
    out = AB.apply_A(acts, BOOK)
    assert [a["action"] for a in out] == ["OPEN", "OPEN"]
    assert [a["leg"] for a in out] == ["YES", "NO"]
    assert [a["qty"] for a in out] == ["0.01", "0.01"]
    assert out[0]["price"] == "0.41" and out[0]["native_price"] == "0.40"


def test_B_KEEPS_ONLY_WHEN_REQUOTING_IMPROVES_NEITHER_QUEUE_NOR_PRICE():
    """The rule this replaced required `ahead_left < ahead_at_place`, which
    is unsatisfiable at the front of the queue -- so it could never preserve
    the most valuable position it existed to protect."""
    depth = {"readable": True,
             "bids": {"levels": [{"price": "0.41", "qty": "300"}]},
             "asks": {"levels": [{"price": "0.59", "qty": "300"}]}}
    live = {"YES": TR.Quote(leg="YES", price=D("0.41"), qty=D("0.01"),
                            placed_ms=D("0"), ahead=D("0"), queue_known=True,
                            kind="OPEN")}
    want = [{"leg": "YES", "price": "0.41", "qty": "0.01", "action": "OPEN"}]
    ev = AB.reconcile(dict(live), want, now_ms=D("1"), depth=depth,
                      default_qty=D("0.01"), preserve=True)
    assert ev["kept"] == 1               # same price: kept outright

    # a front-of-queue rung whose replacement would sit behind 300
    want2 = [{"leg": "YES", "price": "0.42", "qty": "0.01", "action": "OPEN"}]
    l2 = dict(live)
    ev2 = AB.reconcile(l2, want2, now_ms=D("1"), depth=depth,
                       default_qty=D("0.01"), preserve=True)
    d = ev2["decisions"][0]
    assert d["ahead_left"] == "0"
    assert d["decision"] == "KEEP"       # old is cheaper AND at the front
    assert l2["YES"].price == D("0.41")


def test_B_REQUOTES_WHEN_THE_NEW_PRICE_IS_CHEAPER():
    depth = {"readable": True, "bids": {"levels": []}, "asks": {"levels": []}}
    live = {"YES": TR.Quote(leg="YES", price=D("0.45"), qty=D("0.01"),
                            placed_ms=D("0"), ahead=D("0"), queue_known=True,
                            kind="OPEN")}
    want = [{"leg": "YES", "price": "0.40", "qty": "0.01", "action": "OPEN"}]
    ev = AB.reconcile(live, want, now_ms=D("1"), depth=depth,
                      default_qty=D("0.01"), preserve=True)
    assert ev["decisions"][0]["decision"] == "REQUOTE"
    assert live["YES"].price == D("0.40")


def test_PRESERVE_OFF_REPRODUCES_THE_FROZEN_BEHAVIOUR():
    depth = {"readable": True, "bids": {"levels": []}, "asks": {"levels": []}}
    live = {"YES": TR.Quote(leg="YES", price=D("0.41"), qty=D("0.01"),
                            placed_ms=D("0"), ahead=D("0"), queue_known=True,
                            kind="OPEN")}
    want = [{"leg": "YES", "price": "0.42", "qty": "0.01", "action": "OPEN"}]
    ev = AB.reconcile(live, want, now_ms=D("1"), depth=depth,
                      default_qty=D("0.01"), preserve=False)
    assert ev["requoted"] == 1 and ev["decisions"] == []


# ------------------------------- the challenger arm as it actually deploys

def test_THE_CHALLENGER_IS_AN_EXECUTION_ADAPTATION_NOT_A_MODEL_CHANGE():
    """A and B change how an order is placed, never what Model A decided."""
    from src.gemini_execution import shadow_soak as SS
    spec = SS.EXECUTION_ARMS[0]
    assert spec["label"] == "EXEC_A_PLUS_B"
    assert spec["improve"] and spec["preserve"]
    arm = SS._Arm(spec["label"], None, symbol="GEMI-BTC15M2609200045-UP",
                  cohort_prefix="GEMI-BTC15M", policy="P",
                  improve=True, preserve=True)
    # sp is None: it runs the SAME frozen spec as the control
    assert arm.sp is None


def test_THE_LEDGER_COUNTS_THE_MECHANISM_DIRECTLY():
    """zero-ahead rungs are what A exists to create; counting fills alone
    would leave the mechanism inferred rather than measured."""
    from src.gemini_execution import hypothetical as HY
    b = HY.HypotheticalBook(symbol="S", policy="P")
    depth = {"readable": True,
             "bids": {"levels": [{"price": "0.30", "qty": "50"}]},
             "asks": {"levels": [{"price": "0.70", "qty": "50"}]}}
    b.retarget([{"leg": "YES", "price": "0.40", "qty": "0.01",
                 "action": "OPEN"}], now_ms=1, depth=depth)
    r = b.report()
    assert r["zero_ahead_rungs"] == 1        # 0.40 is inside the 0.30 bid
    assert r["rungs_rested"] == 1


def test_PRESERVE_ROUTES_THROUGH_THE_B_RULE_AND_IS_COUNTED():
    from src.gemini_execution import hypothetical as HY
    b = HY.HypotheticalBook(symbol="S", policy="P")
    depth = {"readable": True,
             "bids": {"levels": [{"price": "0.41", "qty": "300"}]},
             "asks": {"levels": [{"price": "0.70", "qty": "50"}]}}
    # rest at 0.42: inside the 0.41 bid, so nothing is ahead of us
    b.retarget([{"leg": "YES", "price": "0.42", "qty": "0.01",
                 "action": "OPEN"}], now_ms=1, depth=depth, preserve=True)
    assert b.live["YES"].ahead == D("0")
    # the touch slips one tick to 0.41. Replacement would sit behind 300, so
    # keeping our place in line is right and B keeps it.
    b.retarget([{"leg": "YES", "price": "0.41", "qty": "0.01",
                 "action": "OPEN"}], now_ms=2, depth=depth, preserve=True)
    assert b.live["YES"].price == D("0.42")
    assert b.report()["preserved_rungs"] == 1


def test_B_STILL_REQUOTES_INTO_A_CLEARLY_SUPERIOR_QUEUE():
    """Preservation is not stubbornness: if the new price puts us in front
    and the old one left us behind 300, we take the better place."""
    from src.gemini_execution import hypothetical as HY
    b = HY.HypotheticalBook(symbol="S", policy="P")
    depth = {"readable": True,
             "bids": {"levels": [{"price": "0.41", "qty": "300"}]},
             "asks": {"levels": [{"price": "0.70", "qty": "50"}]}}
    b.retarget([{"leg": "YES", "price": "0.41", "qty": "0.01",
                 "action": "OPEN"}], now_ms=1, depth=depth, preserve=True)
    assert b.live["YES"].ahead == D("300")
    b.retarget([{"leg": "YES", "price": "0.42", "qty": "0.01",
                 "action": "OPEN"}], now_ms=2, depth=depth, preserve=True)
    assert b.live["YES"].price == D("0.42")
    assert b.report()["preserved_rungs"] == 0
