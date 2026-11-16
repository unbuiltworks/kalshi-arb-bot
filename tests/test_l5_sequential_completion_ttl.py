"""Regression for first-fill-anchored completion deadlines (research only)."""
import importlib.util
import pathlib
import unittest


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


if __name__ == "__main__":
    unittest.main()
