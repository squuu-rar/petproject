#!/usr/bin/env python3
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
import requests

REPO_DIR = Path(os.environ.get("REPO_DIR", "/home/squ/petproject"))
TASKS_FILE = REPO_DIR / "TASKS.md"
LOG_FILE = REPO_DIR / "daily_dev.log"
CPTR_ENDPOINT = os.environ.get("CPTR_ENDPOINT", "http://127.0.0.1:8000/v1/chat/completions")
CPTR_MODEL = os.environ.get("CPTR_MODEL", "cptr/squ")
CPTR_API_KEY = os.environ.get("CPTR_API_KEY", "")

IGNORED_SYSTEM_FILES = {"TASKS.md", "daily_dev.log", "daily_dev.py", "TODO_FAILING.md", "test_probe.txt"}

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

def has_staged_code_changes():
    status = run(["git", "status", "--porcelain"], check=False).stdout.strip().splitlines()
    for line in status:
        file_path = line[3:].strip()
        if Path(file_path).name not in IGNORED_SYSTEM_FILES:
            return True
    return False

def run_tests():
    if not (REPO_DIR / "tests").exists():
        return True, "Тестов нет"
    res = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=REPO_DIR, capture_output=True, text=True)
    return res.returncode == 0, res.stdout + res.stderr

def call_cptr_agent(task_text):
    prompt = (
        f"Ты — ведущий разработчик музыкального плеера. Рабочая директория проекта: {REPO_DIR}.\n"
        f"Задача на сегодня: {task_text}\n\n"
        "Используй свой встроенный инструмент computer:\n"
        f"1. Изучи файлы в директории {REPO_DIR}.\n"
        f"2. Создай или обнови нужные файлы проекта в {REPO_DIR} полностью готовым кодом.\n"
        f"3. Создай или обнови тесты в {REPO_DIR}/tests/ под сделанные изменения.\n"
        "4. Запусти тесты через терминал и убедись, что они проходят без ошибок.\n"
        "5. Выведи краткий итог того, что было сделано."
    )

    headers = {"Content-Type": "application/json"}
    if CPTR_API_KEY:
        headers["Authorization"] = f"Bearer {CPTR_API_KEY}"

    log("Отправка задачи агенту cptr (Computer)...")
    resp = requests.post(
        CPTR_ENDPOINT,
        headers=headers,
        json={
            "model": CPTR_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        },
        timeout=(15, 1200),  # До 20 минут на автономную работу агента
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]

def main():
    REPO_DIR.mkdir(parents=True, exist_ok=True)
    run(["git", "pull", "--rebase", "--autostash"], check=False)

    idx, task_text = get_next_task()
    if task_text is None:
        log("Все задачи в TASKS.md выполнены.")
        return

    log(f"Начало работы над задачей: {task_text}")
    result_text = call_cptr_agent(task_text)
    log(f"Отчет агента:\n{result_text}")

    # Проверяем тесты в окружении
    ok, test_out = run_tests()
    if not ok:
        log(f"Тесты завершились с ошибкой:\n{test_out}")
        log("Изменения сбрасываются.")
        run(["git", "reset", "--hard", "HEAD"])
        return

    run(["git", "add", "-A"])
    if not has_staged_code_changes():
        log("Агент не создал и не изменил ни одного файла с кодом. Коммит отменен.")
        run(["git", "reset", "--hard", "HEAD"])
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
        sys.exit(1)
