import math
import shutil
import subprocess
import wave
from functools import lru_cache
from pathlib import Path

import numpy as np

CLIP_SAMPLE_RATE = 48000


@lru_cache(maxsize=1)
def ffmpeg_exe() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise RuntimeError("ffmpeg was not found. Install ffmpeg or add it to PATH.") from exc


def convert_to_wav(src: Path, dst: Path, sample_rate: int = CLIP_SAMPLE_RATE) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".tmp.wav")
    cmd = [
        ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src), "-vn", "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le", str(tmp),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        message = (proc.stderr or "").strip().splitlines()
        raise ValueError("Could not decode audio: " + (message[-1] if message else "unknown ffmpeg error"))
    tmp.replace(dst)


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as handle:
        sr = handle.getframerate()
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        frames = handle.readframes(handle.getnframes())
    if width == 2:
        data = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        data = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0
    elif width == 1:
        data = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(f"Unsupported WAV sample width: {width}")
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data, sr


def write_wav(path: Path, data: np.ndarray, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(np.asarray(data, dtype=np.float32), -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(sample_rate))
        handle.writeframes(pcm.tobytes())


def _db(value: float) -> float:
    return round(20.0 * math.log10(max(value, 1e-9)), 1)


def analyze(path: Path, bins: int = 96) -> dict:
    data, sr = read_wav(path)
    duration = len(data) / float(sr) if sr else 0.0
    if len(data) == 0:
        return {"duration": 0.0, "sample_rate": sr, "peak_db": -180.0, "rms_db": -180.0, "peaks": [0.0] * bins}
    peak = float(np.max(np.abs(data)))
    rms = float(np.sqrt(np.mean(np.square(data))))
    edges = np.linspace(0, len(data), bins + 1).astype(int)
    peaks = []
    for start, end in zip(edges[:-1], edges[1:]):
        chunk = data[start:max(end, start + 1)]
        peaks.append(round(float(np.max(np.abs(chunk))) if len(chunk) else 0.0, 3))
    top = max(peaks) or 1.0
    return {
        "duration": round(duration, 3),
        "sample_rate": sr,
        "peak_db": _db(peak),
        "rms_db": _db(rms),
        "clipping": peak >= 0.999,
        "peaks": [round(p / top, 3) for p in peaks],
    }


def concat_wavs(paths: list[Path], dst: Path, gap_seconds: float = 0.3) -> None:
    pieces = []
    rate = None
    for path in paths:
        data, sr = read_wav(path)
        if rate is None:
            rate = sr
        elif sr != rate:
            raise ValueError("Reference clips have different sample rates.")
        if pieces:
            pieces.append(np.zeros(int(gap_seconds * sr), dtype=np.float32))
        pieces.append(data)
    if rate is None:
        raise ValueError("No reference clips to concatenate.")
    write_wav(dst, np.concatenate(pieces), rate)
