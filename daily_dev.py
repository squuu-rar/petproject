#!/usr/bin/env python3
import json
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
MAX_TOOL_ITERATIONS = 12
MAX_FIX_ATTEMPTS = 2

IGNORED_SYSTEM_FILES = {"TASKS.md", "daily_dev.log", "daily_dev.py", "TODO_FAILING.md"}

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

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Создать или перезаписать файл в репозитории",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Относительный путь к файлу"},
                    "content": {"type": "string", "description": "Полное содержимое файла"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Прочитать содержимое файла из репозитория",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Относительный путь к файлу"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "Посмотреть список файлов в папке репозитория",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Путь к папке (по умолчанию .)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "Выполнить shell-команду (pytest, python и т.д.)",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell команда"},
                },
                "required": ["command"],
            },
        },
    },
]

def execute_tool(fn_name, args):
    if fn_name == "write_file":
        target = (REPO_DIR / args["path"]).resolve()
        if not str(target).startswith(str(REPO_DIR.resolve())):
            return {"error": "Path escapes repo directory"}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(args["content"], encoding="utf-8")
        log(f"write_file: {args['path']} ({len(args['content'])} chars)")
        return {"status": "ok", "path": args["path"]}

    elif fn_name == "read_file":
        target = (REPO_DIR / args["path"]).resolve()
        if not target.exists():
            return {"error": "File not found"}
        return {"content": target.read_text(encoding="utf-8", errors="ignore")[:5000]}

    elif fn_name == "list_directory":
        sub = args.get("path", ".")
        target = (REPO_DIR / sub).resolve()
        if not target.exists():
            return {"error": "Directory not found"}
        files = [p.name for p in target.iterdir() if not p.name.startswith(".")]
        return {"files": files}

    elif fn_name == "run_shell":
        log(f"run_shell: {args['command']}")
        res = subprocess.run(args["command"], cwd=REPO_DIR, shell=True, capture_output=True, text=True, timeout=120)
        return {"returncode": res.returncode, "stdout": res.stdout[-2000:], "stderr": res.stderr[-2000:]}

    return {"error": f"Unknown tool: {fn_name}"}

def call_model(messages):
    headers = {"Content-Type": "application/json"}
    if CPTR_API_KEY:
        headers["Authorization"] = f"Bearer {CPTR_API_KEY}"
    resp = requests.post(
        CPTR_ENDPOINT,
        headers=headers,
        json={"model": CPTR_MODEL, "messages": messages, "tools": TOOLS, "tool_choice": "auto"},
        timeout=(15, 600),
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]

def agentic_loop(task_text, repo_context):
    system_prompt = (
        "Ты — автономный senior-разработчик пет-проекта (музыкальный плеер со стримингом и 'волной'). "
        "Тебе дается ОДНА конкретная задача на сегодня. Реализуй ее полностью рабочим кодом. "
        "ОБЯЗАТЕЛЬНО используй write_file, чтобы создать или обновить код/модули/тесты в проекте. "
        "Не завершай ответ только текстом без вызова инструментов, если код еще не написан!"
    )
    user_prompt = f"Задача: {task_text}\n\nФайлы в репо:\n{repo_context}"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    for _ in range(MAX_TOOL_ITERATIONS):
        msg = call_model(messages)
        messages.append(msg)
        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            return msg.get("content", "")

        for call in tool_calls:
            fn_name = call["function"]["name"]
            try:
                args = json.loads(call["function"]["arguments"])
                result = execute_tool(fn_name, args)
            except Exception as ex:
                result = {"error": str(ex)}

            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": json.dumps(result, ensure_ascii=False),
            })
    return "Достигнут лимит итераций."

def run_tests():
    if not (REPO_DIR / "tests").exists():
        return True, "Тестов нет"
    res = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=REPO_DIR, capture_output=True, text=True)
    return res.returncode == 0, res.stdout + res.stderr

def has_staged_code_changes():
    status = run(["git", "status", "--porcelain"], check=False).stdout.strip().splitlines()
    for line in status:
        file_path = line[3:].strip()
        if Path(file_path).name not in IGNORED_SYSTEM_FILES:
            return True
    return False

def main():
    REPO_DIR.mkdir(parents=True, exist_ok=True)
    run(["git", "pull", "--rebase", "--autostash"], check=False)

    idx, task_text = get_next_task()
    if task_text is None:
        log("Все задачи в TASKS.md выполнены.")
        return

    log(f"Начало работы над задачей: {task_text}")
    files_tree = run(["git", "ls-files"], check=False).stdout.strip()

    summary = agentic_loop(task_text, files_tree)
    log(f"Ответ модели: {summary}")

    ok, test_out = run_tests()
    attempt = 0
    while not ok and attempt < MAX_FIX_ATTEMPTS:
        attempt += 1
        log(f"Тесты упали, попытка автоисправления {attempt}/{MAX_FIX_ATTEMPTS}")
        agentic_loop(f"Тесты упали:\n{test_out[-2000:]}\nИсправь код или тесты.", files_tree)
        ok, test_out = run_tests()

    if not ok:
        log("Тесты так и не прошли. Изменения сбрасываются.")
        run(["git", "reset", "--hard", "HEAD"])
        return

    run(["git", "add", "-A"])
    if not has_staged_code_changes():
        log("Модель не создала или не изменила ни одного файла с кодом. Задача не закрыта.")
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
