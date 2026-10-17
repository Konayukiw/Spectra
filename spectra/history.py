import json
import secrets
import shutil
import time
from pathlib import Path

from .paths import HISTORY_DIR, safe_id

META = "meta.json"


def new_entry() -> tuple[str, Path]:
    entry_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    path = HISTORY_DIR / entry_id
    path.mkdir(parents=True, exist_ok=False)
    return entry_id, path


def save(path: Path, meta: dict) -> None:
    tmp = path / (META + ".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path / META)


def _load(path: Path) -> dict | None:
    meta = path / META
    if not meta.is_file():
        return None
    try:
        return json.loads(meta.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_entries(offset: int = 0, limit: int = 30, query: str = "") -> tuple[list[dict], int]:
    if not HISTORY_DIR.is_dir():
        return [], 0
    dirs = sorted((p for p in HISTORY_DIR.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True)
    items = []
    needle = query.strip().lower()
    for path in dirs:
        meta = _load(path)
        if meta is None:
            continue
        if needle:
            hay = " ".join([meta.get("text", ""), (meta.get("voice") or {}).get("name", "") or "", meta.get("model", {}).get("label", "")]).lower()
            if needle not in hay:
                continue
        items.append(meta)
    return items[offset: offset + limit], len(items)


def get_entry(entry_id: str) -> dict:
    meta = _load(HISTORY_DIR / safe_id(entry_id))
    if meta is None:
        raise FileNotFoundError(f"History entry not found: {entry_id}")
    return meta


def audio_path(entry_id: str, filename: str) -> Path:
    path = HISTORY_DIR / safe_id(entry_id) / safe_id(filename)
    if not path.is_file() or path.suffix.lower() != ".wav":
        raise FileNotFoundError("Audio not found.")
    return path


def delete_entry(entry_id: str) -> None:
    path = HISTORY_DIR / safe_id(entry_id)
    if not path.is_dir():
        raise FileNotFoundError(f"History entry not found: {entry_id}")
    shutil.rmtree(path)


def discard(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
