"""The universe cross-shadow row is observation-only, pure, and byte-bounded.

It is written to be DEPLOYABLE to the recorder, so the properties that make it
safe to deploy are pinned here rather than argued in a review.
"""
from __future__ import annotations

import ast
import json
from decimal import Decimal as D
from pathlib import Path

from src import pmus_cross_shadow_live as xs
from src import pmus_universe_cross_shadow as u

SRC = Path(u.__file__).read_text(encoding="utf-8")


def _dec(slug, *, relation="complete", bid="0.4950", ask="0.5900",
         bid_qty="5.0000", ask_qty="61.0000", residual="-2", avg_dn="0.40",
         avg_up="0.40", shares="1"):
    return {"kind": "pmus_shadow_decision", "slug": slug,
            "acted": relation, "sampled": relation,
            "inventory_before": {"up": "0", "dn": "2", "residual": residual,
                                 "avg_up": avg_up, "avg_dn": avg_dn},
            "book": {"yes_bid": bid, "yes_ask": ask,
                     "yes_bid_qty": bid_qty, "yes_ask_qty": ask_qty},
            "behavioral": {"side": "YES", "px": "0.50", "shares": shares}}


# --------------------------------------------------------- observation only
def test_no_submission_path_and_stdlib_plus_frozen_module_only():
    tree = ast.parse(SRC)
    roots = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            roots |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            roots.add((n.module or "").split(".")[0])
    assert roots <= {"__future__", "json", "typing", "src"}, roots
    assert "from src import pmus_cross_shadow_live as xs" in SRC
    names = {getattr(n, "attr", None) or getattr(n, "id", None)
             for n in ast.walk(tree) if isinstance(n, (ast.Attribute, ast.Name))}
    for bad in ("request", "urlopen", "socket", "place_limit_buy", "Transport",
                "getenv", "environ", "open", "time", "sleep", "print"):
        assert bad not in names, bad


def test_it_cannot_touch_recorder_selection_capacity_or_status():
    """Asserted against the CODE. The docstring names these deliberately, to
    say what the module must not do."""
    code = ast.parse(SRC)
    for node in ast.walk(code):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            node.value.value = ""
    body = ast.unparse(code)
    for forbidden in ("spring_watch", "top_markets", "build_status",
                      "SPRING_WATCH_LIMIT", "MAX_MARKETS", "subscribe",
                      "write_status_doc", "seat", "numReplicas"):
        assert forbidden not in body, forbidden


def test_would_submit_is_false_on_every_path():
    row = u.universe_row([_dec("a"), _dec("b", relation="open")],
                         ages={"a": 1.0})
    assert row["would_submit"] is False
    assert row["shadow_only"] is True
    for m in row["markets"]:
        assert m["would_submit"] is False


def test_a_non_complete_decision_is_not_a_refusal_it_is_not_counted():
    row = u.universe_row([_dec("x", relation="extend"),
                          _dec("y", relation="open")], ages={})
    assert row["rollup"]["complete_states"] == 0
    assert row["markets"] == []


# --------------------------------------------------- frozen rules are reused
def test_the_arm_and_ceiling_come_from_the_frozen_module():
    row = u.universe_row([_dec("a")], ages={"a": 1.0})
    assert row["prospective_arms"] == ["0.97"]
    assert set(row["excluded_prospective_arms"]) == {"0.98", "0.99", "1.00"}
    assert row["live_pair_ceiling_unchanged"] == "1.00"
    assert row["max_quote_age_s"] == xs.MAX_QUOTE_AGE_S


def test_depth_mapping_follows_the_order_side_both_ways():
    # residual < 0 -> holding DOWN -> COMPLETE buys UP -> ask_qty
    up = u.universe_row([_dec("up", residual="-2")], ages={"up": 1.0})["markets"][0]
    assert up["buys"] == "UP" and up["depth_field"] == "ask_qty"
    assert up["depth"] == "61.0000"
    # residual > 0 -> holding UP -> COMPLETE buys DOWN -> bid_qty
    dn = u.universe_row([_dec("dn", residual="2")], ages={"dn": 1.0})["markets"][0]
    assert dn["buys"] == "DOWN" and dn["depth_field"] == "bid_qty"
    assert dn["depth"] == "5.0000"


def test_missing_age_is_unknown_and_never_a_fill():
    row = u.universe_row([_dec("a")], ages={})            # no age supplied
    m = row["markets"][0]
    assert m["verdict"] == xs.UNKNOWN_EXEC
    assert m["stop"] == "no_authenticated_quote_age"
    assert m["depth"] is None
    assert row["rollup"]["supported"] == 0


def test_stale_and_missing_depth_never_become_supported():
    stale = u.universe_row([_dec("a")], ages={"a": 99.0})["markets"][0]
    assert stale["verdict"] != xs.SUPPORTED and stale["depth"] is None
    nodepth = u.universe_row([_dec("b", ask_qty=None)],
                             ages={"b": 1.0})["markets"][0]
    assert nodepth["verdict"] != xs.SUPPORTED and nodepth["depth"] is None


def test_a_supported_row_carries_both_confirmed_costs():
    # pair 0.40 + 0.59 = 0.99 is over the arm; drop the held cost to clear it
    row = u.universe_row([_dec("a", avg_dn="0.30")], ages={"a": 1.0})
    m = row["markets"][0]
    assert m["pair"] == "0.8900"
    assert m["fee"] is not None and m["rebate_forgone"] is not None
    assert m["verdict"] == xs.SUPPORTED and m["would_submit"] is False
    assert row["rollup"]["supported"] == 1


# --------------------------------------------------- byte budget and honesty
def test_the_row_stays_inside_the_log_stream_byte_budget():
    decs = [_dec(f"aec-some-quite-long-market-slug-2026-09-14-{i:03d}")
            for i in range(400)]
    row = u.universe_row(decs, ages={d["slug"]: 1.0 for d in decs})
    size = len(json.dumps(row, separators=(",", ":"), default=str).encode())
    assert size <= u.MAX_ROW_BYTES, size
    assert row["rollup"]["complete_states"] == 400        # rollup counts ALL
    assert row["markets_emitted"] <= u.MAX_MARKET_ENTRIES
    assert row["markets_emitted"] < 400                   # budget actually bound
    assert row["markets_dropped_for_budget"] >= 0


def test_supported_rows_survive_the_budget_first():
    many = [_dec(f"plain-{i:03d}") for i in range(300)]
    hit = _dec("the-one-that-matters", avg_dn="0.30")
    row = u.universe_row(many + [hit],
                         ages={d["slug"]: 1.0 for d in many + [hit]})
    assert row["rollup"]["supported"] == 1
    assert row["markets"][0]["slug"] == "the-one-that-matters"


def test_the_row_declares_that_it_is_recorder_local_and_optimistic():
    row = u.universe_row([_dec("a")], ages={"a": 1.0},
                         consumer_age_penalty_s=6.1)
    assert row["age_basis"] == "recorder_local_no_transport_delay"
    assert row["consumer_age_penalty_s"] == 6.1
    assert "UPPER BOUND" in row["labels"]
    assert row["provenance"].startswith("RECORDER_LOCAL:SHADOW_ONLY")


def test_it_is_pure_same_input_same_output():
    decs = [_dec("a"), _dec("b", residual="3")]
    a = u.universe_row(decs, ages={"a": 1.0, "b": 2.0})
    b = u.universe_row(decs, ages={"a": 1.0, "b": 2.0})
    assert a == b
