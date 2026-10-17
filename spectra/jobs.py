import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field


class JobCancelled(Exception):
    pass


@dataclass
class Job:
    id: str
    kind: str
    title: str
    status: str = "queued"
    progress: float | None = None
    stage: str = ""
    log: list[str] = field(default_factory=list)
    result: dict | None = None
    error: str | None = None
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    meta: dict = field(default_factory=dict)
    cancel_requested: bool = False
    on_cancel: Callable[[], None] | None = None

    @property
    def active(self) -> bool:
        return self.status in ("queued", "running")

    def to_dict(self, log_from: int = 0) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "status": self.status,
            "progress": self.progress,
            "stage": self.stage,
            "log": self.log[log_from:],
            "log_size": len(self.log),
            "result": self.result,
            "error": self.error,
            "created": self.created,
            "started": self.started,
            "finished": self.finished,
            "meta": self.meta,
        }


class JobStore:
    def __init__(self, keep_seconds: float = 6 * 3600) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._keep = keep_seconds

    def create(self, kind: str, title: str, **meta) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, title=title, meta=meta)
        with self._lock:
            self._prune()
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self, kind: str | None = None, active_only: bool = False) -> list[Job]:
        with self._lock:
            jobs = list(self._jobs.values())
        if kind:
            jobs = [j for j in jobs if j.kind == kind]
        if active_only:
            jobs = [j for j in jobs if j.active]
        return sorted(jobs, key=lambda j: j.created)

    def start(self, job: Job, stage: str = "") -> None:
        job.status = "running"
        job.started = time.time()
        job.stage = stage

    def log(self, job: Job, line: str) -> None:
        job.log.append(line.rstrip("\n"))
        if len(job.log) > 4000:
            del job.log[: len(job.log) - 4000]

    def finish(self, job: Job, result: dict | None = None) -> None:
        job.status = "done"
        job.result = result
        job.progress = 1.0
        job.finished = time.time()

    def fail(self, job: Job, error: str) -> None:
        job.status = "cancelled" if job.cancel_requested else "error"
        job.error = error
        job.finished = time.time()

    def cancel(self, job: Job) -> bool:
        if not job.active:
            return False
        job.cancel_requested = True
        if job.on_cancel is not None:
            try:
                job.on_cancel()
            except Exception:
                pass
        return True

    def _prune(self) -> None:
        cutoff = time.time() - self._keep
        for job_id in [k for k, j in self._jobs.items() if not j.active and (j.finished or 0) < cutoff]:
            del self._jobs[job_id]
