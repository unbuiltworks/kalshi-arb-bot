"""The switch-trigger features are only meaningful if the complement price is
read AS OF the decision instant. A lookahead of even one trade would leak the
outcome into the feature, so the lookup is pinned here."""
import importlib.util, pathlib

_p = (pathlib.Path(__file__).resolve().parents[1]
      / "analysis" / "l5" / "switch_trigger" / "trigger.py")
_s = importlib.util.spec_from_file_location("trigger", _p)
T = importlib.util.module_from_spec(_s); _s.loader.exec_module(T)

BOOK = {"Up": [(10, 0.40), (20, 0.50), (30, 0.60)]}


def test_last_price_is_as_of_the_instant_never_ahead_of_it():
    assert T._last_price_before(BOOK, "Up", 25) == 0.50
    assert T._last_price_before(BOOK, "Up", 20) == 0.50   # inclusive at t
    assert T._last_price_before(BOOK, "Up", 19) == 0.40   # never 0.50


def test_before_any_trade_the_price_is_unknown_not_zero():
    assert T._last_price_before(BOOK, "Up", 9) is None


def test_absent_outcome_is_unknown():
    assert T._last_price_before(BOOK, "Down", 30) is None


def _tape(rows):
    return [{"timestamp": t, "price": p, "size": q, "side": "BUY",
             "outcome": o, "proxyWallet": w} for (t, p, q, o, w) in rows]


def test_no_complement_row_is_emitted_before_inventory_exists():
    """With no position there is no complement, so guessing one would invent
    the very quantity the experiment measures."""
    rec = {"slug": "btc-updown-15m-1000", "cohort": "BTC15m",
           "tape": _tape([(1001, 0.40, 10, "Up", T.L5),
                          (1002, 0.55, 10, "Up", T.L5)])}
    out = T.build([rec])
    # the first fill has no prior inventory; the second is a CONTINUE with a
    # defined complement only if the book carries a Down price -- it does not
    assert all(r["complement_price"] is None for r in out)


def test_switch_is_labelled_against_the_side_not_held():
    rec = {"slug": "btc-updown-15m-1000", "cohort": "BTC15m",
           "tape": _tape([(1001, 0.40, 10, "Up", T.L5),
                          (1002, 0.30, 5, "Down", "0xother"),
                          (1003, 0.30, 10, "Down", T.L5)])}
    out = T.build([rec])
    assert out, "expected a decision row"
    assert out[-1]["is_switch"] == 1
    assert out[-1]["complement_price"] == 0.30
    # pair = held avg (0.40) + complement (0.30)
    assert abs(out[-1]["pair_cost"] - 0.70) < 1e-9


def test_auc_of_a_useless_feature_is_a_half():
    assert T._auc([(1.0, 1), (1.0, 0)]) == 0.5
    assert T._auc([(2.0, 1), (1.0, 0)]) == 1.0
