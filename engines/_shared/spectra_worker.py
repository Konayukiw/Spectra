import gc
import hashlib
import json
import os
import random
import sys
import time
import traceback
import wave
from pathlib import Path

_PROTO = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
os.dup2(2, 1)
sys.stdout = sys.stderr


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def send(payload: dict) -> None:
    _PROTO.write(json.dumps(payload, ensure_ascii=True) + "\n")
    _PROTO.flush()


class Progress:
    def __init__(self, request_id) -> None:
        self.request_id = request_id

    def __call__(self, stage: str, fraction: float | None = None) -> None:
        send({"event": "progress", "id": self.request_id, "stage": stage, "fraction": fraction})


def file_key(path: str) -> str:
    stat = os.stat(path)
    return hashlib.sha1(f"{os.path.abspath(path)}|{stat.st_size}|{stat.st_mtime_ns}".encode()).hexdigest()[:16]


def seed_everything(seed: int) -> None:
    import numpy as np
    import torch

    random.seed(seed)
    np.random.seed(seed % (2**32))
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def resolve_seeds(value, count: int) -> list[int]:
    try:
        seed = int(value)
    except (TypeError, ValueError):
        seed = -1
    if seed >= 0:
        return [seed + index for index in range(count)]
    rng = random.SystemRandom()
    seeds: list[int] = []
    while len(seeds) < count:
        candidate = rng.randrange(0, 2**31 - 1)
        if candidate not in seeds:
            seeds.append(candidate)
    return seeds


def write_wav(path: Path, audio, sample_rate: int) -> float:
    import numpy as np

    data = np.asarray(audio, dtype=np.float32).reshape(-1)
    data = np.nan_to_num(data, nan=0.0, posinf=0.0, neginf=0.0)
    pcm = (np.clip(data, -1.0, 1.0) * 32767.0).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(sample_rate))
        handle.writeframes(pcm.tobytes())
    return len(data) / float(sample_rate)


def cuda_memory() -> dict:
    try:
        import torch

        if not torch.cuda.is_available():
            return {}
        free, total = torch.cuda.mem_get_info()
        return {
            "allocated": torch.cuda.memory_allocated(),
            "reserved": torch.cuda.memory_reserved(),
            "peak": torch.cuda.max_memory_reserved(),
            "free": free,
            "total": total,
        }
    except Exception:
        return {}


def free_memory() -> None:
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


class Engine:
    name = "engine"

    def __init__(self) -> None:
        self.model_dir: str | None = None
        self.load_settings: dict = {}
        self.label = ""

    def load(self, model_dir: str, settings: dict, progress: Progress) -> dict:
        raise NotImplementedError

    def unload(self) -> None:
        pass

    def synthesize(self, request: dict, progress: Progress) -> dict:
        raise NotImplementedError

    def op_ping(self, args: dict, progress: Progress) -> dict:
        return {"engine": self.name, "pid": os.getpid(), "model_dir": self.model_dir}

    def op_load(self, args: dict, progress: Progress) -> dict:
        model_dir = str(args["model_dir"])
        settings = dict(args.get("settings") or {})
        self.label = str(args.get("label") or Path(model_dir).name)
        if self.model_dir is not None:
            progress("Unloading previous model")
            self.unload()
            self.model_dir = None
            free_memory()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.reset_peak_memory_stats()
        except Exception:
            pass
        started = time.perf_counter()
        info = self.load(model_dir, settings, progress) or {}
        self.model_dir = model_dir
        self.load_settings = settings
        info["load_seconds"] = round(time.perf_counter() - started, 2)
        info["memory"] = cuda_memory()
        return info

    def op_unload(self, args: dict, progress: Progress) -> dict:
        if self.model_dir is not None:
            self.unload()
            self.model_dir = None
        free_memory()
        return {"memory": cuda_memory()}

    def op_synthesize(self, args: dict, progress: Progress) -> dict:
        if self.model_dir is None:
            raise RuntimeError("No model is loaded.")
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.reset_peak_memory_stats()
        except Exception:
            pass
        started = time.perf_counter()
        result = self.synthesize(args, progress)
        result["elapsed"] = round(time.perf_counter() - started, 3)
        result["memory"] = cuda_memory()
        return result


def serve(engine: Engine) -> None:
    send({"event": "ready", "engine": engine.name, "pid": os.getpid()})
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            log(f"[worker] invalid message: {raw[:200]}")
            continue
        request_id = message.get("id")
        op = str(message.get("op") or "")
        if op == "shutdown":
            send({"id": request_id, "ok": True, "result": {}})
            break
        handler = getattr(engine, f"op_{op}", None)
        if handler is None:
            send({"id": request_id, "ok": False, "error": f"Unknown operation: {op}"})
            continue
        try:
            result = handler(message.get("args") or {}, Progress(request_id))
            send({"id": request_id, "ok": True, "result": result})
        except BaseException as exc:
            trace = traceback.format_exc()
            log(trace)
            send({"id": request_id, "ok": False, "error": f"{type(exc).__name__}: {exc}", "trace": trace})
            free_memory()
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                break
    try:
        engine.unload()
    except Exception:
        pass
