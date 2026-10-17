import json
import shutil
import time
from pathlib import Path

from . import engines
from .paths import MODELS_DIR, safe_id

META_FILE = "spectra.json"
PENDING_FILE = ".spectra-download.json"


def folder_name(repo_id: str, subfolder: str | None) -> str:
    name = repo_id.replace("/", "--")
    if subfolder:
        name += "--" + subfolder.replace("/", "--")
    return name


def model_dir(engine_id: str, folder: str) -> Path:
    return MODELS_DIR / safe_id(engine_id) / safe_id(folder)


def _size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        if item.is_file() and ".cache" not in item.parts:
            try:
                total += item.stat().st_size
            except OSError:
                pass
    return total


def _entry(engine_id: str, path: Path) -> dict | None:
    meta_path = path / META_FILE
    pending_path = path / PENDING_FILE
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        status = "ready"
    elif pending_path.is_file():
        meta = json.loads(pending_path.read_text(encoding="utf-8"))
        status = "incomplete"
    else:
        return None
    subfolder = meta.get("subfolder")
    model_path = path / subfolder if subfolder and (path / subfolder).is_dir() else path
    return {
        "id": f"{engine_id}/{path.name}",
        "engine": engine_id,
        "folder": path.name,
        "path": str(path),
        "model_path": str(model_path),
        "status": status,
        "label": meta.get("label") or path.name,
        "repo_id": meta.get("repo_id"),
        "subfolder": meta.get("subfolder"),
        "revision": meta.get("revision"),
        "source_url": meta.get("source_url"),
        "downloaded_at": meta.get("downloaded_at"),
        "note": meta.get("note", ""),
        "size": _size(path),
    }


def list_models() -> list[dict]:
    items = []
    known = engines.manifests()
    if not MODELS_DIR.is_dir():
        return items
    for engine_id in known:
        engine_dir = MODELS_DIR / engine_id
        if not engine_dir.is_dir():
            continue
        for path in sorted(p for p in engine_dir.iterdir() if p.is_dir()):
            entry = _entry(engine_dir.name, path)
            if entry:
                items.append(entry)
    return items


def find_by_repo(repo_id: str, subfolder: str | None) -> list[dict]:
    key = (repo_id.lower(), (subfolder or "").lower())
    return [m for m in list_models() if ((m.get("repo_id") or "").lower(), (m.get("subfolder") or "").lower()) == key]


def get_model(model_id: str) -> dict:
    engine_id, _, folder = str(model_id).partition("/")
    path = model_dir(engine_id, folder)
    entry = _entry(engine_id, path) if path.is_dir() else None
    if entry is None:
        raise FileNotFoundError(f"Model not found: {model_id}")
    return entry


def write_meta(path: Path, meta: dict) -> None:
    meta = dict(meta)
    meta.setdefault("downloaded_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
    (path / META_FILE).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (path / PENDING_FILE).unlink(missing_ok=True)


def write_pending(path: Path, meta: dict) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / PENDING_FILE).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def delete_model(model_id: str) -> None:
    entry = get_model(model_id)
    shutil.rmtree(entry["path"])
