"""PMUS identity, pinned. No Gamma, no CTF token ids, no Polymarket.com.

The 2026-10-07T20:39:36Z rejection was not a pricing problem. E1 selected
its market on `gamma-api.polymarket.com`, streamed Polymarket.com's CLOB
for its prices, and then asked `api.polymarket.us` to trade a slug that
venue has never heard of.
"""
from __future__ import annotations

import re

import pytest

from analysis.l5.e1_cohort import pmus_identity as PI


def test_slug_for_matches_the_canonical_builder():
    """THE PIN. `slug_for` is reproduced in this package to avoid dragging
    `src.pmus_q1_executor` -- a second order-capable executor -- into
    E1's image. The proven function stays the source of truth, so the copy
    must agree with it across every window of several days, including DST
    boundaries and midnight rollovers."""
    from src import l5_pmus_auth_book as AB
    base = 1791400500           # 2026-10-07 19:15Z
    checked = 0
    for k in range(-400, 400):          # +/- ~4 days of 15m windows
        ws = base + k * PI.WINDOW_S
        assert PI.slug_for(ws) == AB.slug_for(ws), ws
        checked += 1
    # and the window_start grid agrees too
    for k in range(0, 200):
        t = base + k * 137.7            # arbitrary non-aligned offsets
        assert PI.window_start(t) == AB.window_start(t), t
    assert checked == 800


def test_the_slug_is_the_pmus_shape_and_round_trips():
    ws = PI.window_start(1791400500)
    slug = PI.slug_for(ws)
    assert slug == "cpc-btc-updown-15m-2026-10-07-1915z"
    assert PI.is_pmus_btc15m_slug(slug)
    assert PI.slug_start(slug) == ws


def test_the_regex_is_the_proven_transports_own():
    from src import l5_pmus_transport as PT
    assert PI.BTC15M_SLUG_RE is PT.BTC15M_SLUG_RE


# ============== THE GAMMA SLUG CANNOT PASS AS A PMUS IDENTITY ===========

@pytest.mark.parametrize("bad", [
    "btc-updown-15m-1791405000",            # the exact rejected slug
    "btc-updown-15m-1791400500",
    "btc-updown-15m",
    "cpc-btc-updown-15m-2026-10-07",        # no HHMMz
    "cpc-btc-updown-15m-2026-10-07-2100",   # no trailing z
    "cpc-btc-updown-5m-2026-10-07-2100z",   # wrong series
    "cpc-eth-updown-15m-2026-10-07-2100z",
    "", None, 0,
])
def test_a_non_pmus_identifier_is_refused(bad):
    assert PI.is_pmus_btc15m_slug(bad) is False
    with pytest.raises(ValueError, match="not a PMUS BTC15m slug"):
        PI.assert_pmus_slug(bad)


def test_the_refusal_names_the_actual_production_failure():
    """The error message has to teach the next reader why, or this
    recurs."""
    with pytest.raises(ValueError) as e:
        PI.assert_pmus_slug("btc-updown-15m-1791405000")
    msg = str(e.value)
    assert "Market not found" in msg
    assert "DIFFERENT VENUE" in msg


def test_a_ctf_token_id_is_not_a_pmus_identity():
    """The CTF token ids E1 used to authorize on are 77-digit integers.
    None of them can be a PMUS market identity."""
    ctf = ("45907034430024262874388768637322592547946286361468993415"
           "962658451195312616130")
    assert PI.is_pmus_btc15m_slug(ctf) is False
    with pytest.raises(ValueError):
        PI.assert_pmus_slug(ctf)


# ================ NO POLYMARKET.COM HOST IN THIS PATH ===================

def test_this_module_names_no_polymarket_com_host():
    src = open("analysis/l5/e1_cohort/pmus_identity.py").read()
    code = "\n".join(l for l in src.splitlines()
                     if not l.strip().startswith("#"))
    # the forbidden list itself is the one legitimate mention
    code = code.replace("FORBIDDEN_HOSTS", "")
    for host in ("gamma-api.polymarket.com",
                 "ws-subscriptions-clob.polymarket.com"):
        # appears only inside FORBIDDEN_HOSTS, never as a request target
        assert ".request(" not in code.split(host)[0][-80:], host


def test_the_only_hosts_used_are_pmus():
    assert PI.GATEWAY == "https://gateway.polymarket.us"
    assert PI.API == "https://api.polymarket.us"
    assert "polymarket.us" in PI.GATEWAY
    assert "polymarket.com" not in PI.GATEWAY
    assert "polymarket.com" not in PI.API


def test_the_forbidden_hosts_are_named_so_a_test_can_check_them():
    assert "gamma-api.polymarket.com" in PI.FORBIDDEN_HOSTS
    assert "ws-subscriptions-clob.polymarket.com" in PI.FORBIDDEN_HOSTS


def test_the_read_only_transport_cannot_mutate():
    t = PI.read_only_transport()
    with pytest.raises(PermissionError):
        t.request("POST", "/v1/orders", {})
    with pytest.raises(PermissionError):
        t.request("POST", "/v1/order/preview", {})


# ===================== OUTCOME SEMANTICS, NOT TOKENS ====================

def test_up_is_yes_and_down_is_no():
    o = PI.outcome_sides({"slug": "cpc-btc-updown-15m-2026-10-07-2100z",
                          "outcomes": ["Up", "Down"]})
    assert o["up_is"] == "OUTCOME_SIDE_YES"
    assert o["down_is"] == "OUTCOME_SIDE_NO"
    assert o["CTF_TOKEN_IDS_ARE_NOT_PMUS_IDENTITY"] is True


def test_the_side_mapping_agrees_with_the_proven_serializer():
    """`src.l5_order_body` is the serializer that has actually traded on
    this venue. E1's UP/DOWN must map the way it maps."""
    from src import l5_order_body as OB
    up = OB.build(slug="cpc-btc-updown-15m-2026-10-07-2100z", side="UP",
                  price="0.40", qty="0.13", post_only=True)
    down = OB.build(slug="cpc-btc-updown-15m-2026-10-07-2100z", side="DOWN",
                    price="0.40", qty="0.13", post_only=True)
    o = PI.outcome_sides({})
    assert up["outcomeSide"] == o["up_is"]
    assert down["outcomeSide"] == o["down_is"]
    assert up["participateDontInitiate"] is True


def test_the_serializer_accepts_the_pmus_slug_unchanged():
    from src import l5_order_body as OB
    slug = "cpc-btc-updown-15m-2026-10-07-2100z"
    body = OB.build(slug=slug, side="UP", price="0.40", qty="0.13",
                    post_only=True)
    assert body["marketSlug"] == slug
    assert PI.is_pmus_btc15m_slug(body["marketSlug"])


# ========== THE BBO SEMANTIC READER, PINNED TO THE PROVEN LINEAGE ========
#
# `scripts/l5_e1_bbo_semantics_probe.proven_lineage_read` REPRODUCES
# `pmus_q1_executor.fresh_bbo`'s field selection rather than importing it,
# because importing that module would put an order-capable stack into a
# read-only probe. A reproduction can drift from its original, so it is
# pinned here against the original's own SOURCE.

def test_the_probes_reader_is_the_proven_lineages_reader():
    import os
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    exec_src = open(os.path.join(root, "src", "pmus_q1_executor.py"),
                    encoding="utf8").read()
    # the executor reads marketData.bestBid.value / bestAsk.value
    assert '"bid": (md.get("bestBid") or {}).get("value")' in exec_src
    assert '"ask": (md.get("bestAsk") or {}).get("value")' in exec_src

    probe_src = open(os.path.join(root, "scripts",
                                  "l5_e1_bbo_semantics_probe.py"),
                     encoding="utf8").read()
    assert '"bid": (md.get("bestBid") or {}).get("value")' in probe_src
    assert '"ask": (md.get("bestAsk") or {}).get("value")' in probe_src
    assert 'md = bbo_raw.get("marketData") or {}' in probe_src


def test_the_probe_cannot_construct_or_send_an_order():
    """Structural: the probe names no order endpoint and no POST."""
    import ast
    import os
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    src = open(os.path.join(root, "scripts",
                            "l5_e1_bbo_semantics_probe.py"),
               encoding="utf8").read()
    tree = ast.parse(src)
    code = src
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)):
            d = ast.get_docstring(node, clean=False)
            if d:
                code = code.replace(d, "")
    code = "\n".join(ln for ln in code.splitlines()
                     if not ln.strip().startswith("#"))
    # The probe's OWN DENYLIST names the order-capable modules in order to
    # assert their absence from sys.modules, so those lines are excluded.
    # Naming a module in order to forbid it is the opposite of the hazard
    # being searched for -- the same exemption `pmus_identity` gets for
    # owning FORBIDDEN_HOSTS.
    denylist_start = code.index("FORBIDDEN_MODULES = (")
    denylist_end = code.index(")", denylist_start)
    code = code[:denylist_start] + code[denylist_end:]
    for forbidden in ("POST", "/v1/order", "l5_order_body",
                      "l5_preview_gate", "consume_attempt("):
        assert forbidden not in code, forbidden
    # the denylist itself must still name them, or the sys.modules
    # assertion would be checking nothing
    for named in ("analysis.l5.live_exec2.orders", "src.l5_preview_gate",
                  "src.pmus_q1_executor"):
        assert named in src, named
    # and it REPLACES the latch consumers with raises before any work
    assert "L.consume_attempt = _refuse" in src
    assert "L.consume_complement = _refuse" in src
