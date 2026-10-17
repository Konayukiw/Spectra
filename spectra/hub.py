import fnmatch
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from urllib.parse import urlparse

from huggingface_hub import HfApi, get_session, hf_hub_url
from huggingface_hub.errors import GatedRepoError, RepositoryNotFoundError, RevisionNotFoundError
from huggingface_hub.utils import build_hf_headers

from . import engines, models
from .jobs import Job, JobStore
from .paths import HF_HOME, ROOT

_REPO_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def parse_link(text: str) -> dict:
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("Paste a Hugging Face model link or a repo id such as owner/name.")
    if "://" in raw or raw.startswith(("huggingface.co/", "hf.co/", "www.huggingface.co/")):
        parsed = urlparse(raw if "://" in raw else "https://" + raw)
        host = parsed.netloc.lower()
        if host not in ("huggingface.co", "www.huggingface.co", "hf.co"):
            raise ValueError(f"Not a Hugging Face link: {host}")
        path = parsed.path
    else:
        path = raw.split("?")[0].split("#")[0]
    parts = [p for p in path.strip("/").split("/") if p]
    if parts and parts[0] in ("datasets", "spaces"):
        raise ValueError("Dataset and Space links are not supported. Use a model repository link.")
    if parts and parts[0] == "models":
        parts = parts[1:]
    if len(parts) < 2:
        raise ValueError("Could not find owner/name in the link.")
    owner, name = parts[0], parts[1]
    revision = None
    if "@" in name:
        name, revision = name.split("@", 1)
    subfolder_parts: list[str] = []
    rest = parts[2:]
    if rest and rest[0] in ("tree", "blob", "resolve"):
        if len(rest) < 2:
            raise ValueError("The link is missing a revision after /tree/.")
        revision = rest[1]
        subfolder_parts = rest[2:]
        if rest[0] in ("blob", "resolve") and subfolder_parts:
            subfolder_parts = subfolder_parts[:-1]
    else:
        subfolder_parts = rest
    for part in (owner, name, *subfolder_parts):
        if not _REPO_PART.match(part) or part in (".", ".."):
            raise ValueError(f"Invalid path segment in link: {part!r}")
    return {
        "repo_id": f"{owner}/{name}",
        "revision": revision,
        "subfolder": "/".join(subfolder_parts) or None,
    }


def _fetch_json(repo_id: str, filename: str, revision: str | None) -> dict | None:
    try:
        response = get_session().get(
            hf_hub_url(repo_id, filename, revision=revision),
            headers=build_hf_headers(),
            follow_redirects=True,
            timeout=20,
        )
        if response.status_code != 200:
            return None
        data = response.json()
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _config_matches(config: dict | None, match: dict) -> bool:
    if not config:
        return False
    for key, expected in match.items():
        value = config.get(key)
        if isinstance(value, str):
            value = value.lower()
        options = expected if isinstance(expected, list) else [expected]
        if value not in [o.lower() if isinstance(o, str) else o for o in options]:
            return False
    return True


def _patterns(engine: dict, subfolder: str | None) -> list[str]:
    download = engine.get("download") or {}
    if subfolder:
        return [p.replace("{sub}", subfolder) for p in download.get("subfolder_allow", ["{sub}/*"])]
    return list(download.get("allow", ["*"]))


def _select(files: dict[str, int], patterns: list[str]) -> dict[str, int]:
    return {name: size for name, size in files.items() if any(fnmatch.fnmatch(name, p) for p in patterns)}


def resolve(link: str, engine_override: str | None = None) -> dict:
    target = parse_link(link)
    repo_id, revision, subfolder = target["repo_id"], target["revision"], target["subfolder"]
    api = HfApi()
    try:
        info = api.model_info(repo_id, revision=revision, files_metadata=True)
    except GatedRepoError as exc:
        raise ValueError(f"{repo_id} is gated. Accept its terms on huggingface.co and set the HF_TOKEN environment variable before starting Spectra.") from exc
    except RepositoryNotFoundError as exc:
        raise ValueError(f"Repository not found: {repo_id}. Check the link, or set HF_TOKEN for private repositories.") from exc
    except RevisionNotFoundError as exc:
        raise ValueError(f"Revision not found: {revision}") from exc
    repo_id = info.id or repo_id
    files = {s.rfilename: int(s.size or 0) for s in (info.siblings or [])}
    if subfolder and not any(name.startswith(subfolder + "/") for name in files):
        raise ValueError(f"Subfolder '{subfolder}' does not exist in {repo_id}.")
    prefix = f"{subfolder}/" if subfolder else ""
    configs: dict[str, dict | None] = {}

    def config_for(filename: str) -> dict | None:
        for candidate in (prefix + filename, filename):
            if candidate in files:
                if candidate not in configs:
                    configs[candidate] = _fetch_json(repo_id, candidate, info.sha)
                return configs[candidate]
        return None

    scores = []
    haystack = f"{repo_id}/{subfolder or ''}".lower()
    for engine_id, engine in engines.manifests().items():
        detect = engine.get("detect") or {}
        score = 0
        if any(token.lower() in haystack for token in detect.get("name", [])):
            score += 2
        required = detect.get("files") or []
        if required and all((prefix + f) in files or f in files for f in required):
            score += 1
        cfg_rule = detect.get("config")
        if cfg_rule and _config_matches(config_for(cfg_rule["file"]), cfg_rule["match"]):
            score += 5
        scores.append((score, engine_id))
    scores.sort(reverse=True)
    detected = scores[0][1] if scores and scores[0][0] > 0 else None
    engine_id = engine_override or detected
    warnings: list[str] = []
    if engine_id is None:
        raise ValueError("Could not tell which engine this model belongs to. Choose the engine manually.")
    engine = engines.get(engine_id)
    selected = _select(files, _patterns(engine, subfolder))
    if not selected:
        raise ValueError(f"No files in {repo_id} match what {engine['name']} needs.")
    if engine_id == "qwen3tts":
        cfg = config_for("config.json")
        if cfg and cfg.get("tts_model_type") not in (None, "base"):
            warnings.append(f"This is a Qwen3-TTS '{cfg.get('tts_model_type')}' checkpoint. Only Base checkpoints can clone a voice, so it will refuse to load.")
    if engine_id == "irodori":
        weight = prefix + "model.safetensors"
        if weight not in selected:
            warnings.append("No model.safetensors at this location. If the repo has variants in subfolders, link to the subfolder.")
    if engine_override and detected and engine_override != detected:
        warnings.append(f"Auto-detection suggested {engines.get(detected)['name']}.")
    if info.gated:
        warnings.append("This repository is gated. Downloads need HF_TOKEN with accepted terms.")
    if engine.get("license"):
        warnings.append(f"License: {engine['license']}")
    folder = models.folder_name(repo_id, subfolder)
    target_dir = models.model_dir(engine_id, folder)
    existing = None
    existing_id = None
    if target_dir.is_dir():
        existing = "ready" if (target_dir / models.META_FILE).is_file() else "incomplete"
        existing_id = f"{engine_id}/{folder}"
    if existing != "ready":
        ready = next((m for m in models.find_by_repo(repo_id, subfolder) if m["status"] == "ready"), None)
        if ready:
            existing, existing_id = "ready", ready["id"]
    return {
        "repo_id": repo_id,
        "revision": revision,
        "sha": info.sha,
        "subfolder": subfolder,
        "engine": engine_id,
        "detected": detected,
        "files": [{"name": n, "size": s} for n, s in sorted(selected.items())],
        "total_size": sum(selected.values()),
        "folder": folder,
        "target": str(target_dir),
        "existing": existing,
        "existing_id": existing_id,
        "warnings": warnings,
        "allow": _patterns(engine, subfolder),
        "source_url": f"https://huggingface.co/{repo_id}" + (f"/tree/{revision or 'main'}/{subfolder}" if subfolder else ""),
    }


def _dir_bytes(path: Path) -> int:
    total = 0
    for root, _, names in os.walk(path):
        for name in names:
            try:
                total += os.stat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def _fmt(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0
    return f"{n:.1f} TB"


def fetch_snapshot(jobs: JobStore, job: Job, repo_id: str, revision: str | None, target: Path, allow: list[str], total_size: int) -> bool:
    args = {"repo_id": repo_id, "revision": revision, "local_dir": str(target), "allow": allow}
    env = dict(os.environ)
    env["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    env["HF_XET_CACHE"] = str(HF_HOME / "xet")
    env["PYTHONUTF8"] = "1"
    tail: deque[str] = deque(maxlen=30)
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "spectra.fetch", json.dumps(args)],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        jobs.fail(job, f"Could not start the downloader: {exc}")
        return False
    job.on_cancel = proc.kill

    def pump() -> None:
        for line in proc.stdout:
            tail.append(line.rstrip())
            jobs.log(job, line)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    total = max(1, int(total_size))
    samples: deque[tuple[float, int]] = deque()
    while proc.poll() is None:
        time.sleep(0.5)
        now_bytes = _dir_bytes(target)
        now = time.time()
        samples.append((now, now_bytes))
        while len(samples) > 2 and now - samples[0][0] > 8.0:
            samples.popleft()
        span = now - samples[0][0]
        speed = max(0.0, (now_bytes - samples[0][1]) / span) if span > 0 else 0.0
        remaining = max(0, total - now_bytes)
        eta = f"  ·  {int(remaining / speed // 60)} min {int(remaining / speed % 60)} s left" if speed > 1024 else ""
        job.progress = min(0.99, now_bytes / total)
        job.stage = f"{_fmt(min(now_bytes, total))} / {_fmt(total)}  ·  {_fmt(speed)}/s{eta}"
    reader.join(timeout=2)
    if proc.returncode != 0:
        message = next((l for l in reversed(tail) if l.strip()), f"exit code {proc.returncode}")
        jobs.fail(job, "Download stopped." if job.cancel_requested else f"Download failed: {message}")
        return False
    return True


def start_download(jobs: JobStore, link: str, engine_override: str | None = None) -> Job:
    preview = resolve(link, engine_override)
    if preview["existing"] == "ready":
        raise ValueError(f"{preview['repo_id']} is already downloaded as {preview['existing_id']}. Delete it from Installed models first to download it again.")
    for job in jobs.list(kind="download", active_only=True):
        if job.meta.get("target") == preview["target"]:
            return job
    label = preview["repo_id"].split("/", 1)[1] + (f" / {preview['subfolder']}" if preview["subfolder"] else "")
    job = jobs.create("download", f"Download {label}", target=preview["target"], engine=preview["engine"], total=preview["total_size"])
    target = Path(preview["target"])
    meta = {
        "engine": preview["engine"],
        "repo_id": preview["repo_id"],
        "subfolder": preview["subfolder"],
        "revision": preview["sha"],
        "label": label,
        "source_url": preview["source_url"],
        "link": link,
    }
    models.write_pending(target, meta)

    def run() -> None:
        jobs.start(job, "Starting download")
        if not fetch_snapshot(jobs, job, preview["repo_id"], preview["sha"], target, preview["allow"], preview["total_size"]):
            return
        final_meta = dict(meta)
        final_meta.pop("link", None)
        models.write_meta(target, final_meta)
        jobs.finish(job, {"model_id": f"{preview['engine']}/{preview['folder']}"})

    threading.Thread(target=run, daemon=True, name=f"download-{job.id}").start()
    return job
