"""Pure tests of the market-agnostic directional allocation module."""
import ast
import pathlib
from decimal import Decimal as D

from src import pmus_directional_alloc as DA

T = D("0.01")


def W(yes=None, no=None, ladder=None):
    w = {"YES": yes, "NO": no}
    if ladder is not None:
        w["ladder"] = ladder
    return w


def bid(px, qty="2", role="open"):
    return {"px": D(px), "qty": D(qty), "role": role, "why": None}


FLAT = {"YES": D(0), "NO": D(0)}


def test_probability_validation():
    assert DA.probability("0.6")["lo"] == D("0.6")
    assert DA.probability("0.6", "0.59", "0.61")["hi"] == D("0.61")
    for bad in (("0",), ("1",), ("0.6", "0.62", "0.65"), ("x",),
                ("0.6", "0.5", "0.55")):
        assert DA.probability(*bad) is None


def test_no_probability_changes_nothing():
    w = W(bid("0.47"), bid("0.49"), [{"side": "YES", "px": D("0.45"),
                                      "qty": D(2), "level": 1}])
    out = DA.allocate(prob=None, want=w, pos=FLAT, tick=T)
    assert out["YES"] == w["YES"] and out["NO"] == w["NO"]
    assert out["ladder"] == w["ladder"]
    assert out["directional"] == {"active": False, "sides": {},
                                  "why": "no_probability"}


def test_favoured_side_kept_unfavoured_negative_edge_refused():
    # fair YES 0.60: YES 0.47 is cheap; NO fair 0.40 < NO bid 0.49
    p = DA.probability("0.60")
    out = DA.allocate(prob=p, want=W(bid("0.47"), bid("0.49")), pos=FLAT,
                      tick=T, fill_loss={"YES": "0.04", "NO": "0.04"})
    assert out["YES"]["qty"] == D(2) and out["YES"]["px"] == D("0.47")
    assert out["NO"] is None
    assert out["why_NO"] == "directional:trade_edge_negative"
    assert out["directional"]["favoured"] == "YES"
    assert out["directional"]["order"] == ["YES"]


def test_reward_never_rescues_a_negative_edge_on_the_flat_side():
    p = DA.probability("0.60")
    out = DA.allocate(prob=p, want=W(None, bid("0.49")), pos=FLAT, tick=T,
                      reward={"NO": "1.00"})
    assert out["NO"] is None


def test_heavier_side_shrinks_then_stops_as_inventory_grows():
    # edge 0.60-0.55-0.02 = 0.03; lam 0.005, var 0.24
    p = DA.probability("0.60")
    cfg = {"risk_aversion": "0.005"}
    sizes = []
    for held in (0, 10, 20, 40):
        a = DA.assess(prob=p, side="YES", px="0.55", qty="4",
                      pos={"YES": D(held), "NO": D(0)}, fill_loss="0.02",
                      cfg=cfg)
        sizes.append(D(a["qty"]))
    assert sizes[0] == D(4)
    assert sizes == sorted(sizes, reverse=True)
    assert sizes[-1] == D(0)


def test_lighter_side_bought_for_risk_reduction_only_when_heavy():
    # NO fair 0.40, NO bid 0.405 -> edge slightly negative (-0.005)
    p = DA.probability("0.60")
    cfg = {"risk_aversion": "0.005"}
    flat = DA.assess(prob=p, side="NO", px="0.405", qty="2", pos=FLAT,
                     cfg=cfg)
    heavy = DA.assess(prob=p, side="NO", px="0.405", qty="2",
                      pos={"YES": D(10), "NO": D(0)}, cfg=cfg)
    assert flat["qty"] == "0"
    assert D(heavy["qty"]) == D(2)
    assert D(heavy["risk_per_contract"]) < 0


def test_completions_are_never_refused_resized_or_repriced():
    p = DA.probability("0.90")      # says NO is badly overpriced
    w = W(None, bid("0.49", "3", role="complete"))
    out = DA.allocate(prob=p, want=w, pos={"YES": D(3), "NO": D(0)}, tick=T)
    assert out["NO"] == w["NO"]
    assert out["directional"]["sides"]["NO"]["why"] == \
        "completion_unchanged"


def test_never_adds_a_side_raises_size_or_raises_price():
    p = DA.probability("0.70")
    w = W(bid("0.40", "1"), None)
    out = DA.allocate(prob=p, want=w, pos=FLAT, tick=T,
                      cfg={"risk_aversion": "0"})
    assert out["NO"] is None
    assert out["YES"]["qty"] <= D(1) and out["YES"]["px"] <= D("0.40")


def test_unfavoured_side_rests_further_back_when_its_edge_is_thin():
    # fair 0.52: YES 0.47 edge 0.05 (favoured); NO fair 0.48, NO 0.465
    # edge 0.015 < touch_edge 0.02 -> one tick back to 0.455
    p = DA.probability("0.52")
    out = DA.allocate(prob=p, want=W(bid("0.47"), bid("0.465")), pos=FLAT,
                      tick=T, cfg={"risk_aversion": "0"})
    assert out["YES"]["px"] == D("0.47")
    assert out["NO"]["px"] == D("0.455")
    assert out["directional"]["sides"]["NO"]["depth_moved_ticks"] == 1


def test_favoured_side_hysteresis():
    p = DA.probability("0.50")
    px = {"YES": D("0.47"), "NO": D("0.465")}    # NO edge +0.005 more
    assert DA.favoured(p, px, prev="YES") == "YES"
    assert DA.favoured(p, px, prev=None) == "NO"
    px2 = {"YES": D("0.47"), "NO": D("0.455")}   # NO now leads by 0.015
    assert DA.favoured(p, px2, prev="YES") == "NO"


def test_reallocation_when_the_favoured_outcome_changes():
    """Held YES; fair moves from 0.62 to 0.38: YES additions stop, NO
    (now favoured and risk-reducing) is bought."""
    held = {"YES": D(6), "NO": D(0)}
    w = W(bid("0.45"), bid("0.50"))
    before = DA.allocate(prob=DA.probability("0.62"), want=w, pos=held,
                         tick=T, fill_loss={"YES": "0.04", "NO": "0.04"})
    assert before["YES"] is not None and before["NO"] is None
    after = DA.allocate(prob=DA.probability("0.38"), want=w, pos=held,
                        tick=T, fill_loss={"YES": "0.04", "NO": "0.04"},
                        prev_favoured=before["directional"]["favoured"])
    assert after["YES"] is None
    assert after["NO"] is not None
    assert after["directional"]["favoured"] == "NO"


def test_ladder_levels_judged_on_the_inventory_ahead_of_them():
    p = DA.probability("0.60")
    lad = [{"side": "YES", "px": D("0.53"), "qty": D(4), "level": k}
           for k in (1, 2, 3, 4)]
    out = DA.allocate(prob=p, want=W(bid("0.55", "4"), None, lad),
                      pos=FLAT, tick=T, fill_loss={"YES": "0.02"},
                      cfg={"risk_aversion": "0.02"})
    qs = [l_["qty"] for l_ in out["ladder"]]
    assert qs == sorted(qs, reverse=True)
    assert len(qs) < 4 or qs[-1] < D(4)


def test_ladder_behind_a_refused_front_bid_is_dropped():
    p = DA.probability("0.60")
    lad = [{"side": "NO", "px": D("0.47"), "qty": D(2), "level": 1}]
    out = DA.allocate(prob=p, want=W(None, bid("0.49"), lad), pos=FLAT,
                      tick=T)
    assert out["NO"] is None and out["ladder"] == []


def test_the_module_imports_no_venue_or_market_code():
    src = pathlib.Path(DA.__file__).read_text()
    mods = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add((node.module or "").split(".")[0])
    assert mods <= {"__future__", "decimal", "typing"}
