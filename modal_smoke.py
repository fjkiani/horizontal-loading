"""Minimal Modal GPU smoke test: confirm this account can actually schedule a
GPU and that torch sees it, before paying to build a multi-GB VLM image."""
import modal

app = modal.App("seal-gpu-smoke")
image = modal.Image.debian_slim(python_version="3.11").pip_install("torch==2.5.1")


@app.function(image=image, gpu="a10g", timeout=300)
def gpu_probe():
    import subprocess, torch
    name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
    smi = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total",
                          "--format=csv,noheader"], capture_output=True, text=True)
    return {"cuda_available": torch.cuda.is_available(), "device": name,
            "smi": smi.stdout.strip(), "torch": torch.__version__}


@app.local_entrypoint()
def main():
    print(gpu_probe.remote())
