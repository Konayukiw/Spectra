import time
from concurrent.futures import ThreadPoolExecutor

from . import clips, engines, history, models, voices
from .jobs import Job, JobStore
from .reading import Reader
from .workers import LogBuffer, WorkerError, WorkerExited, WorkerManager

MAX_TEXT = 5000
MAX_READING_TEXT = MAX_TEXT * 4


class GenerationService:
    def __init__(self, jobs: JobStore, workers: WorkerManager, logs: LogBuffer, reader: Reader) -> None:
        self.jobs = jobs
        self.workers = workers
        self.logs = logs
        self.reader = reader
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gpu")

    def _progress(self, job: Job):
        def update(stage: str, fraction: float | None = None) -> None:
            if stage:
                job.stage = stage
                self.workers.activity = stage
            if fraction is not None:
                job.progress = max(0.0, min(1.0, float(fraction)))
        return update

    def _check_cancel(self, job: Job) -> None:
        if job.cancel_requested:
            raise WorkerError("Cancelled before start.")

    def _resolve_model(self, model_id: str) -> tuple[dict, dict]:
        model = models.get_model(model_id)
        if model["status"] != "ready":
            raise ValueError("This model has not finished downloading. Resume or delete it in the Models tab.")
        engine = engines.get(model["engine"])
        if not engines.installed(model["engine"]):
            raise ValueError(f"The {engine['name']} environment is not installed. Install it from the Models tab.")
        return model, engine

    def submit_load(self, model_id: str, settings: dict) -> Job:
        model, engine = self._resolve_model(model_id)
        load_settings, _ = engines.split_settings(model["engine"], settings or {})
        job = self.jobs.create("load", f"Load {model['label']}", model_id=model_id)
        job.on_cancel = lambda: self._cancel_running(job)

        def run() -> None:
            if job.cancel_requested:
                self.jobs.fail(job, "Cancelled.")
                return
            self.jobs.start(job, "Waiting for the GPU")
            with self.workers.busy:
                try:
                    info = self.workers.ensure_model(model, load_settings, self._progress(job))
                    self.jobs.finish(job, {"loaded": info})
                except WorkerError as exc:
                    self.jobs.fail(job, "Cancelled." if job.cancel_requested else str(exc))
                    if exc.trace:
                        self.jobs.log(job, exc.trace)
                except Exception as exc:
                    self.jobs.fail(job, f"{type(exc).__name__}: {exc}")
                finally:
                    self.workers.activity = ""

        self.executor.submit(run)
        return job

    def submit_unload(self) -> Job:
        job = self.jobs.create("unload", "Unload model")

        def run() -> None:
            self.jobs.start(job, "Stopping worker")
            with self.workers.busy:
                self.workers.unload()
            self.jobs.finish(job, {})

        self.executor.submit(run)
        return job

    def _cancel_running(self, job: Job) -> None:
        if job.status == "running":
            self.logs.add("spectra", f"Stopping worker to cancel {job.title}")
            self.workers.kill()

    def submit(self, payload: dict) -> Job:
        model, engine = self._resolve_model(str(payload.get("model_id") or ""))
        text = str(payload.get("text") or "").strip()
        if not text:
            raise ValueError("Enter the text to speak.")
        if len(text) > MAX_TEXT:
            raise ValueError(f"Text is too long ({len(text)} characters). The limit is {MAX_TEXT}.")
        refs = []
        for item in payload.get("clips") or []:
            clip_id = str(item.get("id") or "")
            if not clips.exists(clip_id):
                raise ValueError(f"Reference clip not found: {clip_id}. Upload it again.")
            info = clips.info(clip_id)
            refs.append({
                "id": clip_id,
                "name": info.get("name"),
                "duration": info.get("duration"),
                "transcript": str(item.get("transcript") or "").strip(),
            })
        if not refs:
            raise ValueError("Voice cloning needs reference audio. Add at least one clip or choose a saved voice.")
        mode = engine["reference"].get("transcript")
        if mode == "required" and any(not r["transcript"] for r in refs):
            raise ValueError(f"{engine['name']} needs the exact transcript of every reference clip.")
        voice = None
        voice_id = payload.get("voice_id")
        if voice_id:
            try:
                saved = voices.get_voice(str(voice_id))
                voice = {"id": saved["id"], "name": saved["name"]}
            except (FileNotFoundError, ValueError):
                voice = None
        settings = dict(payload.get("settings") or {})
        load_settings, gen_settings = engines.split_settings(model["engine"], settings)
        spoken_request = None
        if engine.get("reading") and gen_settings.get("reading_conversion", True):
            provided = payload.get("reading") if isinstance(payload.get("reading"), dict) else {}
            provided_text = str(provided.get("text") or "").strip()
            overrides = provided.get("overrides") if isinstance(provided.get("overrides"), dict) else {}
            if provided.get("source") == text and provided_text:
                if len(provided_text) > MAX_READING_TEXT:
                    raise ValueError(f"The reading is too long ({len(provided_text)} characters).")
                spoken_request = {"text": provided_text, "overrides": {str(k): str(v) for k, v in overrides.items()}}
            else:
                spoken_request = {"text": None, "overrides": {}}
        preview = text if len(text) <= 40 else text[:40] + "…"
        job = self.jobs.create("generate", preview, model_id=model["id"])
        job.on_cancel = lambda: self._cancel_running(job)

        def run() -> None:
            if job.cancel_requested:
                self.jobs.fail(job, "Cancelled.")
                return
            self.jobs.start(job, "Waiting for the GPU")
            progress = self._progress(job)
            out_path = None
            started = time.time()
            with self.workers.busy:
                try:
                    messages = []
                    if engine["reference"].get("multi_clip", True) or len(refs) == 1:
                        references = [{"path": str(clips.wav_path(r["id"])), "transcript": r["transcript"]} for r in refs]
                    else:
                        progress("Joining reference clips")
                        joined = clips.concatenated([r["id"] for r in refs])
                        if all(r["transcript"] for r in refs):
                            transcript = " ".join(r["transcript"] for r in refs)
                        else:
                            transcript = ""
                            if any(r["transcript"] for r in refs):
                                messages.append("info: some clips had no transcript, so the transcripts were not used.")
                        references = [{"path": str(joined), "transcript": transcript}]
                        messages.append(f"info: {engine['name']} takes one reference, so {len(refs)} clips were joined in order.")
                    spoken = text
                    if spoken_request is not None:
                        spoken = spoken_request["text"]
                        if spoken is None:
                            progress("Converting English to kana")
                            converted = self.reader.convert(text, bool(gen_settings.get("reading_context", True)))
                            spoken = converted["text"]
                            if converted["context"].get("error"):
                                messages.append(f"warning: the reading context model failed, so default readings were used ({converted['context']['error']}).")
                    loaded = self.workers.ensure_model(model, load_settings, progress)
                    self._check_cancel(job)
                    entry_id, out_path = history.new_entry()
                    progress("Generating", 0.0)
                    result = self.workers.synthesize(
                        {
                            "text": spoken,
                            "references": references,
                            "settings": gen_settings,
                            "output_dir": str(out_path),
                            "prefix": "take",
                        },
                        progress,
                    )
                    meta = {
                        "id": entry_id,
                        "created_at": time.time(),
                        "text": text,
                        "model": {"id": model["id"], "label": model["label"], "engine": model["engine"], "repo_id": model.get("repo_id")},
                        "engine_name": engine["name"],
                        "voice": voice,
                        "clips": refs,
                        "settings": {**load_settings, **gen_settings},
                        "takes": result.get("takes", []),
                        "sample_rate": result.get("sample_rate"),
                        "seed": result.get("seed"),
                        "elapsed": result.get("elapsed"),
                        "wall_seconds": round(time.time() - started, 2),
                        "timings": result.get("timings", {}),
                        "messages": messages + list(result.get("messages", [])),
                        "runtime": {k: v for k, v in loaded.items() if k not in ("memory", "generate_defaults")},
                        "memory": result.get("memory", {}),
                    }
                    if spoken != text:
                        meta["reading"] = spoken
                        if spoken_request and spoken_request["overrides"]:
                            meta["reading_overrides"] = spoken_request["overrides"]
                    history.save(out_path, meta)
                    self.jobs.finish(job, meta)
                except WorkerExited as exc:
                    if out_path is not None:
                        history.discard(out_path)
                    if job.cancel_requested:
                        self.jobs.fail(job, "Cancelled. The model will reload on the next generation.")
                    else:
                        self.jobs.fail(job, f"{exc} The worker crashed. Check the log panel (Frequently happens with large models).")
                        if exc.trace:
                            self.jobs.log(job, exc.trace)
                except WorkerError as exc:
                    if out_path is not None:
                        history.discard(out_path)
                    self.jobs.fail(job, "Cancelled." if job.cancel_requested else str(exc))
                    if exc.trace:
                        self.jobs.log(job, exc.trace)
                except Exception as exc:
                    if out_path is not None:
                        history.discard(out_path)
                    self.jobs.fail(job, f"{type(exc).__name__}: {exc}")
                finally:
                    self.workers.activity = ""

        self.executor.submit(run)
        return job
