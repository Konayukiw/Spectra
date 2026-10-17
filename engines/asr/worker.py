import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_shared"))

import spectra_worker as mw

import torch
from qwen_asr import Qwen3ASRModel


class AsrEngine(mw.Engine):
    name = "asr"

    def __init__(self) -> None:
        super().__init__()
        self.model = None
        self.device = "cpu"

    def load(self, model_dir: str, settings: dict, progress: mw.Progress) -> dict:
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        progress(f"Loading {self.label} on {self.device}")
        self.model = Qwen3ASRModel.from_pretrained(
            model_dir,
            dtype=torch.bfloat16 if self.device == "cuda" else torch.float32,
            device_map="cuda:0" if self.device == "cuda" else "cpu",
            max_inference_batch_size=1,
            max_new_tokens=2048,
        )
        return {"device": self.device}

    def unload(self) -> None:
        self.model = None

    def op_transcribe(self, args: dict, progress: mw.Progress) -> dict:
        if self.model is None:
            raise RuntimeError("No model is loaded.")
        started = time.perf_counter()
        result = self.model.transcribe(audio=str(args["path"]), language=args.get("language") or None)[0]
        return {
            "text": str(result.text or "").strip(),
            "language": result.language,
            "elapsed": round(time.perf_counter() - started, 3),
            "memory": mw.cuda_memory(),
        }


if __name__ == "__main__":
    mw.serve(AsrEngine())
