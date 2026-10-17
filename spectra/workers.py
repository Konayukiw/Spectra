import itertools
import json
import os
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable

from . import engines
from .paths import CACHE_DIR, ENGINES_DIR, HF_HOME, venv_python


class WorkerError(RuntimeError):
    def __init__(self, message: str, trace: str | None = None) -> None:
        super().__init__(message)
        self.trace = trace


class WorkerExited(WorkerError):
    pass


class LogBuffer:
    def __init__(self, size: int = 3000) -> None:
        self._lines: deque[tuple[int, float, str, str]] = deque(maxlen=size)
        self._counter = itertools.count(1)
        self._lock = threading.Lock()

    def add(self, source: str, line: str) -> None:
        line = line.rstrip("\r\n")
        if not line:
            return
        with self._lock:
            self._lines.append((next(self._counter), time.time(), source, line))

    def since(self, cursor: int, limit: int = 500) -> tuple[list[dict], int]:
        with self._lock:
            items = [x for x in self._lines if x[0] > cursor]
            last = self._lines[-1][0] if self._lines else cursor
        items = items[-limit:]
        return [{"n": n, "t": t, "source": s, "line": l} for n, t, s, l in items], max(last, cursor)


class WorkerClient:
    def __init__(self, engine_id: str, logs: LogBuffer, name: str | None = None) -> None:
        self.engine_id = engine_id
        self.logs = logs
        python = venv_python(ENGINES_DIR / engine_id)
        if not python.is_file():
            raise WorkerError(f"The {name or engines.get(engine_id)['name']} environment is not installed. Open the Models tab and click Install.")
        env = dict(os.environ)
        env.update({
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
            "HF_HOME": str(HF_HOME),
            "HF_HUB_DISABLE_PROGRESS_BARS": "1",
            "MODELSCOPE_CACHE": str(CACHE_DIR / "modelscope"),
            "TORCH_HOME": str(CACHE_DIR / "torch"),
            "SPECTRA_CACHE_DIR": str(CACHE_DIR),
            "TOKENIZERS_PARALLELISM": "false",
            "PYTORCH_CUDA_ALLOC_CONF": env.get("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True"),
        })
        env.pop("VIRTUAL_ENV", None)
        self.proc = subprocess.Popen(
            [str(python), "-u", str(ENGINES_DIR / engine_id / "worker.py")],
            cwd=str(ENGINES_DIR / engine_id),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.started = time.time()
        self._ids = itertools.count(1)
        self._pending: dict[int, dict] = {}
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._ready = threading.Event()
        self._stderr_tail: deque[str] = deque(maxlen=40)
        threading.Thread(target=self._read_stdout, daemon=True, name=f"{engine_id}-stdout").start()
        threading.Thread(target=self._read_stderr, daemon=True, name=f"{engine_id}-stderr").start()
        logs.add("spectra", f"Started {engine_id} worker (pid {self.proc.pid})")

    @property
    def alive(self) -> bool:
        return self.proc.poll() is None

    def _read_stdout(self) -> None:
        for raw in self.proc.stdout:
            raw = raw.strip()
            if not raw:
                continue
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                self.logs.add(self.engine_id, raw)
                continue
            if message.get("event") == "ready":
                self._ready.set()
                continue
            request_id = message.get("id")
            with self._lock:
                slot = self._pending.get(request_id)
            if slot is None:
                continue
            if message.get("event") == "progress":
                callback = slot.get("progress")
                if callback:
                    try:
                        callback(message.get("stage") or "", message.get("fraction"))
                    except Exception:
                        pass
                continue
            slot["response"] = message
            slot["event"].set()
        code = self.proc.wait()
        tail = "\n".join(self._stderr_tail)
        self.logs.add("spectra", f"{self.engine_id} worker exited with code {code}")
        with self._lock:
            pending = list(self._pending.values())
        for slot in pending:
            if "response" not in slot:
                slot["response"] = {"ok": False, "error": f"The {self.engine_id} worker exited (code {code}).", "trace": tail, "exited": True}
                slot["event"].set()
        self._ready.set()

    def _read_stderr(self) -> None:
        for line in self.proc.stderr:
            line = line.rstrip()
            if not line:
                continue
            self._stderr_tail.append(line)
            self.logs.add(self.engine_id, line)

    def wait_ready(self, timeout: float = 180.0) -> None:
        if not self._ready.wait(timeout):
            raise WorkerError(f"The {self.engine_id} worker did not start within {int(timeout)} s.")
        if not self.alive:
            raise WorkerExited(f"The {self.engine_id} worker failed to start.", "\n".join(self._stderr_tail))

    def call(self, op: str, args: dict | None = None, progress: Callable[[str, float | None], None] | None = None, timeout: float | None = None) -> dict:
        if not self.alive:
            raise WorkerExited(f"The {self.engine_id} worker is not running.")
        request_id = next(self._ids)
        slot = {"event": threading.Event(), "progress": progress}
        with self._lock:
            self._pending[request_id] = slot
        try:
            payload = json.dumps({"id": request_id, "op": op, "args": args or {}}, ensure_ascii=True)
            with self._write_lock:
                self.proc.stdin.write(payload + "\n")
                self.proc.stdin.flush()
            if not slot["event"].wait(timeout):
                raise WorkerError(f"{op} timed out after {timeout} s.")
            response = slot["response"]
        except (BrokenPipeError, OSError) as exc:
            raise WorkerExited(f"The {self.engine_id} worker is not reachable: {exc}") from exc
        finally:
            with self._lock:
                self._pending.pop(request_id, None)
        if not response.get("ok"):
            cls = WorkerExited if response.get("exited") else WorkerError
            raise cls(response.get("error") or "Unknown worker error", response.get("trace"))
        return response.get("result") or {}

    def kill(self) -> None:
        if self.alive:
            try:
                self.proc.kill()
            except Exception:
                pass
        try:
            self.proc.wait(timeout=10)
        except Exception:
            pass

    def shutdown(self, timeout: float = 8.0) -> None:
        if not self.alive:
            return
        try:
            with self._write_lock:
                self.proc.stdin.write(json.dumps({"id": 0, "op": "shutdown"}) + "\n")
                self.proc.stdin.flush()
            self.proc.wait(timeout=timeout)
        except Exception:
            pass
        self.kill()


class WorkerManager:
    def __init__(self, logs: LogBuffer) -> None:
        self.logs = logs
        self.client: WorkerClient | None = None
        self.loaded: dict | None = None
        self.loaded_key: tuple | None = None
        self.busy = threading.Lock()
        self._state_lock = threading.Lock()
        self.activity = ""
        self.before_load: Callable[[], None] | None = None

    def status(self) -> dict:
        client = self.client
        return {
            "engine": client.engine_id if client and client.alive else None,
            "pid": client.proc.pid if client and client.alive else None,
            "loaded": self.loaded if client and client.alive else None,
            "busy": self.busy.locked(),
            "activity": self.activity,
        }

    def _ensure_client(self, engine_id: str, progress) -> WorkerClient:
        with self._state_lock:
            if self.client is not None and (self.client.engine_id != engine_id or not self.client.alive):
                old = self.client
                self.client = None
                self.loaded = None
                self.loaded_key = None
                progress(f"Stopping {old.engine_id} worker", None)
                old.shutdown()
            if self.client is None:
                progress(f"Starting {engines.get(engine_id)['name']} worker", None)
                self.client = WorkerClient(engine_id, self.logs)
            client = self.client
        client.wait_ready()
        return client

    def ensure_model(self, model: dict, load_settings: dict, progress) -> dict:
        key = (model["id"], model["model_path"], json.dumps(load_settings, sort_keys=True))
        client = self._ensure_client(model["engine"], progress)
        if self.loaded_key == key and self.loaded is not None:
            return self.loaded
        self.loaded = None
        self.loaded_key = None
        if self.before_load is not None:
            self.before_load()
        progress(f"Loading {model['label']}", None)
        info = client.call("load", {"model_dir": model["model_path"], "label": model["label"], "settings": load_settings}, progress=progress)
        info.update({"model_id": model["id"], "label": model["label"], "engine": model["engine"], "load_settings": load_settings})
        self.loaded = info
        self.loaded_key = key
        self.logs.add("spectra", f"Loaded {model['id']} in {info.get('load_seconds')} s")
        return info

    def synthesize(self, request: dict, progress) -> dict:
        if self.client is None:
            raise WorkerError("No worker is running.")
        return self.client.call("synthesize", request, progress=progress)

    def unload(self) -> None:
        with self._state_lock:
            client = self.client
            self.client = None
            self.loaded = None
            self.loaded_key = None
        if client is not None:
            client.shutdown()

    def kill(self) -> None:
        with self._state_lock:
            client = self.client
            self.client = None
            self.loaded = None
            self.loaded_key = None
        if client is not None:
            client.kill()
