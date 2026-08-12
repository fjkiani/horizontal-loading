"""Three-run test-evidence record for one submitted prompt.

Ground rule 8: the evidence must state exactly what the model did on three test
runs, with no cherry-picking, and reviewers open the chat share link. The
failure mode this schema exists to prevent is a prompt marked "evidenced" on
one good run with two blanks, so:

  * every slot must be filled before the record counts as evidenced;
  * a run recorded as correct must carry the model's verbatim answer, and that
    answer must actually match the expected one -- an assertion of correctness
    with no text behind it is refused;
  * every slot must carry its own share link, and the three links must differ,
    because one link pasted three times is one run reported three times;
  * runs cannot be deleted, only recorded -- `n_runs_recorded` is compared with
    `RUN_SLOTS` and any excess is reported rather than trimmed.

Nothing here judges difficulty. It records what happened.
"""
import re

RUN_SLOTS = 3
RUN_FIELDS = ("run_index", "model", "chat_share_url", "ran_at",
              "model_answer_verbatim", "correct", "notes")
SHARE_URL = re.compile(r"^https?://\S+$")


def _norm(s):
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def blank_run(i):
    return {"run_index": i, "model": "", "chat_share_url": "", "ran_at": "",
            "model_answer_verbatim": "", "correct": None, "notes": ""}


def blank_worksheet(trap, sign_off=None):
    """An empty, reviewable worksheet for one prompt."""
    facts = trap.get("facts") or {}
    return {
        "category": trap.get("category"),
        "field": trap.get("field"),
        "expected_answer": str(trap.get("answer") or ""),
        "prompt": trap.get("prompt"),
        "primary_operator": trap.get("primary_operator"),
        "source_operators": trap.get("source_operators"),
        "sources": trap.get("sources"),
        "landing_pages": facts.get("landing_pages"),
        "runs": [blank_run(i + 1) for i in range(RUN_SLOTS)],
        "sign_off": dict(sign_off or {}),
    }


def validate_evidence(ws):
    """Returns (evidenced, problems). `evidenced` is never a default."""
    problems = []
    runs = list(ws.get("runs") or [])
    filled = [r for r in runs if _norm(r.get("model_answer_verbatim"))
              or r.get("correct") is not None
              or str(r.get("chat_share_url") or "").strip()]
    if len(runs) != RUN_SLOTS:
        problems.append(f"R8 worksheet holds {len(runs)} run slots, not "
                        f"{RUN_SLOTS}")
    if len(filled) < RUN_SLOTS:
        problems.append(f"R8 only {len(filled)} of {RUN_SLOTS} run slots are "
                        "populated; a prompt cannot be marked evidenced on a "
                        "partial record")

    expected = _norm(ws.get("expected_answer"))
    links = []
    for r in runs:
        i = r.get("run_index")
        got = str(r.get("model_answer_verbatim") or "").strip()
        url = str(r.get("chat_share_url") or "").strip()
        if not str(r.get("model") or "").strip():
            problems.append(f"R8 run {i} does not name the model")
        if not url:
            problems.append(f"R8 run {i} has no chat share link; reviewers open "
                            "the link on every submission")
        elif not SHARE_URL.match(url):
            problems.append(f"R8 run {i} share link is not a URL: {url[:40]!r}")
        else:
            links.append(url)
        if r.get("correct") is None:
            problems.append(f"R8 run {i} does not record whether the model was "
                            "correct")
        if not got:
            problems.append(f"R8 run {i} does not quote the model's answer")
            continue
        matches = bool(expected) and expected in _norm(got)
        if r.get("correct") is True and not matches:
            problems.append(f"R8 run {i} is marked correct but its quoted "
                            f"answer {got[:40]!r} does not contain the expected "
                            "answer")
        if r.get("correct") is False and matches:
            problems.append(f"R8 run {i} is marked incorrect yet quotes the "
                            "expected answer")
    if links and len(set(links)) != len(links):
        problems.append("R8 the same chat share link appears on more than one "
                        "run; that is one run reported several times")
    return (not problems), problems


def summarise(ws):
    runs = list(ws.get("runs") or [])
    ok, problems = validate_evidence(ws)
    correct = sum(1 for r in runs if r.get("correct") is True)
    wrong = sum(1 for r in runs if r.get("correct") is False)
    return {
        "evidenced": ok,
        "problems": problems,
        "n_runs_recorded": sum(1 for r in runs
                               if str(r.get("chat_share_url") or "").strip()),
        "n_correct": correct,
        "n_incorrect": wrong,
        "stumped_all_runs": (ok and wrong == RUN_SLOTS),
        "models": sorted({str(r.get("model") or "").strip()
                          for r in runs if str(r.get("model") or "").strip()}),
        "note": ("stumped_all_runs records the outcome of these three runs "
                 "only; it is not an estimate of how any other model would do"),
    }
