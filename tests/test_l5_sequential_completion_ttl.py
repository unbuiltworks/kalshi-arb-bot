"""Regression for first-fill-anchored completion deadlines (research only)."""
import importlib.util
import pathlib
import unittest
from unittest import mock
from decimal import Decimal as D
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE = ROOT / "analysis/l5/issue208/sequential_completion_replay.py"


class CompletionTTLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("sequential_completion_replay", MODULE)
        cls.replay = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.replay)

    def test_full_budget_when_quote_immediate(self):
        frames = [{"t": 100.0}, {"t": 160.0}]
        self.assertEqual(self.replay.remaining_completion_ttl(frames, 0, 0, 60), 60)

    def test_quote_near_expiry_gets_only_remaining_time(self):
        frames = [{"t": 100.0}, {"t": 159.0}, {"t": 161.0}]
        self.assertEqual(self.replay.remaining_completion_ttl(frames, 0, 1, 60), 1)
        # An order at t=159 cannot claim a fill observed at t=161.
        self.assertLess(159 + self.replay.remaining_completion_ttl(frames, 0, 1, 60), 161)

    def test_exact_boundary_has_no_future_runway(self):
        frames = [{"t": 100.0}, {"t": 220.0}, {"t": 221.0}]
        self.assertEqual(self.replay.remaining_completion_ttl(frames, 0, 1, 120), 0.0)

    def test_no_ttl_still_means_end_of_recording(self):
        self.assertIsNone(self.replay.remaining_completion_ttl(
            [{"t": 100.0}, {"t": 200.0}], 0, 1, None))

    def test_expired_quote_never_regains_time(self):
        frames = [{"t": 100.0}, {"t": 180.0}]
        self.assertEqual(self.replay.remaining_completion_ttl(frames, 0, 1, 60), 0.0)


    def test_econ_replay_passes_unspent_deadline_to_queue_model(self):
        # Integration boundary: quote search uses 59 of 60 seconds, so
        # the actual completion queue must receive only one second.
        frames = [{"t": 100.0}, {"t": 159.0}, {"t": 161.0}]
        seen = []

        def leg(side, price, observed, *, start, queue_fraction,
                own_qty, ttl_s):
            seen.append((side, start, ttl_s))
            if start == 0:
                return {"fill_index": 0 if side == self.replay.UP else None,
                        "verdict": "PROVEN" if side == self.replay.UP else "IMPOSSIBLE"}
            return {"fill_index": None, "verdict": "IMPOSSIBLE"}

        def filled(result, *, arm):
            return result["fill_index"] is not None

        with tempfile.TemporaryDirectory() as root:
            pathlib.Path(root, "book.json").touch()
            with mock.patch.object(self.replay.QS, "load",
                                   return_value={"market": frames}), \
                 mock.patch.object(self.replay.QS, "touches",
                                   return_value=(D("0.50"), D("0.51"))), \
                 mock.patch.object(self.replay.QS, "leg_outcome",
                                   side_effect=leg), \
                 mock.patch.object(self.replay.QS, "filled",
                                   side_effect=filled), \
                 mock.patch.object(self.replay, "_econ_gate",
                                   return_value=(1, D("0.49"), "TEST")):
                rows = self.replay.sequential(
                    [("book.json", "test")], entry_depth=0,
                    complete_depth=0, scenario=("base", "PROVEN", D("0.5")),
                    completion_ttl_s=60.0, root=root)
        self.assertTrue(any(r["frame"] == 0 for r in rows))
        self.assertIn((self.replay.DOWN, 1, 1.0), seen)
        self.assertNotIn((self.replay.DOWN, 1, 60.0), seen)


    def test_econ_gate_cannot_search_past_first_fill_deadline(self):
        # The first worthwhile quote arrives after expiry; never grant it
        # a new queue TTL or credit a counterfactual pair.
        frames = [{"t": 100.0}, {"t": 161.0}, {"t": 162.0}]
        with mock.patch.object(self.replay.QS, "touches",
                               side_effect=[(D("0.44"), D("0.45")), (D("0.49"), D("0.50"))]):
            k, own, reason = self.replay._econ_gate(
                self.replay.DOWN, D("0.50"), D(1), frames, 0, 0, 60.0)
        self.assertEqual(k, 1)
        self.assertIsNone(own)
        self.assertEqual(reason, "TTL_BEFORE_A_WORTHWHILE_COMPLEMENT")

    def test_econ_gate_rejects_quote_exactly_at_expiry(self):
        # No time remains to place and rest a maker order at the deadline.
        frames = [{"t": 100.0}, {"t": 160.0}, {"t": 161.0}]
        with mock.patch.object(self.replay.QS, "touches",
                               side_effect=[None]):
            k, own, reason = self.replay._econ_gate(
                self.replay.DOWN, D("0.50"), D(1), frames, 0, 0, 60.0)
        self.assertEqual(k, 1)
        self.assertIsNone(own)
        self.assertEqual(reason, "TTL_BEFORE_A_WORTHWHILE_COMPLEMENT")

    def test_econ_gate_quote_with_one_second_left(self):
        frames = [{"t": 100.0}, {"t": 159.0}, {"t": 161.0}]
        with mock.patch.object(self.replay.QS, "touches",
                               side_effect=[None, (D("0.49"), D("0.50"))]):
            k, own, reason = self.replay._econ_gate(
                self.replay.DOWN, D("0.50"), D(1), frames, 0, 0, 60.0)
        self.assertEqual((k, own, reason),
                         (1, D("0.50"), "PAIR_BEATS_CARRY_MODELLED"))
        self.assertEqual(self.replay.remaining_completion_ttl(
            frames, 0, k, 60.0), 1.0)


    def test_entry_order_fills_report_staggered_timing(self):
        # Both entry orders can fill, but at DIFFERENT frames. This must
        # not be counted as a sequential rescue or a simultaneous fill.
        frames = [{"t": 100.0}, {"t": 106.0}, {"t": 112.0}]
        def leg(side, price, observed, *, start, queue_fraction,
                own_qty, ttl_s):
            return {"fill_index": 1 if side == self.replay.UP else 2,
                    "verdict": "PROVEN"}
        with tempfile.TemporaryDirectory() as root:
            pathlib.Path(root, "book.json").touch()
            with mock.patch.object(self.replay.QS, "load",
                                   return_value={"market": frames}), \
                 mock.patch.object(self.replay.QS, "touches",
                                   return_value=(D("0.49"), D("0.50"))), \
                 mock.patch.object(self.replay.QS, "leg_outcome",
                                   side_effect=leg), \
                 mock.patch.object(self.replay.QS, "filled",
                                   side_effect=lambda result, *, arm: True):
                rows = self.replay.sequential(
                    [("book.json", "test")], entry_depth=0,
                    complete_depth=0, scenario=("base", "PROVEN", D("0.5")),
                    root=root)
        row = next(r for r in rows if r["frame"] == 0)
        self.assertEqual(row["state"], "PAIR_AT_ENTRY")
        self.assertEqual((row["up_fill_frame"], row["down_fill_frame"]), (1, 2))
        self.assertFalse(row["entry_fills_simultaneous"])


    def test_unknown_entry_verdict_is_visible_without_invented_pnl(self):
        # A missing queue verdict is not a known no-fill or a settled loss.
        frames = [{"t": 100.0}, {"t": 106.0}, {"t": 112.0}]
        def leg(side, price, observed, *, start, queue_fraction,
                own_qty, ttl_s):
            return {"verdict": None if side == self.replay.UP else "IMPOSSIBLE",
                    "fill_index": None, "proven": False, "possible": False}
        with tempfile.TemporaryDirectory() as root:
            pathlib.Path(root, "book.json").touch()
            with mock.patch.object(self.replay.QS, "load",
                                   return_value={"market": frames}), \
                 mock.patch.object(self.replay.QS, "touches",
                                   return_value=(D("0.49"), D("0.50"))), \
                 mock.patch.object(self.replay.QS, "leg_outcome",
                                   side_effect=leg):
                rows = self.replay.sequential(
                    [("book.json", "test")], entry_depth=0,
                    complete_depth=0, scenario=("base", "PROVEN", D("0.5")),
                    root=root)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["state"] == "INDETERMINATE_ENTRY" for r in rows))
        self.assertTrue(all(r["pnl"] is None for r in rows))
        summary = self.replay.summarise(rows)
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["sequential"], 0)
        self.assertEqual(summary[0]["unscored"], 2 if rows[0]["state"] == "INDETERMINATE_ENTRY" else 1)
        self.assertEqual(summary[0]["completion_rate"], "N/A")
        self.assertIsNone(summary[0]["complete_pnl"])


    def test_unknown_complement_verdict_is_not_scored_as_residual(self):
        frames = [{"t": 100.0}, {"t": 106.0}, {"t": 112.0}]
        def leg(side, price, observed, *, start, queue_fraction,
                own_qty, ttl_s):
            if start == 0:
                return {"fill_index": 0 if side == self.replay.UP else None,
                        "verdict": "PROVEN" if side == self.replay.UP else "IMPOSSIBLE"}
            return {"fill_index": None, "verdict": None}
        def filled(result, *, arm):
            return (None if result["verdict"] is None
                    else result["fill_index"] is not None)
        with tempfile.TemporaryDirectory() as root:
            pathlib.Path(root, "book.json").touch()
            with mock.patch.object(self.replay.QS, "load",
                                   return_value={"market": frames}), \
                 mock.patch.object(self.replay.QS, "touches",
                                   return_value=(D("0.49"), D("0.50"))), \
                 mock.patch.object(self.replay.QS, "leg_outcome",
                                   side_effect=leg), \
                 mock.patch.object(self.replay.QS, "filled",
                                   side_effect=filled), \
                 mock.patch.object(self.replay, "_econ_gate",
                                   return_value=(0, D("0.50"), "TEST")):
                rows = self.replay.sequential(
                    [("book.json", "test")], entry_depth=0,
                    complete_depth=0, scenario=("base", "PROVEN", D("0.5")),
                    root=root)
        row = next(r for r in rows if r["frame"] == 0)
        self.assertEqual(row["state"], "INDETERMINATE_COMPLEMENT")
        self.assertIsNone(row["pnl"])
        summary = self.replay.summarise(rows)
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["sequential"], 0)
        self.assertEqual(summary[0]["unscored"], 2 if rows[0]["state"] == "INDETERMINATE_ENTRY" else 1)
        self.assertEqual(summary[0]["completion_rate"], "N/A")
        self.assertIsNone(summary[0]["complete_pnl"])


    def test_summary_discloses_unscored_complement_alongside_scored_cases(self):
        common = {"scenario": "base", "complete_depth": 0,
                  "completion_ttl_s": 60.0}
        rows = [dict(common, state="PAIR_SEQUENTIAL", pair_cost="0.97",
                     pnl="0.03", hold_pnl="-0.02", reduce_pnl="-0.04"),
                dict(common, state="INDETERMINATE_COMPLEMENT", pnl=None)]
        summary = self.replay.summarise(rows)
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0]["sequential"], 1)
        self.assertEqual(summary[0]["unscored"], 1)
        self.assertEqual(summary[0]["pairs"], 1)
        self.assertEqual(summary[0]["complete_pnl"], "0.0300")



if __name__ == "__main__":
    unittest.main()
