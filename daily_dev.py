#!/usr/bin/env python3
"""
Идеальный агент автономной разработки (daily_dev.py) для локальной Gemma через Ollama.

Ключевые гарантии безопасности:
1. Защита ручной работы: перед запуском проверяет git status. Если есть незакоммиченные
   файлы пользователя — скрипт немедленно останавливается и не трогает рабочее дерево.
2. Изолированный откат (Safe Rollback): команды `git reset --hard` и `git clean -fd`
   полностью удалены. При ошибках модели скрипт точечно возвращает в исходное состояние
   только те файлы, которые модель изменила в текущей попытке. Созданные моделью
   черновики удаляются поштучно. Ваши файлы (run.py, .env и др.) физически неприкосновенны.
3. Автоопределение модели и проверка Ollama: перед обращением к API опрашивает /api/tags.
   Если указанная модель отсутствует, ищет доступные варианты Gemma и переключается
   на них, исключая падения с кодом 404.
4. Валидация перед записью на диск: синтаксис проверяется через AST Python, Node.js (для JS)
   и JSON parser. Код с плейсхолдерами ('// rest of code') отклоняется на лету.
"""

from __future__ import annotations

import ast
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
TEMPERATURE = float(os.environ.get("CPTR_TEMPERATURE", "1.0"))
MAX_PREDICT = int(os.environ.get("CPTR_MAX_PREDICT", "49152"))
THINK = os.environ.get("CPTR_THINK", "false").strip().lower() in ("true", "1", "yes")

# Ограничения и таймауты
MAX_ATTEMPTS = 3
READ_TIMEOUT_S = 2400
ATTEMPT_HARD_TIMEOUT_S = 1500
REASONING_LOOP_ABORT_CHARS = 35000

# Бюджет контекста файлов
MAX_FILE_CONTEXT_CHARS = 22000
MAX_TOTAL_CONTEXT_CHARS = 95000
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

SHRINK_GUARD_RATIO = 0.55
MIN_OLD_LEN_FOR_SHRINK_CHECK = 300

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

def run_cmd(cmd: list[str], cwd: Path = REPO_DIR, check: bool = True) -> subprocess.CompletedProcess:
    log("$ " + " ".join(cmd))
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if res.stdout and res.stdout.strip():
        log(res.stdout.strip())
    if res.stderr and res.stderr.strip():
        log(res.stderr.strip())
    if check and res.returncode != 0:
        raise RuntimeError(f"Команда завершилась с ошибкой ({res.returncode}): {' '.join(cmd)}\n{res.stderr}")
    return res

# -----------------------------------------------------------------------------
# 3. БЕЗОПАСНОСТЬ РЕПОЗИТОРИЯ И ИЗОЛИРОВАННЫЙ ОТКАТ
# -----------------------------------------------------------------------------

def assert_clean_working_tree():
    """Проверяет, нет ли ручных незакоммиченных изменений перед стартом."""
    status_out = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO_DIR, capture_output=True, text=True
    ).stdout.strip().splitlines()

    dirty_files = []
    for line in status_out:
        if len(line) < 4:
            continue
        rel_str = line[3:].strip().strip('"')
        path = Path(rel_str)
        if path.name in PROTECTED_SYSTEM_FILES or any(part in IGNORED_DIRS for part in path.parts):
            continue
        dirty_files.append(rel_str)

    if dirty_files:
        msg = (
            "ОСТАНОВКА: Обнаружены незакоммиченные изменения пользователя в репозитории:\n"
            + "\n".join(f"  - {f}" for f in dirty_files)
            + "\nЗакоммитьте или спрячьте их (git stash) перед запуском авторазработки!"
        )
        log(msg)
        sys.exit(0)

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

    matches = list(re.finditer(r"=== FILE:\s*([^\r\n]+)\s*===\r?\n(.*?)=== END FILE ===", response_text, re.DOTALL))
    if not matches:
        matches = list(re.finditer(r"```(?:[a-zA-Z0-9_\-]+:)?([a-zA-Z0-9_\-\./]+\.[a-zA-Z0-9]+)\r?\n(.*?)```", response_text, re.DOTALL))

    if not matches:
        return False, "Не найдены блоки файлов '=== FILE: путь ==='. Соблюдай предписанный формат!", {}

    staged_ops: List[Tuple[Path, str, str]] = []
    snapshots: Dict[Path, Optional[str]] = {}

    for m in matches:
        rel_path = m.group(1).strip("`* \t\r\n").lstrip("/\\")
        file_code = sanitize_code(m.group(2))

        target = (REPO_DIR / rel_path).resolve()
        try:
            target.relative_to(REPO_DIR)
        except ValueError:
            return False, f"Защита путей: попытка записи вне репозитория ({rel_path})", {}

        if target.name in PROTECTED_SYSTEM_FILES:
            return False, f"Защита системы: модификация служебного файла '{target.name}' запрещена!", {}

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
        "4. ПОЛНАЯ ПЕРЕЗАПИСЬ: каждый файл пишется целиком от начала до конца.\n"
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
    
    # 1. Проверяем Ollama и активируем модель
    active_model = ensure_ollama_model()
    log(f"Локальная среда активна. Модель: {active_model}")

    # 2. Страховка: проверяем, что у пользователя нет незакоммиченной ручной работы
    assert_clean_working_tree()

    # 3. Получаем очередную задачу из TASKS.md
    task_idx, task_text = get_next_task()
    if task_text is None:
        log("Все задачи в TASKS.md уже выполнены!")
        return

    log(f"Начало работы над задачей: '{task_text}'")
    last_error_feedback: Optional[str] = None
    task_passed = False

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

        ok, test_report = run_tests()
        if ok:
            log(f"✓ Тесты успешно пройдены на попытке {attempt}!")
            task_passed = True
            break

        log(f"✗ Тесты провалились на попытке {attempt}:\n{test_report}")
        last_error_feedback = test_report
        safe_rollback(snapshots)

    if not task_passed:
        log(f"Задача не решена за {MAX_ATTEMPTS} попыток. Репозиторий оставлен в чистом исходном состоянии.")
        return

    # 4. Фиксация в Git при полном успехе
    run_cmd(["git", "add", "-A"])
    mark_task_done(task_idx)
    run_cmd(["git", "add", "TASKS.md"])
    run_cmd(["git", "commit", "-m", f"auto: {task_text[:72]}"])
    log("✓ Изменения успешно проверены и зафиксированы коммитом в Git!")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Работа скрипта прервана пользователем (Ctrl+C).")
        sys.exit(0)
    except Exception as e:
        log(f"Критическая ошибка выполнения: {e}")
        sys.exit(1)
