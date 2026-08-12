"""Pins for the clone-detection metrics, including their measured limits.

Three properties are asserted here, two of them positive and one negative. The
negative one matters most: it records, as an executable assertion, that the text
similarity gates do NOT stop an adversarial rewording, so that a later reader
cannot mistake them for a paraphrase defence.
"""
from __future__ import annotations

import itertools
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import source_gate as sg  # noqa: E402

CATALOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "web", "public", "catalog.json")


def _live_traps():
    if not os.path.exists(CATALOG):
        pytest.skip("catalog.json not baked")
    with open(CATALOG) as fh:
        return [t for t in json.load(fh).get("traps", []) if t.get("prompt")]


# --------------------------------------------------------------------------
# 1. symmetry
# --------------------------------------------------------------------------
def test_prompt_similarity_is_symmetric():
    """The defect: difflib junks tokens in the SECOND argument only.

    Measured before the fix, over the 153 pairs in the catalogue plus the
    retired arXiv baseline: 136 pairs disagreed by argument order, maximum
    absolute gap 0.1151, worst multiplicative disagreement 5.29x. The same four
    prompts scored 0.1375 from one call site and 0.0405 from another.
    """
    traps = _live_traps()
    worst = 0.0
    for a, b in itertools.combinations(traps, 2):
        ab = sg.prompt_similarity(a["prompt"], b["prompt"])
        ba = sg.prompt_similarity(b["prompt"], a["prompt"])
        worst = max(worst, abs(ab - ba))
    assert worst == 0.0, f"prompt_similarity asymmetric by {worst}"


def test_content_similarity_is_symmetric():
    traps = _live_traps()
    for a, b in itertools.combinations(traps, 2):
        assert (sg.content_similarity(a["prompt"], b["prompt"])
                == sg.content_similarity(b["prompt"], a["prompt"]))


# --------------------------------------------------------------------------
# 2. the thresholds sit in the measured voids
# --------------------------------------------------------------------------
def test_thresholds_sit_inside_measured_void():
    """A threshold outside its calibration band refuses real work or admits clones."""
    assert sg.CLONE_BAND_NON_CLONE_MAX < sg.CLONE_SIMILARITY_THRESHOLD \
        < sg.CLONE_BAND_CLONE_MIN
    assert sg.CLONE_CONTENT_BAND_NON_CLONE_MAX < sg.CLONE_CONTENT_THRESHOLD \
        < sg.CLONE_CONTENT_BAND_CLONE_MIN


def test_live_sci_prompts_are_below_both_thresholds():
    """The four S&T heads must not trip either gate against each other."""
    sci = [t for t in _live_traps() if t.get("category") == "science and technology"]
    if len(sci) < 2:
        pytest.skip("fewer than two S&T traps baked")
    for a, b in itertools.combinations(sci, 2):
        c = sg.prompt_similarity(a["prompt"], b["prompt"])
        k = sg.content_similarity(a["prompt"], b["prompt"])
        assert c < sg.CLONE_SIMILARITY_THRESHOLD, (a["answer"], b["answer"], c)
        assert k < sg.CLONE_CONTENT_THRESHOLD, (a["answer"], b["answer"], k)


def test_content_metric_separates_better_than_character_metric():
    """Content similarity must beat the character metric on the shipped slate.

    Measured: the four heads reach 0.5404 on characters (shared house grammar)
    but only 0.0732 on content. If that ordering ever reverses, the scaffolding
    word list has stopped doing its job.
    """
    sci = [t for t in _live_traps() if t.get("category") == "science and technology"]
    if len(sci) < 2:
        pytest.skip("fewer than two S&T traps baked")
    pairs = list(itertools.combinations(sci, 2))
    max_char = max(sg.prompt_similarity(a["prompt"], b["prompt"]) for a, b in pairs)
    max_cont = max(sg.content_similarity(a["prompt"], b["prompt"]) for a, b in pairs)
    assert max_cont < max_char


# --------------------------------------------------------------------------
# 3. THE NEGATIVE RESULT: text gates do not stop a rewording; structure does
# --------------------------------------------------------------------------
REWORD_OF_RFC_QUESTION = (
    "Among every Request for Comments document whose publication month is "
    "recorded as February 2012 in the official index of that series, one single "
    "document is longer, measured in pages, than all the rest. Which RFC number "
    "belongs to it? State only the digits.")


def _rfc_head():
    for t in _live_traps():
        if str(t.get("answer")) == "6513":
            return t
    pytest.skip("RFC head 6513 not in catalogue")


def test_text_gates_do_not_catch_an_adversarial_rewording():
    """Documented limitation, asserted so it cannot be quietly overclaimed.

    Measured over 12 hand-written rewordings, different-question MAX against
    reworded-clone MIN: jaccard 0.0732/0.0732, overlap 0.1429/0.1364, dice
    0.1364/0.1364, character 0.5404/0.2156. Every band is zero-width or
    negative, so no threshold separates the populations. This test will start
    failing the day someone finds a lexical metric that does -- which is the
    right time to revisit the gate.
    """
    base = _rfc_head()
    c = sg.prompt_similarity(base["prompt"], REWORD_OF_RFC_QUESTION)
    k = sg.content_similarity(base["prompt"], REWORD_OF_RFC_QUESTION)
    assert c < sg.CLONE_SIMILARITY_THRESHOLD
    assert k < sg.CLONE_CONTENT_THRESHOLD
    assert not sg.CLONE_CONTENT_CATCHES_REWORDING


def test_structural_gate_does_catch_the_rewording():
    """What actually carries the load, and why the negative result is tolerable.

    A rewording still queries the same collection and still answers in the same
    field, so it cannot escape the operator-overlap refusal or inflate
    effective_depth, whatever words it uses.
    """
    base = _rfc_head()
    twin = dict(base)
    twin["prompt"] = REWORD_OF_RFC_QUESTION
    twin["answer"] = "6513-reworded"

    viol, _ = sg.disjointness_violations(twin, [base], hard=True)
    assert any("shares operator" in v for v in viol), viol

    assert sg.effective_depth([base, twin]) == 1, \
        "a rewording must not inflate reported depth"


def test_four_real_heads_do_not_collapse():
    """The converse: genuinely different questions must keep their depth."""
    sci = [t for t in _live_traps() if t.get("category") == "science and technology"]
    if not sci:
        pytest.skip("no S&T traps baked")
    assert sg.effective_depth(sci) == len(sci)
