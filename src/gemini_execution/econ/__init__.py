"""GEMINI_ECON_V1 -- Gemini-specific economics beside the frozen L5 control.

Nothing in this package decides what L5 would do. It decides whether what L5
asked for is worth doing ON THIS VENUE AT THIS SIZE, and every divergence it
creates is recorded against the control's own answer so the two axes -- L5
fidelity and Gemini profitability -- never get conflated into one number.

No module here holds a session, a signer or a submit path.
"""
