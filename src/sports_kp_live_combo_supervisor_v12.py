"""Production combo supervisor v12: v11 plus strict NFL alias normalization."""
from src import sports_kp_mve_poly_nfl_aliases  # noqa: F401 - intentional runtime patch
from src.sports_kp_live_combo_supervisor_v11 import main, run  # noqa: F401

if __name__ == "__main__":
    main()
