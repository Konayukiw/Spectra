import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_shared"))

import spectra_worker as mw

import numpy as np
import torch
from qwen_tts import Qwen3TTSModel

DTYPES = {"bf16": torch.bfloat16, "fp16": torch.float16, "fp32": torch.float32}


class Qwen3Engine(mw.Engine):
    name = "qwen3tts"

    def __init__(self) -> None:
        super().__init__()
        self.tts = None
        self.prompts: dict[tuple, list] = {}

    def load(self, model_dir: str, settings: dict, progress: mw.Progress) -> dict:
        device = str(settings.get("device") or "auto")
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype_name = str(settings.get("dtype") or "bf16")
        if device == "cpu" and dtype_name != "fp32":
            dtype_name = "fp32"
        attention = str(settings.get("attention") or "sdpa")
        progress(f"Loading {self.label} ({dtype_name}, {attention})")
        self.tts = Qwen3TTSModel.from_pretrained(
            model_dir,
            device_map="cuda:0" if device == "cuda" else "cpu",
            dtype=DTYPES[dtype_name],
            attn_implementation=attention,
        )
        model = self.tts.model
        if getattr(model, "tts_model_type", None) != "base":
            kind = getattr(model, "tts_model_type", "unknown")
            self.tts = None
            raise ValueError(f"This Qwen3-TTS checkpoint is a '{kind}' model. Only Base checkpoints can clone a voice from reference audio.")
        self.prompts.clear()
        return {
            "device": device,
            "dtype": dtype_name,
            "attention": attention,
            "sample_rate": int(self.tts.model.speech_tokenizer.get_output_sample_rate()),
            "languages": list(model.get_supported_languages() or []),
            "generate_defaults": dict(self.tts.generate_defaults or {}),
        }

    def unload(self) -> None:
        self.tts = None
        self.prompts.clear()

    def _prompt(self, path: str, transcript: str, xvector: bool):
        key = (mw.file_key(path), "" if xvector else transcript, xvector)
        cached = self.prompts.get(key)
        if cached is None:
            cached = self.tts.create_voice_clone_prompt(
                ref_audio=path,
                ref_text=None if xvector else transcript,
                x_vector_only_mode=xvector,
            )
            if len(self.prompts) > 16:
                self.prompts.pop(next(iter(self.prompts)))
            self.prompts[key] = cached
        return cached

    def synthesize(self, request: dict, progress: mw.Progress) -> dict:
        s = dict(request.get("settings") or {})
        references = request.get("references") or []
        if not references:
            raise ValueError("Reference audio is required.")
        ref = references[0]
        transcript = str(ref.get("transcript") or "").strip()
        mode = str(s.get("clone_mode") or "auto")
        if mode == "auto":
            mode = "icl" if transcript else "xvector"
        if mode == "icl" and not transcript:
            raise ValueError("ICL mode needs the transcript of the reference audio. Fill in the transcript or switch Clone mode to 'Speaker embedding only'.")
        messages = []
        if mode == "xvector":
            messages.append("info: cloning from the speaker embedding only (no transcript). Similarity is usually lower than ICL mode.")
        progress("Encoding reference audio")
        prompt = self._prompt(ref["path"], transcript, mode == "xvector")

        def pick(name, cast, off=None):
            value = s.get(name)
            if value is None or value == "" or (off is not None and value == off):
                return None
            return cast(value)

        gen_kwargs = {
            "do_sample": bool(s.get("do_sample", True)),
            "temperature": pick("temperature", float),
            "top_k": pick("top_k", int),
            "top_p": pick("top_p", float),
            "repetition_penalty": pick("repetition_penalty", float),
            "subtalker_dosample": bool(s.get("subtalker_dosample", True)),
            "subtalker_temperature": pick("subtalker_temperature", float),
            "subtalker_top_k": pick("subtalker_top_k", int),
            "subtalker_top_p": pick("subtalker_top_p", float),
            "max_new_tokens": pick("max_new_tokens", int, 0),
        }
        language = str(s.get("language") or "Auto")
        takes_count = max(1, min(8, int(s.get("takes") or 1)))
        seeds = mw.resolve_seeds(s.get("seed", -1), takes_count)
        out_dir = Path(request["output_dir"])
        prefix = str(request.get("prefix") or "take")
        takes = []
        sample_rate = 24000
        for index in range(takes_count):
            seed = seeds[index]
            progress(f"Generating take {index + 1}/{takes_count}", index / takes_count)
            mw.seed_everything(seed)
            wavs, sample_rate = self.tts.generate_voice_clone(
                text=str(request["text"]),
                language=language,
                voice_clone_prompt=prompt,
                **gen_kwargs,
            )
            audio = np.asarray(wavs[0], dtype=np.float32)
            name = f"{prefix}_{index + 1}.wav"
            duration = mw.write_wav(out_dir / name, audio, sample_rate)
            takes.append({"file": name, "duration": round(duration, 3), "seed": seed})
            if gen_kwargs["max_new_tokens"] and duration >= gen_kwargs["max_new_tokens"] / 12.5 - 0.5:
                messages.append(f"warning: take {index + 1} may have hit Max new tokens and been cut off.")
        return {"takes": takes, "sample_rate": int(sample_rate), "seed": seeds[0], "messages": messages, "timings": {}}


if __name__ == "__main__":
    mw.serve(Qwen3Engine())
