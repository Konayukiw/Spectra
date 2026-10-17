import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"
ENGINES_DIR = ROOT / "engines"
HOME_DIR = Path(os.environ["SPECTRA_HOME"]).resolve() if os.environ.get("SPECTRA_HOME") else Path.home() / ".spectra"
MODELS_DIR = HOME_DIR / "models"
DATA_DIR = HOME_DIR / "data"
CLIPS_DIR = DATA_DIR / "clips"
VOICES_DIR = DATA_DIR / "voices"
HISTORY_DIR = DATA_DIR / "history"
CACHE_DIR = DATA_DIR / "cache"
HF_HOME = CACHE_DIR / "huggingface"
TMP_DIR = DATA_DIR / "tmp"

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def ensure_dirs() -> None:
    for path in (MODELS_DIR, CLIPS_DIR, VOICES_DIR, HISTORY_DIR, CACHE_DIR, HF_HOME, TMP_DIR):
        path.mkdir(parents=True, exist_ok=True)


def safe_id(value: str) -> str:
    value = str(value or "")
    if not _SAFE_ID.match(value) or ".." in value:
        raise ValueError(f"Invalid identifier: {value!r}")
    return value


def venv_python(env_dir: Path) -> Path:
    if os.name == "nt":
        return env_dir / ".venv" / "Scripts" / "python.exe"
    return env_dir / ".venv" / "bin" / "python"
