import hmac
import ipaddress
import os
import shutil
import subprocess
import time
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import asr, clips, engines, history, hub, models, reading, voices
from .generation import MAX_TEXT, GenerationService
from .jobs import JobStore
from .paths import STATIC_DIR, ensure_dirs
from .workers import LogBuffer, WorkerError, WorkerManager

logs = LogBuffer()
jobs = JobStore()
workers = WorkerManager(logs)
reader = reading.Reader(logs)
service = GenerationService(jobs, workers, logs, reader)
transcriber = asr.Transcriber(logs, workers, lambda: _gpu())
workers.before_load = transcriber.release
_gpu_cache: dict = {"t": 0.0, "data": None}
ACCESS_TOKEN = os.environ.get("SPECTRA_TOKEN") or None
TOKEN_COOKIE = "spectra_token"
TOKEN_PAGE = """<!doctype html><meta charset="utf-8"><title>Spectra</title>
<body style="font-family:system-ui,sans-serif;max-width:520px;margin:15vh auto;padding:0 16px;line-height:1.6">
<h2>Access token required</h2>
<p>This Spectra server is shared on the network. Open the URL with <code>?token=</code> that is printed in the console where Spectra is running.</p>
</body>"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dirs()
    logs.add("spectra", "Server started")
    yield
    workers.unload()
    transcriber.release()
    reader.release()


app = FastAPI(title="Spectra", lifespan=lifespan)


@app.middleware("http")
async def no_stale_assets(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/") or request.url.path == "/":
        response.headers["Cache-Control"] = "no-cache"
    return response


def _from_loopback(request: Request) -> bool:
    try:
        address = ipaddress.ip_address(request.client.host)
    except (AttributeError, TypeError, ValueError):
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return address.is_loopback or bool(mapped and mapped.is_loopback)


def _token_matches(value: str | None) -> bool:
    return bool(value) and hmac.compare_digest(value.encode(), ACCESS_TOKEN.encode())


@app.middleware("http")
async def require_token(request: Request, call_next):
    if ACCESS_TOKEN is None or _from_loopback(request):
        return await call_next(request)
    if _token_matches(request.query_params.get("token")):
        target = request.url.remove_query_params("token")
        response = RedirectResponse(target.path + (f"?{target.query}" if target.query else ""), status_code=303)
        response.set_cookie(TOKEN_COOKIE, ACCESS_TOKEN, httponly=True, samesite="lax")
        return response
    if _token_matches(request.cookies.get(TOKEN_COOKIE)):
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=401, content={"detail": "Access token required. Open the URL with ?token= printed in the Spectra console."})
    return HTMLResponse(TOKEN_PAGE, status_code=401)


@app.exception_handler(ValueError)
async def value_error(_: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(FileNotFoundError)
async def not_found(_: Request, exc: FileNotFoundError):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(KeyError)
async def key_error(_: Request, exc: KeyError):
    return JSONResponse(status_code=404, content={"detail": str(exc).strip("'\"")})


def _gpu() -> dict | None:
    now = time.time()
    if now - _gpu_cache["t"] < 2.0:
        return _gpu_cache["data"]
    data = None
    exe = shutil.which("nvidia-smi")
    if exe:
        try:
            out = subprocess.run(
                [exe, "--query-gpu=name,memory.used,memory.total,utilization.gpu", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=3,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout.strip().splitlines()
            if out:
                name, used, total, util = [x.strip() for x in out[0].split(",")]
                data = {"name": name, "used_mb": int(float(used)), "total_mb": int(float(total)), "util": int(float(util))}
        except Exception:
            data = None
    _gpu_cache.update(t=now, data=data)
    return data


@app.get("/api/state")
def state():
    return {
        "engines": engines.public(),
        "models": models.list_models(),
        "worker": workers.status(),
        "jobs": [j.to_dict(log_from=len(j.log)) for j in jobs.list(active_only=True)],
        "gpu": _gpu(),
    }


@app.get("/api/system")
def system():
    return {"worker": workers.status(), "gpu": _gpu(), "jobs": [j.to_dict(log_from=len(j.log)) for j in jobs.list(active_only=True)]}


@app.get("/api/engines")
def list_engines():
    return engines.public()


@app.post("/api/engines/{engine_id}/install")
def install_engine(engine_id: str):
    return engines.start_install(jobs, engine_id).to_dict()


@app.get("/api/models")
def list_models():
    return models.list_models()


@app.post("/api/models/resolve")
def resolve_model(payload: dict = Body(...)):
    return hub.resolve(payload.get("link", ""), payload.get("engine") or None)


@app.post("/api/models/download")
def download_model(payload: dict = Body(...)):
    return hub.start_download(jobs, payload.get("link", ""), payload.get("engine") or None).to_dict()


@app.post("/api/models/{engine_id}/{folder}/resume")
def resume_model(engine_id: str, folder: str):
    entry = models.get_model(f"{engine_id}/{folder}")
    link = entry.get("source_url") or entry.get("repo_id")
    if entry.get("subfolder") and entry.get("repo_id"):
        link = f"{entry['repo_id']}/{entry['subfolder']}"
    return hub.start_download(jobs, link, engine_id).to_dict()


@app.delete("/api/models/{engine_id}/{folder}")
def delete_model(engine_id: str, folder: str):
    model_id = f"{engine_id}/{folder}"
    if workers.loaded and workers.loaded.get("model_id") == model_id:
        if workers.busy.locked():
            raise HTTPException(409, "The model is in use. Wait for the current job to finish.")
        workers.unload()
    for job in jobs.list(kind="download", active_only=True):
        if job.meta.get("target", "").endswith(folder):
            jobs.cancel(job)
            time.sleep(0.5)
    models.delete_model(model_id)
    return {"ok": True}


@app.post("/api/models/load")
def load_model(payload: dict = Body(...)):
    return service.submit_load(str(payload.get("model_id") or ""), payload.get("settings") or {}).to_dict()


@app.post("/api/models/unload")
def unload_model():
    return service.submit_unload().to_dict()


@app.post("/api/clips")
def upload_clips(files: list[UploadFile] = File(...)):
    results, errors = [], []
    for upload in files:
        try:
            results.append(clips.ingest_upload(upload.file, upload.filename or "clip"))
        except ValueError as exc:
            errors.append({"name": upload.filename, "error": str(exc)})
    if not results and errors:
        raise HTTPException(400, errors[0]["error"])
    return {"clips": results, "errors": errors}


@app.get("/api/clips/{clip_id}")
def clip_info(clip_id: str):
    return clips.info(clip_id)


@app.post("/api/clips/{clip_id}/transcribe")
def transcribe_clip(clip_id: str, payload: dict = Body(default={})):
    try:
        return transcriber.transcribe(clip_id, (payload or {}).get("language") or None)
    except WorkerError as exc:
        raise HTTPException(500, str(exc))


@app.get("/api/asr")
def asr_status():
    return transcriber.status()


@app.post("/api/asr/install")
def asr_install():
    return asr.start_setup(jobs).to_dict()


@app.delete("/api/asr/model")
def asr_delete_model():
    if transcriber.busy or any(j.meta.get("engine") == asr.ENV_ID for j in jobs.list(kind="install", active_only=True)):
        raise HTTPException(409, "Transcription is busy. Try again when it finishes.")
    transcriber.delete_model()
    return {"ok": True}


@app.get("/api/reading")
def reading_status():
    return reader.status()


@app.post("/api/reading/convert")
def reading_convert(payload: dict = Body(...)):
    text = str(payload.get("text") or "")
    if len(text) > MAX_TEXT:
        raise ValueError(f"Text is too long ({len(text)} characters). The limit is {MAX_TEXT}.")
    return reader.convert(text, bool(payload.get("context", True)))


@app.post("/api/reading/install")
def reading_install():
    return reading.start_setup(jobs).to_dict()


@app.delete("/api/reading/model")
def reading_delete_model():
    if reader.busy or any(j.meta.get("engine") == reading.ENV_ID for j in jobs.list(kind="install", active_only=True)):
        raise HTTPException(409, "The reading context model is busy. Try again when it finishes.")
    reader.delete_model()
    return {"ok": True}


@app.get("/api/reading/dictionary")
def reading_dictionary():
    return {"entries": reading.user_entries()}


@app.put("/api/reading/dictionary")
def reading_dictionary_set(payload: dict = Body(...)):
    return {"entries": reading.set_user_entry(payload.get("word"), payload.get("reading"))}


@app.delete("/api/reading/dictionary")
def reading_dictionary_delete(payload: dict = Body(...)):
    return {"entries": reading.delete_user_entry(payload.get("word"))}


@app.get("/api/clips/{clip_id}/audio")
def clip_audio(clip_id: str):
    path = clips.wav_path(clip_id)
    if not path.is_file():
        raise HTTPException(404, "Clip not found")
    return FileResponse(path, media_type="audio/wav")


@app.get("/api/voices")
def list_voices():
    return voices.list_voices()


@app.post("/api/voices")
def create_voice(payload: dict = Body(...)):
    return voices.create_voice(payload.get("name", ""), payload.get("clips") or [], payload.get("notes", ""))


@app.get("/api/voices/{voice_id}")
def get_voice(voice_id: str):
    return voices.get_voice(voice_id)


@app.put("/api/voices/{voice_id}")
def update_voice(voice_id: str, payload: dict = Body(...)):
    return voices.update_voice(voice_id, payload.get("name"), payload.get("notes"), payload.get("clips"))


@app.delete("/api/voices/{voice_id}")
def delete_voice(voice_id: str):
    voices.delete_voice(voice_id)
    return {"ok": True}


@app.post("/api/generate")
def generate(payload: dict = Body(...)):
    return service.submit(payload).to_dict()


@app.get("/api/jobs")
def list_jobs(limit: int = Query(20, ge=1, le=100), kind: str | None = None):
    items = sorted(jobs.list(kind=kind), key=lambda j: j.created, reverse=True)[:limit]
    return [j.to_dict(log_from=max(0, len(j.log) - 60)) for j in items]


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str, log_from: int = Query(0, ge=0)):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job.to_dict(log_from=log_from)


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return {"cancelled": jobs.cancel(job)}


@app.get("/api/history")
def list_history(offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=200), q: str = ""):
    items, total = history.list_entries(offset, limit, q)
    return {"items": items, "total": total}


@app.get("/api/history/{entry_id}")
def get_history(entry_id: str):
    return history.get_entry(entry_id)


@app.get("/api/history/{entry_id}/audio/{filename}")
def history_audio(entry_id: str, filename: str, download: bool = False):
    path = history.audio_path(entry_id, filename)
    if download:
        return FileResponse(path, media_type="audio/wav", filename=f"{entry_id}_{filename}")
    return FileResponse(path, media_type="audio/wav")


@app.delete("/api/history/{entry_id}")
def delete_history(entry_id: str):
    history.delete_entry(entry_id)
    return {"ok": True}


@app.get("/api/logs")
def get_logs(since: int = Query(0, ge=0)):
    lines, cursor = logs.since(since)
    return {"lines": lines, "cursor": cursor}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
