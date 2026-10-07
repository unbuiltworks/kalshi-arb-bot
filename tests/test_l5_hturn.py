"""Tests for the H-TURN book-arrival test (`analysis/l5/hturn/hturn.py`).

The two things the brief requires proof of:

  1. the CONTROL rows are matched as described -- every turn is compared only
     with extends that stayed extends in the same stratum;
  2. no POST-TURN information enters a PRE-TURN feature.

(2) is proved three ways: at the AST level (the ex-ante feature builder never
names a next-fill field), by construction (a book read is refused unless its
snapshot is at or before the reference time), and by a mutation test (changing
everything about the next fill except its outcome leaves every `pre_*` feature
byte-identical).
"""
from __future__ import annotations

import ast
import inspect
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from analysis.l5.hturn import hturn as H  # noqa: E402

JSON = os.path.join(os.path.dirname(H.__file__), "HTURN_TEST.json")


# --------------------------------------------------------------- fixtures

@pytest.fixture(scope="module")
def books():
    return H.load_books()


@pytest.fixture(scope="module")
def rows(books):
    return H.attach_book(H.build_decisions(H.load_overlap_fills(books)),
                         books, H.load_roles())


@pytest.fixture(scope="module")
def ext(rows):
    return [r for r in rows if r["prev_rel"] == "extend"]


# ------------------------------------------------- 1. the control is matched

def test_every_matched_pair_shares_its_stratum_exactly(ext):
    pairs = H.matched_pairs(ext)
    assert pairs
    for t, c in pairs:
        assert H.stratum(t) == H.stratum(c), (t["slug"], c["slug"])


def test_matched_pairs_are_turn_against_non_turn_extend(ext):
    for t, c in H.matched_pairs(ext):
        assert t["is_turn"] is True and t["next_rel"] == "complete"
        assert c["is_turn"] is False and c["next_rel"] == "extend"
        assert t["prev_rel"] == c["prev_rel"] == "extend"


def test_controls_are_used_at_most_once(ext):
    pairs = H.matched_pairs(ext)
    ids = [(c["slug"], c["fill_index"]) for _, c in pairs]
    assert len(ids) == len(set(ids))


def test_stratum_is_a_function_of_pre_decision_state_only(ext):
    """The stratum keys must not move when the next fill changes."""
    for r in ext[:400]:
        m = dict(r)
        m["t_next"] = r["t_next"] + 999
        m["next_rel"] = "complete" if r["next_rel"] == "extend" else "extend"
        m["next_size"] = r["next_size"] + 7777
        m["tx_next"] = "0xdead"
        assert H.stratum(m) == H.stratum(r)


def test_stratum_matches_the_documented_dimensions(ext):
    s = H.stratum(ext[0])
    assert len(s) == 4
    assert s[0] in ("5m", "15m")
    assert s[1].startswith("tte")
    assert s[2].startswith("run")
    assert s[3].startswith("res")


def test_stratified_auc_only_uses_strata_holding_both_classes(ext):
    out = H.stratified_auc([r for r in ext if r.get("pre_headroom") is not None],
                           "pre_headroom")
    assert out["n_strata_informative"] <= out["n_strata_total"]
    assert out["concordant_pair_weight"] > 0


# ------------------------------------------------------- 2. no forward leak

def _names(fn):
    src = inspect.getsource(fn)
    tree = ast.parse("".join(src.splitlines(True)[0:]).lstrip())
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.add(n.value)
        if isinstance(n, ast.Name):
            out.add(n.id)
        if isinstance(n, ast.Attribute):
            out.add(n.attr)
    return out


FORBIDDEN = {"t_next", "next_rel", "next_size", "tx_next", "is_turn",
             "l5_role_chain"}


def test_pair_feature_builder_never_names_a_next_fill_field():
    assert not (_names(H._pair_features) & FORBIDDEN)


def test_relation_is_a_function_of_prior_residual_and_outcome_only():
    sig = list(inspect.signature(H.relation).parameters)
    assert sig == ["prev_residual", "outcome"]


def test_pre_features_are_invariant_to_everything_about_the_next_fill(books, ext):
    """MUTATION TEST. Rebuild the ex-ante features with the next fill's time,
    size, hash and relation corrupted. Every `pre_*` value must be identical."""
    n = 0
    for r in ext:
        if not r["pre_readable"]:
            continue
        m = dict(r)
        m["t_next"] = r["t_next"] + 12345
        m["next_rel"] = "complete" if r["next_rel"] == "extend" else "extend"
        m["next_size"] = -1.0
        m["tx_next"] = None
        a = H._pair_features(books[r["slug"]], r, float(r["t_dec"]), "pre")
        b = H._pair_features(books[m["slug"]], m, float(m["t_dec"]), "pre")
        assert a == b
        n += 1
        if n >= 300:
            break
    assert n >= 100


def test_book_reads_never_use_a_snapshot_after_the_reference_time(books):
    for slug, m in list(books.items())[:40]:
        for tb in m.values():
            if not tb.t:
                continue
            for t in (tb.t[0] - 1, tb.t[len(tb.t) // 2] + 0.5, tb.t[-1] + 5):
                i = tb.idx_at_or_before(t)
                if i is not None:
                    assert tb.t[i] <= t


def test_book_reads_refuse_a_snapshot_staler_than_the_resync_interval(books):
    tb = next(tb for m in books.values() for tb in m.values() if tb.t)
    assert tb.idx_at_or_before(tb.t[-1] + H.MAX_CARRY_S + 1.0) is None
    assert tb.idx_at_or_before(tb.t[-1] + H.MAX_CARRY_S - 1.0) is not None


def test_at_next_family_is_read_strictly_before_the_next_fill(ext):
    """The DIAGNOSTIC family still never reads a snapshot containing the fill
    it is predicting: the reference is t_next - 1s and the index is <=."""
    for r in ext:
        if r.get("nxt_age_s") is not None:
            assert r["nxt_age_s"] >= 0.0


def test_crossing_scan_upper_bound_is_strictly_before_the_next_fill(ext):
    for r in ext:
        if r["cross_t"] is not None:
            assert r["t_dec"] < r["cross_t"] <= r["t_next"] - 1.0


# ------------------------------------------- provenance and label discipline

def test_role_provenance_is_never_called_isAggressor():
    src = open(H.__file__).read()
    assert "execution.isAggressor" in src, "the absence must be documented"
    # it is only ever mentioned as NOT the source
    assert "CHAIN_TOPIC_POSITION" in src
    tree = ast.parse(src)
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute):
            assert n.attr not in ("isAggressor", "aggressor")
        if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant):
            assert n.slice.value not in ("isAggressor", "aggressor")


def test_only_buy_fills_in_the_overlap(books):
    fills = H.load_overlap_fills(books)
    assert fills
    # load_overlap_fills filters to BUY; assert the corpus really holds no
    # SELL in these slugs, so `relation`'s SELL branch is unreachable here.
    import gzip
    want = set(books)
    sells = 0
    for line in gzip.open(H.FILLS_GZ, "rt"):
        for r in json.loads(line).get("payload") or []:
            if r.get("type") == "TRADE" and r.get("side") != "BUY" \
                    and (r.get("slug") or r.get("eventSlug")) in want:
                sells += 1
    assert sells == 0


def test_token_id_join_is_consistent_with_the_slug_outcome_join(books):
    """The book is joined by (market_slug, outcome). Assert that pair is a
    bijection onto token_id, i.e. the join IS the token-id join."""
    seen = {}
    for slug, m in books.items():
        for oc, tb in m.items():
            assert (slug, oc) not in seen
            seen[(slug, oc)] = tb.token_id
    assert len(set(seen.values())) == len(seen)


def test_published_json_matches_a_fresh_run():
    assert os.path.exists(JSON)
    pub = json.load(open(JSON))
    fresh = H.run()
    for k in ("overlap", "base_rate_turn_given_prev_extend"):
        assert pub[k] == fresh[k]
    assert pub["ALL"]["HEADLINE"] == fresh["ALL"]["HEADLINE"]


def test_persistence_is_the_stated_baseline():
    pub = json.load(open(JSON))
    b = pub["ALL"]
    assert b["persistence_auc"] == 0.5
    assert abs(b["persistence_accuracy"] - (1 - b["base_rate_turn"])) < 1e-9


def test_whole_overlap_is_inside_the_forward_relevant_regime():
    pub = json.load(open(JSON))
    assert pub["ALL"]["n_decisions"] == pub["REGIME_2026-08-19_ON"]["n_decisions"]
