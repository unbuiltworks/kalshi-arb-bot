"""P10-F: augment forward collection WITHOUT touching the protected holdout.

    python3 analysis/gemini/p10f_forward_aug.py [YYYY-MM-DD]

THE CONSTRAINT IS THE DESIGN. `analysis/gemini/forward/` is the sealed 09-23+
holdout. This module must not read it, must not write into it and must not
change what the existing collector puts there, or the holdout stops being one.
So the augmented stream lives in its own directory, `analysis/gemini/
forward_aug/`, and carries the two things the original stream cannot supply
after the fact:

  * the venue's `tid` and `timestampms` on every trade
  * a BOOK SNAPSHOT beside the trades, so a print can be decoded later

The original collector keeps writing exactly what it wrote before. The two
streams are joined by (symbol, tid) when and if the decoder is validated;
until then this is collection, not evidence.

READ-ONLY against the venue. A guard refuses to run if the path it is about to
write resolves inside the protected directory, and a test asserts this module
never opens it.
"""
import datetime as _dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
import p10a_capture as CAP                                     # noqa: E402

PROTECTED = os.path.join(HERE, "forward")
OUT = os.path.join(HERE, "forward_aug")


def _guard(path):
    p = os.path.abspath(path)
    if p.startswith(os.path.abspath(PROTECTED) + os.sep) or p == os.path.abspath(PROTECTED):
        raise SystemExit(f"REFUSED: {p} is inside the protected holdout")
    return p


def collect(day=None, minutes=50.0, n_symbols=6):
    day = day or _dt.datetime.utcnow().date().isoformat()
    os.makedirs(OUT, exist_ok=True)
    path = _guard(os.path.join(OUT, f"{day}.jsonl"))
    return path, CAP.capture(path, minutes, n_symbols)


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else None
    mins = float(sys.argv[2]) if len(sys.argv) > 2 else 50.0
    p, r = collect(d, mins)
    print(json.dumps({"path": p, **r}))
