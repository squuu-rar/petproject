#!/usr/bin/env python3
"""
Идеальный агент автономной разработки (daily_dev.py) для локальной Gemma через Ollama.

Ключевые гарантии безопасности:
1. Автосохранение ручной работы: ваши незакоммиченные правки коммитятся отдельным коммитом
   «autosave» и пушатся ПЕРЕД работой модели, поэтому останавливаться и просить закоммитить
   вручную больше не нужно. Секреты (.env, ключи API), базы, логи, музыка, кэш и слишком
   большие файлы не коммитятся никогда; всё прочее остаётся на диске нетронутым.
2. Изолированный откат (Safe Rollback): команды `git reset --hard` и `git clean -fd`
   полностью удалены. При ошибках модели скрипт точечно возвращает в исходное состояние
   только те файлы, которые модель изменила в текущей попытке. Созданные моделью
   черновики удаляются поштучно. Ваши файлы (run.py, .env и др.) физически неприкосновенны.
3. Автоопределение модели и проверка Ollama: перед обращением к API опрашивает /api/tags.
   Если указанная модель отсутствует, ищет доступные варианты Gemma и переключается
   на них, исключая падения с кодом 404.
4. Автопуш: после каждого коммита выполняется git push с повторами; при расхождении с
   удалённой веткой делается pull --rebase. Непушнутые коммиты (например, при отсутствии
   сети) уходят при следующем запуске. Коммитятся только файлы, записанные моделью.
5. Валидация перед записью на диск: синтаксис проверяется через AST Python, Node.js (для JS)
   и JSON parser. Код с плейсхолдерами ('// rest of code') отклоняется на лету.
"""

from __future__ import annotations

import ast
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import requests

# -----------------------------------------------------------------------------
# 1. КОНФИГУРАЦИЯ И ЗАГРУЗКА .ENV
# -----------------------------------------------------------------------------

REPO_DIR = Path(os.environ.get("REPO_DIR", "/home/squ/petproject")).resolve()
TASKS_FILE = REPO_DIR / "TASKS.md"
LOG_FILE = REPO_DIR / "daily_dev.log"

# Загружаем переменные из .env без сторонних библиотек
ENV_FILE = REPO_DIR / ".env"
if ENV_FILE.exists():
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
CPTR_ENDPOINT = f"{OLLAMA_HOST}/v1/chat/completions"

# Если в переменных окружения случайно осталась старая модель от Gemini, очищаем её
_env_model = os.environ.get("CPTR_MODEL", "").strip()
if not _env_model or "gemini" in _env_model.lower():
    CPTR_MODEL = "gemma-coder:latest"
else:
    CPTR_MODEL = _env_model

# Настройки генерации для моделей Gemma
TEMPERATURE = float(os.environ.get("CPTR_TEMPERATURE", "0.2"))
MAX_PREDICT = int(os.environ.get("CPTR_MAX_PREDICT", "65536"))
THINK = os.environ.get("CPTR_THINK", "false").strip().lower() in ("true", "1", "yes")

# Ограничения и таймауты
MAX_ATTEMPTS = 3
READ_TIMEOUT_S = 2400
ATTEMPT_HARD_TIMEOUT_S = 1500
REASONING_LOOP_ABORT_CHARS = 35000

# Бюджет контекста файлов
MAX_FILE_CONTEXT_CHARS = 65000
MAX_TOTAL_CONTEXT_CHARS = 115000
CONTEXT_GLOBS = ("*.py", "*.html", "*.css", "*.js", "*.json")

# Файлы и папки, которые агент никогда не читает в контекст и не модифицирует
PROTECTED_SYSTEM_FILES = {
    "TASKS.md", "daily_dev.log", "daily_dev.py", "daily_dev_2.py", "daily_dev_3.py",
    "daily_dev_4.py", "run.py", ".env", "tracks.db", "tracks.db-wal", "tracks.db-shm"
}
IGNORED_DIRS = {"__pycache__", ".pytest_cache", ".git", "venv", ".venv", "cache", "static/cache"}

# Защита от ленивого вывода модели
LAZY_PATTERNS = [
    "... (обрезано",
    "... (файл обрезан",
    "// rest of code",
    "/* rest of code",
    "# rest of code",
    "// existing code",
    "/* existing code",
    "# existing code",
    "# ... existing code ...",
    "// ... keep existing",
    "/* keep existing",
    "// previous code",
    "# previous code",
]

SHRINK_GUARD_RATIO = 0.85
MIN_OLD_LEN_FOR_SHRINK_CHECK = 300

# Git-автоматика
def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    return default if raw is None else raw.strip().lower() in ("1", "true", "yes", "on")

AUTO_PUSH = _flag("AUTO_PUSH", True)                          # пушить после каждого коммита
AUTOSAVE_USER_CHANGES = _flag("AUTOSAVE_USER_CHANGES", True)  # коммитить ваши ручные правки
DEFER_STUCK_TASKS = _flag("DEFER_STUCK_TASKS", True)          # откладывать задачи, которые модель не тянет
GIT_REMOTE = os.environ.get("GIT_REMOTE", "origin")
AUTOSAVE_MAX_FILE_BYTES = int(os.environ.get("AUTOSAVE_MAX_FILE_BYTES", str(1024 * 1024)))
PUSH_RETRY_DELAYS = (10, 30, 90)       # паузы между попытками пуша, секунды
GIT_NET_TIMEOUT_S = 120                # потолок на сетевую git-команду (чтобы ssh не зависал)
MAX_TASK_FAIL_RUNS = int(os.environ.get("MAX_TASK_FAIL_RUNS", "3"))

# Что автосохранение не коммитит НИКОГДА
NEVER_COMMIT_NAMES = {".env", "daily_dev.log", "tracks.db", "tracks.db-wal", "tracks.db-shm",
                      "id_rsa", "id_ed25519", "id_ecdsa"}
NEVER_COMMIT_DIRS = ("venv/", ".venv/", "node_modules/", "__pycache__/", ".pytest_cache/",
                     "static/cache/", "static/covers/", "backups/")
NEVER_COMMIT_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".log", ".pyc", ".pem", ".key", ".p12", ".pfx",
                         ".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav", ".webm", ".aac",
                         ".zip", ".tar", ".gz", ".7z", ".part", ".tmp", ".swp"}
# Новые (неотслеживаемые) файлы берём только такие; изменения уже отслеживаемых — любые разрешённые выше
NEW_FILE_SUFFIXES = {".py", ".js", ".css", ".html", ".md", ".txt", ".json", ".svg", ".toml",
                     ".yml", ".yaml", ".cfg", ".ini", ".sh", ".service", ".timer"}
NEW_FILE_NAMES = {"Dockerfile", "docker-compose.yml", "requirements.txt", "Makefile", "LICENSE",
                  ".gitignore", ".dockerignore", ".env.example", ".env.sample"}
NEW_IMAGE_DIRS = ("docs/", "static/img/")
NEW_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

SECRET_PATTERNS = [
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "приватный ключ"),
    (r"\bAIza[0-9A-Za-z_\-]{35}\b", "ключ Google API"),
    (r"\bAQ\.[A-Za-z0-9_\-]{30,}", "ключ Google/Gemini API"),
    (r"\bgh[pousr]_[A-Za-z0-9]{36,}\b", "токен GitHub"),
    (r"\bgithub_pat_[A-Za-z0-9_]{40,}\b", "токен GitHub"),
    (r"\bsk-[A-Za-z0-9_\-]{32,}\b", "ключ API (sk-)"),
    (r"\bxox[baprs]-[A-Za-z0-9\-]{10,}", "токен Slack"),
    (r"(?i)\b(api[_-]?key|secret|token|passwd|password)\b\s*[:=]\s*['\"][A-Za-z0-9_\-/+=.]{20,}['\"]", "ключ или пароль в коде"),
]
GITIGNORE_ENTRIES = [".env", ".env.*", "!.env.example", "!.env.sample", "daily_dev.log", "tracks.db", "tracks.db-wal",
                     "tracks.db-shm", "__pycache__/", "*.pyc", ".pytest_cache/", "venv/", ".venv/", "static/cache/"]

# Пути, изменённые вами и НЕ попавшие в автосохранение (секрет, размер и т.п.): модель их не трогает
LOCKED_PATHS: set = set()

# -----------------------------------------------------------------------------
# 2. СИСТЕМНОЕ ЛОГИРОВАНИЕ И КОМАНДЫ
# -----------------------------------------------------------------------------

def log(msg: str):
    timestamp = datetime.now().isoformat(timespec="seconds")
    line = f"[{timestamp}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def run_cmd(cmd: list[str], cwd: Path = REPO_DIR, check: bool = True,
            env: Optional[dict] = None, timeout: Optional[int] = None) -> subprocess.CompletedProcess:
    shown = " ".join(cmd)
    log("$ " + (shown if len(shown) < 300 else shown[:300] + " …"))
    try:
        res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        res = subprocess.CompletedProcess(cmd, 124, "", f"таймаут {timeout}с")
    if res.stdout and res.stdout.strip():
        log(res.stdout.strip())
    if res.stderr and res.stderr.strip():
        log(res.stderr.strip())
    if check and res.returncode != 0:
        raise RuntimeError(f"Команда завершилась с ошибкой ({res.returncode}): {' '.join(cmd)[:300]}\n{res.stderr}")
    return res

# -----------------------------------------------------------------------------
# 3. БЕЗОПАСНОСТЬ РЕПОЗИТОРИЯ И ИЗОЛИРОВАННЫЙ ОТКАТ
# -----------------------------------------------------------------------------

# --- служебные пути git ------------------------------------------------------

def _git_dir() -> Path:
    res = subprocess.run(["git", "rev-parse", "--git-dir"], cwd=REPO_DIR, capture_output=True, text=True)
    if res.returncode != 0:
        log(f"Критическая ошибка: {REPO_DIR} не является git-репозиторием.")
        sys.exit(1)
    p = Path(res.stdout.strip())
    return p if p.is_absolute() else (REPO_DIR / p)

def acquire_lock():
    """Не даёт двум запускам (таймер и ручной) работать одновременно: они дрались бы за индекс git."""
    fh = open(_git_dir() / "daily_dev.lock", "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("Другой запуск daily_dev.py уже работает — выхожу.")
        sys.exit(0)
    return fh  # ссылку нужно держать до конца процесса

def abort_if_git_busy():
    gd = _git_dir()
    busy = [n for n in ("rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD") if (gd / n).exists()]
    if busy:
        log(f"ОСТАНОВКА: в репозитории не завершено слияние/ребейз ({', '.join(busy)}). "
            "Завершите его вручную (git status), автоматика пока ничего не трогает.")
        sys.exit(1)

# --- журнал попытки: защита от падения посреди изменения файлов -------------------

def _journal_path() -> Path:
    return _git_dir() / "daily_dev_attempt.json"

def write_attempt_journal(snapshots: Dict[Path, Optional[str]]):
    data = {str(p.relative_to(REPO_DIR)): orig for p, orig in snapshots.items()}
    _journal_path().write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

def clear_attempt_journal():
    try:
        _journal_path().unlink()
    except FileNotFoundError:
        pass

def recover_interrupted_attempt():
    """Если прошлый запуск убили (питание, kill -9) посреди попытки, файлы модели остались бы
    «грязными», и автосохранение закоммитило бы непроверенный код как вашу работу."""
    jp = _journal_path()
    if not jp.exists():
        return
    try:
        data = json.loads(jp.read_text(encoding="utf-8"))
    except Exception:
        clear_attempt_journal()
        return
    log("Найден журнал прерванной попытки — возвращаю файлы к исходному состоянию.")
    safe_rollback({REPO_DIR / rel: orig for rel, orig in data.items()})
    clear_attempt_journal()

# --- .gitignore ---------------------------------------------------------------------

def ensure_gitignore():
    """Секреты и служебные файлы должны быть в .gitignore: иначе любой ручной `git add .` их утащит."""
    gi = REPO_DIR / ".gitignore"
    current = gi.read_text(encoding="utf-8").splitlines() if gi.exists() else []
    have = {line.strip() for line in current}
    missing = [e for e in GITIGNORE_ENTRIES if e not in have]
    if not missing:
        return
    addition = ["", "# служебные файлы (добавлено daily_dev.py)"] + missing
    gi.write_text("\n".join(current + addition).lstrip("\n") + "\n", encoding="utf-8")
    log(f"В .gitignore добавлено: {', '.join(missing)}")

# --- что в рабочем дереве изменено ------------------------------------------------------

def git_changes() -> List[Tuple[str, str]]:
    """[(XY, путь)] по `git status --porcelain=v1 -z`. Нормально обрабатывает пробелы и переименования."""
    out = subprocess.run(["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
                         cwd=REPO_DIR, capture_output=True).stdout
    parts = out.split(b"\0")
    changes: List[Tuple[str, str]] = []
    i = 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) < 4:
            continue
        xy = entry[:2].decode()
        path = entry[3:].decode("utf-8", "surrogateescape")
        if xy[0] in "RC" and i < len(parts):
            old = parts[i].decode("utf-8", "surrogateescape")
            i += 1
            if xy[0] == "R":
                changes.append((" D", old))  # старый путь при переименовании — это удаление
        changes.append((xy, path))
    return changes

_SECRET_RES = [(re.compile(pattern), label) for pattern, label in SECRET_PATTERNS]

def find_secret(path: Path) -> Optional[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    for rx, label in _SECRET_RES:
        m = rx.search(text)
        if m:
            return f"{label}, строка {text.count(chr(10), 0, m.start()) + 1}"
    return None

def is_never_commit_path(rel: str) -> Optional[str]:
    p = Path(rel)
    name, posix = p.name, p.as_posix()
    if name in NEVER_COMMIT_NAMES or name.startswith(("id_rsa", "id_ed25519")):
        return "секреты и служебные файлы не коммитятся"
    if name.startswith(".env") and name not in NEW_FILE_NAMES:
        return "файлы .env не коммитятся"
    if any(posix.startswith(d) or f"/{d}" in f"/{posix}" for d in NEVER_COMMIT_DIRS):
        return "служебная папка (кэш, окружение, данные)"
    if p.suffix.lower() in NEVER_COMMIT_SUFFIXES:
        return f"файлы {p.suffix} не коммитятся автоматически"
    return None

def blocked_reason(rel: str, xy: str) -> Optional[str]:
    """Почему этот изменённый путь нельзя коммитить автоматически (None = можно)."""
    reason = is_never_commit_path(rel)
    if reason or "D" in xy:
        return reason
    full = REPO_DIR / rel
    if not full.is_file():
        return None
    size = full.stat().st_size
    if size > AUTOSAVE_MAX_FILE_BYTES:
        return f"слишком большой ({size // 1024} КБ, лимит {AUTOSAVE_MAX_FILE_BYTES // 1024} КБ)"
    p = Path(rel)
    if xy == "??" and p.name in PROTECTED_SYSTEM_FILES and p.name != "TASKS.md":
        # run.py, копии daily_dev_N.py и т.п.: вы намеренно держите их вне git, публиковать их без вас не будем
        return "служебный файл вне git (добавьте вручную через git add, если он нужен в репозитории)"
    if xy == "??":
        in_img_dir = any(p.as_posix().startswith(d) for d in NEW_IMAGE_DIRS)
        allowed = (p.suffix.lower() in NEW_FILE_SUFFIXES or p.name in NEW_FILE_NAMES
                   or (in_img_dir and p.suffix.lower() in NEW_IMAGE_SUFFIXES))
        if not allowed:
            return "новый файл такого типа автоматически не коммитится"
    hit = find_secret(full)
    return f"возможный секрет ({hit})" if hit else None

def commit_paths(paths: List[str], message: str, body: str = "") -> bool:
    """Коммит РОВНО этих путей. --only игнорирует всё, что вы успели добавить в индекс сами
    (например, случайно проиндексированный .env), поэтому оно не попадёт в коммит."""
    paths = sorted(set(paths))
    run_cmd(["git", "add", "-A", "--"] + paths, check=False)
    cmd = ["git", "commit", "--only", "-m", message]
    if body:
        cmd += ["-m", body]
    res = run_cmd(cmd + ["--"] + paths, check=False)
    if res.returncode != 0:
        log("Коммит не создан (возможно, изменений нет).")
        return False
    return True

def autosave_user_changes() -> bool:
    """Коммитит ваши ручные правки, чтобы не просить вас делать это самому."""
    if not AUTOSAVE_USER_CHANGES:
        return False
    changes = git_changes()
    if not changes:
        return False
    if any("U" in xy or xy in ("AA", "DD") for xy, _ in changes):
        log("Автосохранение пропущено: в дереве есть неразрешённые конфликты.")
        return False

    tracked = subprocess.run(["git", "ls-files"], cwd=REPO_DIR, capture_output=True, text=True).stdout.splitlines()
    deleted = [p for xy, p in changes if "D" in xy]
    if len(deleted) > max(10, int(len(tracked) * 0.3)):
        log(f"Автосохранение пропущено: удалено {len(deleted)} из {len(tracked)} файлов — похоже на сбой "
            "(например, не примонтирован диск). Проверьте `git status`.")
        return False

    paths, skipped = [], {}
    for xy, p in changes:
        reason = blocked_reason(p, xy)
        if reason:
            skipped[p] = reason
        else:
            paths.append(p)
    for p, reason in skipped.items():
        if p not in NEVER_COMMIT_NAMES and Path(p).name != "daily_dev.log":
            log(f"  не коммичу {p}: {reason}")
    if not paths:
        return False

    names = [Path(p).name for p in paths]
    title = "autosave: " + ", ".join(names[:4]) + (f" и ещё {len(names) - 4}" if len(names) > 4 else "")
    ok = commit_paths(paths, title, "Локальные изменения, сохранённые автоматически перед запуском daily_dev.py:\n"
                      + "\n".join(f"- {p}" for p in paths[:40]))
    if ok:
        log(f"✓ Ваши правки сохранены коммитом ({len(paths)} файл.).")
    return ok

def refresh_locked_paths():
    """Изменённое вами, но не попавшее в автосохранение: модель такие файлы не трогает."""
    LOCKED_PATHS.clear()
    LOCKED_PATHS.update(Path(p).as_posix() for _, p in git_changes())

# --- синхронизация и пуш ----------------------------------------------------------------

def _git_env() -> dict:
    """Окружение для сетевых git-команд: без интерактивных запросов, с таймаутами ssh.
    Ваш core.sshCommand (например, порт 443) сохраняется — мы только дописываем опции."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    ssh = env.get("GIT_SSH_COMMAND", "").strip()
    if not ssh:
        cfg = subprocess.run(["git", "config", "--get", "core.sshCommand"], cwd=REPO_DIR,
                             capture_output=True, text=True).stdout.strip()
        ssh = cfg or "ssh"
    if "BatchMode" not in ssh:
        ssh += " -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=15 -o ServerAliveCountMax=3"
    env["GIT_SSH_COMMAND"] = ssh
    return env

def git_net(args: List[str]) -> subprocess.CompletedProcess:
    return run_cmd(["git"] + args, check=False, env=_git_env(), timeout=GIT_NET_TIMEOUT_S)

def current_branch() -> Optional[str]:
    res = subprocess.run(["git", "symbolic-ref", "--short", "-q", "HEAD"], cwd=REPO_DIR, capture_output=True, text=True)
    return res.stdout.strip() or None

def _push_enabled() -> Optional[str]:
    """Имя ветки, если пушить можно; иначе None (с пояснением в логе)."""
    if not AUTO_PUSH:
        return None
    if subprocess.run(["git", "remote", "get-url", GIT_REMOTE], cwd=REPO_DIR, capture_output=True).returncode != 0:
        log(f"Автопуш выключен: remote '{GIT_REMOTE}' не настроен (git remote add {GIT_REMOTE} <url>).")
        return None
    branch = current_branch()
    if not branch:
        log("Автопуш выключен: HEAD не на ветке (detached HEAD).")
    return branch

def commits_ahead(branch: str) -> Optional[int]:
    res = subprocess.run(["git", "rev-list", "--count", f"{GIT_REMOTE}/{branch}..HEAD"],
                         cwd=REPO_DIR, capture_output=True, text=True)
    return int(res.stdout.strip() or 0) if res.returncode == 0 else None  # None: ветки на сервере ещё нет

def sync_with_remote() -> bool:
    branch = _push_enabled()
    if not branch:
        return True
    res = git_net(["pull", "--rebase", "--autostash", GIT_REMOTE, branch])
    if res.returncode == 0:
        return True
    if "couldn't find remote ref" in (res.stderr or "").lower():
        return True  # на сервере такой ветки ещё нет — подтягивать нечего
    gd = _git_dir()
    if (gd / "rebase-merge").exists() or (gd / "rebase-apply").exists():
        run_cmd(["git", "rebase", "--abort"], check=False)
    log("pull --rebase не удался (конфликт или нет сети). Локальные коммиты сохранены; "
        "пуш повторится при следующем запуске.")
    return False

def push_pending() -> bool:
    """Пушит всё, что ещё не на сервере. True — если пушить нечего или пуш удался."""
    branch = _push_enabled()
    if not branch:
        return True
    if commits_ahead(branch) == 0:
        return True
    attempts = len(PUSH_RETRY_DELAYS) + 1
    for i in range(attempts):
        res = git_net(["push", "-u", GIT_REMOTE, branch])
        if res.returncode == 0:
            log(f"✓ Запушено в {GIT_REMOTE}/{branch}.")
            return True
        err = ((res.stderr or "") + (res.stdout or "")).lower()
        if "permission denied" in err or "authentication failed" in err or "publickey" in err:
            log("Пуш отклонён: нет доступа. Проверьте ssh-ключ (`ssh -T git@github.com`) и права на репозиторий.")
            return False
        if any(k in err for k in ("rejected", "non-fast-forward", "fetch first")):
            if not sync_with_remote():
                break
            continue
        if i < attempts - 1:
            log(f"Пуш не удался, повтор через {PUSH_RETRY_DELAYS[i]}с ({i + 1}/{attempts - 1}).")
            time.sleep(PUSH_RETRY_DELAYS[i])
    log("Не удалось запушить. Коммиты остались локально и уйдут при следующем запуске.")
    return False

# --- застрявшие задачи ------------------------------------------------------------------

def _state_path() -> Path:
    return _git_dir() / "daily_dev_state.json"

def _load_state() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:
        return {}

def note_task_result(task_idx: int, task_text: str, passed: bool):
    """Считает подряд проваленные запуски по задаче. После MAX_TASK_FAIL_RUNS откладывает её
    в конец очереди, чтобы каждый день не биться об одно и то же."""
    key = hashlib.sha1(task_text.encode("utf-8")).hexdigest()[:12]
    state = _load_state()
    fails = state.setdefault("fails", {})
    if passed:
        fails.pop(key, None)
    else:
        fails[key] = fails.get(key, 0) + 1
        log(f"Задача не решена: это {fails[key]}-й провальный запуск подряд (порог откладывания: {MAX_TASK_FAIL_RUNS}).")
        if DEFER_STUCK_TASKS and fails[key] >= MAX_TASK_FAIL_RUNS and defer_task(task_idx, task_text):
            fails.pop(key, None)
    _state_path().write_text(json.dumps(state), encoding="utf-8")

def defer_task(idx: int, text: str) -> bool:
    lines = TASKS_FILE.read_text(encoding="utf-8").splitlines()
    others = [i for i, l in enumerate(lines) if i != idx and re.match(r"^\s*-\s*\[\s*\]\s*", l)]
    if not others:
        return False  # откладывать некуда: это последняя открытая задача
    lines.append(lines.pop(idx))
    TASKS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok = commit_paths(["TASKS.md"], f"auto: отложена задача после {MAX_TASK_FAIL_RUNS} неудачных запусков: {text[:50]}")
    log("Задача перенесена в конец очереди TASKS.md." if ok else "Не удалось закоммитить перенос задачи.")
    return ok

def scan_snapshots_for_secrets(snapshots: Dict[Path, Optional[str]]) -> Optional[str]:
    for p in snapshots:
        hit = find_secret(p) if p.exists() else None
        if hit:
            return f"{p.relative_to(REPO_DIR)}: {hit}"
    return None

def commit_model_changes(task_text: str, snapshots: Dict[Path, Optional[str]]) -> bool:
    rels = {p.relative_to(REPO_DIR).as_posix() for p in snapshots} | {"TASKS.md"}
    return commit_paths(sorted(rels), f"auto: {task_text[:72]}")

def safe_rollback(staged_targets: Dict[Path, Optional[str]]):
    """
    Точечный и безопасный откат.
    staged_targets: словарь {Path: исходный_текст_до_изменения}.
    Если значение None — файл был создан моделью с нуля, и его нужно удалить.
    """
    log("Точечный откат сгенерированных файлов попытки...")
    for target_path, orig_content in staged_targets.items():
        try:
            if orig_content is None:
                if target_path.exists():
                    target_path.unlink()
                    log(f"  - Удален временный файл: {target_path.name}")
            else:
                target_path.write_text(orig_content, encoding="utf-8")
                log(f"  - Восстановлено исходное состояние: {target_path.name}")
        except Exception as e:
            log(f"  ! Ошибка при откате {target_path.name}: {e}")
    clear_attempt_journal()

# -----------------------------------------------------------------------------
# 4. ВАЛИДАЦИЯ И ПОДГОТОВКА МОДЕЛИ В OLLAMA
# -----------------------------------------------------------------------------

def ensure_ollama_model() -> str:
    """Проверяет доступность Ollama и находит точное имя рабочей модели Gemma."""
    global CPTR_MODEL
    tags_url = f"{OLLAMA_HOST}/api/tags"
    try:
        r = requests.get(tags_url, timeout=5)
        if r.status_code != 200:
            raise RuntimeError(f"Ollama вернула статус {r.status_code}")
        models = [m.get("name") for m in r.json().get("models", [])]
    except requests.exceptions.ConnectionError:
        log(f"Критическая ошибка: Ollama не запущена на {OLLAMA_HOST}!")
        log("Запустите сервис командой: ollama serve (или systemctl --user start ollama)")
        sys.exit(1)
    except Exception as e:
        log(f"Не удалось получить список моделей Ollama: {e}")
        sys.exit(1)

    # 1. Прямое совпадение
    if CPTR_MODEL in models:
        return CPTR_MODEL
    if f"{CPTR_MODEL}:latest" in models:
        CPTR_MODEL = f"{CPTR_MODEL}:latest"
        return CPTR_MODEL

    # 2. Автоподбор модели Gemma из локально установленных
    gemma_models = [m for m in models if "gemma" in m.lower()]
    if gemma_models:
        log(f"Модель '{CPTR_MODEL}' не найдена. Автоматически переключаюсь на доступную локальную модель: '{gemma_models[0]}'")
        CPTR_MODEL = gemma_models[0]
        return CPTR_MODEL

    # 3. Если никакой Gemma нет — предлагаем загрузить
    log(f"Критическая ошибка: модель '{CPTR_MODEL}' отсутствует в Ollama.")
    log(f"Доступные модели на машине: {models}")
    log("Выполните команду в терминале: ollama pull gemma-coder")
    sys.exit(1)

# -----------------------------------------------------------------------------
# 5. УПРАВЛЕНИЕ ЗАДАЧАМИ И КОНТЕКСТОМ
# -----------------------------------------------------------------------------

def get_next_task() -> Tuple[Optional[int], Optional[str]]:
    if not TASKS_FILE.exists():
        return None, None
    lines = TASKS_FILE.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if re.match(r"^\s*-\s*\[\s*\]\s*", line):
            task_text = re.sub(r"^\s*-\s*\[\s*\]\s*", "", line).strip()
            return i, task_text
    return None, None

def mark_task_done(index: int):
    lines = TASKS_FILE.read_text(encoding="utf-8").splitlines()
    lines[index] = re.sub(r"\[\s*\]", "[x]", lines[index], count=1)
    TASKS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

def get_repo_context(task_text: str = "") -> str:
    task_lower = task_text.lower()
    is_frontend_task = any(k in task_lower for k in ("frontend", "ui", "css", "html", "style", "view"))
    is_backend_task = any(k in task_lower for k in ("api", "db", "cache", "provider", "scanner", "history", "stream"))

    all_files = set()
    for pattern in CONTEXT_GLOBS:
        all_files.update(REPO_DIR.rglob(pattern))

    candidates: List[Path] = []
    for p in all_files:
        rel = p.relative_to(REPO_DIR)
        if rel.name in PROTECTED_SYSTEM_FILES or any(part in IGNORED_DIRS for part in rel.parts):
            continue
        if is_frontend_task and p.suffix == ".py" and rel.name not in ("main.py", "schemas.py"):
            continue
        if is_backend_task and p.suffix in (".js", ".html", ".css"):
            continue
        candidates.append(p)

    keywords = set(re.findall(r"[a-zA-Zа-яА-Я_]{2,}", task_lower)) if task_text else set()
    if is_backend_task:
        keywords.update({"db", "track", "history"})

    def sort_key(p: Path):
        rel_str = str(p.relative_to(REPO_DIR)).lower()
        score = sum(1 for kw in keywords if kw in rel_str)
        if rel_str in ("db.py", "main.py", "schemas.py", "static/js/app.js"):
            score += 15
        return (-score, -p.stat().st_mtime)

    candidates.sort(key=sort_key)

    included = []
    total_chars = 0
    trunc_notice = "\n... (файл обрезан по лимиту объема) ...\n"

    for p in candidates:
        rel = p.relative_to(REPO_DIR)
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if len(text) > MAX_FILE_CONTEXT_CHARS:
            text = text[:MAX_FILE_CONTEXT_CHARS] + trunc_notice
        if total_chars + len(text) > MAX_TOTAL_CONTEXT_CHARS:
            continue
        total_chars += len(text)
        included.append((rel, text))

    log(f"Контекст репозитория: {len(included)} файлов, {total_chars} симв. (лимит {MAX_TOTAL_CONTEXT_CHARS})")
    included.sort(key=lambda pair: str(pair[0]))
    return "\n\n".join([f"=== Файл: {rel} ===\n{text}" for rel, text in included])

# -----------------------------------------------------------------------------
# 6. ВАЛИДАЦИЯ КОДА И ПРИМЕНЕНИЕ ИЗМЕНЕНИЙ
# -----------------------------------------------------------------------------

def sanitize_code(code: str) -> str:
    lines = code.strip().splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()

def validate_code_syntax(rel_path: str, code: str) -> Tuple[bool, Optional[str]]:
    code_lower = code.lower()
    for pattern in LAZY_PATTERNS:
        if pattern in code_lower:
            return False, f"Обнаружен маркер пропуска кода ('{pattern}'). Выводи файл полностью без сокращений!"

    # Проверка синтаксиса Python
    if rel_path.endswith(".py"):
        try:
            ast.parse(code)
        except SyntaxError as e:
            return False, f"Синтаксическая ошибка AST Python в {rel_path}:{e.lineno}: {e.msg}"

    # Проверка синтаксиса JavaScript через Node.js
    if rel_path.endswith(".js") and shutil.which("node"):
        res = subprocess.run(["node", "--check", "-"], input=code, text=True, capture_output=True)
        if res.returncode != 0:
            err_line = res.stderr.strip().splitlines()[-1] if res.stderr.strip() else "Syntax error"
            return False, f"Синтаксическая ошибка JavaScript в {rel_path}: {err_line}"

    # Проверка синтаксиса JSON
    if rel_path.endswith(".json"):
        try:
            json.loads(code)
        except Exception as e:
            return False, f"Ошибка структуры JSON в {rel_path}: {e}"

    return True, None

def parse_and_apply_files(response_text: str) -> Tuple[bool, Optional[str], Dict[Path, Optional[str]]]:
    """
    Парсит блоки файлов, проверяет их синтаксис в памяти и сохраняет снапшот
    исходных версий для безопасного отката при сбое тестов.
    """
    if not response_text or not isinstance(response_text, str):
        return False, "Ответ модели пуст", {}

    file_matches = [("FILE", m.group(1), m.group(2)) for m in re.finditer(r"=== FILE:\s*([^\r\n]+)\s*===\r?\n(.*?)=== END FILE ===", response_text, re.DOTALL)]
    append_matches = [("APPEND", m.group(1), m.group(2)) for m in re.finditer(r"=== APPEND:\s*([^\r\n]+)\s*===\r?\n(.*?)=== END APPEND ===", response_text, re.DOTALL)]
    ops_raw = file_matches + append_matches
    if not ops_raw:
        ops_raw = [("FILE", m.group(1), m.group(2)) for m in re.finditer(r"```(?:[a-zA-Z0-9_\-]+:)?([a-zA-Z0-9_\-\./]+\.[a-zA-Z0-9]+)\r?\n(.*?)```", response_text, re.DOTALL)]

    if not ops_raw:
        return False, "Не найдены блоки '=== FILE: путь ===' или '=== APPEND: путь ==='. Соблюдай формат!", {}

    staged_ops: List[Tuple[Path, str, str]] = []
    snapshots: Dict[Path, Optional[str]] = {}

    for mode, raw_path, raw_code in ops_raw:
        rel_path = raw_path.strip("`* \t\r\n").lstrip("/\\")
        chunk_code = sanitize_code(raw_code)
        target = (REPO_DIR / rel_path).resolve()

        if mode == "FILE" and target.exists() and rel_path in ("static/js/app.js", "static/css/style.css"):
            old_sz = len(target.read_text(encoding="utf-8", errors="ignore"))
            if len(chunk_code) < old_sz * 0.85:
                return False, f"Запрещено переписывать {rel_path} целиком с потерей кода! Используй блок === APPEND: {rel_path} === ... === END APPEND ===", {}

        if mode == "APPEND" and target.exists():
            orig_text = target.read_text(encoding="utf-8", errors="ignore")
            if rel_path.endswith("app.js") and orig_text.rstrip().endswith("});"):
                idx = orig_text.rfind("});")
                file_code = orig_text[:idx].rstrip() + "\n\n    // --- Авто-дополнение ---\n" + chunk_code + "\n});"
            else:
                file_code = orig_text.rstrip() + "\n\n" + chunk_code
        else:
            file_code = chunk_code

        try:
            target.relative_to(REPO_DIR)
        except ValueError:
            return False, f"Защита путей: попытка записи вне репозитория ({rel_path})", {}

        if target.name in PROTECTED_SYSTEM_FILES:
            return False, f"Защита системы: модификация служебного файла '{target.name}' запрещена!", {}

        rel_posix = target.relative_to(REPO_DIR).as_posix()
        if rel_posix in LOCKED_PATHS:
            return False, f"Файл '{rel_posix}' содержит ваши локальные изменения, которые нельзя автосохранить: не трогай его.", {}
        never = is_never_commit_path(rel_posix)
        if never:
            return False, f"Запись в '{rel_posix}' запрещена ({never}).", {}

        valid, err = validate_code_syntax(rel_path, file_code)
        if not valid:
            return False, err, {}

        # Проверка подозрительной потери объема
        if target.exists():
            old_len = len(target.read_text(encoding="utf-8", errors="ignore"))
            new_len = len(file_code)
            if old_len > MIN_OLD_LEN_FOR_SHRINK_CHECK and new_len < old_len * SHRINK_GUARD_RATIO:
                return False, f"Подозрительное урезание файла {rel_path}: было {old_len} символов, стало {new_len}", {}
            snapshots[target] = target.read_text(encoding="utf-8")
        else:
            snapshots[target] = None

        staged_ops.append((target, rel_path, file_code))

    # Журнал пишем ДО записи: если процесс убьют посреди попытки, следующий запуск вернёт файлы назад
    write_attempt_journal(snapshots)

    # Запись файлов на диск
    for target, rel_path, file_code in staged_ops:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(file_code + "\n", encoding="utf-8")
        log(f"Обновлен файл: {rel_path}")

    return True, None, snapshots

# -----------------------------------------------------------------------------
# 7. ТЕСТОВЫЙ ПРОГОН
# -----------------------------------------------------------------------------

def run_tests() -> Tuple[bool, str]:
    # 1. Валидация JS в статике
    node_bin = shutil.which("node")
    if node_bin:
        js_dir = REPO_DIR / "static" / "js"
        if js_dir.exists():
            for js_file in js_dir.glob("*.js"):
                res = subprocess.run([node_bin, "--check", str(js_file)], capture_output=True, text=True)
                if res.returncode != 0:
                    return False, f"Ошибка синтаксиса JavaScript в {js_file.name}:\n{res.stderr.strip()}"

    # 2. Проверка импортов и компиляции Python
    py_files = list(REPO_DIR.glob("*.py"))
    for py in py_files:
        if py.name in PROTECTED_SYSTEM_FILES:
            continue
        res = subprocess.run([sys.executable, "-m", "py_compile", str(py)], capture_output=True, text=True)
        if res.returncode != 0:
            return False, f"Ошибка компиляции Python в {py.name}:\n{res.stderr.strip()}"

    # 3. Запуск Pytest (если есть тесты)
    tests_dir = REPO_DIR / "tests"
    if tests_dir.exists() and any(tests_dir.glob("test_*.py")):
        try:
            res = subprocess.run(
                [sys.executable, "-m", "pytest", "-q"],
                cwd=REPO_DIR, capture_output=True, text=True, timeout=300
            )
            if res.returncode != 0:
                return False, f"Провал тестов Pytest:\n{res.stdout}\n{res.stderr}"
        except subprocess.TimeoutExpired:
            return False, "Тесты Pytest превысили таймаут (300с)"

    return True, "Все проверки успешно пройдены"

# -----------------------------------------------------------------------------
# 8. ЗАПРОС К OLLAMA
# -----------------------------------------------------------------------------

def call_gemma_agent(task_text: str, error_feedback: Optional[str] = None) -> str:
    repo_files = get_repo_context(task_text)

    system_prompt = (
        "Ты — старший ведущий инженер проекта. Твоя цель — надежная и законченная реализация функционала.\n\n"
        "ПРАВИЛА И ФОРМАТ ОТВЕТА:\n"
        "1. Рассуждения (Chain of Thought): строго кратко (до 50-80 слов). Никаких зацикливаний.\n"
        "2. Вывод кода производи строго в специальных блоках:\n\n"
        "=== FILE: путь/к/файлу.ext ===\n"
        "# полный рабочий код файла без пропусков\n"
        "=== END FILE ===\n\n"
        "3. МИНИМАЛЬНЫЙ ДИФФ: выводи блоки ТОЛЬКО для файлов, которые ты реально изменяешь или создаешь.\n"
        "4. РЕЖИМ ДОПИСЫВАНИЯ ДЛЯ ФРОНТЕНДА: для крупных файлов static/js/app.js и static/css/style.css ЗАПРЕЩЕНО выводить весь файл целиком! Используй ТОЛЬКО блок дописывания:\n\n"
        "=== APPEND: static/css/style.css ===\n"
        "/* только новые CSS-классы */\n"
        "=== END APPEND ===\n\n"
        "=== APPEND: static/js/app.js ===\n"
        "// только новые функции и слушатели событий (код автоматически вставляется внутрь DOMContentLoaded перед финальной }); поэтому НЕ оборачивай его в DOMContentLoaded повторно; переменные tracklist, audioPlayer, currentlyPlayingTrack, currentView, playFromList, createTrackRow, switchView, showToast, fetchJson уже доступны!)\n"
        "=== END APPEND ===\n\n"
        "Для небольших файлов (.py, .html, тестов) используй обычный === FILE: путь === ... === END FILE ===.\n"
        "5. ЗАПРЕЩЕНО использовать комментарии вида '# rest of code', '// ... keep existing'.\n"
        "6. Сохраняй контракты API, пути эндпоинтов и целостность базы данных."
    )

    feedback_chunk = ""
    if error_feedback:
        feedback_chunk = (
            f"\nВНИМАНИЕ! ПРЕДЫДУЩАЯ ПОПЫТКА УПАЛА С ОШИБКОЙ:\n{error_feedback}\n"
            "Учти и точечно исправь эту ошибку. План — максимум 2 предложения, и сразу блоки файлов.\n"
        )

    user_prompt = (
        f"Задача: {task_text}\n"
        f"{feedback_chunk}\n"
        f"КОД ПРОЕКТА:\n{repo_files}\n\n"
        "Сформируй решение и выведи обновленные файлы в формате === FILE: путь === ... === END FILE ==="
    )

    payload = {
        "model": CPTR_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": MAX_PREDICT,
        "temperature": TEMPERATURE,
        "stream": True,
        "think": THINK
    }

    log(f"Отправка запроса в Ollama ({CPTR_MODEL}), temperature={TEMPERATURE}, think={THINK}...")
    try:
        resp = requests.post(CPTR_ENDPOINT, json=payload, stream=True, timeout=(15, READ_TIMEOUT_S))
    except Exception as e:
        raise RuntimeError(f"Сбой соединения с Ollama по адресу {CPTR_ENDPOINT}: {e}")

    if resp.status_code != 200:
        raise RuntimeError(f"Ollama API HTTP {resp.status_code}: {resp.text[:300]}")

    full_content: List[str] = []
    reasoning_len = 0
    content_len = 0
    start_time = time.monotonic()
    abort_msg = None

    print(f"\n--- [Ответ модели: {CPTR_MODEL}] ---")
    for raw_line in resp.iter_lines():
        if not raw_line:
            continue
        line = raw_line.decode("utf-8")
        if not line.startswith("data: "):
            continue
        data_str = line[6:].strip()
        if data_str == "[DONE]":
            break

        try:
            chunk = json.loads(data_str)
            choices = chunk.get("choices", [])
            if not choices:
                continue
            delta = choices[0].get("delta", {})

            # Рассуждения (thinking/reasoning) выводим серым цветом
            r_chunk = delta.get("reasoning") or delta.get("reasoning_content") or delta.get("thinking") or ""
            if r_chunk:
                reasoning_len += len(r_chunk)
                sys.stdout.write(f"\033[90m{r_chunk}\033[0m")
                sys.stdout.flush()

            # Код и текст решения
            c_chunk = delta.get("content") or ""
            if c_chunk:
                content_len += len(c_chunk)
                full_content.append(c_chunk)
                sys.stdout.write(c_chunk)
                sys.stdout.flush()
        except Exception:
            continue

        # Защита от бесконечного цикла рассуждений
        elapsed = time.monotonic() - start_time
        if content_len == 0 and reasoning_len > REASONING_LOOP_ABORT_CHARS:
            abort_msg = f"Превышен лимит рассуждений ({REASONING_LOOP_ABORT_CHARS} симв.) без генерации кода"
            break
        if elapsed > ATTEMPT_HARD_TIMEOUT_S:
            abort_msg = f"Превышен общий лимит времени на попытку ({ATTEMPT_HARD_TIMEOUT_S}с)"
            break

    resp.close()
    print("\n--- [Конец ответа] ---\n")

    if abort_msg:
        log(f"Попытка прервана: {abort_msg}")
        return ""

    return "".join(full_content).strip()

# -----------------------------------------------------------------------------
# 9. ГЛАВНЫЙ ЦИКЛ РАЗРАБОТКИ
# -----------------------------------------------------------------------------

def main():
    REPO_DIR.mkdir(parents=True, exist_ok=True)
    lock = acquire_lock()  # noqa: F841  (держим ссылку до конца процесса)
    abort_if_git_busy()
    recover_interrupted_attempt()

    # 1. Ваша ручная работа: сохраняем коммитом и пушим ПЕРЕД всем остальным.
    #    Так вы не обязаны коммитить сами, а падение Ollama не задерживает ваши коммиты.
    ensure_gitignore()
    autosave_user_changes()
    sync_with_remote()
    pushed = push_pending()
    refresh_locked_paths()

    def finish() -> None:
        if not pushed:
            log("Завершено, но не все коммиты запушены (см. выше).")
            sys.exit(2)

    # 2. Проверяем Ollama и активируем модель
    active_model = ensure_ollama_model()
    log(f"Локальная среда активна. Модель: {active_model}")

    # 3. Получаем очередную задачу из TASKS.md
    task_idx, task_text = get_next_task()
    if task_text is None:
        log("Все задачи в TASKS.md уже выполнены!")
        finish()
        return

    log(f"Начало работы над задачей: '{task_text}'")
    last_error_feedback: Optional[str] = None
    task_passed = False
    snapshots: Dict[Path, Optional[str]] = {}

    base_ok, base_report = run_tests()
    if not base_ok:
        log(f"ОСТАНОВКА: Тесты в репозитории падают ещё до запуска модели!\n{base_report}")
        finish()
        return

    for attempt in range(1, MAX_ATTEMPTS + 1):
        log(f"=== Попытка {attempt} из {MAX_ATTEMPTS} ===")
        response_text = call_gemma_agent(task_text, error_feedback=last_error_feedback)

        if not response_text:
            last_error_feedback = "Ответ модели пуст или застрял в рассуждениях. Выводи файлы в формате === FILE: путь ==="
            continue

        applied, parse_err, snapshots = parse_and_apply_files(response_text)
        if not applied:
            log(f"Ошибка применения файлов: {parse_err}")
            last_error_feedback = f"Ошибка формата: {parse_err}. Перепиши файлы строго по правилам!"
            safe_rollback(snapshots)
            continue

        try:
            ok, test_report = run_tests()
        except BaseException:
            safe_rollback(snapshots)  # Ctrl+C или сбой на тестах не должен оставить файлы модели «грязными»
            raise

        if ok:
            leaked = scan_snapshots_for_secrets(snapshots)
            if leaked:
                log(f"✗ В коде модели найден секрет ({leaked}) — попытка отклонена.")
                last_error_feedback = "В коде нельзя хранить ключи и пароли: читай их из переменных окружения."
                safe_rollback(snapshots)
                continue
            log(f"✓ Тесты успешно пройдены на попытке {attempt}!")
            task_passed = True
            break

        log(f"✗ Тесты провалились на попытке {attempt}:\n{test_report}")
        last_error_feedback = test_report
        safe_rollback(snapshots)

    if not task_passed:
        log(f"Задача не решена за {MAX_ATTEMPTS} попыток. Репозиторий оставлен в чистом исходном состоянии.")
        note_task_result(task_idx, task_text, False)
        pushed = push_pending() and pushed  # noqa: F841
        finish()
        return

    # 4. Фиксация при успехе: ТОЛЬКО файлы, записанные моделью, и TASKS.md (никакого `git add -A`)
    mark_task_done(task_idx)
    committed = commit_model_changes(task_text, snapshots)
    clear_attempt_journal()
    if committed:
        log("✓ Изменения проверены и зафиксированы коммитом в Git!")
    else:
        log("Коммит не создан: файлы остались в дереве, следующий запуск сохранит их автосохранением.")
    note_task_result(task_idx, task_text, True)
    pushed = push_pending() and pushed  # noqa: F841
    finish()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Работа скрипта прервана пользователем (Ctrl+C).")
        sys.exit(0)
    except Exception as e:
        log(f"Критическая ошибка выполнения: {e}")
        sys.exit(1)
