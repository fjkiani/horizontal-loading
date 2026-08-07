"""
cohere_adjudicate_test.py — decisive test of the Cohere confirm step.

The 1922-04-06 Evening Public Ledger masthead truly reads 175. BOTH tesseract
(3-scale consensus) and EasyOCR independently misread it as 176. If the Cohere
vision LLM reads 175, the confirm architecture provably catches the exact failure
mode that makes OCR-only generation untrustworthy.

Checkpoints to disk so an interrupted run loses nothing.
"""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trap_generator as tg

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cohere_adjudication.json")

# (image, tesseract_reading, ground_truth_from_agent_vision)
CASES = [
    ("generated_images/sn83045211_1922-04-06.jpg", "176", "175"),  # known OCR failure
    ("generated_images/sn83030214_1900-07-04.jpg", "19589", "19589"),  # known OCR success
    ("generated_images/sn83045211_1922-03-11.jpg", "158", "158"),
    ("generated_images/sn83030214_1900-02-11.jpg", "19446", "19446"),
]


def main():
    if not tg.cohere_available():
        print("COHERE_API_KEY not set")
        return 1
    done = json.load(open(OUT)) if os.path.exists(OUT) else {}
    for img, tess, truth in CASES:
        if img in done:
            print(f"{os.path.basename(img)}: cached -> {done[img]['cohere']}")
            continue
        if not os.path.exists(img):
            print(f"{os.path.basename(img)}: missing")
            continue
        crop = tg._masthead_crop(img)
        t0 = time.time()
        raw = tg._cohere_read_masthead(crop, max_retries=8)
        ans, _ = tg._parse_cohere_answer(raw)
        rec = {"cohere": ans, "tesseract": tess, "truth": truth, "raw": raw,
               "cohere_correct": ans == truth,
               "catches_ocr_error": (tess != truth) and (ans == truth),
               "secs": round(time.time() - t0, 1)}
        done[img] = rec
        json.dump(done, open(OUT, "w"), indent=2)  # checkpoint
        print(f"{os.path.basename(img)}: cohere={ans} tesseract={tess} truth={truth} "
              f"correct={rec['cohere_correct']} ({rec['secs']}s)", flush=True)
        time.sleep(75)  # trial vision endpoint sustains roughly 1 call/min

    n = len(done)
    ok = sum(1 for v in done.values() if v["cohere_correct"])
    caught = [k for k, v in done.items() if v["catches_ocr_error"]]
    print(f"--- cohere correct on {ok}/{n} ---")
    print(f"--- OCR errors caught: {len(caught)} {[os.path.basename(c) for c in caught]} ---")
    return 0


if __name__ == "__main__":
    sys.exit(main())
