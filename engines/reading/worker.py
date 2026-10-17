import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_shared"))

import spectra_worker as mw

import torch
from transformers import AutoModel


class ReadingEngine(mw.Engine):
    name = "reading"

    def __init__(self) -> None:
        super().__init__()
        self.model = None

    def load(self, model_dir: str, settings: dict, progress: mw.Progress) -> dict:
        threads = int(settings.get("threads") or 0) or max(1, min(8, (os.cpu_count() or 2) // 2))
        torch.set_num_threads(threads)
        progress(f"Loading {self.label} on cpu")
        self.model = AutoModel.from_pretrained(model_dir, trust_remote_code=True, dtype=torch.float32).eval()
        return {"device": "cpu", "threads": threads}

    def unload(self) -> None:
        self.model = None

    def op_decide(self, args: dict, progress: mw.Progress) -> dict:
        if self.model is None:
            raise RuntimeError("No model is loaded.")
        items = list(args.get("items") or [])
        if not items:
            return {"answers": [], "elapsed": 0.0}
        started = time.perf_counter()
        results = self.model.system_one_batch([(str(item["state"]), {"q": item["question"]}) for item in items])
        answers = [result["answers"]["q"] for result in results]
        return {"answers": answers, "elapsed": round(time.perf_counter() - started, 3)}


if __name__ == "__main__":
    mw.serve(ReadingEngine())
