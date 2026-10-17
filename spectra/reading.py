import json
import re
import shutil
import threading
import time
import unicodedata
from functools import lru_cache

import kanalizer
from huggingface_hub import HfApi

from . import engines, hub, models
from .jobs import Job, JobStore
from .paths import DATA_DIR, ENGINES_DIR, MODELS_DIR, PACKAGE_DIR, venv_python
from .workers import LogBuffer, WorkerClient, WorkerExited

ENV_ID = "reading"
NAME = "Reading"
REPO_ID = "LiquidAI/d1-omni-600M"
REVISION = "02b55d7076f15129e59ab3f94783f32c4b088674"
LABEL = "d1-omni-600M"
MODEL_DIR = MODELS_DIR / ENV_ID / models.folder_name(REPO_ID, None)
USER_FILE = DATA_DIR / "reading_dictionary.json"
IDLE_SECONDS = 600
MAX_WORD = 64
MAX_READING = 128
MAX_SENTENCE = 240
SENTENCE_ENDS = "。．！？!?\n"
SPELL_NOTE = "アルファベットを1文字ずつ読む"
WORD_NOTE = "英単語として読む"

_LETTER = "A-Za-zÀ-ÖØ-öø-ɏ"
_WORD = re.compile(f"[{_LETTER}]+(?:['’][{_LETTER}]+)*")
_CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|[a-z]+")
_NUMBER_AFTER = re.compile(r"\.\s*\d")
_HALFWIDTH = str.maketrans({chr(c): chr(c - 0xFEE0) for c in range(0xFF01, 0xFF5F)})
_V_KANA = (("ヴァ", "バ"), ("ヴィ", "ビ"), ("ヴェ", "ベ"), ("ヴォ", "ボ"), ("ヴュ", "ビュ"), ("ヴ", "ブ"))
_ATION = re.compile(r"([ケゲセゼテデネヘベペメレエ])イション")
_VOWELS = set("AEIOUY")
_user_lock = threading.Lock()


@lru_cache(maxsize=1)
def builtin() -> dict:
    data = json.loads((PACKAGE_DIR / "reading_data.json").read_text(encoding="utf-8"))
    data["words"] = {k.lower(): v for k, v in data["words"].items()}
    data["heteronyms"] = {k.lower(): v for k, v in data["heteronyms"].items()}
    return data


@lru_cache(maxsize=8192)
def kana(word: str) -> str:
    letters = "".join(c for c in word.lower() if c in kanalizer.INPUT_CHARS)
    if not letters:
        return ""
    try:
        result = kanalizer.convert(letters, on_invalid_input="ignore", on_incomplete="ignore")
    except Exception:
        result = ""
    if not result:
        return spell(letters)
    for old, new in _V_KANA:
        result = result.replace(old, new)
    return _ATION.sub(r"\1ーション", result)


def spell(word: str) -> str:
    letters = builtin()["letters"]
    return "".join(letters.get(c.upper(), "") for c in word)


def _pronounceable(word: str) -> bool:
    run = 0
    for char in word.upper():
        run = 0 if char in _VOWELS else run + 1
        if run > 2:
            return False
    return True


def user_entries() -> list[dict]:
    try:
        data = json.loads(USER_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = data.get("entries") if isinstance(data, dict) else None
    return [{"word": str(e["word"]), "reading": str(e["reading"])} for e in items or [] if isinstance(e, dict) and e.get("word") and e.get("reading")]


def _write_user(entries: list[dict]) -> None:
    USER_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = USER_FILE.with_name(USER_FILE.name + ".tmp")
    tmp.write_text(json.dumps({"entries": entries}, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(USER_FILE)


def _clean_word(word) -> str:
    return unicodedata.normalize("NFKC", str(word or "")).strip()


def _fold(token: str) -> str:
    decomposed = unicodedata.normalize("NFKD", token.replace("’", "'"))
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def set_user_entry(word, reading) -> list[dict]:
    word = _clean_word(word)
    reading = str(reading or "").strip()
    if not word:
        raise ValueError("Enter the word to register.")
    if not reading:
        raise ValueError("Enter how the word should be read.")
    if len(word) > MAX_WORD or len(reading) > MAX_READING:
        raise ValueError(f"Words are limited to {MAX_WORD} characters and readings to {MAX_READING}.")
    if "\n" in word or "\n" in reading:
        raise ValueError("Words and readings must fit on one line.")
    with _user_lock:
        entries = [e for e in user_entries() if e["word"].lower() != word.lower()]
        entries.append({"word": word, "reading": reading})
        entries.sort(key=lambda e: e["word"].lower())
        _write_user(entries)
    return entries


def delete_user_entry(word) -> list[dict]:
    word = _clean_word(word)
    with _user_lock:
        entries = user_entries()
        kept = [e for e in entries if e["word"].lower() != word.lower()]
        if len(kept) == len(entries):
            raise FileNotFoundError(f"Not in the dictionary: {word}")
        _write_user(kept)
    return kept


def _bounded(phrase: str) -> str:
    head = r"(?<![A-Za-z0-9])" if phrase[0].isascii() and phrase[0].isalnum() else ""
    tail = r"(?![A-Za-z0-9])" if phrase[-1].isascii() and phrase[-1].isalnum() else ""
    return head + re.escape(phrase) + tail


def _phrase_matcher() -> tuple[re.Pattern | None, list[tuple[str, str, bool]]]:
    entries: dict[str, tuple[str, str, bool]] = {}
    for word, reading in builtin()["phrases"].items():
        entries[word.lower()] = (word, reading, False)
    for entry in user_entries():
        word = _clean_word(entry["word"]).translate(_HALFWIDTH)
        if word:
            entries[word.lower()] = (word, entry["reading"], True)
    items = sorted(entries.values(), key=lambda e: len(e[0]), reverse=True)
    if not items:
        return None, []
    parts = []
    for index, (word, _, _) in enumerate(items):
        exact = word.isascii() and word.upper() == word and any(c.isalpha() for c in word)
        body = _bounded(word) if exact else f"(?i:{_bounded(word)})"
        parts.append(f"(?P<p{index}>{body})")
    return re.compile("|".join(parts)), items


def _sentence(text: str, start: int, end: int) -> str:
    left = max(text.rfind(c, 0, start) for c in SENTENCE_ENDS) + 1
    rights = [i for i in (text.find(c, end) for c in SENTENCE_ENDS) if i >= 0]
    right = min(rights) + 1 if rights else len(text)
    if right - left > MAX_SENTENCE:
        left = max(left, start - MAX_SENTENCE // 2)
        right = min(right, end + MAX_SENTENCE // 2)
    return text[left:right].strip()


def _context(candidates: list[dict], choice: int, text: str, start: int, end: int, ask: bool = True) -> dict:
    seen, unique = set(), []
    for candidate in candidates:
        if candidate["reading"] not in seen:
            seen.add(candidate["reading"])
            unique.append(dict(candidate))
    if len(unique) < 2:
        return {"kind": "dict", "reading": unique[0]["reading"]}
    choice = max(0, min(choice, len(unique) - 1))
    return {
        "kind": "context",
        "reading": unique[choice]["reading"],
        "candidates": unique,
        "choice": choice,
        "confidence": None,
        "by": "default" if ask else "rule",
        "_sentence": _sentence(text, start, end),
        "_ask": ask,
    }


def _part(part: str, following: str | None) -> str:
    data = builtin()
    if part in data["cased"]:
        return data["cased"][part]
    if part.lower() in data["words"]:
        return data["words"][part.lower()]
    if len(part) == 1:
        if part == "a" and following is None:
            return "ア"
        return spell(part)
    if part.isupper():
        return spell(part)
    return kana(part)


def _resolve(token: str, text: str, start: int, end: int) -> dict:
    data = builtin()
    norm = _fold(token)
    low = norm.lower()
    if norm in data["cased"]:
        return {"kind": "dict", "reading": data["cased"][norm]}
    if low in data["heteronyms"]:
        return _context(data["heteronyms"][low], 0, text, start, end)
    if low in data["words"]:
        return {"kind": "dict", "reading": data["words"][low]}
    letters = "".join(c for c in norm if c.isascii() and c.isalpha())
    if not letters:
        return {"kind": "plain", "reading": None}
    if letters.isupper() and len(letters) >= 2:
        spelled = spell(letters)
        if len(letters) >= 4 and any(c in _VOWELS for c in letters):
            options = [{"reading": spelled, "note": SPELL_NOTE}, {"reading": kana(letters), "note": WORD_NOTE}]
            return _context(options, 1 if _pronounceable(letters) else 0, text, start, end, ask=False)
        return {"kind": "letters", "reading": spelled}
    if len(letters) == 1:
        return {"kind": "letters", "reading": "ア" if letters == "a" else spell(letters)}
    parts = _CAMEL.findall(letters)
    if len(parts) > 1 and any(c.isupper() for c in letters[1:]):
        readings = [_part(p, parts[i + 1] if i + 1 < len(parts) else None) for i, p in enumerate(parts)]
        return {"kind": "auto", "reading": "".join(readings)}
    return {"kind": "auto", "reading": kana(letters)}


def _latin_edge(char: str) -> bool:
    return char.isascii() and char.isalpha()


def analyze(text: str) -> list[dict]:
    scan_text = text.translate(_HALFWIDTH)
    pattern, phrases = _phrase_matcher()
    spans: list[tuple[int, int, dict]] = []

    def scan(lo: int, hi: int) -> None:
        for match in _WORD.finditer(scan_text, lo, hi):
            start, end = match.span()
            token = match.group()
            if token == "No" and end < len(scan_text) and scan_text[end] == ".":
                end += 1
                if _NUMBER_AFTER.match(scan_text, end - 1):
                    spans.append((start, end, {"kind": "dict", "reading": "ナンバー"}))
                else:
                    spans.append((start, end, _context(builtin()["heteronyms"]["no."], 1, text, start, end)))
                continue
            spans.append((start, end, _resolve(token, text, start, end)))

    cursor = 0
    if pattern is not None:
        for match in pattern.finditer(scan_text):
            scan(cursor, match.start())
            _, reading, user = phrases[int(match.lastgroup[1:])]
            spans.append((match.start(), match.end(), {"kind": "user" if user else "dict", "reading": reading}))
            cursor = match.end()
    scan(cursor, len(scan_text))
    spans.sort(key=lambda s: s[0])

    segments: list[dict] = []
    position = 0
    previous_end = None
    for start, end, segment in spans:
        if segment.get("reading") is None:
            continue
        between = text[position:start]
        if between:
            joinable = previous_end == position and between.strip(" ") == "" and _latin_edge(scan_text[position - 1]) and _latin_edge(scan_text[start])
            segments.append({"text": between, "kind": "gap", "reading": ""} if joinable else {"text": between, "kind": "plain", "reading": None})
        segment["text"] = text[start:end]
        segment["key"] = f"{segment['text']}\n{_sentence(text, start, end)}"
        segments.append(segment)
        position = end
        previous_end = end
    if position < len(text):
        segments.append({"text": text[position:], "kind": "plain", "reading": None})
    return segments


def compose(segments: list[dict], overrides: dict | None = None) -> str:
    overrides = overrides or {}
    out = []
    for segment in segments:
        if segment.get("reading") is None:
            out.append(segment["text"])
        else:
            out.append(str(overrides.get(segment.get("key"), segment["reading"])))
    return "".join(out)


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
    job = jobs.create("install", f"Set up the reading context model ({LABEL})", engine=ENV_ID)

    def run() -> None:
        jobs.start(job, "Installing the environment")
        if not engines.uv_sync(jobs, job, ENGINES_DIR / ENV_ID):
            return
        if model_status() != "ready":
            job.stage = f"Checking {REPO_ID}"
            try:
                info = HfApi().model_info(REPO_ID, revision=REVISION, files_metadata=True)
            except Exception as exc:
                jobs.fail(job, f"Could not reach Hugging Face: {exc}")
                return
            meta = {
                "engine": ENV_ID,
                "repo_id": REPO_ID,
                "subfolder": None,
                "revision": REVISION,
                "label": LABEL,
                "source_url": f"https://huggingface.co/{REPO_ID}",
            }
            models.write_pending(MODEL_DIR, meta)
            total = sum(int(s.size or 0) for s in info.siblings or [])
            if not hub.fetch_snapshot(jobs, job, REPO_ID, REVISION, MODEL_DIR, ["*"], total):
                return
            models.write_meta(MODEL_DIR, meta)
        jobs.finish(job, {"engine": ENV_ID})

    threading.Thread(target=run, daemon=True, name="install-reading").start()
    return job


class Reader:
    def __init__(self, logs: LogBuffer) -> None:
        self.logs = logs
        self.client: WorkerClient | None = None
        self.lock = threading.Lock()
        self.busy = False
        self.last_used = 0.0
        threading.Thread(target=self._reap_idle, daemon=True, name="reading-idle").start()

    def ready(self) -> bool:
        return installed() and model_status() == "ready"

    def loaded(self) -> bool:
        return self.client is not None and self.client.alive

    def status(self) -> dict:
        state = model_status()
        return {
            "name": NAME,
            "label": LABEL,
            "repo_id": REPO_ID,
            "revision": REVISION,
            "installed": installed(),
            "model": state,
            "size": models._size(MODEL_DIR) if state else 0,
            "ready": self.ready(),
            "loaded": self.loaded(),
            "busy": self.busy,
            "kanalizer": kanalizer.__version__,
            "dictionary": len(user_entries()),
        }

    def convert(self, text: str, use_context: bool = True) -> dict:
        segments = analyze(str(text or ""))
        pending = []
        for segment in segments:
            sentence = segment.pop("_sentence", None)
            if segment.pop("_ask", False) and sentence is not None:
                pending.append((segment, sentence))
        info = {"enabled": bool(use_context), "available": self.ready(), "loaded": self.loaded(), "label": LABEL, "words": len(pending), "decided": 0, "elapsed": None, "wall": None, "error": None}
        if use_context and pending and info["available"]:
            items = []
            for segment, sentence in pending:
                items.append({
                    "state": f"文: {sentence}\n対象の語: {segment['text']}",
                    "question": {
                        "type": "choice",
                        "instructions": f"この日本語の文に含まれる英字の語「{segment['text']}」は、文脈上どの読み方が自然ですか？",
                        "criteria": {c["reading"]: f"{c['reading']}（{c['note']}）" for c in segment["candidates"]},
                    },
                })
            try:
                started = time.perf_counter()
                answers, elapsed = self._decide(items)
                info["elapsed"] = elapsed
                info["wall"] = round(time.perf_counter() - started, 3)
                for (segment, _), answer in zip(pending, answers):
                    readings = [c["reading"] for c in segment["candidates"]]
                    if answer.get("choice") in readings:
                        segment["choice"] = readings.index(answer["choice"])
                        segment["reading"] = answer["choice"]
                        segment["confidence"] = round(float(answer.get("confidence") or 0.0), 3)
                        segment["by"] = "context"
                        info["decided"] += 1
            except Exception as exc:
                info["error"] = str(exc)
                self.logs.add("spectra", f"Reading context model failed: {exc}")
        reading = compose(segments)
        return {"source": str(text or ""), "text": reading, "changed": reading != str(text or ""), "segments": segments, "context": info}

    def _decide(self, items: list[dict]) -> tuple[list[dict], float | None]:
        with self.lock:
            self.busy = True
            try:
                client = self._ensure_client()
                result = client.call("decide", {"items": items}, timeout=300)
                return result.get("answers") or [], result.get("elapsed")
            except WorkerExited:
                self.client = None
                raise
            finally:
                self.busy = False
                self.last_used = time.time()

    def _ensure_client(self) -> WorkerClient:
        if self.client is not None and self.client.alive:
            return self.client
        self.client = None
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

    def release(self) -> None:
        if not self.lock.acquire(blocking=False):
            return
        try:
            client, self.client = self.client, None
            if client is not None:
                self.logs.add("spectra", "Stopping the reading context worker to free memory")
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

    def _reap_idle(self) -> None:
        while True:
            time.sleep(30)
            if self.client is not None and not self.busy and time.time() - self.last_used > IDLE_SECONDS:
                self.release()
