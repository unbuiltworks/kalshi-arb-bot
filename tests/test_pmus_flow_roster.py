"""The roster must select on flow measured BEFORE the window, and never rank
an unmeasured market as a quiet one.

Every test here corresponds to a failure this campaign actually made, or to
the one it is specifically designed to prevent. The flow-blind run of
2026-09-09 polled ten county cricket matches at a correct 5 s cadence and
produced 4 opportunities in 2,136 observations; the obvious fix -- rank on
everything you have -- would have introduced a look-ahead instead.
"""

import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import pmus_flow_roster as fr


def flow(intervals=8, prints=4, spm=100.0, two_sided=True, decided=True):
    return {"intervals": intervals, "prints_seen": prints,
            "shares_per_min": spm, "two_sided": two_sided, "gaps": 0,
            "two_sided_resolvable": decided,
            "sided_prints": 2 if decided else 1}


def run_survey(r, flows=None):
    """Drive a roster through one complete survey pass and return the pin row."""
    row = None
    for _ in range(len(fr.batches(r.universe, r.roster_size))
                   * r.survey_cycles_per_batch):
        row = r.advance(flows) or row
    return row


class ActivityScore(unittest.TestCase):

    def test_unmeasured_is_none_not_zero(self):
        """A market with too few intervals is UNKNOWN, not quiet.

        Scoring it 0.0 would sort it below every measured market, which reads
        as a decision ("we looked, it was quiet") that was never made.
        """
        for n in range(fr.MIN_SURVEY_SAMPLES):
            self.assertIsNone(fr.activity_score(flow(intervals=n)),
                              "intervals=%d must be unmeasured" % n)
        self.assertIsNotNone(
            fr.activity_score(flow(intervals=fr.MIN_SURVEY_SAMPLES)))

    def test_unmeasured_never_outranks_a_measured_quiet_market(self):
        flows = {"unmeasured": flow(intervals=1, prints=1, spm=9999.0),
                 "quiet": flow(intervals=9, prints=0, spm=0.0,
                               two_sided=False)}
        ranked = fr.rank_by_flow(flows)
        self.assertEqual([r["slug"] for r in ranked], ["quiet"])

    def test_two_sided_multiplies_so_one_sided_cannot_buy_the_top(self):
        """One-sided volume must not outrank a quieter two-sided tape.

        The complement is on the other side of the book; a market that only
        ever prints at one touch is not a market we can pair in. The penalty
        therefore has to MULTIPLY -- added as a term it is just another point
        of flow, and a loud one-sided market buys its way past it.
        """
        loudest_one_sided = flow(intervals=8, prints=8,
                                 spm=fr.SHARES_PER_MIN_SCALE,
                                 two_sided=False)
        quieter_two_sided = flow(intervals=10, prints=8, spm=0.0,
                                 two_sided=True)
        self.assertLess(fr.activity_score(loudest_one_sided),
                        fr.activity_score(quieter_two_sided))
        # and, holding flow equal, two-sided always wins
        self.assertLess(fr.activity_score(flow(two_sided=False)),
                        fr.activity_score(flow(two_sided=True)))

    def test_undecided_two_sidedness_is_not_penalised_as_one_sided(self):
        """Measured on the first live pin, and it made the term useless.

        At 4 survey intervals not one of 18 scored markets had the two prints
        needed to decide two-sidedness, so every market took the one-sided
        penalty. A multiplier every row shares is not a discriminator; it is
        an absent term that looks like a present one. Undecided must cost
        nothing, and the pin row must say how many rows decided it.
        """
        undecided = flow(prints=1, spm=0.0, two_sided=False, decided=False)
        known_one_sided = flow(prints=1, spm=0.0, two_sided=False,
                               decided=True)
        self.assertGreater(fr.activity_score(undecided),
                           fr.activity_score(known_one_sided))
        # ...and undecided is scored exactly as a decided two-sided market
        self.assertEqual(fr.activity_score(undecided),
                         fr.activity_score(flow(prints=1, spm=0.0,
                                                two_sided=True)))

    def test_the_ranking_row_reports_two_sidedness_as_tri_state(self):
        ranked = fr.rank_by_flow({
            "undecided": flow(prints=1, decided=False),
            "yes": flow(two_sided=True), "no": flow(two_sided=False)})
        by = {r["slug"]: r for r in ranked}
        self.assertIsNone(by["undecided"]["two_sided"])
        self.assertIs(by["yes"]["two_sided"], True)
        self.assertIs(by["no"]["two_sided"], False)

    def test_a_pin_that_could_not_decide_two_sidedness_says_so(self):
        r = fr.Roster(roster_size=2, pin_cycles=4)
        r.set_universe(["a", "b"])
        row = run_survey(r, {s: flow(prints=1, decided=False)
                             for s in ("a", "b")})
        self.assertEqual(row["kind"], "roster_pinned")
        self.assertEqual(row["two_sidedness_decided_on"], 0)

    def test_more_prints_scores_higher(self):
        self.assertGreater(fr.activity_score(flow(prints=8)),
                           fr.activity_score(flow(prints=1)))

    def test_shares_term_is_bounded_and_cannot_dominate(self):
        """One enormous print must not pin a market that never trades again."""
        huge = fr.activity_score(flow(intervals=8, prints=1, spm=10 ** 9))
        busy = fr.activity_score(flow(intervals=8, prints=8, spm=0.0))
        self.assertGreaterEqual(busy, huge)

    def test_missing_fields_do_not_crash_or_inflate(self):
        self.assertIsNone(fr.activity_score({}))
        s = fr.activity_score({"intervals": 8})
        self.assertEqual(s, 0.0 * fr.ONE_SIDED_PENALTY)

    def test_ranking_is_deterministic_on_ties(self):
        flows = {c: flow() for c in "zyxw"}
        self.assertEqual([r["slug"] for r in fr.rank_by_flow(flows)],
                         ["w", "x", "y", "z"])


class NoLookAhead(unittest.TestCase):
    """The property the owner named: ranking uses flow from BEFORE the window."""

    def _pinned(self, universe=("a", "b", "c"), flows=None):
        r = fr.Roster(roster_size=2, pin_cycles=6)
        r.set_universe(universe)
        row = run_survey(r, flows or {s: flow() for s in universe})
        return r, row

    def test_no_window_opens_during_the_survey_phase(self):
        r = fr.Roster(roster_size=2)
        r.set_universe(["a", "b", "c"])
        self.assertEqual(r.phase, fr.SURVEY)
        for slug in ("a", "b", "c"):
            self.assertFalse(r.eligible_for_window(slug))

    def test_a_market_cannot_enter_a_window_without_a_prior_estimate(self):
        """An unsurveyed slug is refused even while the roster is observing."""
        flows = {"a": flow(prints=9), "b": flow(prints=8), "c": flow(prints=7)}
        r, _ = self._pinned(("a", "b", "c"), flows)
        self.assertEqual(r.phase, fr.PINNED)
        self.assertTrue(r.eligible_for_window("a"))
        self.assertFalse(r.eligible_for_window("c"),
                         "unpinned market must not open a window")
        self.assertFalse(r.eligible_for_window("never_seen"))

    def test_flow_during_the_window_cannot_change_the_roster(self):
        """The frozen scores are frozen.

        This is the look-ahead the two-phase design exists to prevent: if
        `advance` re-ranked on flow arriving during the pinned phase, the
        roster would be partly chosen for what happened inside the very
        windows being measured.
        """
        flows = {"a": flow(prints=9), "b": flow(prints=8),
                 "c": flow(intervals=fr.MIN_SURVEY_SAMPLES, prints=0,
                           spm=0.0, two_sided=False)}
        r, _ = self._pinned(("a", "b", "c"), flows)
        before = dict(r.pinned_scores)
        roster_before = list(r.roster)
        # 'c' erupts mid-window; it must not displace anyone
        loud = dict(flows, c=flow(intervals=99, prints=99, spm=10 ** 6))
        for _ in range(r.pin_cycles - 1):
            r.advance(loud)
        self.assertEqual(r.phase, fr.PINNED)
        self.assertEqual(r.roster, roster_before)
        self.assertEqual(r.pinned_scores, before)
        self.assertFalse(r.eligible_for_window("c"))

    def test_eligibility_cannot_see_live_flow_at_all(self):
        """Structural, not conventional: the method has no way to look.

        `eligible_for_window` takes a slug and nothing else, so no future
        edit can quietly start scoring current flow inside it -- the flow is
        not in scope. This is the strongest form of the owner's requirement.
        """
        import inspect
        params = list(inspect.signature(
            fr.Roster.eligible_for_window).parameters)
        self.assertEqual(params, ["self", "slug"])

    def test_eligibility_reads_the_snapshot_not_a_live_recompute(self):
        """Eligibility must not be recoverable from current flow.

        Mutation guard: if `eligible_for_window` were rewritten to call
        `activity_score` on live flow, a pinned market whose flow later went
        quiet would lose eligibility mid-window. It must not.
        """
        r, _ = self._pinned()
        pinned = r.roster[0]
        r.advance({pinned: flow(intervals=0, prints=0, spm=0.0,
                                two_sided=False)})
        self.assertTrue(r.eligible_for_window(pinned))

    def test_the_pin_ranks_only_on_survey_flow_it_was_given(self):
        flows = {"quiet": flow(prints=0, spm=0.0, two_sided=False),
                 "busy": flow(prints=9, spm=500.0, two_sided=True)}
        r = fr.Roster(roster_size=1, pin_cycles=4)
        r.set_universe(["quiet", "busy"])
        row = run_survey(r, flows)
        self.assertEqual(row["kind"], "roster_pinned")
        self.assertEqual(r.roster, ["busy"])
        self.assertEqual([x["slug"] for x in row["ranking"]], ["busy"])

    def test_releasing_the_pin_clears_every_frozen_score(self):
        """A new pass must re-earn its roster; stale scores cannot carry over."""
        r, _ = self._pinned()
        pinned = list(r.roster)
        row = None
        for _ in range(r.pin_cycles):
            row = r.advance() or row
        self.assertEqual(row["kind"], "roster_survey_start")
        self.assertEqual(r.phase, fr.SURVEY)
        self.assertEqual(r.pinned_scores, {})
        for s in pinned:
            self.assertFalse(r.eligible_for_window(s))


class Rotation(unittest.TestCase):

    def test_a_pass_covers_every_market_exactly_once(self):
        universe = ["m%02d" % i for i in range(29)]
        r = fr.Roster(roster_size=12, pin_cycles=3)
        r.set_universe(universe)
        seen, n = [], 0
        while r.phase == fr.SURVEY and n < 500:
            seen.extend(r.current())
            r.advance({s: flow() for s in universe})
            n += 1
        self.assertEqual(sorted(set(seen)), sorted(universe))

    def test_no_cycle_polls_more_markets_than_the_rate_budget_allows(self):
        """12 is not a preference, it is floor(2.5 * poll_s) at 5 s."""
        universe = ["m%02d" % i for i in range(60)]
        r = fr.Roster(roster_size=12, pin_cycles=3)
        r.set_universe(universe)
        for _ in range(80):
            self.assertLessEqual(len(r.current()), 12)
            r.advance({s: flow() for s in universe})

    def test_each_batch_is_polled_long_enough_to_be_measurable(self):
        """A batch must get MIN_SURVEY_SAMPLES+1 consecutive polls, or every
        market in it is unmeasured and the pin has nothing to rank."""
        self.assertGreater(fr.SURVEY_CYCLES_PER_BATCH, fr.MIN_SURVEY_SAMPLES)
        r = fr.Roster(roster_size=2)
        r.set_universe(["a", "b", "c", "d"])
        first = r.current()
        for _ in range(fr.SURVEY_CYCLES_PER_BATCH - 1):
            r.advance({})
            self.assertEqual(r.current(), first)
        r.advance({})
        self.assertNotEqual(r.current(), first)

    def test_an_unmeasurable_universe_surveys_again_instead_of_pinning(self):
        """Never pin an arbitrary slate. That is the flow-blind bug."""
        universe = ["a", "b", "c"]
        r = fr.Roster(roster_size=2, pin_cycles=3)
        r.set_universe(universe)
        row = run_survey(r, {s: flow(intervals=1) for s in universe})
        self.assertEqual(row["kind"], "roster_survey_empty")
        self.assertEqual(r.phase, fr.SURVEY)
        self.assertEqual(r.roster, [])

    def test_pin_expiry_forces_a_fresh_wide_discovery(self):
        r, _ = fr.Roster(roster_size=2, pin_cycles=3), None
        r.set_universe(["a", "b", "c"])
        run_survey(r, {s: flow() for s in ("a", "b", "c")})
        for _ in range(r.pin_cycles):
            r.advance()
        self.assertTrue(r.needs_universe(),
                        "a new pass must rediscover the live universe")

    def test_batches_partition_without_loss_or_duplication(self):
        xs = [str(i) for i in range(25)]
        bs = fr.batches(xs, 7)
        self.assertEqual([x for b in bs for x in b], xs)
        self.assertEqual(fr.batches(xs, 0), [])
        self.assertEqual(fr.batches([], 5), [])

    def test_set_universe_deduplicates_and_preserves_order(self):
        r = fr.Roster()
        r.set_universe(["b", "a", "b", "c", "a"])
        self.assertEqual(r.universe, ["b", "a", "c"])

    def test_empty_universe_polls_nothing_and_does_not_advance_forever(self):
        r = fr.Roster()
        self.assertEqual(r.current(), [])
        self.assertIsNone(r.advance({}))
        self.assertEqual(r.phase, fr.SURVEY)


class NonTrading(unittest.TestCase):
    """This module selects markets. It must not be able to do anything else."""

    FORBIDDEN = ("post", "place_order", "submit", "cancel", "sign",
                 "private_key", "api_key", "secret", "urlopen", "requests")

    def test_module_source_contains_no_order_path(self):
        src = open(fr.__file__, encoding="utf-8").read()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef, ast.Module)):
                if (node.body and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)):
                    node.body[0].value.value = ""
        stripped = ast.unparse(tree).lower()
        for word in self.FORBIDDEN:
            self.assertNotIn(word, stripped,
                             "%r must not appear outside prose" % word)

    def test_module_imports_no_transport(self):
        tree = ast.parse(open(fr.__file__, encoding="utf-8").read())
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module.split(".")[0])
        self.assertEqual(mods - {"typing"}, set(),
                         "roster selection needs nothing but typing")


if __name__ == "__main__":
    unittest.main()
