#!/usr/bin/env python3
import ast
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
import requests

REPO_DIR = Path(os.environ.get("REPO_DIR", "/home/squ/petproject")).resolve()
TASKS_FILE = REPO_DIR / "TASKS.md"
LOG_FILE = REPO_DIR / "daily_dev.log"
CPTR_ENDPOINT = os.environ.get("CPTR_ENDPOINT", "http://127.0.0.1:8000/v1/chat/completions")
CPTR_MODEL = os.environ.get("CPTR_MODEL", "cptr/squ")
CPTR_API_KEY = os.environ.get("CPTR_API_KEY", "")

MAX_ATTEMPTS = 3
IGNORED_SYSTEM_FILES = {"TASKS.md", "daily_dev.log", "daily_dev.py", "TODO_FAILING.md", "test_probe.txt", "CHECKLIST.md"}
IGNORED_DIRS = {"__pycache__", ".pytest_cache", ".git", "venv", ".venv"}

def log(msg: str):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def run(cmd, cwd=REPO_DIR, check=True):
    log(f"$ {' '.join(cmd)}")
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if res.stdout:
        log(res.stdout.strip())
    if res.stderr:
        log(res.stderr.strip())
    if check and res.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{res.stderr}")
    return res

def rollback():
    log("Откат всех незакоммиченных изменений (git reset & clean)...")
    run(["git", "reset", "--hard", "HEAD"], check=False)
    run(["git", "clean", "-fd"], check=False)

def get_next_task():
    if not TASKS_FILE.exists():
        return None, None
    lines = TASKS_FILE.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if re.match(r"^\s*-\s*\[\s*\]\s*", line):
            task_text = re.sub(r"^\s*-\s*\[\s*\]\s*", "", line).strip()
            return i, task_text
    return None, None

def mark_task_done(index):
    lines = TASKS_FILE.read_text(encoding="utf-8").splitlines()
    lines[index] = re.sub(r"\[\s*\]", "[x]", lines[index], count=1)
    TASKS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

def get_repo_context():
    context = []
    for p in sorted(REPO_DIR.rglob("*.py")):
        rel = p.relative_to(REPO_DIR)
        if rel.name in IGNORED_SYSTEM_FILES or any(part.startswith(".") or part in IGNORED_DIRS for part in rel.parts):
            continue
        context.append(f"--- Файл: {rel} ---\n{p.read_text(encoding='utf-8')}")
    return "\n\n".join(context)

def sanitize_code(code: str) -> str:
    lines = code.strip().splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()

def write_target_file(rel_path: str, code: str):
    clean_path = rel_path.strip("`* \t\r\n").lstrip("/\\")
    target_file = (REPO_DIR / clean_path).resolve()
    try:
        target_file.relative_to(REPO_DIR)
    except ValueError:
        log(f"Защита пути: отклонена попытка записи вне репозитория ({rel_path})")
        return False

    clean_content = sanitize_code(code)

    if clean_path.endswith(".py"):
        try:
            ast.parse(clean_content)
        except SyntaxError as e:
            log(f"Синтаксическая ошибка AST в коде для {clean_path}: {e}")
            return False

    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text(clean_content + "\n", encoding="utf-8")
    log(f"Обновлен файл: {clean_path}")
    return True

def apply_files_from_response(content: str):
    if not content or not isinstance(content, str):
        return False

    updated = False
    for m in re.finditer(r"=== FILE:\s*([^\r\n]+)\s*===\r?\n(.*?)(?:=== END FILE ===|(?==== FILE:)|\Z)", content, re.DOTALL):
        if write_target_file(m.group(1), m.group(2)):
            updated = True
    if updated:
        return True

    for m in re.finditer(r"```(?:python:)?([a-zA-Z0-9_\-\./]+\.py)\r?\n(.*?)```", content, re.DOTALL):
        if write_target_file(m.group(1), m.group(2)):
            updated = True
    if updated:
        return True

    for m in re.finditer(r"(?:###\s*|\*\*)?(?:Файл:\s*|File:\s*)?`?([a-zA-Z0-9_\-\./]+\.py)`?\*?\*?\s*\r?\n```(?:python)?\r?\n(.*?)```", content, re.DOTALL):
        if write_target_file(m.group(1), m.group(2)):
            updated = True

    return updated

def has_staged_code_changes():
    status = run(["git", "status", "--porcelain"], check=False).stdout.strip().splitlines()
    for line in status:
        if len(line) < 4:
            continue
        file_path = Path(line[3:].strip().strip('"'))
        if file_path.name in IGNORED_SYSTEM_FILES:
            continue
        if any(part.startswith(".") or part in IGNORED_DIRS for part in file_path.parts):
            continue
        return True
    return False

def run_tests():
    if not (REPO_DIR / "tests").exists():
        return True, "Тестов нет"
    res = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=REPO_DIR, capture_output=True, text=True)
    return res.returncode == 0, res.stdout + res.stderr

def call_cptr_agent(task_text: str, error_feedback: str | None = None):
    repo_files = get_repo_context()
    feedback_section = ""
    if error_feedback:
        feedback_section = f"""
ВНИМАНИЕ! ПРЕДЫДУЩИЙ ВАРИАНТ КОДА ЗАВЕРШИЛСЯ ОШИБКОЙ В ТЕСТАХ:
{error_feedback}

Внимательно изучи трейсбек ошибки выше. Исправь все ненайденные переменные (NameError), отсутствующие импорты (ImportError), несовпадения в схеме БД или сломанные тесты.
"""

    prompt = f"""Ты — ведущий Python-разработчик музыкального плеера.
Рабочая директория: {REPO_DIR}
Текущая задача: {task_text}
{feedback_section}
ТЕКУЩИЙ КОД ПРОЕКТА:
{repo_files}

ПРАВИЛА ГЕНЕРАЦИИ:
1. Не вызывай внешние инструменты или CLI-команды.
2. Выводи каждый создаваемый или изменяемый файл целиком строго в блоках:

=== FILE: путь/к/файлу.py ===
# полный рабочий код файла без markdown-тегов внутри
=== END FILE ===

3. ОБЯЗАТЕЛЬНЫЙ ЧЕКЛИСТ КАЧЕСТВА:
- Импорты: каждый используемый модуль или объект (например, scanner, mutagen, asynccontextmanager, pytest) ДОЛЖЕН быть явно импортирован в начале файла. Никаких неявных глобальных сущностей.
- База данных: сохраняй все существующие поля таблицы tracks (path, title, artist, album, genre, year, track_number, duration, cover_path, source, external_id, cache_path). Не удаляй существующие колонки!
- Тесты: используй фикстуры temp_db и temp_dir из conftest.py. Не создавай базу данных вручную в обход conftest.
- Только полный код: никаких псевдокодов, многоточий и сокращений."""

    headers = {"Content-Type": "application/json"}
    if CPTR_API_KEY:
        headers["Authorization"] = f"Bearer {CPTR_API_KEY}"

    log("Отправка запроса агенту cptr (temperature=0.0)...")
    resp = requests.post(
        CPTR_ENDPOINT,
        headers=headers,
        json={
            "model": CPTR_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": 8192,
        },
        timeout=(15, 1800),
    )
    resp.raise_for_status()
    data = resp.json()
    message = data.get("choices", [{}])[0].get("message", {})
    return message.get("content") or ""

def main():
    REPO_DIR.mkdir(parents=True, exist_ok=True)
    run(["git", "pull", "--rebase", "--autostash"], check=False)

    idx, task_text = get_next_task()
    if task_text is None:
        log("Все задачи в TASKS.md выполнены.")
        return

    log(f"Начало работы над задачей: {task_text}")
    last_error = None
    task_passed = False

    for attempt in range(1, MAX_ATTEMPTS + 1):
        log(f"--- Попытка {attempt} из {MAX_ATTEMPTS} ---")
        result_text = call_cptr_agent(task_text, error_feedback=last_error)
        if not result_text:
            log("Модель вернула пустой ответ.")
            last_error = "Модель вернула пустой ответ. Предоставь полный рабочий код файлов в блоках === FILE: путь === ... === END FILE ==="
            continue

        applied = apply_files_from_response(result_text)
        if not applied:
            log("Не удалось применить файлы из ответа модели (синтаксическая ошибка или неверный формат).")
            last_error = "Файлы не применились. Убедись, что код синтаксически корректен (нет SyntaxError) и заключен в === FILE: путь === ... === END FILE ==="
            continue

        ok, test_out = run_tests()
        if ok:
            log(f"Тесты успешно пройдены на попытке {attempt}!")
            task_passed = True
            break

        log(f"Тесты провалились на попытке {attempt}:\n{test_out}")
        last_error = test_out

    if not task_passed:
        log(f"Задача не решена за {MAX_ATTEMPTS} попыток. Откат изменений.")
        rollback()
        return

    run(["git", "add", "-A"])
    if not has_staged_code_changes():
        log("Нет фактических изменений в кодовой базе.")
        rollback()
        return

    mark_task_done(idx)
    run(["git", "add", "TASKS.md"])
    run(["git", "commit", "-m", f"auto: {task_text[:70]}"])
    run(["git", "push"])
    log("Успешно закоммичено и отправлено в репозиторий.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"Критическая ошибка: {e}")
        rollback()
        sys.exit(1)
