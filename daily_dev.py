#!/usr/bin/env python3
"""
daily_dev.py — автоматический ежедневный апдейт пет-проекта силами gpt-oss (cptr).

Логика одного запуска:
1. git pull
2. берёт первую невыполненную задачу из TASKS.md ("- [ ] ...")
3. собирает контекст репозитория (список файлов + содержимое ключевых файлов)
4. отправляет задачу в cptr (OpenAI-compatible endpoint) с tool-calling
   (write_file — создать/перезаписать файл, run_shell — выполнить команду)
5. agentic-цикл: модель зовёт тулы -> скрипт их исполняет -> результат
   возвращается модели -> и так до финального текстового ответа или лимита итераций
6. прогоняет pytest (если есть тесты), при падении просит модель поправить (до 2 попыток)
7. отмечает задачу выполненной в TASKS.md
8. коммитит и пушит

Настройка — через переменные окружения (см. .service файл):
  REPO_DIR        — путь к репозиторию (по умолчанию /home/squ/petproject)
  CPTR_ENDPOINT   — OpenAI-compatible chat/completions endpoint cptr
  CPTR_MODEL      — имя модели в cptr

Важно: если у вашего уже работающего bridge для write_file другая схема
параметров (не path/content) — поправьте TOOLS и tool_write_file под неё,
чтобы не тестировать новый tool-calling с нуля.
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import requests

# ---------- КОНФИГ ----------
REPO_DIR = Path(os.environ.get("REPO_DIR", "/home/squ/petproject"))
TASKS_FILE = REPO_DIR / "TASKS.md"
LOG_FILE = REPO_DIR / "daily_dev.log"
CPTR_ENDPOINT = os.environ.get("CPTR_ENDPOINT", "http://127.0.0.1:8000/v1/chat/completions")
CPTR_MODEL = os.environ.get("CPTR_MODEL", "cptr/squ")
CPTR_API_KEY = os.environ.get("CPTR_API_KEY", "")
MAX_TOOL_ITERATIONS = 12
MAX_FIX_ATTEMPTS = 2

CONTEXT_ALWAYS = ["README.md", "TASKS.md", "requirements.txt", "main.py"]
MAX_TREE_FILES = 200
MAX_FILE_CHARS = 6000


def log(msg: str):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(cmd, cwd=REPO_DIR, check=True):
    log(f"$ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.stdout:
        log(result.stdout.strip())
    if result.stderr:
        log(result.stderr.strip())
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{result.stderr}")
    return result


# ---------- ЗАДАЧИ ----------
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


# ---------- КОНТЕКСТ РЕПО ----------
def get_repo_tree():
    result = run(["git", "ls-files"], check=False)
    files = result.stdout.strip().splitlines()
    return files[:MAX_TREE_FILES]


def get_file_snippets():
    snippets = []
    for name in CONTEXT_ALWAYS:
        p = REPO_DIR / name
        if p.exists():
            text = p.read_text(encoding="utf-8", errors="ignore")[:MAX_FILE_CHARS]
            snippets.append(f"### {name}\n```\n{text}\n```")
    return "\n\n".join(snippets)


# ---------- TOOL-CALLING ----------
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Создать или перезаписать файл в репозитории",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Путь относительно корня репо"},
                    "content": {"type": "string", "description": "Полное содержимое файла"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "Выполнить shell-команду в корне репозитория (pytest, pip install и т.п.)",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Команда для выполнения"},
                },
                "required": ["command"],
            },
        },
    },
]


def tool_write_file(path, content):
    target = (REPO_DIR / path).resolve()
    if not str(target).startswith(str(REPO_DIR.resolve())):
        return {"error": "path escapes repo dir, refused"}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    log(f"write_file: {path} ({len(content)} chars)")
    return {"status": "ok", "path": path}


def tool_run_shell(command):
    log(f"run_shell: {command}")
    result = subprocess.run(
        command, cwd=REPO_DIR, shell=True, capture_output=True, text=True, timeout=180
    )
    return {
        "returncode": result.returncode,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-4000:],
    }


def call_model(messages):
    headers = {"Content-Type": "application/json"}
    if CPTR_API_KEY:
        headers["Authorization"] = f"Bearer {CPTR_API_KEY}"
    resp = requests.post(
        CPTR_ENDPOINT,
        headers=headers,
        json={"model": CPTR_MODEL, "messages": messages, "tools": TOOLS, "tool_choice": "auto"},
        timeout=300,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]


def agentic_loop(task_text, repo_context):
    system_prompt = (
        "Ты — автономный разработчик пет-проекта (self-hosted музыкальный плеер со своей "
        "'волной'-рекомендацией, только на своей библиотеке файлов). "
        "Тебе даётся ОДНА конкретная задача. Реализуй её маленьким, аккуратным изменением. "
        "Используй write_file для создания/правки файлов и run_shell для запуска тестов/установки "
        "зависимостей. Не переписывай архитектуру целиком, не трогай файлы, не относящиеся к задаче. "
        "Когда закончишь — ответь текстом (без вызова тулов) с кратким summary изменений."
    )
    user_prompt = f"Задача: {task_text}\n\nКонтекст репозитория (ключевые файлы):\n{repo_context}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    for _ in range(MAX_TOOL_ITERATIONS):
        message = call_model(messages)
        messages.append(message)

        tool_calls = message.get("tool_calls")
        if not tool_calls:
            return message.get("content", "")

        for call in tool_calls:
            fn_name = call["function"]["name"]
            args = json.loads(call["function"]["arguments"])
            if fn_name == "write_file":
                result = tool_write_file(**args)
            elif fn_name == "run_shell":
                result = tool_run_shell(**args)
            else:
                result = {"error": f"unknown tool {fn_name}"}

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

    return "Достигнут лимит итераций, задача не завершена полностью."


# ---------- ТЕСТЫ ----------
def run_tests():
    if not (REPO_DIR / "tests").exists() and not list(REPO_DIR.glob("test_*.py")):
        return True, "тестов нет — пропуск"
    result = subprocess.run(["python", "-m", "pytest", "-q"], cwd=REPO_DIR, capture_output=True, text=True)
    return result.returncode == 0, result.stdout + result.stderr


# ---------- GIT ----------
def git_commit_and_push(task_text):
    run(["git", "add", "-A"])
    status = run(["git", "status", "--porcelain"], check=False)
    if not status.stdout.strip():
        log("Нет изменений — коммит не нужен")
        return False
    msg = f"auto: {task_text[:72]}"
    run(["git", "commit", "-m", msg])
    run(["git", "push"])
    return True


# ---------- MAIN ----------
def main():
    REPO_DIR.mkdir(parents=True, exist_ok=True)
    run(["git", "pull", "--rebase"], check=False)

    idx, task_text = get_next_task()
    if task_text is None:
        log("Задач в TASKS.md не осталось — нечего делать сегодня")
        return

    log(f"Задача дня: {task_text}")

    get_repo_tree()
    context = get_file_snippets()

    summary = agentic_loop(task_text, context)
    log(f"Модель закончила: {summary}")

    ok, output = run_tests()
    attempt = 0
    while not ok and attempt < MAX_FIX_ATTEMPTS:
        attempt += 1
        log(f"Тесты упали, попытка исправления {attempt}/{MAX_FIX_ATTEMPTS}")
        fix_prompt = f"Тесты упали после твоих изменений. Вывод:\n{output[-3000:]}\nИсправь."
        summary = agentic_loop(fix_prompt, get_file_snippets())
        ok, output = run_tests()

    if not ok:
        with open(REPO_DIR / "TODO_FAILING.md", "a", encoding="utf-8") as f:
            f.write(
                f"\n## {datetime.now().date()} — {task_text}\n"
                f"Тесты не прошли после {MAX_FIX_ATTEMPTS} попыток:\n```\n{output[-2000:]}\n```\n"
            )
        log("Тесты так и не прошли — зафиксировано в TODO_FAILING.md")

    mark_task_done(idx)
    git_commit_and_push(task_text)
    log("Готово.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"ОШИБКА: {e}")
        sys.exit(1)
