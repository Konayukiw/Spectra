import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_shared"))

import spectra_worker as mw

import torch
from voxcpm import VoxCPM
from voxcpm.model.utils import next_and_close

CACHE_DIR = Path(os.environ.get("SPECTRA_CACHE_DIR", Path(__file__).resolve().parent / ".cache")) / "voxcpm-denoised"
DENOISER_ID = "iic/speech_zipenhancer_ans_multiloss_16k_base"


class VoxCPMEngine(mw.Engine):
    name = "voxcpm"

    def __init__(self) -> None:
        super().__init__()
        self.model = None
        self.denoiser = None
        self.normalizer = None
        self.prompts: dict[tuple, dict] = {}

    def load(self, model_dir: str, settings: dict, progress: mw.Progress) -> dict:
        device = str(settings.get("device") or "auto")
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        progress(f"Loading {self.label}")
        self.model = VoxCPM(
            voxcpm_model_path=model_dir,
            zipenhancer_model_path=None,
            enable_denoiser=False,
            optimize=False,
            device=device,
        )
        self.prompts.clear()
        tts = self.model.tts_model
        return {
            "device": device,
            "dtype": str(tts.config.dtype),
            "sample_rate": int(tts.sample_rate),
            "architecture": type(tts).__name__,
        }

    def unload(self) -> None:
        self.model = None
        self.denoiser = None
        self.prompts.clear()

    def _denoised(self, path: str, progress: mw.Progress) -> str:
        target = CACHE_DIR / f"{mw.file_key(path)}.wav"
        if target.is_file():
            return str(target)
        if self.denoiser is None:
            progress("Loading denoiser (first use downloads it)")
            from voxcpm.zipenhancer import ZipEnhancer

            self.denoiser = ZipEnhancer(DENOISER_ID)
        progress("Denoising reference audio")
        target.parent.mkdir(parents=True, exist_ok=True)
        self.denoiser.enhance(path, output_path=str(target))
        return str(target)

    def synthesize(self, request: dict, progress: mw.Progress) -> dict:
        s = dict(request.get("settings") or {})
        references = request.get("references") or []
        if not references:
            raise ValueError("Reference audio is required.")
        ref = references[0]
        transcript = str(ref.get("transcript") or "").strip()
        mode = str(s.get("clone_mode") or "auto")
        if mode == "auto":
            mode = "combined" if transcript else "reference"
        if mode in ("continuation", "combined") and not transcript:
            raise ValueError("This clone mode needs the transcript of the reference audio. Fill in the transcript or switch Clone mode to 'Reference only'.")
        is_v2 = type(self.model.tts_model).__name__ == "VoxCPM2Model"
        if mode in ("reference", "combined") and not is_v2:
            mode = "continuation"
            if not transcript:
                raise ValueError("VoxCPM 1.x checkpoints only support continuation cloning, which needs the reference transcript.")
        path = ref["path"]
        if bool(s.get("denoise", False)):
            path = self._denoised(path, progress)
        trim_vad = bool(s.get("trim_silence", False))
        key = (mw.file_key(path), mode, transcript if mode != "reference" else "", trim_vad)
        cache = self.prompts.get(key)
        if cache is None:
            progress("Encoding reference audio")
            kwargs = {"trim_silence_vad": trim_vad}
            if mode in ("reference", "combined"):
                kwargs["reference_wav_path"] = path
            if mode in ("continuation", "combined"):
                kwargs["prompt_wav_path"] = path
                kwargs["prompt_text"] = transcript
            if not is_v2:
                kwargs.pop("trim_silence_vad", None)
            cache = self.model.tts_model.build_prompt_cache(**kwargs)
            if len(self.prompts) > 16:
                self.prompts.pop(next(iter(self.prompts)))
            self.prompts[key] = cache
        text = re.sub(r"\s+", " ", str(request["text"]).replace("\n", " ")).strip()
        messages = []
        style = str(s.get("style") or "").strip()
        if style:
            if mode == "reference":
                text = f"({style.strip('()（）')}){text}"
            else:
                messages.append("info: the style instruction is only applied in 'Reference only' mode, so it was ignored.")
        if bool(s.get("normalize", False)):
            if self.normalizer is None:
                from voxcpm.utils.text_normalize import TextNormalizer

                self.normalizer = TextNormalizer()
            text = self.normalizer.normalize(text)
        takes_count = max(1, min(8, int(s.get("takes") or 1)))
        seeds = mw.resolve_seeds(s.get("seed", -1), takes_count)
        out_dir = Path(request["output_dir"])
        prefix = str(request.get("prefix") or "take")
        sample_rate = int(self.model.tts_model.sample_rate)
        takes = []
        for index in range(takes_count):
            seed = seeds[index]
            progress(f"Generating take {index + 1}/{takes_count}", index / takes_count)
            mw.seed_everything(seed)
            wav, _, _ = next_and_close(
                self.model.tts_model._generate_with_prompt_cache(
                    target_text=text,
                    prompt_cache=cache,
                    min_len=int(s.get("min_len") or 2),
                    max_len=int(s.get("max_len") or 4096),
                    inference_timesteps=int(s.get("inference_timesteps") or 10),
                    cfg_value=float(s.get("cfg_value") or 2.0),
                    retry_badcase=bool(s.get("retry_badcase", True)),
                    retry_badcase_max_times=int(s.get("retry_badcase_max_times") or 3),
                    retry_badcase_ratio_threshold=float(s.get("retry_badcase_ratio_threshold") or 6.0),
                    streaming=False,
                )
            )
            audio = wav.squeeze(0).float().cpu().numpy()
            name = f"{prefix}_{index + 1}.wav"
            duration = mw.write_wav(out_dir / name, audio, sample_rate)
            takes.append({"file": name, "duration": round(duration, 3), "seed": seed})
        messages.insert(0, f"info: clone mode = {mode}.")
        return {"takes": takes, "sample_rate": sample_rate, "seed": seeds[0], "messages": messages, "timings": {}}


if __name__ == "__main__":
    mw.serve(VoxCPMEngine())
