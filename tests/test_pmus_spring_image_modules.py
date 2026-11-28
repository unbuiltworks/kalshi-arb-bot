"""The pmus-spring-l5 image copies src/ files one by one. Every src module
referenced (module scope or lazily inside a function) by a copied src file
must itself be copied. LIVE 2026-11-27 12:47Z: release 1c1d546b6 shipped
without pmus_duration_rank / pmus_reward_calibration / pmus_campaign_economics
and the loop raised ImportError once their flags were on."""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCKERFILE = os.path.join(ROOT, "deploy", "pmus_spring_l5", "Dockerfile")


def _copied():
    text = open(DOCKERFILE).read()
    return {os.path.basename(s)[:-3]
            for s in re.findall(r"^COPY (src/\w+\.py) ", text, re.M)}


def test_every_referenced_src_module_is_in_the_image():
    copied = _copied()
    missing = {}
    for mod in sorted(copied):
        text = open(os.path.join(ROOT, "src", mod + ".py")).read()
        refs = {a or b for a, b in
                re.findall(r"from src(?:\.(\w+))? import (\w+)", text)}
        refs |= set(re.findall(r"import src\.(\w+)", text))
        for r in refs:
            if os.path.exists(os.path.join(ROOT, "src", r + ".py")) and \
                    r not in copied:
                missing.setdefault(r, []).append(mod)
    assert not missing, missing
