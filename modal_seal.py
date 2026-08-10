"""
modal_seal.py — GPU masthead reading + parallel page fan-out on Modal.

WHY THIS SHAPE
--------------
Two separate problems were conflated by "run it on GPU":

1. tesseract has NO GPU code path. Moving the existing cross-resolution reader
   to an A10 would leave the GPU idle. What made Render slow was 0.1 vCPU and a
   strictly serial walk, so the fix for THAT is real cores + parallelism.

2. The genuinely GPU-shaped job is the vision-LLM reader that was wanted all
   along. The Cohere trial key could not sustain it (~1 call per several
   minutes, HTTP 429). Self-hosting open weights on an A10 removes the rate
   limit entirely.

So this module does both, and keeps them independent on purpose.

INDEPENDENCE IS THE WHOLE POINT
-------------------------------
The 176 bug happened because tesseract, EasyOCR and Cohere all read the SAME
pct:15 raster and agreed on a wrong digit. Three readers, one input, zero real
independence -> "high confidence" on a wrong answer.

Here the two readers are independent in the pixel->text step, which is exactly
where that error lived:

    CPU reader : tesseract, rasters pct:40 / pct:60 / pct:25, cross-resolution vote
    GPU reader : Qwen2.5-VL, raster pct:100 (full resolution)

Full resolution is the read the CPU path could never afford: the sweep showed
pct:100 reads the hard 1922-04-06 masthead correctly. Giving the expensive raster
to the GPU is the point of the GPU.

Both readings are then parsed by the SAME regexes (masthead_reader._parse), so a
disagreement is attributable to pixels-to-text, not to parser drift.

ADJUDICATION
------------
An answer is only ACCEPTED when both engines agree. Disagreement is reported as
a conflict with both readings, never silently resolved -- the failure mode being
defended against is a confident wrong answer, not a refusal.
"""
import modal

app = modal.App("seal-masthead-gpu")

# Model weights live in a Volume, not baked into the image: a code edit then
# does not invalidate a ~16GB layer and force a re-download.
hf_cache = modal.Volume.from_name("seal-hf-cache", create_if_missing=True)

MODEL_ID = "Qwen/Qwen2.5-VL-7B-Instruct"

_LOCAL = ["masthead_reader.py", "join_engine.py"]

# CPU image: the existing reader, imported verbatim rather than reimplemented,
# so the Modal path and the app path cannot drift apart.
cpu_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("tesseract-ocr")
    .pip_install("pytesseract>=0.3.10", "Pillow>=10.0")
    # import closure: masthead_reader -> join_engine, trap_generator (shared regexes)
    .add_local_file("masthead_reader.py", "/root/masthead_reader.py")
    .add_local_file("join_engine.py", "/root/join_engine.py")
    .add_local_file("trap_generator.py", "/root/trap_generator.py")
)

gpu_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("tesseract-ocr")
    .pip_install(
        "torch==2.5.1",
        "torchvision==0.20.1",
        "transformers==4.51.3",
        "accelerate==1.6.0",
        "Pillow>=10.0",
        "pytesseract>=0.3.10",
    )
    .env({"HF_HOME": "/cache", "HF_HUB_ENABLE_HF_TRANSFER": "0"})
    .add_local_file("masthead_reader.py", "/root/masthead_reader.py")
    .add_local_file("join_engine.py", "/root/join_engine.py")
    .add_local_file("trap_generator.py", "/root/trap_generator.py")
)


# --------------------------------------------------------------------------
# one-time weight download into the Volume
# --------------------------------------------------------------------------
@app.function(image=gpu_image, volumes={"/cache": hf_cache}, timeout=3600)
def fetch_weights():
    from huggingface_hub import snapshot_download
    p = snapshot_download(MODEL_ID, ignore_patterns=["*.pth", "*.msgpack", "*.h5"])
    hf_cache.commit()
    import os
    total = sum(os.path.getsize(os.path.join(r, f))
                for r, _, fs in os.walk(p) for f in fs)
    return {"path": p, "gb": round(total / 1e9, 2)}


# --------------------------------------------------------------------------
# page enumeration (cheap, network-bound, inherently serial)
# --------------------------------------------------------------------------
@app.function(image=cpu_image, timeout=1800)
def enumerate_pages(lccn: str, start_date: str, n: int):
    """Walk the LOC next_resource chain to collect page identities only.

    This is the one part that MUST be serial -- each page's URL is only
    discoverable from the previous page's JSON. It is also cheap (small JSON,
    no rasters), which is precisely why it is worth separating from the
    expensive per-page read that CAN then be fanned out in parallel.
    """
    import re
    import join_engine as je

    url = f"https://www.loc.gov/resource/{lccn}/{start_date}/ed-1/?sp=1"
    pages = []
    for _ in range(n):
        meta = je.get(url + ("&fo=json" if "?" in url else "?fo=json"), as_json=True)
        item = meta.get("item", {})
        date = item.get("date", start_date)
        title = re.sub(r"\s*\(.*?\)\s*", " ", item.get("title", lccn)).split(",")[0].strip()
        pages.append({"lccn": lccn, "date": date, "paper": title, "resource_url": url})
        nxt = meta.get("next_resource", {}).get("url")
        if not nxt:
            break
        url = nxt.replace("&fo=json", "").replace("?fo=json", "?")
        if "sp=1" not in url:
            url += ("&sp=1" if "?" in url else "?sp=1")
    return pages


# --------------------------------------------------------------------------
# CPU engine: existing cross-resolution tesseract reader, unchanged
# --------------------------------------------------------------------------
@app.function(image=cpu_image, cpu=4.0, timeout=1800, max_containers=12)
def read_cpu(page: dict):
    import masthead_reader as mr
    try:
        r = mr.read_masthead(page["resource_url"], workdir="/tmp/adj",
                             cache_tag=f"{page['lccn']}_{page['date']}")
    except Exception as e:
        return {**page, "engine": "tesseract-xres", "answer": None,
                "confidence": "error", "detail": str(e)[:200]}
    return {**page, "engine": "tesseract-xres", "answer": r.get("answer"),
            "confidence": r.get("confidence"),
            "per_resolution": {str(k): v.get("answer")
                               for k, v in (r.get("per_resolution") or {}).items()}}


# --------------------------------------------------------------------------
# GPU engine: vision LLM on the full-resolution raster
# --------------------------------------------------------------------------
VLM_PROMPT = (
    "This is a crop from the masthead of a historical newspaper front page.\n"
    "Transcribe the volume and issue line EXACTLY as printed, character for "
    "character, including punctuation. Do not correct, expand or interpret "
    "anything. If a character is illegible, write '?' in its place.\n"
    "Reply with the transcription only, on a single line."
)

# The GPU's job is the raster the CPU path cannot afford. tesseract votes across
# pct:40/60/25; giving the VLM pct:100 keeps the two engines on genuinely
# different pixels, which is what makes their agreement meaningful.
GPU_PCT = 100


@app.cls(image=gpu_image, gpu="a10g", volumes={"/cache": hf_cache},
         timeout=3600, scaledown_window=240, max_containers=4)
class VLMReader:
    @modal.enter()
    def load(self):
        import torch
        from transformers import Qwen2_5_VLForConditionalGeneration, AutoProcessor
        self.torch = torch
        # Cap vision tokens: the input is a thin masthead strip, so a large
        # max_pixels would spend VRAM and latency on empty newsprint.
        self.proc = AutoProcessor.from_pretrained(
            MODEL_ID, min_pixels=256 * 28 * 28, max_pixels=1600 * 28 * 28)
        self.model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            MODEL_ID, torch_dtype=torch.bfloat16, device_map="cuda")
        self.model.eval()

    def _crop(self, page):
        """Download the full-res page and cut the masthead band.

        Reuses masthead_reader's BAND geometry so the GPU looks at the same
        region of the page as the CPU engine -- same region, different pixels.
        """
        import os, re
        import join_engine as je
        import masthead_reader as mr
        from PIL import Image

        os.makedirs("/tmp/gpu", exist_ok=True)
        raw = f"/tmp/gpu/{page['lccn']}_{page['date']}_p{GPU_PCT}.jpg"
        if not (os.path.exists(raw) and os.path.getsize(raw) > 10000):
            meta = je.get(page["resource_url"] + ("&fo=json" if "?" in page["resource_url"]
                                                  else "?fo=json"), as_json=True)
            img_url = meta["item"]["resources"][0]["image"]
            img_url = re.sub(r"pct:\d+(\.\d+)?", f"pct:{GPU_PCT}", img_url)
            import urllib.request
            req = urllib.request.Request(img_url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"})
            with urllib.request.urlopen(req, timeout=180, context=je._CTX) as r, \
                    open(raw, "wb") as f:
                f.write(r.read())
        im = Image.open(raw).convert("RGB")
        w, h = im.size
        lo, hi = mr.BAND
        return im.crop((0, int(h * lo), int(w * 0.45), int(h * hi))), (w, h)

    @modal.method()
    def read(self, page: dict):
        import masthead_reader as mr
        try:
            crop, full_size = self._crop(page)
        except Exception as e:
            return {**page, "engine": f"qwen2.5vl-pct{GPU_PCT}", "answer": None,
                    "confidence": "error", "detail": f"fetch/crop failed: {str(e)[:200]}"}

        msgs = [{"role": "user", "content": [{"type": "image"},
                                             {"type": "text", "text": VLM_PROMPT}]}]
        text = self.proc.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
        inputs = self.proc(text=[text], images=[crop], return_tensors="pt").to("cuda")
        with self.torch.inference_mode():
            out = self.model.generate(**inputs, max_new_tokens=96, do_sample=False)
        gen = out[0][inputs["input_ids"].shape[1]:]
        raw_text = self.proc.decode(gen, skip_special_tokens=True).strip()

        # Parse with the SAME regexes the CPU engine uses, so any disagreement is
        # attributable to reading the pixels, not to two different parsers.
        parsed = mr._parse([raw_text])
        return {**page, "engine": f"qwen2.5vl-pct{GPU_PCT}",
                "answer": parsed[0], "field": parsed[1],
                "confidence": "vlm" if parsed[0] else "none",
                "transcription": raw_text, "crop_px": list(crop.size),
                "full_page_px": list(full_size)}


# --------------------------------------------------------------------------
# adjudication
# --------------------------------------------------------------------------
def adjudicate(cpu_rows, gpu_rows):
    """Accept only where the two independent engines agree.

    Deliberately conservative: a conflict is surfaced with BOTH readings rather
    than resolved by preferring one engine. The bug being defended against is a
    confident wrong answer; a refusal is cheap by comparison.
    """
    import re
    def norm(v):
        return re.sub(r"[,\.\s]", "", v) if v else None

    by = {(r["lccn"], r["date"]): r for r in cpu_rows}
    out = []
    for g in gpu_rows:
        c = by.get((g["lccn"], g["date"]), {})
        cn, gn = norm(c.get("answer")), norm(g.get("answer"))
        if cn and gn and cn == gn:
            verdict, agreed = "accept", cn
        elif cn and gn:
            verdict, agreed = "conflict", None
        elif cn or gn:
            verdict, agreed = "single-engine", None
        else:
            verdict, agreed = "unread", None
        out.append({"lccn": g["lccn"], "date": g["date"], "paper": g.get("paper"),
                    "verdict": verdict, "answer": agreed,
                    "cpu": c.get("answer"), "cpu_confidence": c.get("confidence"),
                    "cpu_per_resolution": c.get("per_resolution"),
                    "gpu": g.get("answer"), "gpu_transcription": g.get("transcription"),
                    "gpu_full_page_px": g.get("full_page_px"),
                    # carry failure detail through: an unread page is a finding to
                    # investigate, not a blank to shrug at
                    "cpu_detail": c.get("detail"), "gpu_detail": g.get("detail")})
    return out


@app.local_entrypoint()
def main(lccn: str = "sn83045211", start_date: str = "1922-04-06", n: int = 6):
    import json, time
    t0 = time.time()
    pages = enumerate_pages.remote(lccn, start_date, n)
    print(f"enumerated {len(pages)} pages in {time.time()-t0:.1f}s: "
          f"{[p['date'] for p in pages]}")

    t1 = time.time()
    # Each .map() is internally parallel but blocking locally, so drive the two
    # engines from separate threads: wall-clock becomes max(cpu, gpu) rather than
    # their sum, and neither engine waits on the other.
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_gpu = ex.submit(lambda: list(VLMReader().read.map(pages)))
        f_cpu = ex.submit(lambda: list(read_cpu.map(pages)))
        gpu_rows, cpu_rows = f_gpu.result(), f_cpu.result()
    print(f"read {len(pages)} pages on both engines in {time.time()-t1:.1f}s")

    rows = adjudicate(cpu_rows, gpu_rows)
    print(json.dumps(rows, indent=2)[:4000])
    with open("modal_gpu_reads.json", "w") as f:
        json.dump({"lccn": lccn, "start_date": start_date, "pages": len(pages),
                   "elapsed_s": round(time.time() - t0, 1), "rows": rows}, f, indent=2)
    print("wrote modal_gpu_reads.json")


@app.function(image=cpu_image, timeout=1800, max_containers=16)
def check_api_proof(entry: dict):
    """Re-test the api-proof gate for one pooled trap against its answer.

    This has to be re-run whenever an answer changes, because the gate is
    CONTINGENT on the answer being right. Discovered the hard way: 1922-03-11 was
    recorded as 158 and passed api-proof, but the true answer is 153 and 153 is
    plainly present in the LOC text layer ("VOL. VIII. NO. 153"). A misread
    answer is checked against the page as a string that is not there, so the gate
    reports "absent" and certifies a page that a text-only solver can in fact
    solve. Wrong answers are therefore MORE likely to pass this gate than right
    ones -- the safety property is inverted, so it needs auditing, not trust.
    """
    import re
    import join_engine as je
    import trap_generator as tg
    try:
        ocr = je.loc_page_ocr(entry["resource_url"])
    except Exception as e:
        return {**{k: entry[k] for k in ("lccn", "date", "answer")},
                "api_proof": None, "detail": str(e)[:200]}
    ans = tg._norm_digits(entry["answer"])
    norm = tg._norm_digits(ocr)
    substring = ans in norm
    # Also report a standalone-token match on the RAW text. The gate strips all
    # separators from the whole page before matching, which can create spurious
    # hits across number boundaries; distinguishing the two says whether a
    # rejection is real or an artifact of that concatenation.
    standalone = bool(re.search(rf"(?<![0-9]){re.escape(entry['answer'])}(?![0-9])", ocr))
    ctx = None
    if standalone:
        m = re.search(rf"(?<![0-9]){re.escape(entry['answer'])}(?![0-9])", ocr)
        ctx = " ".join(ocr[max(0, m.start() - 60):m.start() + 40].split())
    return {"lccn": entry["lccn"], "date": entry["date"], "answer": entry["answer"],
            "api_proof": not substring, "substring_hit": substring,
            "standalone_token_hit": standalone, "context": ctx,
            "ocr_chars": len(ocr)}


@app.local_entrypoint()
def audit_gates():
    """Re-validate the api-proof gate for every pooled trap, in parallel."""
    import json
    pool = json.load(open("generated_pool.json"))
    rows = list(check_api_proof.map(pool))
    bad = [r for r in rows if r["api_proof"] is not True]
    print(f"{'date':<12}{'answer':>8}  api_proof  substring  standalone")
    for r in sorted(rows, key=lambda x: (x["lccn"], x["date"])):
        print(f"{r['date']:<12}{str(r['answer']):>8}  {str(r['api_proof']):<9}  "
              f"{str(r.get('substring_hit')):<9}  {r.get('standalone_token_hit')}"
              + (f"   <- {r['context'][:70]}" if r.get("context") else ""))
    print(f"\n{len(rows) - len(bad)}/{len(rows)} traps hold api-proof")
    for r in bad:
        print(f"  FAILS: {r['date']} = {r['answer']}  {r.get('context') or r.get('detail')}")
    with open("pool_api_proof_audit.json", "w") as f:
        json.dump(rows, f, indent=2)
    print("wrote pool_api_proof_audit.json")


@app.local_entrypoint()
def audit_curated():
    """Apply the same gate audit to the hand-curated V01-V05 prompts.

    The pooled Ledger traps failed api-proof because the LOC text layer for that
    paper contains the masthead VOL/NO line. If that is a property of the paper
    rather than of individual pages, the curated Ledger prompts are unsound too
    and cannot be shipped as vision traps.
    """
    import json
    curated = [
        {"id": "V01", "lccn": "sn83030214", "date": "1900-01-01", "answer": "19405"},
        {"id": "V02", "lccn": "sn83045211", "date": "1922-03-06", "answer": "148"},
        {"id": "V04", "lccn": "sn83030214", "date": "1900-01-06", "answer": "19410"},
        {"id": "V05", "lccn": "sn83045211", "date": "1922-03-27", "answer": "166"},
        # V03 (Newark, "ESTABLISHED 1832") is a year-established field, audited separately
        {"id": "V03", "lccn": "sn84020504", "date": "1907-12-02", "answer": "1832"},
    ]
    for c in curated:
        c["resource_url"] = (f"https://www.loc.gov/resource/{c['lccn']}/{c['date']}"
                             f"/ed-1/?&sp=1")
    rows = list(check_api_proof.map(curated))
    ids = {(c["lccn"], c["date"]): c["id"] for c in curated}
    print(f"{'id':<5}{'date':<12}{'answer':>8}  api_proof  standalone")
    for r in sorted(rows, key=lambda x: x["date"]):
        print(f"{ids[(r['lccn'], r['date'])]:<5}{r['date']:<12}{str(r['answer']):>8}  "
              f"{str(r['api_proof']):<9}  {r.get('standalone_token_hit')}"
              + (f"   <- {r['context'][:65]}" if r.get("context") else ""))
    bad = [r for r in rows if r["api_proof"] is not True]
    print(f"\n{len(rows)-len(bad)}/{len(rows)} curated prompts hold api-proof")
    with open("curated_api_proof_audit.json", "w") as f:
        json.dump(rows, f, indent=2)
    print("wrote curated_api_proof_audit.json")


@app.local_entrypoint()
def bench():
    """Score both engines against the 14 agent-vision-confirmed ground truths.

    This is the only test that can justify promoting the GPU engine. The CPU
    reader's published record on this set is 8/10 correct, 0 accepted-wrong; a
    replacement has to be measured on the same pages, not on new ones it happens
    to read well.
    """
    import json, time
    pool = json.load(open("generated_pool.json"))
    pages = [{"lccn": t["lccn"], "date": t["date"], "paper": t["paper"],
              "resource_url": t["resource_url"]} for t in pool]
    truth = {(t["lccn"], t["date"]): t["answer"] for t in pool}
    print(f"benchmarking {len(pages)} ground-truth pages on both engines")

    t0 = time.time()
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_gpu = ex.submit(lambda: list(VLMReader().read.map(pages)))
        f_cpu = ex.submit(lambda: list(read_cpu.map(pages)))
        gpu_rows, cpu_rows = f_gpu.result(), f_cpu.result()
    elapsed = round(time.time() - t0, 1)

    rows = adjudicate(cpu_rows, gpu_rows)
    score = {"cpu": {"correct": 0, "wrong": 0, "refused": 0},
             "gpu": {"correct": 0, "wrong": 0, "refused": 0},
             "dual": {"accept_correct": 0, "accept_wrong": 0, "refused": 0}}
    for r in rows:
        gt = truth[(r["lccn"], r["date"])]
        for eng in ("cpu", "gpu"):
            v = r[eng]
            if not v:
                score[eng]["refused"] += 1
            elif str(v) == str(gt):
                score[eng]["correct"] += 1
            else:
                score[eng]["wrong"] += 1
        if r["verdict"] == "accept":
            key = "accept_correct" if str(r["answer"]) == str(gt) else "accept_wrong"
            score["dual"][key] += 1
        else:
            score["dual"]["refused"] += 1
        r["truth"] = gt
        r["cpu_ok"] = (str(r["cpu"]) == str(gt)) if r["cpu"] else None
        r["gpu_ok"] = (str(r["gpu"]) == str(gt)) if r["gpu"] else None

    print(f"\n{'date':<12}{'truth':>7}{'cpu':>7}{'gpu':>7}  verdict")
    for r in sorted(rows, key=lambda x: (x["lccn"], x["date"])):
        print(f"{r['date']:<12}{r['truth']:>7}{str(r['cpu']):>7}{str(r['gpu']):>7}  {r['verdict']}"
              + (f"   gpu_err={r['gpu_detail'][:60]}" if r.get("gpu_detail") else ""))
    print("\nscores:", json.dumps(score, indent=2))
    with open("modal_gpu_benchmark.json", "w") as f:
        json.dump({"model": MODEL_ID, "gpu_pct": GPU_PCT, "elapsed_s": elapsed,
                   "n_pages": len(pages), "score": score, "rows": rows}, f, indent=2)
    print(f"\nwall clock {elapsed}s for {len(pages)} pages on both engines")
    print("wrote modal_gpu_benchmark.json")


# ---------------------------------------------------------------------------
# WHOLE-ISSUE api-proof sweep.
#
# The gate has only ever tested ONE page: the one the trap points at. But a
# newspaper prints its issue number in the masthead of EVERY page, and its
# founding year on the front page of EVERY issue. So the security property the
# gate claims -- "this answer is not recoverable from the LOC text layer" -- is
# not page-scoped. A text-only solver that is told the paper and the date can
# fetch any page of that issue.
#
# This was exposed by re-auditing the curated prompts on the pages they actually
# name: V02 names page 23 and V03 page 9, and BOTH of those pages are clean --
# but page 1 of the same issue prints the answer in its text layer. The prompts'
# own claim ("the transcription of this page omits it") is literally true and
# still does not make them traps.
#
# ~220 independent fetches across the pool + curated set: embarrassingly
# parallel, so fan it out.
# ---------------------------------------------------------------------------
@app.function(image=cpu_image, timeout=1800, max_containers=16)
def page_leak(task: dict):
    """Test one PAGE of one issue under all three gate definitions at once.

      old_norm_substring -- the shipped gate: answer in strip_separators(page).
      standalone         -- answer as a whole token, separators allowed only at
                            thousands boundaries.
      label_bearing      -- answer carrying its own field label (NO. / ESTABLISHED).

    Reporting all three on the same pages is the only way to tell which
    disagreements are real leaks and which are scoring artifacts.
    """
    import join_engine as je
    import trap_generator as tg
    url = (f"https://www.loc.gov/resource/{task['lccn']}/{task['date']}"
           f"/ed-1/?&sp={task['sp']}")
    out = {"lccn": task["lccn"], "date": task["date"], "sp": task["sp"],
           "answer": task["answer"], "label": task.get("label", ""),
           "field": task.get("field", "issue number")}
    try:
        ocr = je.loc_page_ocr(url)
    except Exception as e:
        return {**out, "old_norm_substring": None, "error": str(e)[:150]}
    old = tg._norm_digits(task["answer"]) in tg._norm_digits(ocr)
    stand = tg.standalone_hits(ocr, task["answer"])
    lab = tg.label_bearing_leak(ocr, task["answer"], out["field"])
    ctx = None
    if stand:
        i = stand[0]
        ctx = " ".join(ocr[max(0, i - 60):i + 45].split())
    return {**out, "old_norm_substring": old, "n_standalone": len(stand),
            "label_bearing": lab, "context": ctx, "ocr_chars": len(ocr)}


@app.function(image=cpu_image, timeout=900, max_containers=16)
def issue_page_count(task: dict):
    """How many pages does this issue have? Read it, do not assume."""
    import join_engine as je
    url = (f"https://www.loc.gov/resource/{task['lccn']}/{task['date']}"
           f"/ed-1/?&sp=1&fo=json")
    try:
        meta = je.get(url, as_json=True)
        n = (meta.get("resource") or {}).get("segment_count")
        return {**task, "pages": int(n) if n else 1}
    except Exception as e:
        return {**task, "pages": 1, "error": str(e)[:150]}


@app.local_entrypoint()
def sweep_issues():
    """Sweep EVERY page of every pooled + curated trap's issue, scoring all three
    gate definitions, so real leaks and scoring artifacts separate cleanly."""
    import json, time
    from collections import defaultdict

    pool = json.load(open("generated_pool.json"))
    issues = [{"lccn": t["lccn"], "date": t["date"], "answer": t["answer"],
               "field": t.get("field", "issue number"),
               "label": f"pool {t['date']}"} for t in pool]
    issues += [
        {"lccn": "sn83030214", "date": "1900-01-01", "answer": "19405",
         "field": "issue number", "label": "V01"},
        {"lccn": "sn83045211", "date": "1922-03-06", "answer": "148",
         "field": "issue number", "label": "V02"},
        {"lccn": "sn84020504", "date": "1907-12-02", "answer": "1832",
         "field": "year established", "label": "V03"},
        {"lccn": "sn83030214", "date": "1900-01-06", "answer": "19410",
         "field": "issue number", "label": "V04"},
        {"lccn": "sn83045211", "date": "1922-03-27", "answer": "166",
         "field": "issue number", "label": "V05"},
    ]
    t0 = time.time()
    counts = list(issue_page_count.map(issues))
    tasks = [{**c, "sp": sp} for c in counts for sp in range(1, c["pages"] + 1)]
    print(f"{len(issues)} issues -> {len(tasks)} page fetches")
    rows = list(page_leak.map(tasks))
    print(f"swept in {time.time() - t0:.1f}s")

    by = defaultdict(list)
    for r in rows:
        by[(r["label"], r["lccn"], r["date"], r["answer"])].append(r)

    report = []
    print(f"\n{'trap':<18}{'answer':>8}{'pg':>4}  {'oldgate':>7} {'token':>6} "
          f"{'LABEL':>6}   verdict / evidence")
    for (label, lccn, date, answer), rs in sorted(by.items()):
        old_pages = sorted(r["sp"] for r in rs if r.get("old_norm_substring"))
        tok_pages = sorted(r["sp"] for r in rs if r.get("n_standalone"))
        lab_pages = sorted(r["sp"] for r in rs if r.get("label_bearing"))
        lab_ex = next((r["label_bearing"][0] for r in rs if r.get("label_bearing")), None)
        errs = [r["sp"] for r in rs if r.get("old_norm_substring") is None]
        holds = (not lab_pages) and not errs
        report.append({"label": label, "lccn": lccn, "date": date, "answer": answer,
                       "field": rs[0].get("field"), "pages": len(rs),
                       "old_gate_reject_pages": old_pages,
                       "standalone_token_pages": tok_pages,
                       "label_bearing_pages": lab_pages,
                       "label_example": lab_ex, "errors": errs,
                       "api_proof_whole_issue": holds})
        ev = f"LEAK {lab_ex!r} p{lab_pages}" if lab_pages else "holds"
        print(f"{label:<18}{answer:>8}{len(rs):>4}  {len(old_pages):>7} "
              f"{len(tok_pages):>6} {len(lab_pages):>6}   {ev}")
    clean = [r for r in report if r["api_proof_whole_issue"]]
    n_false_rej = sum(len(set(r["old_gate_reject_pages"]) - set(r["label_bearing_pages"]))
                      for r in report)
    print(f"\n{len(clean)}/{len(report)} hold api-proof across the WHOLE issue")
    print(f"old gate flagged {n_false_rej} page(s) with no label-bearing leak")
    with open("issue_sweep_api_proof.json", "w") as f:
        json.dump(report, f, indent=2)
    print("wrote issue_sweep_api_proof.json")


# ---------------------------------------------------------------------------
# Is the Ledger "leak" real, or a coincidence of small numbers?
#
# The whole-issue sweep flags the Ledger answers (148, 166, 227) but not the
# Tribune's (19405, 19446, ...). That difference could be entirely an artifact of
# digit count: a 3-digit token has ~900 possible values and a 30-page newspaper
# is full of page numbers, prices, addresses and stock quotes, so hitting "227"
# somewhere may carry no information at all. A 5-digit token has ~90,000 values
# and is correspondingly rare.
#
# Withdrawing a trap on a coincidence would be as wrong as shipping one on a
# false gate, so measure the null instead of asserting it:
#
#   1. BASE RATE  -- what fraction of ALL 3-digit numbers 100-999 occur as
#      standalone tokens somewhere in this issue? If most of them do, a hit on
#      the answer is uninformative and the gate is measuring nothing.
#   2. FORM       -- is the hit in masthead form ("NO. 227" / "VOL. VIII.-NO. 227")
#      or is it loose text? Only the masthead form is recoverable BY A SOLVER,
#      because only that form tells the solver which number is the issue number.
# ---------------------------------------------------------------------------
@app.function(image=cpu_image, timeout=1800, max_containers=16)
def page_number_profile(task: dict):
    """Return the answer's hit contexts AND the full numeric token census."""
    import re
    import join_engine as je
    url = (f"https://www.loc.gov/resource/{task['lccn']}/{task['date']}"
           f"/ed-1/?&sp={task['sp']}")
    out = {k: task[k] for k in ("lccn", "date", "sp", "answer", "label")}
    try:
        ocr = je.loc_page_ocr(url)
    except Exception as e:
        return {**out, "error": str(e)[:150], "tokens": [], "hits": [], "masthead": []}
    ans = task["answer"]
    width = len(ans)
    tokens = sorted(set(re.findall(rf"(?<![0-9])([0-9]{{{width}}})(?![0-9])", ocr)))
    hits = [" ".join(ocr[max(0, m.start() - 70):m.end() + 50].split())
            for m in re.finditer(rf"(?<![0-9]){re.escape(ans)}(?![0-9])", ocr)]
    # Masthead form: the answer preceded by a NO./No. marker within a few chars,
    # optionally with VOL. before it. This is what a solver could actually use.
    # The label must match the FIELD. An issue number is labelled "NO."; a
    # founding year is labelled ESTABLISHED / FOUNDED / EST. Using the issue-number
    # label for both scored V03's real "ESTABLISHED 1832" leak as clean -- a false
    # negative in the auditor itself.
    label_pat = (rf"(?:VOL[^0-9]{{0,20}})?N[O0o\u00ba\u00b0][.,: ]{{0,4}}{re.escape(ans)}(?![0-9])"
                 rf"|(?:ESTABLISHED|ESTAB|ESTD|EST|FOUNDED)[^0-9]{{0,20}}{re.escape(ans)}(?![0-9])")
    masthead = [" ".join(m.group(0).split())
                for m in re.finditer(label_pat, ocr, re.I)]
    return {**out, "tokens": tokens, "hits": hits, "masthead": masthead,
            "ocr_chars": len(ocr)}


@app.local_entrypoint()
def leak_null_model():
    """Measure the chance-hit base rate before believing any Ledger rejection."""
    import json, time
    from collections import defaultdict

    targets = [
        {"lccn": "sn83045211", "date": "1922-03-06", "answer": "148", "label": "V02 Ledger"},
        {"lccn": "sn83045211", "date": "1922-03-27", "answer": "166", "label": "V05 Ledger"},
        {"lccn": "sn83045211", "date": "1922-06-06", "answer": "227", "label": "pool Ledger"},
        {"lccn": "sn84020504", "date": "1907-12-02", "answer": "1832", "label": "V03 Newark"},
        # Tribune controls: same method, 5-digit answers.
        {"lccn": "sn83030214", "date": "1900-01-01", "answer": "19405", "label": "V01 Tribune"},
        {"lccn": "sn83030214", "date": "1900-11-16", "answer": "19724", "label": "pool Tribune"},
    ]
    t0 = time.time()
    counts = list(issue_page_count.map(targets))
    tasks = [{**c, "sp": sp} for c in counts for sp in range(1, c["pages"] + 1)]
    print(f"{len(targets)} issues -> {len(tasks)} page fetches")
    rows = list(page_number_profile.map(tasks))
    print(f"profiled in {time.time() - t0:.1f}s\n")

    by = defaultdict(list)
    for r in rows:
        by[r["label"]].append(r)

    report = []
    for label, rs in by.items():
        ans = rs[0]["answer"]
        width = len(ans)
        universe = 9 * (10 ** (width - 1))
        seen = set()
        for r in rs:
            seen.update(r.get("tokens", []))
        hits = [h for r in rs for h in r.get("hits", [])]
        mast = [m for r in rs for m in r.get("masthead", [])]
        base = len(seen) / universe
        rec = {"label": label, "answer": ans, "digits": width, "pages": len(rs),
               "distinct_tokens_seen": len(seen), "token_universe": universe,
               "chance_hit_rate": round(base, 4), "n_hits": len(hits),
               "n_masthead_form": len(mast), "masthead_examples": mast[:3],
               "hit_examples": hits[:4],
               "leak_is_solver_usable": bool(mast)}
        report.append(rec)
        print(f"=== {label}  answer={ans} ({width} digits, {len(rs)} pages) ===")
        print(f"  distinct {width}-digit tokens in issue : {len(seen)} / {universe}")
        print(f"  P(random {width}-digit token appears)  : {base:.3f}")
        print(f"  occurrences of the answer            : {len(hits)}")
        print(f"  in MASTHEAD form (NO. x)             : {len(mast)}  {mast[:2]}")
        for h in hits[:3]:
            print(f"     ctx: ...{h[:110]}...")
        print()
    clean = [r for r in report if not r["leak_is_solver_usable"]]
    print(f"{len(clean)}/{len(report)} have no solver-usable masthead leak")
    with open("leak_null_model.json", "w") as f:
        json.dump(report, f, indent=2)
    print("wrote leak_null_model.json")


# ---------------------------------------------------------------------------
# Pool growth under the corrected gate.
#
# The old gate discarded pages it should have kept. Re-walking October 1900 shows
# 5 of 8 pages carry a REAL masthead leak ("N* 19.678") and are rightly refused,
# but the rest were thrown away on a normalization artifact. Combine the fixed
# gate with the dual-engine reader and the arithmetic cross-check, all in
# parallel, and accept a page only if every independent line of evidence agrees:
#
#   1. tesseract cross-resolution vote (pct 40/60/25)
#   2. Qwen2.5-VL on a pct:100 crop -- different pixels, same parser
#   3. the answer implied by the paper's own issue sequence
#   4. no label-bearing leak on ANY page of that issue
#
# Any single engine can be wrong. Requiring all four to coincide is what buys
# the 0-accepted-wrong record; the GPU model never refuses, so it must never be
# trusted alone.
# ---------------------------------------------------------------------------
@app.local_entrypoint()
def grow(lccn: str = "sn83030214", dates: str = "", anchor_date: str = "1900-01-01",
         anchor_value: int = 19405):
    """Dual-engine + arithmetic + whole-issue gate, fully parallel."""
    import json, time, datetime
    from concurrent.futures import ThreadPoolExecutor
    from collections import defaultdict

    if dates:
        want = [d.strip() for d in dates.split(",") if d.strip()]
    else:
        d0 = datetime.date(1900, 10, 1)
        want = [(d0 + datetime.timedelta(days=i)).isoformat() for i in range(14)]

    pool = {t["date"] for t in json.load(open("generated_pool.json"))
            if t["lccn"] == lccn}
    want = [d for d in want if d not in pool]
    pages = [{"lccn": lccn, "date": d,
              "resource_url": f"https://www.loc.gov/resource/{lccn}/{d}/ed-1/?&sp=1"}
             for d in want]
    print(f"{len(pages)} candidate dates (skipping {len(pool)} already pooled)")

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_cpu = ex.submit(lambda: list(read_cpu.map(pages)))
        f_gpu = ex.submit(lambda: list(VLMReader().read.map(pages)))
        cpu_rows, gpu_rows = f_cpu.result(), f_gpu.result()
    print(f"dual-engine read in {time.time() - t0:.1f}s")

    cpu = {r["date"]: r for r in cpu_rows}
    gpu = {r["date"]: r for r in gpu_rows}

    # Arithmetic prediction from the pool's own validated anchor.
    a = datetime.date.fromisoformat(anchor_date)
    def predict(d):
        return anchor_value + (datetime.date.fromisoformat(d) - a).days

    agreed = []
    for d in want:
        c, g = cpu.get(d, {}), gpu.get(d, {})
        ca, ga, pa = c.get("answer"), g.get("answer"), str(predict(d))
        ok = ca and ga and str(ca) == str(ga) == pa
        print(f"  {d}  cpu={str(ca):>7} gpu={str(ga):>7} seq={pa:>7}  "
              f"{'AGREE' if ok else 'no'}")
        if ok:
            agreed.append({"lccn": lccn, "date": d, "answer": pa,
                           "field": "issue number", "label": d})
    print(f"\n{len(agreed)}/{len(want)} pass triple agreement")
    if not agreed:
        return

    counts = list(issue_page_count.map(agreed))
    tasks = [{**c, "sp": sp} for c in counts for sp in range(1, c["pages"] + 1)]
    print(f"whole-issue gate: {len(tasks)} page fetches")
    rows = list(page_leak.map(tasks))
    by = defaultdict(list)
    for r in rows:
        by[r["date"]].append(r)

    accepted = []
    for cand in agreed:
        rs = by[cand["date"]]
        lab = sorted(r["sp"] for r in rs if r.get("label_bearing"))
        errs = [r["sp"] for r in rs if r.get("old_norm_substring") is None]
        ex_lab = next((r["label_bearing"][0] for r in rs if r.get("label_bearing")), None)
        holds = (not lab) and not errs
        print(f"  {cand['date']}  pages={len(rs)}  leak_pages={lab or '-'}  "
              f"errors={errs or '-'}  -> {'ACCEPT' if holds else 'reject'}"
              + (f"  {ex_lab!r}" if ex_lab else ""))
        if holds:
            accepted.append({**cand, "pages_swept": len(rs),
                             "cpu": cpu[cand["date"]], "gpu": gpu[cand["date"]]})
    print(f"\n{len(accepted)}/{len(agreed)} clear the whole-issue gate "
          f"({time.time() - t0:.1f}s total)")
    with open("modal_grow_candidates.json", "w") as f:
        json.dump(accepted, f, indent=2)
    print("wrote modal_grow_candidates.json")
