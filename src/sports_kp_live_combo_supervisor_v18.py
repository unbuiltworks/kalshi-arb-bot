"""Production combo supervisor v18: NFL + MLB games + MLB props.

Combines v17 family-fair event discovery with the composite cross-venue preprice
hook. NFL keeps the proven Polymarket orientation logic; MLB full-game markets use
the strict three-venue snapshot normalizer; MLB props use exact player/line/game
matching. External prices influence manufacture order only. Live execution gates
remain unchanged and fail closed.
"""
from src import sports_kp_live_combo_supervisor_v17 as v17
from src import sports_kp_mve_combo_generator_v2 as generator_v2
from src import sports_kp_mve_mlb_preprice as mlb_preprice

# Replace v16's NFL-only hook with the composite hook. Candidate ranking still
# reads the same side-specific cache populated by the NFL module and extended by
# the MLB module.
generator_v2._candidate_preprice_hook = mlb_preprice.preprice_hook

run = v17.run
main = v17.main

if __name__ == "__main__":
    main()
