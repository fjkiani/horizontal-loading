"""
resolution_probe.py — is the 175->176 misread an intrinsic glyph ambiguity, or an
artifact of the downscaled image we feed the OCR?

Ground truth (agent vision, full-res LOC scan): VOL. VIII.-NO. 175.
tesseract, EasyOCR and Cohere vision ALL returned 176 when reading the pct:15
image (1143x1424). This sweeps the LOC pct: parameter and re-reads the masthead
with the identical extractor at each resolution.

If accuracy is resolution-dependent, the fix is a bigger crop, not a better engine.
"""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import join_engine as je
import trap_generator as tg
from PIL import Image

URL = "https://www.loc.gov/item/sn83045211/1922-04-06/ed-1/"
TRUTH = "175"
PCTS = [15, 25, 40, 60, 100]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resolution_probe.json")


def main():
    done = json.load(open(OUT)) if os.path.exists(OUT) else {}
    for pct in PCTS:
        k = str(pct)
        if k in done:
            print(f"pct:{pct} cached -> {done[k]['answer']}")
            continue
        path = f"/workspace/adjudicate/ledger_pct{pct}.jpg"
        if not os.path.exists(path):
            je.loc_page_image(URL, path, pct=pct)
        size = Image.open(path).size
        crop = tg._masthead_crop(path)
        t0 = time.time()
        reads = [tg._ocr_at_scale(crop, s) for s in (2, 3, 4)]
        answer, field, conf = tg._extract_with_confidence(*reads)
        rec = {"pct": pct, "image_size": list(size), "crop_size": list(crop.size),
               "answer": answer, "field": field, "confidence": conf,
               "correct": tg._norm_digits(answer or "") == TRUTH,
               "per_scale": reads, "secs": round(time.time() - t0, 1)}
        done[k] = rec
        json.dump(done, open(OUT, "w"), indent=2)
        print(f"pct:{pct:3d} img={size} crop={crop.size} -> answer={answer} "
              f"conf={conf} correct={rec['correct']} ({rec['secs']}s)", flush=True)

    print("\n--- resolution sweep summary (truth = 175) ---")
    for k in sorted(done, key=lambda x: int(x)):
        v = done[k]
        print(f"pct:{v['pct']:3d}  crop={v['crop_size'][0]}x{v['crop_size'][1]:<5} "
              f"answer={str(v['answer']):>6}  conf={v['confidence']:<6} "
              f"{'CORRECT' if v['correct'] else 'WRONG'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
