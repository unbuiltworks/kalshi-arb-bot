"""Deterministic research evidence gates; no live or modeled fill promotion."""
import unittest
from analysis.l5.issue208.venue_bound_route_b import (
    Unscorable, verified_first_fill, causal_book, settled_residual, paired_counterfactual, verified_pair_net
)
from decimal import Decimal

F = dict(market="m", order_id="o", fill_id="f", side="UP",
         qty="0.30", price="0.47", fee="0", timestamp="100")

class VenueBoundRouteBTests(unittest.TestCase):
    def test_real_residual_loses_if_opposite_wins(self):
        self.assertEqual(settled_residual(F, dict(market="m", settlement_id="s", winner="DOWN"), "m"), Decimal("-0.141"))
    def test_real_residual_wins_if_held_side_wins(self):
        self.assertEqual(settled_residual(F, dict(market="m", settlement_id="s", winner="UP"), "m"), Decimal("0.159"))
    def test_pair_over_rescue_ceiling_stays_negative(self):
        self.assertEqual(paired_counterfactual(F, "0.54", "0.002", "m"), Decimal("-0.005"))
    def test_causal_book_age_boundaries(self):
        self.assertEqual(causal_book(dict(observed_t="98"), "100"), Decimal("2"))
        for t in ("100.001", "97.999"):
            with self.assertRaises(Unscorable):
                causal_book(dict(observed_t=t), "100")
    def test_missing_fill_or_settlement_is_not_inferred(self):
        for key in ("order_id", "fill_id", "price", "fee", "timestamp"):
            f = dict(F); f.pop(key)
            with self.assertRaises(Unscorable):
                verified_first_fill(f, "m")
        with self.assertRaises(Unscorable):
            settled_residual(F, dict(market="m", winner="DOWN"), "m")
    def test_invalid_settlement_identifier_rejected(self):
        for invalid in (None, "", "  ", 0, 42, False):
            with self.assertRaises(Unscorable):
                settled_residual(F, dict(market="m", settlement_id=invalid, winner="DOWN"), "m")
    def test_verified_pair_net_uses_actual_fills_and_fees(self):
        opposite = dict(F, order_id="o2", fill_id="f2", side="DOWN",
                        price="0.48", fee="0.001", timestamp="101")
        self.assertEqual(verified_pair_net(F, opposite, "m"), Decimal("0.014"))
    def test_partial_or_invalid_complement_never_promotes_to_pair(self):
        good = dict(F, order_id="o2", fill_id="f2", side="DOWN",
                    price="0.48", fee="0", timestamp="101")
        for changes in (dict(qty="0.29"), dict(side="UP"),
                        dict(fill_id="f"), dict(order_id="o"),
                        dict(timestamp="99"), dict(market="other")):
            bad = dict(good, **changes)
            with self.assertRaises(Unscorable):
                verified_pair_net(F, bad, "m")
    def test_market_mismatch_rejected(self):
        with self.assertRaises(Unscorable):
            verified_first_fill(F, "other")
    def test_nonfinite_and_negative_quantities_rejected(self):
        for q in ("NaN", "-1", "0", "Infinity"):
            f = dict(F, qty=q)
            with self.assertRaises(Unscorable):
                verified_first_fill(f, "m")

if __name__ == "__main__":
    unittest.main()
