import json
import shutil
import subprocess
import threading
from functools import lru_cache
from pathlib import Path

from .jobs import Job, JobStore
from .paths import ENGINES_DIR, venv_python


@lru_cache(maxsize=1)
def manifests() -> dict[str, dict]:
    items = []
    for path in ENGINES_DIR.glob("*/engine.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["dir"] = str(path.parent)
        items.append(data)
    items.sort(key=lambda d: (d.get("order", 99), d["id"]))
    return {data["id"]: data for data in items}


def get(engine_id: str) -> dict:
    engine = manifests().get(engine_id)
    if engine is None:
        raise KeyError(f"Unknown engine: {engine_id}")
    return engine


def installed(engine_id: str) -> bool:
    return venv_python(ENGINES_DIR / engine_id).is_file()


def defaults(engine_id: str, scope: str | None = None) -> dict:
    return {
        s["key"]: s.get("default")
        for s in get(engine_id)["settings"]
        if scope is None or s.get("scope", "generate") == scope
    }


def split_settings(engine_id: str, values: dict) -> tuple[dict, dict]:
    load, generate = {}, {}
    for spec in get(engine_id)["settings"]:
        key = spec["key"]
        value = values.get(key, spec.get("default"))
        value = _coerce(spec, value)
        (load if spec.get("scope", "generate") == "load" else generate)[key] = value
    return load, generate


def _coerce(spec: dict, value):
    kind = spec.get("type")
    if value is None:
        return spec.get("default")
    try:
        if kind in ("int", "seed"):
            value = int(float(value))
        elif kind == "float":
            value = float(value)
        elif kind == "bool":
            value = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "on", "yes")
        elif kind == "select":
            allowed = [str(o["value"]) for o in spec.get("options", [])]
            value = str(value)
            if value not in allowed:
                value = spec.get("default")
        else:
            value = str(value)
    except (TypeError, ValueError):
        return spec.get("default")
    if kind in ("int", "float"):
        lo, hi = spec.get("min"), spec.get("max")
        if lo is not None and value < lo:
            value = type(value)(lo)
        if hi is not None and value > hi:
            value = type(value)(hi)
    return value


def public() -> list[dict]:
    items = []
    for engine_id, data in manifests().items():
        entry = {k: v for k, v in data.items() if k not in ("dir", "download")}
        entry["installed"] = installed(engine_id)
        items.append(entry)
    return items


def uv_sync(jobs: JobStore, job: Job, project: Path) -> bool:
    uv = shutil.which("uv")
    if uv is None:
        jobs.fail(job, "uv was not found on PATH. Install uv from https://docs.astral.sh/uv/ and retry.")
        return False
    cmd = [uv, "sync", "--project", str(project)]
    jobs.log(job, "$ " + " ".join(cmd))
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        job.on_cancel = proc.kill
        for line in proc.stdout:
            jobs.log(job, line)
            job.stage = line.strip()[:120] or job.stage
        code = proc.wait()
    except Exception as exc:
        jobs.fail(job, f"{type(exc).__name__}: {exc}")
        return False
    if code != 0:
        jobs.fail(job, "Installation stopped." if job.cancel_requested else f"uv sync failed with exit code {code}. See the log for details.")
        return False
    return True


def start_install(jobs: JobStore, engine_id: str) -> Job:
    get(engine_id)
    for job in jobs.list(kind="install", active_only=True):
        if job.meta.get("engine") == engine_id:
            return job
    job = jobs.create("install", f"Install {get(engine_id)['name']} environment", engine=engine_id)

    def run() -> None:
        jobs.start(job, "Resolving and installing packages")
        if uv_sync(jobs, job, ENGINES_DIR / engine_id):
            jobs.finish(job, {"engine": engine_id})

    threading.Thread(target=run, daemon=True, name=f"install-{engine_id}").start()
    return job
