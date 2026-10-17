import json
import re
import secrets
import time
from pathlib import Path

from . import clips
from .paths import VOICES_DIR, safe_id


def _path(voice_id: str) -> Path:
    return VOICES_DIR / f"{safe_id(voice_id)}.json"


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32]
    return slug or "voice"


def _expand(voice: dict) -> dict:
    expanded = dict(voice)
    items = []
    for clip in voice.get("clips", []):
        try:
            data = clips.info(clip["id"])
        except (FileNotFoundError, ValueError):
            data = {"id": clip["id"], "name": "(missing clip)", "missing": True, "duration": 0, "peaks": []}
        items.append({**data, "transcript": clip.get("transcript", "")})
    expanded["clips"] = items
    expanded["total_duration"] = round(sum(c.get("duration", 0) for c in items), 2)
    return expanded


def _clean_clips(items: list[dict]) -> list[dict]:
    cleaned = []
    for item in items or []:
        clip_id = safe_id(str(item.get("id", "")))
        if not clips.exists(clip_id):
            raise ValueError(f"Reference clip not found: {clip_id}")
        cleaned.append({"id": clip_id, "transcript": str(item.get("transcript") or "").strip()})
    if not cleaned:
        raise ValueError("A voice needs at least one reference clip.")
    return cleaned


def list_voices() -> list[dict]:
    voices = []
    for path in VOICES_DIR.glob("*.json"):
        try:
            voices.append(_expand(json.loads(path.read_text(encoding="utf-8"))))
        except Exception:
            continue
    return sorted(voices, key=lambda v: v.get("updated_at", 0), reverse=True)


def get_voice(voice_id: str) -> dict:
    path = _path(voice_id)
    if not path.is_file():
        raise FileNotFoundError(f"Voice not found: {voice_id}")
    return _expand(json.loads(path.read_text(encoding="utf-8")))


def _write(voice: dict) -> None:
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _path(voice["id"]).with_suffix(".tmp")
    tmp.write_text(json.dumps(voice, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_path(voice["id"]))


def create_voice(name: str, items: list[dict], notes: str = "") -> dict:
    name = str(name or "").strip()
    if not name:
        raise ValueError("Give the voice a name.")
    now = time.time()
    voice = {
        "id": f"{_slug(name)}-{secrets.token_hex(3)}",
        "name": name[:80],
        "notes": str(notes or "")[:2000],
        "clips": _clean_clips(items),
        "created_at": now,
        "updated_at": now,
    }
    _write(voice)
    return _expand(voice)


def update_voice(voice_id: str, name: str | None = None, notes: str | None = None, items: list[dict] | None = None) -> dict:
    path = _path(voice_id)
    if not path.is_file():
        raise FileNotFoundError(f"Voice not found: {voice_id}")
    voice = json.loads(path.read_text(encoding="utf-8"))
    if name is not None:
        if not str(name).strip():
            raise ValueError("Give the voice a name.")
        voice["name"] = str(name).strip()[:80]
    if notes is not None:
        voice["notes"] = str(notes)[:2000]
    if items is not None:
        voice["clips"] = _clean_clips(items)
    voice["updated_at"] = time.time()
    _write(voice)
    return _expand(voice)


def delete_voice(voice_id: str) -> None:
    path = _path(voice_id)
    if not path.is_file():
        raise FileNotFoundError(f"Voice not found: {voice_id}")
    path.unlink()
