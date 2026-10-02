"""Proof that the latent-quote shadow is read-only, and that its fill rules hold."""
from __future__ import annotations
import ast, glob, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.l5_latent_quote import config as C, arms, lifecycle as L, models

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "src", "l5_latent_quote")
FORBIDDEN_NAMES = {"submit", "place_order", "create_order", "cancel_order",
                   "sign", "sign_order", "arm", "transport", "post", "delete", "put"}
FORBIDDEN_IMPORTS = {"requests", "websocket", "websockets", "web3", "eth_account"}
CRED_HINTS = {"API_KEY", "SECRET", "PRIVATE_KEY", "PASSPHRASE", "MNEMONIC", "TOKEN_SECRET"}


def _sources():
    return sorted(glob.glob(os.path.join(PKG, "*.py")))


def test_no_order_path_and_no_credentials():
    for p in _sources():
        src = open(p).read()
        tree = ast.parse(src)
        for n in ast.walk(tree):
            if isinstance(n, ast.Attribute) and n.attr.lower() in FORBIDDEN_NAMES:
                raise AssertionError(f"{p}: forbidden attribute .{n.attr}")
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                mods = ([x.name for x in n.names] if isinstance(n, ast.Import)
                        else [n.module or ""])
                for m in mods:
                    root = (m or "").split(".")[0]
                    assert root not in FORBIDDEN_IMPORTS, f"{p}: forbidden import {m}"
        for c in CRED_HINTS:
            assert f'environ["{c}' not in src and f"environ.get('{c}" not in src, \
                f"{p}: reads a credential-shaped env var"


def test_only_get_requests():
    """urlopen is called with a Request that never sets a method or a body."""
    src = open(os.path.join(PKG, "feed.py")).read()
    tree = ast.parse(src)
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr == "Request":
            for kw in n.keywords:
                assert kw.arg in ("headers",), f"Request got unexpected kwarg {kw.arg}"
            assert len(n.args) == 1, "Request must take only a URL positionally"


def test_no_recorder_or_gemini_or_production_paths():
    for p in _sources():
        src = open(p).read().lower()
        for bad in ("pmus_ws", "pmus-ws", "/state/", "gemini", "live_mandate",
                    "pmus_q1", "railway"):
            assert bad not in src, f"{p}: references {bad!r}"
        assert C.OUT_DIR.startswith("data/l5_latent_quote")


def test_arms_never_cross():
    s = {"up_bid": 0.30, "up_ask": 0.31, "dn_bid": 0.69, "dn_ask": 0.70,
         "l5_obs_dn_basis": 0.60, "l5_obs_up_basis": 0.30}
    bk = {"bids": [{"p": 0.30, "q": 7.0}], "asks": [{"p": 0.31, "q": 5.0}]}
    out = arms.build("up", s, bk)
    for arm, q in out.items():
        if q and q.get("status") == "PLACEABLE":
            assert q["price"] < s["up_ask"], f"{arm} would cross"
    assert out["P3_IMPROVE_BID"]["status"] == "REFUSED_CROSS", \
        "improving into a one-tick spread must be refused, not crossed"


def test_no_touch_equals_fill():
    q = {"price": 0.30, "size": 5.0, "queue_ahead": 10.0,
         "bid_at_place": 0.30, "ask_at_place": 0.32}
    bk = {"bids": [{"p": 0.30, "q": 5.0}], "asks": [{"p": 0.32, "q": 9.0}]}
    o = L.place(100.0, "s", "S5_FULL", "P1_AT_BID", "up", q)
    o = L.observe(o, 101.0, [{"t": 100.5, "p": 0.30, "q": 4.0, "up": True, "buy": False}], bk)
    assert o["state"] == L.RESTING and o["filled_q"] == 0.0


def test_unknown_queue_never_fills():
    q = {"price": 0.30, "size": 5.0, "queue_ahead": None,
         "bid_at_place": 0.30, "ask_at_place": 0.32}
    bk = {"bids": [], "asks": []}
    o = L.place(100.0, "s", "S5_FULL", "P1_AT_BID", "up", q)
    o = L.observe(o, 101.0, [{"t": 100.5, "p": 0.30, "q": 999.0, "up": True, "buy": False}], bk)
    assert o["filled_q"] == 0.0 and o["evidence"] == L.UNKNOWN


def test_trade_through_is_defensible():
    q = {"price": 0.30, "size": 5.0, "queue_ahead": 10.0,
         "bid_at_place": 0.30, "ask_at_place": 0.32}
    bk = {"bids": [{"p": 0.29, "q": 1.0}], "asks": [{"p": 0.32, "q": 9.0}]}
    o = L.place(100.0, "s", "S5_FULL", "P1_AT_BID", "up", q)
    o = L.observe(o, 101.0, [{"t": 100.5, "p": 0.28, "q": 6.0, "up": True, "buy": False}], bk)
    assert o["state"] == L.FULL and o["evidence"] == "TRADE_THROUGH"


def test_reprice_is_explicit():
    q = {"price": 0.30, "size": 5.0, "queue_ahead": 1.0,
         "bid_at_place": 0.30, "ask_at_place": 0.32}
    o = L.place(100.0, "s", "S5_FULL", "P1_AT_BID", "up", q)
    L.cancel_for_reprice(o, 105.0)
    assert o["state"] == L.REPRICED
    assert [e[1] for e in o["events"]] == [L.PLACED, L.REPRICED]


def test_abstain_is_available_and_models_bounded():
    s = {"taker_up_share_60": None, "up_ask": None, "dn_ask": None,
         "l5_obs_up_basis": None, "l5_obs_dn_basis": None, "up_bid": None,
         "dn_bid": None, "l5_prev_up": None, "l5_run": None, "secs_to_settle": 400}
    p = models.predict_all(s)
    assert set(p) == set(C.SIDE_MODELS)
    for v in p.values():
        assert 0.0 <= v["p_up"] <= 1.0 and 0.0 <= v["confidence"] <= 1.0
        assert isinstance(v["abstain"], bool)
