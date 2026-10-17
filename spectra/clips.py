import hashlib
import json
import shutil
import time
import uuid
from pathlib import Path

from . import audio
from .paths import CACHE_DIR, CLIPS_DIR, TMP_DIR, safe_id


def _meta_path(clip_id: str) -> Path:
    return CLIPS_DIR / f"{safe_id(clip_id)}.json"


def wav_path(clip_id: str) -> Path:
    return CLIPS_DIR / f"{safe_id(clip_id)}.wav"


def exists(clip_id: str) -> bool:
    try:
        return wav_path(clip_id).is_file()
    except ValueError:
        return False


def info(clip_id: str) -> dict:
    meta = _meta_path(clip_id)
    if not meta.is_file():
        raise FileNotFoundError(f"Reference clip not found: {clip_id}")
    return json.loads(meta.read_text(encoding="utf-8"))


def ingest(src: Path, original_name: str) -> dict:
    digest = hashlib.sha256()
    with src.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    clip_id = digest.hexdigest()[:20]
    target = wav_path(clip_id)
    if not target.is_file():
        audio.convert_to_wav(src, target)
    meta_file = _meta_path(clip_id)
    if meta_file.is_file():
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    else:
        meta = {"id": clip_id, "name": original_name, "created_at": time.time(), **audio.analyze(target)}
        meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def ingest_upload(stream, original_name: str) -> dict:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(original_name or "clip").suffix[:12] or ".bin"
    tmp = TMP_DIR / f"upload_{uuid.uuid4().hex}{suffix}"
    try:
        with tmp.open("wb") as handle:
            shutil.copyfileobj(stream, handle, 1 << 20)
        if tmp.stat().st_size == 0:
            raise ValueError("Uploaded file is empty.")
        return ingest(tmp, original_name or "clip")
    finally:
        tmp.unlink(missing_ok=True)


def concatenated(clip_ids: list[str], gap_seconds: float = 0.3) -> Path:
    key = hashlib.sha256(("|".join(clip_ids) + f"|{gap_seconds}").encode()).hexdigest()[:20]
    target = CACHE_DIR / "concat" / f"{key}.wav"
    if not target.is_file():
        audio.concat_wavs([wav_path(c) for c in clip_ids], target, gap_seconds=gap_seconds)
    return target
