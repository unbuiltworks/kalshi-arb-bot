"""Production combo supervisor v15: strict Polymarket NFL spreads + totals."""
from src import sports_kp_mve_poly_nfl_aliases  # noqa: F401
from src import sports_kp_mve_poly_nfl_semantics  # noqa: F401
from src import sports_kp_mve_poly_nfl_totals  # noqa: F401
from src.sports_kp_live_combo_supervisor_v11 import main, run  # noqa: F401

if __name__ == "__main__":
    main()
