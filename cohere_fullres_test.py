"""
cohere_fullres_test.py — FAIR test of the Cohere vision LLM.

The earlier test fed Cohere the pct:15 downscale (857x1068), the same degraded
raster that made tesseract and EasyOCR read 176 instead of 175. That was not a
test of the model, it was a test of the input. This re-runs the read on the
full-resolution masthead crop, which agent vision reads unambiguously as 175.

One call per case, checkpointed, generously spaced for the trial rate limit.
"""
import base64, io, json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trap_generator as tg
from PIL import Image

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cohere_fullres.json")

# (label, local full-res image, truth)
CASES = [
    ("ledger_1922-04-06_pct100", "/workspace/adjudicate/ledger_pct100.jpg", "175"),
    ("ledger_1922-04-06_pct40", "/workspace/adjudicate/ledger_pct40.jpg", "175"),
]


def crop_masthead(path, frac=0.045, max_w=2200):
    """Tight masthead band, downsized only enough to keep the payload sane."""
    im = Image.open(path).convert("L")
    w, h = im.size
    c = im.crop((0, 0, w, int(h * frac)))
    if c.width > max_w:
        r = max_w / c.width
        c = c.resize((max_w, max(1, int(c.height * r))), Image.LANCZOS)
    return c


def main():
    if not tg.cohere_available():
        print("COHERE_API_KEY not set")
        return 1
    done = json.load(open(OUT)) if os.path.exists(OUT) else {}
    for label, path, truth in CASES:
        if label in done:
            print(f"{label}: cached -> {done[label]['cohere']}")
            continue
        if not os.path.exists(path):
            print(f"{label}: missing {path}")
            continue
        crop = crop_masthead(path)
        t0 = time.time()
        raw = tg._cohere_read_masthead(crop, max_retries=10)
        ans, _ = tg._parse_cohere_answer(raw)
        rec = {"cohere": ans, "raw": raw, "truth": truth,
               "crop_size": list(crop.size), "correct": ans == truth,
               "secs": round(time.time() - t0, 1)}
        done[label] = rec
        json.dump(done, open(OUT, "w"), indent=2)
        print(f"{label}: crop={crop.size} cohere={ans} truth={truth} "
              f"correct={rec['correct']} ({rec['secs']}s)", flush=True)
        time.sleep(60)

    print("\n--- fair Cohere test (full-resolution input) ---")
    for k, v in done.items():
        print(f"{k}: cohere={v['cohere']} truth={v['truth']} "
              f"{'CORRECT' if v['correct'] else 'WRONG'} crop={v['crop_size']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
