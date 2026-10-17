import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_shared"))

import spectra_worker as mw

import numpy as np
import soundfile as sf
import torch
from safetensors import safe_open

from irodori_tts import inference_runtime as rt
from irodori_tts import meanflow as rt_meanflow
from irodori_tts import rf as rt_rf

try:
    from transformers.initialization import no_init_weights
except ImportError:
    from transformers.modeling_utils import no_init_weights

CACHE_DIR = Path(os.environ.get("SPECTRA_CACHE_DIR", Path(__file__).resolve().parent / ".cache")) / "irodori-latents"
_CAST_DTYPE: torch.dtype | None = None
_ORIGINAL_LOADER = rt._load_checkpoint_from_safetensors
_MODEL_CLS = rt.TextToLatentRFDiT


def _build_model_without_random_init(*args, **kwargs):
    with no_init_weights():
        return _MODEL_CLS(*args, **kwargs)


def _load_safetensors_with_cast(path: Path):
    if _CAST_DTYPE is None:
        return _ORIGINAL_LOADER(path)
    with safe_open(str(path), framework="pt", device="cpu") as handle:
        metadata = handle.metadata() or {}
        if rt.parse_quantization_metadata(metadata) is not None:
            return _ORIGINAL_LOADER(path)
        state = {}
        for name in handle.keys():
            tensor = handle.get_tensor(name)
            if tensor.is_floating_point() and tensor.dtype != _CAST_DTYPE:
                tensor = tensor.to(_CAST_DTYPE)
            state[name] = tensor
    flat_config = rt._parse_json_mapping(metadata.get(rt._CONFIG_META_KEY), field=rt._CONFIG_META_KEY, path=path, required=True)
    text_encoder_config = rt._parse_json_mapping(metadata.get(rt._TEXT_ENCODER_CONFIG_META_KEY), field=rt._TEXT_ENCODER_CONFIG_META_KEY, path=path)
    model_cfg, inference_cfg = rt._split_flat_checkpoint_config(path=path, flat_config=flat_config)
    return state, model_cfg, inference_cfg, text_encoder_config


rt.TextToLatentRFDiT = _build_model_without_random_init
rt._load_checkpoint_from_safetensors = _load_safetensors_with_cast


class _PerTakeNoise:
    def __init__(self, seeds: list[int]) -> None:
        self.seeds = seeds
        self.used = False

    def __getattr__(self, name):
        return getattr(torch, name)

    def randn(self, *size, generator=None, **kwargs):
        shape = tuple(size[0]) if len(size) == 1 and isinstance(size[0], (tuple, list, torch.Size)) else tuple(size)
        if self.used or generator is None or len(shape) < 2 or shape[0] != len(self.seeds):
            return torch.randn(*size, generator=generator, **kwargs)
        self.used = True
        return torch.cat([
            torch.randn((1, *shape[1:]), generator=torch.Generator(device=generator.device).manual_seed(seed), **kwargs)
            for seed in self.seeds
        ])


def _checkpoint_stats(path: Path) -> dict:
    params = 0
    dtypes: dict[str, int] = {}
    with safe_open(str(path), framework="pt", device="cpu") as handle:
        metadata = handle.metadata() or {}
        quantized = rt.parse_quantization_metadata(metadata) is not None
        for name in handle.keys():
            sl = handle.get_slice(name)
            count = int(np.prod(sl.get_shape())) if sl.get_shape() else 1
            params += count
            dtype = str(sl.get_dtype())
            dtypes[dtype] = dtypes.get(dtype, 0) + count
    stored = max(dtypes, key=dtypes.get) if dtypes else "F32"
    return {"params": params, "stored_dtype": stored, "quantized": quantized}


def _find_checkpoint(model_dir: Path) -> Path:
    direct = model_dir / "model.safetensors"
    if direct.is_file():
        return direct
    found = sorted(model_dir.rglob("model.safetensors"))
    if not found:
        found = sorted(model_dir.rglob("*.safetensors"))
    if not found:
        raise FileNotFoundError(f"No model.safetensors found in {model_dir}")
    return found[0]


class IrodoriEngine(mw.Engine):
    name = "irodori"

    def __init__(self) -> None:
        super().__init__()
        self.runtime = None
        self.precision = "fp32"

    def load(self, model_dir: str, settings: dict, progress: mw.Progress) -> dict:
        global _CAST_DTYPE
        checkpoint = _find_checkpoint(Path(model_dir))
        stats = _checkpoint_stats(checkpoint)
        device = str(settings.get("device") or "auto")
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        precision = str(settings.get("precision") or "auto")
        notes = []
        if precision == "auto":
            if device != "cuda":
                precision = "fp32"
            elif stats["quantized"] or stats["stored_dtype"] == "BF16":
                precision = "bf16"
            else:
                total = torch.cuda.get_device_properties(0).total_memory
                precision = "bf16" if stats["params"] * 4 + 3 * 2**30 > total else "fp32"
            notes.append(f"Auto precision selected {precision}.")
        if precision == "bf16" and device not in ("cuda", "xpu"):
            raise ValueError("BF16 precision needs a CUDA GPU. Use FP32 on CPU.")
        codec_precision = str(settings.get("codec_precision") or "fp32")
        if codec_precision == "bf16" and device not in ("cuda", "xpu"):
            codec_precision = "fp32"
        _CAST_DTYPE = torch.bfloat16 if precision == "bf16" else None
        progress(f"Loading {self.label} ({precision})")
        key = rt.RuntimeKey(
            checkpoint=str(checkpoint),
            model_device=device,
            model_precision=precision,
            codec_device=device,
            codec_precision=codec_precision,
        )
        try:
            self.runtime = rt.InferenceRuntime.from_key(key)
        finally:
            _CAST_DTYPE = None
        self.precision = precision
        cfg = self.runtime.model_cfg
        return {
            "device": device,
            "precision": precision,
            "params": stats["params"],
            "sample_rate": int(self.runtime.codec.sample_rate),
            "max_ref_seconds": self.runtime.default_max_ref_seconds,
            "capabilities": {
                "caption": bool(cfg.use_caption_condition),
                "speaker": bool(cfg.use_speaker_condition_resolved),
                "duration_predictor": bool(cfg.use_duration_predictor),
                "meanflow": cfg.flow_parameterization == "meanflow",
                "watermark": bool(self.runtime.watermarker.ready),
            },
            "notes": notes,
        }

    def unload(self) -> None:
        if self.runtime is not None:
            self.runtime.unload()
            self.runtime = None

    def _reference_latent(self, path: str, normalize_db, ensure_max: bool) -> str:
        codec_tag = self.runtime.key.codec_repo.replace("/", "--")
        norm_tag = "none" if normalize_db is None else f"{float(normalize_db):.2f}"
        target = CACHE_DIR / codec_tag / f"{mw.file_key(path)}_{norm_tag}_{int(ensure_max)}.pt"
        if target.is_file():
            return str(target)
        data, sr = sf.read(path, dtype="float32", always_2d=True)
        wav = torch.from_numpy(np.ascontiguousarray(data.T))
        if wav.shape[0] > 1:
            wav = wav.mean(dim=0, keepdim=True)
        with torch.inference_mode():
            latent = self.runtime.codec.encode_waveform(
                wav.unsqueeze(0),
                sample_rate=int(sr),
                normalize_db=normalize_db,
                ensure_max=ensure_max,
            ).cpu()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        torch.save(latent[0].contiguous(), tmp)
        tmp.replace(target)
        return str(target)

    def synthesize(self, request: dict, progress: mw.Progress) -> dict:
        s = dict(request.get("settings") or {})
        references = request.get("references") or []
        if not references:
            raise ValueError("Reference audio is required.")
        normalize = bool(s.get("ref_normalize", True))
        normalize_db = float(s.get("ref_normalize_db", -16.0)) if normalize else None
        ensure_max = True
        progress("Encoding reference audio")
        latents = [self._reference_latent(ref["path"], normalize_db, ensure_max) for ref in references]

        def opt_float(name, off=0.0):
            value = s.get(name)
            if value is None or value == "":
                return None
            value = float(value)
            return None if value == off else value

        num_steps = int(s.get("num_steps") or 0)
        seconds = opt_float("seconds")
        rescale_k = opt_float("rescale_k")
        rescale_sigma = opt_float("rescale_sigma")
        if (rescale_k is None) != (rescale_sigma is None):
            rescale_k = rescale_sigma = None
        kv_scale = opt_float("speaker_kv_scale")
        kv_layers = int(s.get("speaker_kv_max_layers") or 0)
        max_ref = float(s.get("max_ref_seconds") or 0)
        seeds = mw.resolve_seeds(s.get("seed", -1), max(1, min(8, int(s.get("takes") or 1))))
        req = rt.SamplingRequest(
            text=str(request["text"]),
            caption=(str(s.get("caption") or "").strip() or None),
            ref_latents=latents,
            num_candidates=len(seeds),
            decode_mode=str(s.get("decode_mode") or "sequential"),
            seconds=seconds,
            duration_scale=float(s.get("duration_scale") or 1.0),
            max_seconds=float(s.get("max_seconds") or 30.0),
            max_ref_seconds=None if max_ref <= 0 else max_ref,
            num_steps=None if num_steps <= 0 else num_steps,
            cfg_scale_text=float(s.get("cfg_scale_text", 3.0)),
            cfg_scale_caption=float(s.get("cfg_scale_caption", 3.0)),
            cfg_scale_speaker=float(s.get("cfg_scale_speaker", 5.0)),
            cfg_guidance_mode=str(s.get("cfg_guidance_mode") or "independent"),
            cfg_min_t=float(s.get("cfg_min_t", 0.5)),
            cfg_max_t=float(s.get("cfg_max_t", 1.0)),
            truncation_factor=opt_float("truncation_factor"),
            rescale_k=rescale_k,
            rescale_sigma=rescale_sigma,
            context_kv_cache=bool(s.get("context_kv_cache", True)),
            speaker_kv_scale=kv_scale,
            speaker_kv_min_t=float(s.get("speaker_kv_min_t", 0.9)) if kv_scale is not None else None,
            speaker_kv_max_layers=kv_layers if kv_layers > 0 else None,
            seed=seeds[0],
            t_schedule_mode=str(s.get("t_schedule_mode") or "linear"),
            sway_coeff=float(s.get("sway_coeff", -1.0)),
            trim_tail=bool(s.get("trim_tail", True)),
        )
        is_meanflow = self.runtime.model_cfg.flow_parameterization == "meanflow"
        total_steps = req.num_steps or (4 if is_meanflow else 40)
        seen_t: set[float] = set()
        model = self.runtime.model
        original_forward = model.forward_with_encoded_conditions

        def tracked_forward(*args, **kwargs):
            t = kwargs.get("t")
            if t is not None:
                seen_t.add(round(float(t.reshape(-1)[0].item()), 6))
                progress(f"Sampling step {min(len(seen_t), total_steps)}/{total_steps}", min(len(seen_t) / total_steps, 1.0) * 0.9 + 0.05)
            return original_forward(*args, **kwargs)

        stage_after = {
            "prepare_reference": "Predicting duration",
            "predict_duration": "Sampling",
            "sample_rf": "Decoding audio",
            "sample_meanflow": "Decoding audio",
            "decode_latent": "Finishing",
        }

        def on_log(message: str) -> None:
            mw.log(message)
            if message.startswith("[runtime] "):
                stage = message[len("[runtime] "):].split(":")[0].split(" (")[0]
                if stage in stage_after:
                    progress(stage_after[stage])

        mw.log(f"[spectra] take seeds: {', '.join(map(str, seeds))}")
        noise = _PerTakeNoise(seeds)
        model.forward_with_encoded_conditions = tracked_forward
        rt_rf.torch = rt_meanflow.torch = noise
        try:
            result = self.runtime.synthesize(req, log_fn=on_log)
        finally:
            model.forward_with_encoded_conditions = original_forward
            rt_rf.torch = rt_meanflow.torch = torch
        if not noise.used:
            raise RuntimeError("Per-take seeding did not reach the Irodori sampler. The irodori-tts package may have changed.")
        out_dir = Path(request["output_dir"])
        prefix = str(request.get("prefix") or "take")
        takes = []
        for index, audio in enumerate(result.audios, start=1):
            name = f"{prefix}_{index}.wav"
            duration = mw.write_wav(out_dir / name, audio.detach().float().cpu().numpy(), result.sample_rate)
            takes.append({"file": name, "duration": round(duration, 3), "seed": seeds[index - 1]})
        messages = [m for m in result.messages if m.startswith(("warning", "info: predicted", "info: using manual", "info: MeanFlow"))]
        return {
            "takes": takes,
            "sample_rate": result.sample_rate,
            "seed": result.used_seed,
            "timings": {name: round(sec, 3) for name, sec in result.stage_timings},
            "messages": messages,
        }


if __name__ == "__main__":
    mw.serve(IrodoriEngine())
