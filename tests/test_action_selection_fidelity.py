"""The action-selection semantics the FIDELITY artifact actually supports.

LIVE DEFECT, 2026-09-17 (diagnosed read-only on
aec-itfme-adrbar-fautab-2026-09-17): COMPLETE carried p=0.2563-0.3682 on 76 of
77 decisions and the production modal selector chose it 0 times. Not a
fallback, not a default, not execution: the argmax of a stochastic policy
cannot select a minority relation, ever, at any price, in any state.

These tests pin the semantics, not a preference.
"""
import collections

import pytest

from src import pmus_l5_fidelity_live as fid
from src import pmus_spring_l5_session as sess

# The live distribution, verbatim from the diagnostic.
LIVE_RANKED = [{"relation": "extend", "p": 0.6786},
               {"relation": "complete", "p": 0.3214},
               {"relation": "open", "p": 0.0}]
MODEL_SHA = "bbb55bf90638d2bd"
SLUG = "aec-itfme-adrbar-fautab-2026-09-17"


def _draws(n, ranked=LIVE_RANKED, slug=SLUG):
    return [fid.sample_relation(ranked, fid.seed_material(slug, i, MODEL_SHA))
            for i in range(n)]


# ---------------------------------------------------- the defect itself

def test_the_gate_scores_behavioral_fidelity_on_the_SAMPLED_draw():
    """THE evidence that settles which selector is supported. The frozen gate
    splits its own layers: behavioral is the sampled draw, executable is what
    survived the shell. The behavioral claim is therefore a claim about a
    stochastic policy, and modal cannot be the intended semantics."""
    import inspect
    src = inspect.getsource(sess) if False else None
    from src import l5_fidelity_gate as gate
    layers = inspect.getsource(gate.layers) if hasattr(gate, "layers") else \
        inspect.getsource(gate)
    assert "l1_sampled_vs_l5" in layers and "l1_acted_vs_l5" in layers
    # the behavioral layer is labelled as the model's own wanted action
    assert "what the frozen model wanted to do (sampled draw)" in layers
    assert "what reached the venue after the safety / economic shell" in layers


def test_modal_can_never_select_a_minority_relation():
    """Arithmetic, not opinion: argmax over a distribution whose mode is
    EXTEND returns EXTEND for every decision, forever."""
    modal = max(LIVE_RANKED, key=lambda r: r["p"])["relation"]
    assert modal == "extend"
    # there is no seed, no ordering, no state that changes this
    for _ in range(100):
        assert max(LIVE_RANKED, key=lambda r: r["p"])["relation"] == "extend"


def test_a_stochastic_policy_cannot_produce_zero_completes_over_many_decisions():
    """THE REGRESSION THE OWNER ASKED FOR.

    With P(complete)=0.3214, the chance of zero completes across 77 comparable
    decisions is 0.6786**77, about 4e-14. Observing zero is therefore proof
    that the selector is not sampling. This asserts the evidence-supported
    policy DOES complete, and quantifies how impossible the live record is
    under it."""
    n = 77                                    # the live decision count
    draws = _draws(n)
    completes = sum(1 for d in draws if d["sampled"] == "complete")
    assert completes > 0, "a sampling policy produced zero completes"
    # and it is near the mass, not a token few
    assert 0.15 < completes / n < 0.55, completes / n
    # the live modal record, for contrast, was exactly zero
    p_zero_under_sampling = 0.6786 ** n
    assert p_zero_under_sampling < 1e-12


def test_the_draw_is_deterministic_and_reproducible():
    """Same seed material -> same draw, every time and across processes. This
    is what makes live execution replayable rather than merely random."""
    a = _draws(40)
    b = _draws(40)
    assert [x["sampled"] for x in a] == [x["sampled"] for x in b]
    assert [x["u"] for x in a] == [x["u"] for x in b]


def test_every_draw_persists_seed_probability_and_chosen_relation():
    """An auditable draw has to carry its own evidence."""
    for d in _draws(10):
        assert d["seed"] and d["u"] is not None
        assert d["p"] is not None and d["rank"] in (1, 2, 3)
        assert d["sampled"] in ("extend", "complete", "open")


def test_the_seed_changes_with_the_decision_index_so_draws_are_not_constant():
    """A seed that did not move would give one relation forever -- the same
    defect with a different mechanism."""
    assert len({d["u"] for d in _draws(40)}) > 30


def test_draw_frequencies_track_the_distribution():
    """Calibration: over many decisions the draw reproduces the masses it was
    given, which is the whole point of sampling rather than arg-maxing."""
    n = 2000
    draws = [fid.sample_relation(LIVE_RANKED,
                                 fid.seed_material(f"m{i}", i, MODEL_SHA))
             for i in range(n)]
    freq = collections.Counter(d["sampled"] for d in draws)
    assert abs(freq["complete"] / n - 0.3214) < 0.05, freq
    assert abs(freq["extend"] / n - 0.6786) < 0.05, freq


# ------------------------------------------- the switch already exists

def test_the_stochastic_selector_is_already_implemented_and_owner_specified():
    """This is NOT new code. OWNER 5556232035 Phase 1 built the switch; it
    defaults to modal and was never turned on."""
    import inspect
    src = inspect.getsource(sess)
    assert sess.ACTION_SELECTION_ENV == "PMUS_SPRING_ACTION_SELECTION"
    assert 'os.getenv(ACTION_SELECTION_ENV, "modal")' in src
    assert 'elif selection == "stochastic" and draw.get("sampled"):' in src
    assert 'relation = str(draw["sampled"])' in src


def test_the_residual_cap_blocks_extend_but_NOT_complete():
    """Why modal deadlocks and sampling does not. At the cap a modal selector
    only ever proposes EXTEND, which the cap converts to HOLD forever; the
    position can never be reduced. COMPLETE is explicitly still allowed."""
    import ast, inspect, textwrap
    src = inspect.getsource(sess)
    i = src.index('f"residual_cap:{abs(inv.behavioral_residual())}"')
    window = src[i - 700:i]
    # STRUCTURAL, not prose: the cap branch is GUARDED on the relation being
    # extend, so a drawn COMPLETE is not captured by it. Matching the comment
    # would pass on a stale comment over changed code.
    assert 'relation == "extend"' in window
    # the guard must be a conjunction with the cap test, not the cap alone
    guard = window[window.index('relation == "extend"'):]
    assert "behavioral_residual()" in guard and "max_residual_units()" in guard
    # and COMPLETE must not appear as a capped relation anywhere in the branch
    assert 'relation == "complete"' not in window


def test_a_refused_action_becomes_HOLD_and_never_a_substituted_relation():
    """THE anti-silent-downgrade property. Every shell refusal in the decision
    path assigns hold + a reason. If a refused COMPLETE could become EXTEND,
    a fidelity signal would vanish into a different action with no record."""
    import inspect, re
    src = inspect.getsource(sess)
    # every `relation, refusal = X, "..."` in the decision path sets hold
    assigns = re.findall(r'relation,\s*refusal\s*=\s*([\"\w]+)\s*,', src)
    assert assigns, "no refusal assignments found -- pattern changed"
    for a in assigns:
        assert a.strip('"') == "hold", f"a refusal substituted {a!r}, not hold"


def test_every_shell_refusal_has_a_named_divergence_class():
    """FIDELITY_COMPLETE -> EXECUTION_REFUSED:<reason> is only auditable if the
    reason maps to a class. Pin the shells that can refuse a COMPLETE."""
    for key in ("safety_adaptation:complement_not_priceable",
                "safety_adaptation:complement_preview_rejected",
                "safety_adaptation:matched_pair_ceiling",
                "safety_adaptation:complement_acceptance_unproven",
                "venue_execution_adaptation:authenticated_book_unusable",
                "safety_adaptation:residual_cap"):
        assert key in sess.DIVERGENCE_CLASSES, key
    # and the defect itself is a named class, not an unclassified surprise
    assert sess.DIVERGENCE_CLASSES["behavioral:modal_argmax_vs_stochastic_draw"] \
        == "model deficiency (argmax execution of a stochastic policy)"


def test_executing_the_draw_is_classified_as_agreement_not_divergence():
    """Under the supported policy, executing the draw must not be logged as a
    mismatch -- otherwise the fidelity score would penalise correctness."""
    got = sess.divergence_reason(
        refusal=None, v2_relation="extend", acted="complete", sampled="complete")
    assert got == "none:stochastic_draw_executed"
    assert sess.DIVERGENCE_CLASSES[got] == "no disagreement"


def test_modal_disagreeing_with_the_draw_is_classified_as_the_defect():
    got = sess.divergence_reason(
        refusal=None, v2_relation="extend", acted="extend", sampled="complete")
    assert got == "behavioral:modal_argmax_vs_stochastic_draw"
