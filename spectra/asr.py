import shutil
import threading
import time
from collections.abc import Callable

from huggingface_hub import HfApi

from . import clips, engines, hub, models
from .jobs import Job, JobStore
from .paths import ENGINES_DIR, MODELS_DIR, venv_python
from .workers import LogBuffer, WorkerClient, WorkerExited, WorkerManager

ENV_ID = "asr"
NAME = "Transcription"
REPO_ID = "Qwen/Qwen3-ASR-1.7B"
LABEL = "Qwen3-ASR-1.7B"
MODEL_DIR = MODELS_DIR / ENV_ID / models.folder_name(REPO_ID, None)
VRAM_MB = 6144
IDLE_SECONDS = 180


def installed() -> bool:
    return venv_python(ENGINES_DIR / ENV_ID).is_file()


def model_status() -> str | None:
    if (MODEL_DIR / models.META_FILE).is_file():
        return "ready"
    if MODEL_DIR.is_dir():
        return "incomplete"
    return None


def start_setup(jobs: JobStore) -> Job:
    for job in jobs.list(kind="install", active_only=True):
        if job.meta.get("engine") == ENV_ID:
            return job
    job = jobs.create("install", f"Set up transcription ({LABEL})", engine=ENV_ID)

    def run() -> None:
        jobs.start(job, "Installing the environment")
        if not engines.uv_sync(jobs, job, ENGINES_DIR / ENV_ID):
            return
        if model_status() != "ready":
            job.stage = f"Checking {REPO_ID}"
            try:
                info = HfApi().model_info(REPO_ID, files_metadata=True)
            except Exception as exc:
                jobs.fail(job, f"Could not reach Hugging Face: {exc}")
                return
            meta = {
                "engine": ENV_ID,
                "repo_id": REPO_ID,
                "subfolder": None,
                "revision": info.sha,
                "label": LABEL,
                "source_url": f"https://huggingface.co/{REPO_ID}",
            }
            models.write_pending(MODEL_DIR, meta)
            total = sum(int(s.size or 0) for s in info.siblings or [])
            if not hub.fetch_snapshot(jobs, job, REPO_ID, info.sha, MODEL_DIR, ["*"], total):
                return
            models.write_meta(MODEL_DIR, meta)
        jobs.finish(job, {"engine": ENV_ID})

    threading.Thread(target=run, daemon=True, name="install-asr").start()
    return job


class Transcriber:
    def __init__(self, logs: LogBuffer, workers: WorkerManager, gpu: Callable[[], dict | None]) -> None:
        self.logs = logs
        self.workers = workers
        self.gpu = gpu
        self.client: WorkerClient | None = None
        self.lock = threading.Lock()
        self.busy = False
        self.last_used = 0.0
        threading.Thread(target=self._reap_idle, daemon=True, name="asr-idle").start()

    def status(self) -> dict:
        state = model_status()
        return {
            "name": NAME,
            "label": LABEL,
            "repo_id": REPO_ID,
            "path": str(MODEL_DIR),
            "installed": installed(),
            "model": state,
            "size": models._size(MODEL_DIR) if state else 0,
            "ready": installed() and state == "ready",
            "loaded": self.client is not None and self.client.alive,
            "busy": self.busy,
        }

    def transcribe(self, clip_id: str, language: str | None = None) -> dict:
        path = clips.wav_path(clip_id)
        if not path.is_file():
            raise FileNotFoundError(f"Reference clip not found: {clip_id}")
        if not installed() or model_status() != "ready":
            raise ValueError("Transcription is not set up yet. Install it in the Models tab.")
        with self.lock:
            self.busy = True
            try:
                client = self._ensure_client()
                return client.call("transcribe", {"path": str(path), "language": language}, timeout=900)
            except WorkerExited:
                self.client = None
                raise
            finally:
                self.busy = False
                self.last_used = time.time()

    def release(self) -> None:
        if not self.lock.acquire(blocking=False):
            return
        try:
            client, self.client = self.client, None
            if client is not None:
                self.logs.add("spectra", "Stopping the transcription worker to free VRAM")
                client.shutdown()
        finally:
            self.lock.release()

    def delete_model(self) -> None:
        with self.lock:
            client, self.client = self.client, None
            if client is not None:
                client.shutdown()
            if MODEL_DIR.is_dir():
                shutil.rmtree(MODEL_DIR)

    def _ensure_client(self) -> WorkerClient:
        if self.client is not None and self.client.alive:
            return self.client
        self.client = None
        self._make_room()
        client = WorkerClient(ENV_ID, self.logs, NAME)
        try:
            client.wait_ready()
            info = client.call("load", {"model_dir": str(MODEL_DIR), "label": LABEL, "settings": {}}, timeout=600)
        except Exception:
            client.kill()
            raise
        self.logs.add("spectra", f"Loaded {LABEL} on {info.get('device')} in {info.get('load_seconds')} s")
        self.client = client
        return client

    def _make_room(self) -> None:
        gpu = self.gpu()
        tts = self.workers.client
        if not gpu or tts is None or not tts.alive:
            return
        free = gpu["total_mb"] - gpu["used_mb"]
        if free >= VRAM_MB:
            return
        self.logs.add("spectra", f"Only {free} MB of VRAM is free. Unloading the TTS model to make room for transcription.")
        with self.workers.busy:
            self.workers.unload()

    def _reap_idle(self) -> None:
        while True:
            time.sleep(15)
            if self.client is not None and not self.busy and time.time() - self.last_used > IDLE_SECONDS:
                self.release()

