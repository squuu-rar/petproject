#!/usr/bin/env python3
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

def write_target_file(rel_path: str, code: str):
    clean_path = rel_path.strip("`* \t\r\n").lstrip("/\\")
    target_file = (REPO_DIR / clean_path).resolve()
    try:
        target_file.relative_to(REPO_DIR)
    except ValueError:
        log(f"Защита пути: попытка записи вне репозитория ({rel_path})")
        return False

    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text(code.strip() + "\n", encoding="utf-8")
    log(f"Обновлен файл: {clean_path}")
    return True

def apply_files_from_response(content: str):
    if not content or not isinstance(content, str):
        return False

    updated = False
    # Формат 1: === FILE: path === с защитой от поглощения следующего файла
    for m in re.finditer(r"=== FILE:\s*([^\r\n]+)\s*===\r?\n(.*?)(?:=== END FILE ===|(?==== FILE:)|\Z)", content, re.DOTALL):
        if write_target_file(m.group(1), m.group(2)):
            updated = True
    if updated:
        return True

    # Формат 2: ```python:path/to/file.py
    for m in re.finditer(r"```(?:python:)?([a-zA-Z0-9_\-\./]+\.py)\r?\n(.*?)```", content, re.DOTALL):
        if write_target_file(m.group(1), m.group(2)):
            updated = True
    if updated:
        return True

    # Формат 3: Заголовок файла перед блоком кода
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

def call_cptr_agent(task_text):
    repo_files = get_repo_context()
    prompt = f"""Ты — ведущий Python-разработчик музыкального плеера.
Рабочая директория: {REPO_DIR}
Текущая задача: {task_text}

ТЕКУЩИЙ КОД ПРОЕКТА:
{repo_files}

ПРАВИЛА ГЕНЕРАЦИИ:
1. Не используй вызовы внешних инструментов или функций (list_directory, write_file и т.д.).
2. Выводи каждый создаваемый или изменяемый файл целиком строго в блоках следующего вида:

=== FILE: путь/к/файлу.py ===
# полный код файла
=== END FILE ===

3. Обязательно создай или дополни тесты в папке tests/ под новую функциональность.
4. Предоставляй только полный рабочий код без псевдокода и сокращений."""

    headers = {"Content-Type": "application/json"}
    if CPTR_API_KEY:
        headers["Authorization"] = f"Bearer {CPTR_API_KEY}"

    log("Отправка задачи агенту cptr...")
    resp = requests.post(
        CPTR_ENDPOINT,
        headers=headers,
        json={
            "model": CPTR_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "reasoning_effort": "high",
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
    result_text = call_cptr_agent(task_text)
    if not result_text:
        log("Модель вернула пустой ответ. Откат.")
        rollback()
        return

    log("Ответ модели получен. Применяем изменения к файлам...")
    applied = apply_files_from_response(result_text)
    if not applied:
        log("Модель не вернула файлы в ожидаемом формате. Откат.")
        rollback()
        return

    ok, test_out = run_tests()
    if not ok:
        log(f"Тесты завершились с ошибкой:\n{test_out}")
        log("Изменения сбрасываются.")
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
