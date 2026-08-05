"""
batch_prompts.py — Project Seal batch (API-proof rebuild).

Every CLEAN prompt's ground truth is COMPUTED by join_engine over live data
(never declared). After the "model can just call those APIs" constraint, every
clean trap is API-PROOF: no single API call returns the answer.

Two verified API-proof trap classes:
  * VISION-TRUE (vertical): the answer is readable ONLY in a LOC scan IMAGE;
    the OCR text layer is corrupted and provably does NOT contain the answer.
    The solver payload is the image file, not text.
  * NIH RANKING (horizontal): the answer requires paginating past the 500/call
    cap, deduplicating by project_num, and sorting — the API silently ignores
    sort_field, so a single 'sorted' call returns unsorted rows.

Dropped classes (honest): SEC single-concept extremes (raw series is pre-sorted,
single call + min() solves it) and Nobel modern-country N+1 (city mappings are in
parametric memory). Both fail the API-proof / obscurity-floor constraints.

Structure per prompt:
    id, domain, method, intended, prompt, compute (callable->dict), exploit, sources, flaw
The compute() dict carries: answer, trace, payload, unique, n_base, survivors, api_proof.
verify_joins.py runs every compute(), asserts unique==True, answer present, api_proof==True.
"""
from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Callable
import join_engine as je

_REPO = os.path.dirname(os.path.abspath(__file__))


def _img(name):
    """Resolve a scan-image path relative to the repo root (portable across deploys)."""
    return os.path.join(_REPO, name)


@dataclass
class SealPrompt:
    id: str
    domain: str
    method: str           # 'horizontal' | 'vertical'
    intended: str         # 'clean' | 'flawed'
    prompt: str
    compute: Callable[[], dict]
    exploit: list
    sources: list
    flaw: str = ""


# ---------------------------------------------------------------------------
# VISION-TRUE trap definitions (resource_url, image, answer, OCR-forbidden, field)
# OCR-forbidden strings are asserted ABSENT from the OCR text layer (api-proof).
# ---------------------------------------------------------------------------
VISION = {
    "V01": dict(url="https://www.loc.gov/resource/sn83030214/1900-01-01/ed-1/?sp=1",
                img=_img("V01_tribune_scan.jpg"),
                answer="19405",
                forbidden=["19405", "19,405", "19.405", "NO. 19405"],
                field="issue number"),
    "V02": dict(url="https://www.loc.gov/resource/sn83045211/1922-03-06/ed-1/?sp=23",
                img=_img("V02_ledger_scan.jpg"),
                answer="148",
                forbidden=["148", "NO. 148", "MARCH 6", "TWO CENTS"],
                field="issue number"),
    "V03": dict(url="https://www.loc.gov/resource/sn84020504/1907-12-02/ed-1/?sp=9",
                img=_img("V03_newark_scan.jpg"),
                answer="1832",
                forbidden=["1832", "DECEMBER 2", "ONE CENT"],
                field="year established"),
    "V04": dict(url="https://www.loc.gov/resource/sn83030214/1900-01-06/ed-1/?sp=1",
                img=_img("V04_tribune_scan.jpg"),
                answer="19410",
                forbidden=["19410", "19,410", "19.410", "NO. 19410"],
                field="issue number"),
    "V05": dict(url="https://www.loc.gov/resource/sn83045211/1922-03-27/ed-1/?sp=1",
                img=_img("V05_ledger_scan.jpg"),
                answer="166",
                forbidden=["166", "NO. 166", "TWO CENTS"],
                field="issue number"),
}


def _vision_compute(pid):
    v = VISION[pid]
    return lambda: je.compute_vision_extract(v["url"], v["img"], v["answer"],
                                             v["forbidden"], v["field"], pct=20)


# ---------------------------------------------------------------------------
# NIH RANKING trap definitions (fy, term, rank_from_bottom) — answers computed live.
# ---------------------------------------------------------------------------
NIH = {
    "S01": (2020, "glioblastoma", 3),
    "S02": (2019, "malaria", 5),
    "S03": (2021, "alzheimer", 3),
    "S04": (2020, "leukemia", 4),
    "S05": (2021, "diabetes", 5),
    "S06": (2022, "melanoma", 3),
    "S07": (2021, "fibrosis", 4),
}

_ORD = {3: "third", 4: "fourth", 5: "fifth"}


def _nih_prompt(fy, term, rank):
    o = _ORD[rank]
    return (f"Using the NIH RePORTER project database, consider every research project "
            f"whose project title or terms match \"{term}\" in fiscal year {fy}. Retrieve the "
            f"complete set of matching projects — note that the database returns at most 500 "
            f"records per request and does not honor server-side sorting, so you must page "
            f"through all results and combine them yourself. Remove any duplicate project "
            f"records, then rank the unique projects by their awarded dollar amount from "
            f"smallest to largest. Identify the project with the {o}-lowest award amount and "
            f"report its full project number exactly as recorded. Do not rely on a single "
            f"query response or on the order results are returned in.")


def _nih_compute(fy, term, rank):
    return lambda: je.compute_nih_rank(fy, term, rank)


BATCH: list[SealPrompt] = []

# ---- VISION-TRUE vertical traps ----
BATCH += [
    SealPrompt(
        id="V01", domain="Historical newspaper", method="vertical", intended="clean",
        prompt=("Open the front-page scan of the New-York Tribune for its January 1, 1900 "
                "edition, held by the Library of Congress (newspaper LCCN sn83030214, the "
                "January 1, 1900 edition, page 1). Read the masthead directly from the page "
                "image — the machine-transcribed text garbles the issue number and cannot be "
                "trusted. The masthead prints \"Vol. LIX....No. _____\" near the top. "
                "Determine the complete issue number that follows \"No.\", reading every "
                "digit carefully from the image, and report it as a single integer with no "
                "punctuation. The digits legible in the scan, not the OCR layer, are the "
                "authority."),
        compute=_vision_compute("V01"),
        exploit=["ocr_misread", "vision_required", "api_proof"],
        sources=["https://www.loc.gov/resource/sn83030214/1900-01-01/ed-1/?sp=1",
                 "https://www.loc.gov/item/sn83030214/1900-01-01/ed-1/",
                 "https://chroniclingamerica.loc.gov/lccn/sn83030214/"]),
    SealPrompt(
        id="V02", domain="Historical newspaper", method="vertical", intended="clean",
        prompt=("Examine the scan of the Evening Public Ledger (Philadelphia) front page for "
                "its March 6, 1922 issue, held by the Library of Congress (newspaper LCCN "
                "sn83045211, the March 6, 1922 edition, page 23). The automated text "
                "transcription of this page is severely corrupted, so read the masthead from "
                "the page image itself. In the masthead, the paper prints its volume and "
                "issue number as \"VOL. VIII.—NO. ___\". Determine the issue number that "
                "follows \"NO.\" and report that integer. Trust only what is legible in the "
                "image, not the OCR layer."),
        compute=_vision_compute("V02"),
        exploit=["ocr_misread", "vision_required", "api_proof"],
        sources=["https://www.loc.gov/resource/sn83045211/1922-03-06/ed-1/?sp=23",
                 "https://www.loc.gov/item/sn83045211/1922-03-06/ed-1/",
                 "https://chroniclingamerica.loc.gov/lccn/sn83045211/"]),
    SealPrompt(
        id="V03", domain="Historical newspaper", method="vertical", intended="clean",
        prompt=("Look at the masthead of The Evening Star and Newark Advertiser (Newark, New "
                "Jersey) for its December 2, 1907 issue, held by the Library of Congress "
                "(newspaper LCCN sn84020504, the December 2, 1907 edition, page 9). The "
                "machine-readable text for this page omits the founding line, so read the "
                "page image directly. The masthead states \"ESTABLISHED\" followed by a "
                "four-digit year. Determine that year and report it. Use only the year "
                "visible in the scanned image, not the catalog record or the OCR."),
        compute=_vision_compute("V03"),
        exploit=["ocr_misread", "vision_required", "api_proof"],
        sources=["https://www.loc.gov/resource/sn84020504/1907-12-02/ed-1/?sp=9",
                 "https://www.loc.gov/item/sn84020504/1907-12-02/ed-1/",
                 "https://chroniclingamerica.loc.gov/lccn/sn84020504/"]),
    SealPrompt(
        id="V04", domain="Historical newspaper", method="vertical", intended="clean",
        prompt=("Open the front-page scan of the New-York Tribune for its January 6, 1900 "
                "edition, held by the Library of Congress (newspaper LCCN sn83030214, the "
                "January 6, 1900 edition, page 1). Read the masthead directly from the page "
                "image — the machine transcription drops the issue number entirely. The "
                "masthead prints \"Nº. LIX.... Nº _____\" near the top. Determine the full "
                "issue number that follows the second \"Nº\", reading each digit from the "
                "image, and report it as one integer with no punctuation. The digits in the "
                "scan, not the OCR, are the authority."),
        compute=_vision_compute("V04"),
        exploit=["ocr_misread", "vision_required", "api_proof"],
        sources=["https://www.loc.gov/resource/sn83030214/1900-01-06/ed-1/?sp=1",
                 "https://www.loc.gov/item/sn83030214/1900-01-06/ed-1/",
                 "https://chroniclingamerica.loc.gov/lccn/sn83030214/"]),
    SealPrompt(
        id="V05", domain="Historical newspaper", method="vertical", intended="clean",
        prompt=("Examine the front-page scan of the Evening Public Ledger (Philadelphia) for "
                "its March 27, 1922 issue, held by the Library of Congress (newspaper LCCN "
                "sn83045211, the March 27, 1922 edition, page 1). The automated text "
                "transcription of this page omits the issue number, so read the masthead from "
                "the page image itself. The masthead prints \"VOL. VIII.—NO. ___\" near the "
                "top. Determine the issue number that follows \"NO.\" and report that "
                "integer. Trust only what is legible in the image, not the OCR layer."),
        compute=_vision_compute("V05"),
        exploit=["ocr_misread", "vision_required", "api_proof"],
        sources=["https://www.loc.gov/resource/sn83045211/1922-03-27/ed-1/?sp=1",
                 "https://www.loc.gov/item/sn83045211/1922-03-27/ed-1/",
                 "https://chroniclingamerica.loc.gov/lccn/sn83045211/"]),
]

# ---- NIH RANKING horizontal traps — REMOVED (non-reproducible ground truth) ----
# The RePORTER API never surfaces ~13% of matching records, duplicates rows within and
# across offset pages, truncates large result sets, and ties many awards at $1. The
# "Nth-lowest award" answer is therefore computed on a provably incomplete set and is not
# defensible as ground truth. This class is dropped until a complete, stable data source
# replaces it. The compute_nih_rank machinery is retained in join_engine for future use.

# ---- NEGATIVE CONTROLS (must be caught by the gate) ----
BATCH += [
    SealPrompt(
        id="C01", domain="Control", method="horizontal", intended="flawed",
        prompt=("Name the 18th-century military officer who led colonial forces to victory "
                "and later became the first President of the United States; this same man, a "
                "former general, also served as commander-in-chief. Report his full name."),
        compute=lambda: {"answer": "George Washington", "unique": True, "n_base": 1,
                         "payload": "George Washington", "survivors": ["George Washington"],
                         "trace": ["self-correcting chain"], "api_proof": False},
        exploit=["self_correcting"], sources=["https://en.wikipedia.org/wiki/George_Washington"],
        flaw="W1 self-correcting chain (later clue reveals the answer)"),
    SealPrompt(
        id="C02", domain="Control", method="horizontal", intended="flawed",
        prompt=("Using pro-football-reference.com, find the quarterback with the most career "
                "passing yards and report his full name as listed on that site."),
        compute=lambda: {"answer": "Tom Brady", "unique": True, "n_base": 1,
                         "payload": "Tom Brady", "survivors": ["Tom Brady"],
                         "trace": ["banned source"], "api_proof": False},
        exploit=["banned_source"],
        sources=["https://www.pro-football-reference.com/leaders/pass_yds_career.htm"],
        flaw="R4 banned source (Sports Reference network)"),
    SealPrompt(
        id="C03", domain="Control", method="horizontal", intended="flawed",
        prompt=("Take the three highest career home-run totals in MLB history, add them "
                "together, then compute the average of the three and report that number."),
        compute=lambda: {"answer": "750", "unique": True, "n_base": 1, "payload": "750",
                         "survivors": ["750"], "trace": ["arithmetic"], "api_proof": False},
        exploit=["arithmetic"], sources=["https://www.baseball-reference.com/leaders/HR_career.shtml"],
        flaw="R5 arithmetic framing (sum + average)"),
]


if __name__ == "__main__":
    clean = [p for p in BATCH if p.intended == "clean"]
    flawed = [p for p in BATCH if p.intended == "flawed"]
    print(f"{len(clean)} clean + {len(flawed)} flawed = {len(BATCH)} total")
    for p in clean:
        print(f"  {p.id}: {p.domain} / {p.method}  words={len(p.prompt.split())}")
