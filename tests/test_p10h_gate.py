"""The gate scorer decides whether the Gemini lane continues, so its arithmetic
is tested against closed forms it does not share.

The bound is the sensitive number: G1 and G4 are the same statement, and a
bisection that drifted by a percentage point would move the required n by a
hundred rows -- three hours of capture -- in either direction.
"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "analysis", "gemini"))

import p10h_gate as G                                          # noqa: E402


def test_zero_successes_matches_the_closed_form():
    """For k = 0 the exact bound is 1 - alpha^(1/n), with no bisection needed."""
    for n in (10, 50, 149, 150, 1000):
        want = 1 - 0.05 ** (1.0 / n)
        assert abs(G.binom_upper(0, n) - want) < 1e-6, n


def test_rule_of_three():
    """The familiar 3/n approximation, which is what k = 0 should look like."""
    assert abs(G.binom_upper(0, 300) - 3.0 / 300) < 2e-4


def test_bound_solves_its_own_defining_equation():
    """P(X <= k | n, p) = 0.05 at the returned p, checked by direct summation
    rather than by re-running the solver."""
    for k, n in ((1, 36), (1, 236), (2, 313), (5, 400)):
        p = G.binom_upper(k, n)
        cdf = sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i)
                  for i in range(k + 1))
        assert abs(cdf - 0.05) < 1e-6, (k, n, cdf)


def test_the_required_n_table_in_the_gate_document():
    """The numbers written into P10_DECODER_GATE.md, recomputed here."""
    for k, n in ((0, 149), (1, 236), (2, 313), (3, 386), (4, 456)):
        assert G.binom_upper(k, n) <= 0.02, (k, n)
        assert G.binom_upper(k, n - 1) > 0.02, (k, n)


def test_more_successes_never_lowers_the_bound():
    assert all(G.binom_upper(k, 200) < G.binom_upper(k + 1, 200)
               for k in range(0, 8))


def test_empty_sample_admits_everything():
    assert G.binom_upper(0, 0) == 1.0


def test_thresholds_match_the_document():
    """The scorer's constants against the document that pre-registered them."""
    doc = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "analysis", "gemini",
        "P10_DECODER_GATE.md")).read()
    assert "| **G1** | decoded n" in doc and "**≥ 150**" in doc
    assert G.G1_MIN_DECODED == 150
    assert G.G2_MAX_AMBIGUOUS == 0.25
    assert G.G3_MIN_AGREEMENT == 0.98
    assert G.G4_MAX_H2_UPPER == 0.02
    assert G.G5_MIN_STRUCTURAL == 0.80


def test_a_decided_label_outranks_a_refused_one():
    """Two recorders seeing one print keep the better-observed reading."""
    assert G._rank("H1") < G._rank("AMBIGUOUS_BOTH") < G._rank("STALE")
    assert G._rank("STALE") < G._rank("PRE_CAPTURE")


def test_scorer_places_no_orders():
    """No credential, no mutation path, anywhere in the scorer."""
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "analysis", "gemini",
        "p10h_gate.py")).read()
    for bad in ("place_order", "new_order", "cancel_order", "/v1/order",
                "createOrder", "submit_order", "api_key", "api_secret",
                "private_rest", "urlopen", "requests.post"):
        assert bad not in src, bad
