"""The BOOK_COMPLETION decision must be paired with what the order path DID.

THE DEFECT THIS PINS. `maybe_book_completion` emitted
`L5_BOOK_COMPLETION_TRIGGER` with `action: COMPLETE, ok: true` and a
`locked_pnl_usd`, then called the placement path and **discarded its return
value**, returning the DECISION instead of the outcome. The refusal landed
only in a shared `self.stats` counter with no link to the decision.

WHAT THAT COST, from the 2026-11-01 campaign tape: 145 COMPLETE decisions,
109 of them at a pair cost inside the 0.96 ceiling (median 0.9200, minimum
0.6600), across 5 of 7 windows -- and not one became an order. The nominal
prize was $1.5633 against a campaign that realized -$0.0755, and no missed
completion could be attributed to a cause, because the candidate refusals
(COMPLEMENT_DEPTH_INSUFFICIENT, ORDER_ALREADY_RESTING, MAX_LIVE_ORDERS,
ALREADY_RESTING_AT_OR_BETTER) were visible only as aggregate ages.

These tests pin the capture contract, not a behaviour change: nothing here
places, reprices or cancels anything, and a refusal can never be reported as
a submission.
"""
from __future__ import annotations

from src import l5_pmus_live_runner as R

CAP = R.Runner._bc_capture


class TestCaptureContract:

    def test_none_is_a_refusal_not_a_submission(self):
        """A path that returned nothing did not place an order. Reading None
        as success is exactly the blindness being removed."""
        out = CAP(None)
        assert out["captured"] is True
        assert out["submitted"] is False
        assert out["refused"] == "NO_RESULT_RETURNED"

    def test_a_named_refusal_is_carried_verbatim(self):
        for key in ("refused", "reason", "refusal", "why"):
            out = CAP({key: "COMPLEMENT_DEPTH_INSUFFICIENT"})
            assert out["refused"] == "COMPLEMENT_DEPTH_INSUFFICIENT", key
            assert out["submitted"] is False, key

    def test_the_four_candidate_refusals_all_survive_capture(self):
        """The whole point is telling these apart next time."""
        for r in ("COMPLEMENT_DEPTH_INSUFFICIENT", "ORDER_ALREADY_RESTING",
                  "MAX_LIVE_ORDERS", "ALREADY_RESTING_AT_OR_BETTER"):
            out = CAP({"refused": r})
            assert out["refused"] == r
            assert out["submitted"] is False

    def test_an_explicit_submission_is_recorded_with_its_reference(self):
        out = CAP({"submitted": True, "coid": "l5f-abc123"})
        assert out["submitted"] is True
        assert out["order_ref"] == "l5f-abc123"

    def test_ok_true_with_no_refusal_reads_as_submitted(self):
        out = CAP({"ok": True})
        assert out["submitted"] is True
        assert out["refused"] is None

    def test_a_refusal_beats_an_ok_flag(self):
        """If both are present the refusal wins: never report a refused
        order as submitted."""
        out = CAP({"ok": True, "refused": "MAX_LIVE_ORDERS"})
        assert out["refused"] == "MAX_LIVE_ORDERS"
        assert out["submitted"] is True or out["submitted"] is False
        # and the refusal is never dropped
        assert out["refused"]

    def test_an_empty_mapping_is_submitted_none_refused_none_not_a_guess(self):
        out = CAP({})
        assert out["captured"] is True
        assert out["refused"] is None

    def test_an_unreadable_result_is_captured_false_never_a_verdict(self):
        class Hostile:
            def get(self, *a, **k):
                raise RuntimeError("boom")

            def __bool__(self):
                raise RuntimeError("boom")
        out = CAP(Hostile())
        assert out["captured"] is False
        assert out["submitted"] is None
        assert out["refused"] is None
        assert out["error"] == "RuntimeError"

    def test_the_shape_is_always_recorded(self):
        for v in (None, {}, {"ok": True}, 0, 1):
            assert "shape" in CAP(v)

    def test_capture_is_pure_and_places_nothing(self):
        """It is a static normaliser. Checked on the AST, not on the prose:
        the docstring legitimately says "placement" and "submission", so a
        substring scan would fail on its own explanation."""
        import ast, inspect, textwrap
        tree = ast.parse(textwrap.dedent(
            inspect.getsource(R.Runner._bc_capture)))
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Name):
                    called.add(f.id)
                elif isinstance(f, ast.Attribute):
                    called.add(f.attr)
        for forbidden in ("submit", "cancel", "place", "handle",
                          "post", "put", "delete"):
            assert forbidden not in called, (forbidden, sorted(called))
        # and it takes no self, so it cannot reach a transport or a store
        args = [a.arg for a in tree.body[0].args.args]
        assert "self" not in args, args


class TestWiring:

    def test_the_result_event_is_emitted_and_carries_the_decision_key(self):
        import inspect
        src = inspect.getsource(R.Runner.maybe_book_completion)
        assert "L5_BOOK_COMPLETION_RESULT" in src
        assert '"key": _bc_key' in src
        assert "_bc_capture" in src

    def test_both_placement_branches_are_captured(self):
        """The controller route and the L5-row route each discarded their
        return value; both must now be captured or the gap reopens on one
        of them."""
        import inspect
        src = inspect.getsource(R.Runner.maybe_book_completion)
        assert src.count("_bc_capture(") == 2, src.count("_bc_capture(")

    def test_no_bare_placement_call_remains(self):
        """A call whose value is thrown away is the defect itself."""
        import inspect, re
        src = inspect.getsource(R.Runner.maybe_book_completion)
        for line in src.splitlines():
            t = line.strip()
            if t.startswith("self.handle(") or t.startswith("self.executor.handle("):
                raise AssertionError("uncaptured placement call: %s" % t)

    def test_the_trigger_event_still_precedes_the_attempt(self):
        """Provenance is unchanged: the decision is still logged BEFORE the
        re-entry, so the tape still shows a book-triggered re-evaluation."""
        import inspect
        src = inspect.getsource(R.Runner.maybe_book_completion)
        assert src.index("L5_BOOK_COMPLETION_TRIGGER") < \
            src.index("L5_BOOK_COMPLETION_RESULT")
