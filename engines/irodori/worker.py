import math
import os
import sys
import time
import unicodedata
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
from irodori_tts.duration import build_duration_features
from irodori_tts.text_normalization import normalize_text

try:
    from transformers.initialization import no_init_weights
except ImportError:
    from transformers.modeling_utils import no_init_weights

CACHE_DIR = Path(os.environ.get("SPECTRA_CACHE_DIR", Path(__file__).resolve().parent / ".cache")) / "irodori-latents"
_CAST_DTYPE: torch.dtype | None = None
_ORIGINAL_LOADER = rt._load_checkpoint_from_safetensors
_MODEL_CLS = rt.TextToLatentRFDiT

PART_SECONDS = 20.0
PAUSE_SCALES = (("…‥", 1.4), ("。．！!？?", 1.0), ("、，,", 0.5))
DEFAULT_PAUSE = 0.30
PAUSE_RANGE = (0.25, 1.2)
MIN_INNER_PAUSE = 0.2
SPLIT_PAUSE = 0.05
HEAD_MARGIN = 0.04
TAIL_MARGIN = 0.08
FADE_SECONDS = 0.008
SILENCE_RANGE_DB = 40.0
SILENCE_FLOOR_DB = -70.0
SEED_STRIDE = 1000
_OPENERS = "「『（(【［[〈《“‘"
_CLOSERS = "」』）)】］]〉》”’"
_SENTENCE_ENDS = "。．！!？?…‥"
_CLAUSE_ENDS = "、，,"
_JOINERS = "\u200d\ufe0f"
_SKIPPED_CATEGORIES = {"So", "Sk", "Mn", "Me", "Cf"}
_KEPT_MESSAGES = ("warning", "info: predicted", "info: using manual", "info: MeanFlow")
_WATERMARK_WARNING = "warning: SilentCipher watermark is unavailable"
_STAGE_AFTER = {
    "prepare_reference": "Predicting duration",
    "predict_duration": "Sampling",
    "sample_rf": "Decoding audio",
    "sample_meanflow": "Decoding audio",
    "decode_latent": "Finishing",
}


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


def _ends_here(text: str, index: int, enders: str) -> bool:
    char = text[index]
    if char == "\n":
        return True
    if char not in enders:
        return False
    if char != ".":
        return True
    following = text[index + 1] if index + 1 < len(text) else " "
    return following.isspace() or following in enders or following in _CLOSERS


def _split_text(text: str, enders: str, nested: bool) -> list[str]:
    pieces = []
    start = depth = index = 0
    while index < len(text):
        char = text[index]
        if char in _OPENERS:
            depth += 1
        elif char in _CLOSERS:
            depth = max(0, depth - 1)
        if (depth == 0 or not nested or char == "\n") and _ends_here(text, index, enders):
            end = index + 1
            while end < len(text) and (text[end] in enders or text[end] in _CLOSERS or text[end].isspace()):
                if text[end] in _CLOSERS:
                    depth = max(0, depth - 1)
                end += 1
            if "\n" in text[index:end]:
                depth = 0
            pieces.append(text[start:end])
            start = index = end
            continue
        index += 1
    if start < len(text):
        pieces.append(text[start:])
    return pieces


def _split_evenly(text: str, count: int) -> list[str]:
    pieces = []
    start = 0
    for part in range(1, count):
        cut = round(len(text) * part / count)
        while 0 < cut < len(text) and (text[cut] in _JOINERS or text[cut - 1] == "\u200d"):
            cut += 1
        if start < cut < len(text):
            pieces.append(text[start:cut])
            start = cut
    pieces.append(text[start:])
    return pieces


def _fit_piece(piece: str, estimate, budget: float) -> list[str]:
    seconds = estimate(piece)
    if seconds <= budget:
        return [piece]
    for enders in (_SENTENCE_ENDS, _CLAUSE_ENDS):
        pieces = _split_text(piece, enders, nested=False)
        if len(pieces) > 1:
            return [fitted for part in pieces for fitted in _fit_piece(part, estimate, budget)]
    count = math.ceil(seconds / budget) + 1 if math.isfinite(seconds) else math.ceil(len(piece) / 80) + 1
    pieces = _split_evenly(piece, max(2, count))
    if len(pieces) < 2:
        return [piece]
    return [fitted for part in pieces for fitted in _fit_piece(part, estimate, budget)]


def _plan_parts(text: str, estimate, budget: float) -> list[str]:
    units = [fitted for piece in _split_text(text, _SENTENCE_ENDS, nested=True) for fitted in _fit_piece(piece, estimate, budget)]
    parts = []
    current = ""
    for unit in units:
        if normalize_text(current).strip() and estimate(current + unit) > budget:
            parts.append(current)
            current = unit
        else:
            current += unit
    parts.append(current)
    return [part for part in parts if normalize_text(part).strip()]


def _pause_scale(text: str) -> float | None:
    scale = None
    line_break = False
    for char in reversed(text):
        if char == "\n":
            line_break = True
        if char.isspace() or char in _CLOSERS or unicodedata.category(char) in _SKIPPED_CATEGORIES:
            continue
        scale = next((value for chars, value in PAUSE_SCALES if char in chars), None)
        break
    return max(scale or 0.0, 1.0) if line_break else scale


def _pause_seconds(scale: float | None, base: float) -> float:
    return SPLIT_PAUSE if scale is None else base * scale


def _voiced_frames(audio: np.ndarray, sample_rate: int) -> tuple[np.ndarray, int]:
    frame = max(1, sample_rate // 100)
    count = len(audio) // frame
    if count == 0:
        return np.zeros(0, dtype=bool), frame
    rms = np.sqrt(np.mean(np.square(audio[: count * frame].reshape(count, frame)), axis=1) + 1e-12)
    level = 20.0 * np.log10(rms)
    return level > max(float(level.max()) - SILENCE_RANGE_DB, SILENCE_FLOOR_DB), frame


def _typical_pause(parts: list[np.ndarray], sample_rate: int) -> float:
    pauses = []
    for audio in parts:
        voiced, frame = _voiced_frames(audio, sample_rate)
        indices = np.flatnonzero(voiced)
        if indices.size < 2:
            continue
        gaps = np.diff(indices) - 1
        pauses.extend(float(gap) * frame / sample_rate for gap in gaps if gap * frame >= MIN_INNER_PAUSE * sample_rate)
    if not pauses:
        return DEFAULT_PAUSE
    return float(min(max(np.median(pauses), PAUSE_RANGE[0]), PAUSE_RANGE[1]))


def _trim_silence(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    voiced, frame = _voiced_frames(audio, sample_rate)
    voiced = np.flatnonzero(voiced)
    if voiced.size == 0:
        return audio
    start = max(0, int(voiced[0]) * frame - int(HEAD_MARGIN * sample_rate))
    end = min(len(audio), (int(voiced[-1]) + 1) * frame + int(TAIL_MARGIN * sample_rate))
    trimmed = audio[start:end].copy()
    fade = min(int(FADE_SECONDS * sample_rate), len(trimmed) // 2)
    if fade > 0:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        trimmed[:fade] *= ramp
        trimmed[-fade:] *= ramp[::-1]
    return trimmed


def _join_parts(parts: list[np.ndarray], scales: list[float | None], sample_rate: int) -> tuple[np.ndarray, list[int]]:
    base = _typical_pause(parts, sample_rate)
    pieces = []
    cuts = []
    length = 0
    for index, audio in enumerate(parts):
        trimmed = _trim_silence(audio, sample_rate)
        pieces.append(trimmed)
        length += len(trimmed)
        if index < len(scales):
            gap = int(round(max(0.0, _pause_seconds(scales[index], base) - HEAD_MARGIN - TAIL_MARGIN) * sample_rate))
            pieces.append(np.zeros(gap, dtype=np.float32))
            cuts.append(length + gap // 2)
            length += gap
    return np.concatenate(pieces), cuts


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
        watermark = not bool(s.get("disable_watermark", False))
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
        base = dict(
            caption=(str(s.get("caption") or "").strip() or None),
            ref_latents=latents,
            num_candidates=len(seeds),
            decode_mode=str(s.get("decode_mode") or "sequential"),
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
            t_schedule_mode=str(s.get("t_schedule_mode") or "linear"),
            sway_coeff=float(s.get("sway_coeff", -1.0)),
            trim_tail=bool(s.get("trim_tail", True)),
        )
        text = str(request["text"])
        progress("Planning parts")
        started = time.perf_counter()
        parts, part_seconds, plan_messages = self._plan(text, seconds, base)
        plan_seconds = time.perf_counter() - started
        if len(parts) < 2:
            result = self._sample(rt.SamplingRequest(text=text, seconds=seconds, seed=seeds[0], **base), seeds, progress, "", 0.05, 0.95, watermark=watermark)
            sample_rate = result.sample_rate
            audios = [audio.detach().float().cpu().numpy().reshape(-1) for audio in result.audios]
            messages = [m for m in result.messages if m.startswith(_KEPT_MESSAGES)]
            if not watermark:
                messages = [m for m in messages if not m.startswith(_WATERMARK_WARNING)]
            timings = {name: round(sec, 3) for name, sec in result.stage_timings}
            used_seed = result.used_seed
        else:
            audios, sample_rate, messages, timings = self._synthesize_parts(parts, part_seconds, base, seeds, progress, watermark)
            messages = plan_messages + messages
            timings["plan_parts"] = round(plan_seconds, 3)
            used_seed = seeds[0]
        out_dir = Path(request["output_dir"])
        prefix = str(request.get("prefix") or "take")
        takes = []
        for index, audio in enumerate(audios, start=1):
            name = f"{prefix}_{index}.wav"
            duration = mw.write_wav(out_dir / name, audio, sample_rate)
            takes.append({"file": name, "duration": round(duration, 3), "seed": seeds[index - 1]})
        return {
            "takes": takes,
            "sample_rate": sample_rate,
            "seed": used_seed,
            "timings": timings,
            "messages": messages,
        }

    def _duration_estimator(self, base: dict):
        runtime = self.runtime
        cfg = runtime.model_cfg
        if not cfg.use_duration_predictor:
            return None
        model = runtime.model
        device = runtime.model_device
        max_len = runtime.default_text_max_len
        caption_text = base.get("caption") or ""
        probe = rt.SamplingRequest(text="", ref_latents=base["ref_latents"], max_ref_seconds=base["max_ref_seconds"])
        with torch.inference_mode():
            ref_latent, ref_mask = runtime._load_reference_latent(req=probe, batch_size=1, messages=[])
            has_speaker = torch.zeros((1,), dtype=torch.bool, device=device)
            if cfg.use_speaker_condition_resolved and ref_mask is not None:
                has_speaker = ref_mask.any(dim=1)
            caption_ids = caption_mask = has_caption = None
            if cfg.use_caption_condition:
                caption_ids, caption_mask = runtime.caption_tokenizer.batch_encode([caption_text], max_length=runtime.default_caption_max_len)
                if not caption_text:
                    caption_mask.zero_()
                caption_ids, caption_mask = caption_ids.to(device), caption_mask.to(device)
                has_caption = torch.full((1,), bool(caption_text), dtype=torch.bool, device=device)
        frame_seconds = int(runtime.codec.model.hop_length) / float(runtime.codec.sample_rate)
        cache: dict[str, float] = {}

        def estimate(text: str) -> float:
            normalized = normalize_text(text).strip()
            if not normalized:
                return 0.0
            if normalized in cache:
                return cache[normalized]
            if len(runtime.tokenizer.tokenizer.encode(normalized, add_special_tokens=False)) >= max_len:
                cache[normalized] = math.inf
                return math.inf
            with torch.inference_mode():
                ids, mask = runtime.tokenizer.batch_encode([normalized], max_length=max_len)
                ids, mask = ids.to(device), mask.to(device)
                features = build_duration_features([normalized], token_counts=mask.sum(dim=1), max_text_len=max_len, has_speaker=has_speaker).to(device)
                text_state, text_mask, speaker_state, speaker_mask, caption_state, caption_state_mask = model.encode_conditions(
                    text_input_ids=ids,
                    text_mask=mask,
                    ref_latent=ref_latent,
                    ref_mask=ref_mask,
                    caption_input_ids=caption_ids,
                    caption_mask=caption_mask,
                )
                log_frames = model.predict_duration_log_frames(
                    text_state=text_state,
                    text_mask=text_mask,
                    speaker_state=speaker_state,
                    speaker_mask=speaker_mask,
                    caption_state=caption_state,
                    caption_mask=caption_state_mask,
                    duration_features=features,
                    has_speaker=has_speaker,
                    has_caption=has_caption,
                )
            cache[normalized] = float(torch.expm1(log_frames).float().mean().item()) * frame_seconds
            return cache[normalized]

        return estimate

    def _plan(self, text: str, seconds: float | None, base: dict) -> tuple[list[str], list[float | None], list[str]]:
        limit = min(PART_SECONDS, float(base["max_seconds"]))
        if seconds is not None and seconds <= limit:
            return [text], [seconds], []
        estimate = self._duration_estimator(base)
        if estimate is None:
            return [text], [seconds], []
        if seconds is None:
            scale = float(base["duration_scale"])
            if estimate(text) * scale <= limit:
                return [text], [None], []
            parts = _plan_parts(text, estimate, limit / scale)
            if len(parts) < 2:
                return [text], [None], []
            summary = ", ".join(f"{estimate(part) * scale:.0f}" for part in parts)
            return parts, [None] * len(parts), [f"info: long text was split into {len(parts)} parts ({summary} s)."]

        def spread(parts: list[str]) -> float:
            pauses = sum(_pause_seconds(_pause_scale(part), DEFAULT_PAUSE) for part in parts[:-1])
            speech = max(seconds - pauses, 0.5 * len(parts))
            return speech / max(sum(estimate(part) for part in parts), 1e-6)

        parts = _plan_parts(text, estimate, limit)
        ratio = spread(parts)
        if ratio * max(estimate(part) for part in parts) > limit:
            parts = _plan_parts(text, estimate, max(limit / ratio, 2.0))
            ratio = spread(parts)
        if len(parts) < 2:
            return [text], [seconds], []
        lengths = [estimate(part) * ratio for part in parts]
        summary = ", ".join(f"{value:.0f}" for value in lengths)
        return parts, lengths, [f"info: fixed duration {seconds:.1f} s was spread over {len(parts)} parts ({summary} s)."]

    def _sample(self, req, seeds: list[int], progress: mw.Progress, label: str, low: float, high: float, watermark: bool = True):
        is_meanflow = self.runtime.model_cfg.flow_parameterization == "meanflow"
        total_steps = req.num_steps or (4 if is_meanflow else 40)
        prefix = f"{label}: " if label else ""
        seen_t: set[float] = set()
        model = self.runtime.model
        original_forward = model.forward_with_encoded_conditions

        def tracked_forward(*args, **kwargs):
            t = kwargs.get("t")
            if t is not None:
                seen_t.add(round(float(t.reshape(-1)[0].item()), 6))
                done = min(len(seen_t) / total_steps, 1.0)
                progress(f"{prefix}Sampling step {min(len(seen_t), total_steps)}/{total_steps}", low + (high - low) * done)
            return original_forward(*args, **kwargs)

        def on_log(message: str) -> None:
            if not watermark and message.startswith(_WATERMARK_WARNING):
                return
            mw.log(message)
            if message.startswith("[runtime] "):
                stage = message[len("[runtime] "):].split(":")[0].split(" (")[0]
                if stage in _STAGE_AFTER:
                    progress(prefix + _STAGE_AFTER[stage])

        mw.log(f"[spectra] {prefix}take seeds: {', '.join(map(str, seeds))}")
        noise = _PerTakeNoise(seeds)
        watermarker = self.runtime.watermarker
        backend = watermarker.model
        model.forward_with_encoded_conditions = tracked_forward
        rt_rf.torch = rt_meanflow.torch = noise
        if not watermark:
            watermarker.model = None
        try:
            result = self.runtime.synthesize(req, log_fn=on_log)
        finally:
            model.forward_with_encoded_conditions = original_forward
            rt_rf.torch = rt_meanflow.torch = torch
            watermarker.model = backend
        if not noise.used:
            raise RuntimeError("Per-take seeding did not reach the Irodori sampler. The irodori-tts package may have changed.")
        return result

    def _synthesize_parts(self, parts: list[str], part_seconds: list[float | None], base: dict, seeds: list[int], progress: mw.Progress, watermark: bool):
        watermarker = self.runtime.watermarker
        backend = watermarker.model
        weights = np.cumsum([0] + [max(len(part), 1) for part in parts]) / max(sum(len(part) for part in parts), 1)
        collected: list[list[np.ndarray]] = [[] for _ in seeds]
        messages: list[str] = []
        timings: dict[str, float] = {}
        sample_rate = int(self.runtime.codec.sample_rate)
        watermarker.model = None
        try:
            for index, (part, seconds) in enumerate(zip(parts, part_seconds)):
                part_seeds = [seed + index * SEED_STRIDE for seed in seeds]
                label = f"Part {index + 1}/{len(parts)}"
                low = 0.05 + 0.9 * float(weights[index])
                high = 0.05 + 0.9 * float(weights[index + 1])
                mw.log(f"[spectra] {label}: {part}")
                result = self._sample(rt.SamplingRequest(text=part, seconds=seconds, seed=part_seeds[0], **base), part_seeds, progress, label, low, high, watermark=False)
                sample_rate = result.sample_rate
                for take, audio in enumerate(result.audios):
                    collected[take].append(audio.detach().float().cpu().numpy().reshape(-1))
                for name, sec in result.stage_timings:
                    timings[name] = timings.get(name, 0.0) + sec
                for message in result.messages:
                    if message.startswith("warning") and not message.startswith(_WATERMARK_WARNING) and message not in messages:
                        messages.append(message)
        finally:
            watermarker.model = backend
        progress("Joining parts", 0.96)
        started = time.perf_counter()
        scales = [_pause_scale(part) for part in parts[:-1]]
        joined = [_join_parts(pieces, scales, sample_rate) for pieces in collected]
        timings["join_parts"] = time.perf_counter() - started
        audios = [audio for audio, _ in joined]
        if watermark and watermarker.ready:
            progress("Watermarking", 0.97)
            started = time.perf_counter()
            audios = [self._watermark_joined(audio, sample_rate, cuts) for audio, cuts in joined]
            timings["silentcipher_watermark"] = time.perf_counter() - started
        elif watermark:
            messages.append(f"{_WATERMARK_WARNING}, so the audio was not watermarked.")
        return audios, sample_rate, messages, {name: round(sec, 3) for name, sec in timings.items()}

    def _watermark_joined(self, audio: np.ndarray, sample_rate: int, cuts: list[int]) -> np.ndarray:
        watermarker = self.runtime.watermarker
        config = watermarker.model.config
        period = int(config.HOP_LENGTH) * int(config.message_len)
        ratio = float(watermarker.model.sr) / float(sample_rate)
        bounds = [0] + cuts + [len(audio)]
        pieces = []
        for start, end in zip(bounds[:-1], bounds[1:]):
            if end <= start:
                continue
            origin = min(start, int(round(math.floor(start * ratio / period) * period / ratio)))
            marked = watermarker.encode_one(torch.from_numpy(np.ascontiguousarray(audio[origin:end])), sample_rate=sample_rate)
            marked = marked.numpy().reshape(-1)[: end - origin]
            if len(marked) < end - origin:
                marked = np.concatenate([marked, audio[origin + len(marked):end]])
            pieces.append(marked[start - origin:].astype(np.float32))
        return np.concatenate(pieces)


if __name__ == "__main__":
    mw.serve(IrodoriEngine())
