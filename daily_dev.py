#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
import requests

REPO_DIR = Path(os.environ.get("REPO_DIR", "/home/squ/petproject")).resolve()
TASKS_FILE = REPO_DIR / "TASKS.md"
LOG_FILE = REPO_DIR / "daily_dev.log"

_raw_ep = os.environ.get("CPTR_ENDPOINT", "http://127.0.0.1:11434/v1/chat/completions").strip()
if "](" in _raw_ep:
    _raw_ep = _raw_ep.split("](")[-1]
CPTR_ENDPOINT = _raw_ep.strip("[]()\"' \t\r\n")

CPTR_MODEL = os.environ.get("CPTR_MODEL", "gemma-coder:latest").strip()
CPTR_API_KEY = os.environ.get("CPTR_API_KEY", "").strip()

NUM_CTX = int(os.environ.get("CPTR_NUM_CTX", "65536"))
MAX_PREDICT = int(os.environ.get("CPTR_MAX_PREDICT", "49152"))

MAX_ATTEMPTS = 3
IGNORED_SYSTEM_FILES = {
    "TASKS.md", "daily_dev.log", "daily_dev.py", "daily_dev_2.py",
    "TODO_FAILING.md", "test_probe.txt", "CHECKLIST.md"
}
IGNORED_DIRS = {"__pycache__", ".pytest_cache", ".git", "venv", ".venv"}

MAX_FILE_CONTEXT_CHARS = 20000
MAX_TOTAL_CONTEXT_CHARS = 60000
CONTEXT_GLOBS = ("*.py", "*.html", "*.css", "*.js", "*.json")

SHRINK_GUARD_RATIO = 0.6
MIN_OLD_LEN_FOR_SHRINK_CHECK = 250

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
]

def log(msg: str):
    line = "[" + datetime.now().isoformat(timespec='seconds') + "] " + msg
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def run(cmd, cwd=REPO_DIR, check=True):
    log("$ " + " ".join(cmd))
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if res.stdout:
        log(res.stdout.strip())
    if res.stderr:
        log(res.stderr.strip())
    if check and res.returncode != 0:
        raise RuntimeError("Command failed: " + " ".join(cmd) + "\n" + res.stderr)
    return res

def rollback():
    log("Откат незакоммиченных изменений (git reset & clean)...")
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

def get_repo_context(task_text: str = "") -> str:
    task_lower = task_text.lower()
    is_frontend_task = any(task_lower.startswith("- [ ] " + x) or (x + ":") in task_lower for x in ["frontend", "ui", "css", "html"])
    is_backend_task = any(task_lower.startswith("- [ ] " + x) or (x + ":") in task_lower for x in ["api", "db", "cache", "providers", "scanner", "history", "wave", "auth"])

    all_files = set()
    for pattern in CONTEXT_GLOBS:
        all_files.update(REPO_DIR.rglob(pattern))

    candidates = []
    for p in all_files:
        rel = p.relative_to(REPO_DIR)
        if rel.name in IGNORED_SYSTEM_FILES or any(part.startswith(".") or part in IGNORED_DIRS for part in rel.parts):
            continue
        if is_frontend_task and p.suffix == ".py":
            continue
        if is_backend_task and p.suffix in (".js", ".html", ".css"):
            continue
        candidates.append(p)

    keywords = set(re.findall(r"[a-zA-Zа-яА-Я_]{2,}", task_lower)) if task_text else set()
    if is_backend_task:
        keywords.add("db")

    def sort_key(p: Path):
        rel_str = str(p.relative_to(REPO_DIR)).lower()
        relevance = sum(1 for kw in keywords if kw in rel_str)
        if is_backend_task and rel_str in ("db.py", "main.py", "app.py"):
            relevance += 10
        return (-relevance, -p.stat().st_mtime)

    candidates.sort(key=sort_key)

    included = []
    skipped = []
    total_chars = 0
    trunc_notice = "\n... (файл обрезан по лимиту) ...\n"

    for p in candidates:
        rel = p.relative_to(REPO_DIR)
        text = p.read_text(encoding="utf-8", errors="ignore")
        if len(text) > MAX_FILE_CONTEXT_CHARS:
            text = text[:MAX_FILE_CONTEXT_CHARS] + trunc_notice
        if total_chars + len(text) > MAX_TOTAL_CONTEXT_CHARS:
            skipped.append(str(rel))
            continue
        total_chars += len(text)
        included.append((rel, text))

    if skipped:
        log("В контекст НЕ попали (исчерпан общий бюджет): " + ", ".join(skipped))
    log("Контекст репозитория: " + str(len(included)) + " файлов, " + str(total_chars) + " симв. (бюджет " + str(MAX_TOTAL_CONTEXT_CHARS) + ")")

    included.sort(key=lambda pair: str(pair[0]))
    chunks = []
    for rel, text in included:
        chunks.append("--- Файл: " + str(rel) + " ---\n" + text)
    return "\n\n".join(chunks)

def sanitize_code(code: str) -> str:
    lines = code.strip().splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()

def validate_code_syntax(rel_path: str, code: str) -> tuple[bool, str | None]:
    code_lower = code.lower()
    for pattern in LAZY_PATTERNS:
        if pattern in code_lower:
            return False, "Обнаружен плейсхолдер пропуска кода (" + pattern + "). Выведи файл полностью!"

    if rel_path.endswith(".py"):
        try:
            ast.parse(code)
        except SyntaxError as e:
            return False, "Синтаксическая ошибка AST Python в " + rel_path + " на строке " + str(e.lineno) + ": " + str(e.msg)

    if rel_path.endswith(".js") and shutil.which("node"):
        res = subprocess.run(["node", "--check", "-"], input=code, text=True, capture_output=True)
        if res.returncode != 0:
            err_line = res.stderr.strip().splitlines()[-1] if res.stderr.strip() else "Syntax error"
            return False, "Синтаксическая ошибка JavaScript в " + rel_path + ": " + err_line

    if rel_path.endswith(".json"):
        try:
            json.loads(code)
        except Exception as e:
            return False, "Ошибка синтаксиса JSON в " + rel_path + ": " + str(e)

    return True, None

def apply_files_from_response(content: str):
    if not content or not isinstance(content, str):
        return False, "Ответ модели пуст"

    matches = list(re.finditer(r"=== FILE:\s*([^\r\n]+)\s*===\r?\n(.*?)(?:=== END FILE ===|(?==== FILE:)|\Z)", content, re.DOTALL))
    if not matches:
        matches = list(re.finditer(r"```(?:[a-zA-Z0-9_\-]+:)?([a-zA-Z0-9_\-\./]+\.[a-zA-Z0-9]+)\r?\n(.*?)```", content, re.DOTALL))

    if not matches:
        return False, "Блоки файлов === FILE: ... === не найдены в ответе"

    staged = []
    for m in matches:
        rel_path = m.group(1).strip("`* \t\r\n").lstrip("/\\")
        file_code = sanitize_code(m.group(2))

        target = (REPO_DIR / rel_path).resolve()
        try:
            target.relative_to(REPO_DIR)
        except ValueError:
            return False, "Защита пути: путь вне репозитория отклонён: " + rel_path

        valid, err = validate_code_syntax(rel_path, file_code)
        if not valid:
            return False, err

        if target.exists():
            old_len = len(target.read_text(encoding="utf-8", errors="ignore"))
            new_len = len(file_code)
            if old_len > MIN_OLD_LEN_FOR_SHRINK_CHECK and new_len < old_len * SHRINK_GUARD_RATIO:
                return False, "Подозрительная потеря объёма в " + rel_path + ": было " + str(old_len) + ", стало " + str(new_len)

        staged.append((target, rel_path, file_code))

    for target, rel_path, file_code in staged:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(file_code + "\n", encoding="utf-8")
        log("Обновлен файл: " + rel_path)

    return True, None

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

def validate_frontend() -> tuple[bool, str]:
    node_bin = shutil.which("node")
    if node_bin:
        js_dir = REPO_DIR / "static" / "js"
        if js_dir.exists():
            for js_file in js_dir.glob("*.js"):
                res = subprocess.run([node_bin, "--check", str(js_file)], capture_output=True, text=True)
                if res.returncode != 0:
                    err_msg = res.stderr.strip()
                    return False, "JS Syntax Error in " + js_file.name + ": " + err_msg

    for html_path in [REPO_DIR / "static" / "index.html", REPO_DIR / "index.html"]:
        if html_path.exists():
            html_text = html_path.read_text(encoding="utf-8")
            if "app.js" not in html_text or "<script" not in html_text:
                return False, "HTML Error: " + html_path.name + " is missing <script> tag for app.js"

    return True, ""

def run_tests():
    ok, err = validate_frontend()
    if not ok:
        return False, "Frontend validation failed:\n" + err

    if not (REPO_DIR / "tests").exists():
        return True, "Тестов нет"
    try:
        res = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=REPO_DIR, capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") + (e.stderr or "")
        return False, "Тесты не уложились в 300с:\n" + out
    return res.returncode == 0, res.stdout + res.stderr

def call_cptr_agent(task_text: str, error_feedback: str | None = None):
    repo_files = get_repo_context(task_text)

    system_prompt = (
        "Ты — ведущий full-stack инженер проекта. Твоя цель — надежная и чистая реализация функционала.\n\n"
        "РЕГЛАМЕНТ РАБОТЫ:\n"
        "1. Рассуждения (CoT): СТРОГО КРАТКО (до 50-100 слов). Зафиксируй ключевые сущности и СРАЗУ переходи к коду.\n"
        "2. Вывод кода: закончив план, СРАЗУ переходи к коду в блоках:\n\n"
        "=== FILE: путь/к/файлу ===\n"
        "# полный рабочий код файла без сокращений и пропусков\n"
        "=== END FILE ===\n\n"
        "Требования:\n"
        "- МИНИМАЛЬНЫЙ ДИФФ: выводи блоки === FILE: ... === ТОЛЬКО для файлов, которые ты создаешь или модифицируешь.\n"
        "- ПОЛНАЯ ПЕРЕЗАПИСЬ ФАЙЛА: каждый блок заменяет файл на диске целиком.\n"
        "- КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО писать комментарии в духе '/* rest of code */' или '// ... existing code'.\n"
        "- НЕ МЕНЯЙ КОНТРАКТ: запрещено ломать существующие HTTP-статусы, пути роутов, структуру элементов DOM и ID.\n"
        "- Явные импорты в начале каждого файла.\n"
        "- Никаких заглушек pass или ... Только готовая реализация.\n"
        "- Полноценные тесты."
    )

    feedback_text = ""
    if error_feedback:
        feedback_text = (
            "\nПРЕДЫДУЩАЯ ПОПЫТКА УПАЛА С ОШИБКОЙ:\n"
            + error_feedback
            + "\nВНИМАНИЕ: исправь точечно указанную проблему. План — максимум 2 предложения, "
            + "сразу выводи блоки исправленных файлов целиком.\n"
        )

    user_prompt = (
        "Задача: " + task_text + "\n"
        + feedback_text
        + "\nТЕКУЩИЙ КОД РЕПОЗИТОРИЯ:\n" + repo_files
        + "\n\nСоставь краткий план (до 200 слов) и выведи файлы в формате === FILE: ... ==="
    )

    headers = {"Content-Type": "application/json"}
    if CPTR_API_KEY:
        headers["Authorization"] = "Bearer " + CPTR_API_KEY

    payload = {
        "model": CPTR_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
        "max_tokens": MAX_PREDICT,
        "options": {
            "num_ctx": NUM_CTX,
            "num_predict": MAX_PREDICT,
        },
    }

    log("Отправка запроса в Ollama (" + CPTR_MODEL + ")...")
    resp = requests.post(CPTR_ENDPOINT, headers=headers, json=payload, timeout=(15, 2400))
    resp.raise_for_status()
    data = resp.json()

    choices = data.get("choices", [])
    if not choices:
        log("Пустой ответ API: " + str(data))
        return ""

    choice = choices[0]
    message = choice.get("message", {})
    finish_reason = choice.get("finish_reason", "unknown")

    reasoning = (
        message.get("reasoning")
        or message.get("reasoning_content")
        or message.get("thinking")
        or ""
    )
    content = message.get("content") or ""

    if not content.strip() and isinstance(message.get("text"), str):
        content = message["text"]

    log("Ollama ответ (finish_reason=" + str(finish_reason) + "): content=" + str(len(content)) + " симв., reasoning=" + str(len(reasoning)) + " симв.")

    if reasoning.strip():
        log("--- Ход мыслей модели (Reasoning) ---")
        for r_line in reasoning.strip().splitlines()[:15]:
            log("CoT: " + r_line)

    if not content.strip() and len(reasoning) > 2000:
        log("Похоже на reasoning-loop: контент пуст, весь бюджет ушёл на CoT.")
        return ""

    return content.strip()

def main():
    REPO_DIR.mkdir(parents=True, exist_ok=True)
    pull_res = run(["git", "pull", "--rebase", "--autostash"], check=False)
    if pull_res.returncode != 0:
        log("git pull --rebase не удался — прерываю прогон, чтобы не работать поверх сломанного дерева.")
        run(["git", "rebase", "--abort"], check=False)
        return

    idx, task_text = get_next_task()
    if task_text is None:
        log("Все задачи в TASKS.md выполнены.")
        return

    log("Начало работы над задачей: " + task_text)
    last_test_error = None
    fmt_warning = ""
    task_passed = False

    for attempt in range(1, MAX_ATTEMPTS + 1):
        log("--- Попытка " + str(attempt) + " из " + str(MAX_ATTEMPTS) + " ---")
        feedback = (fmt_warning + ("\n" + last_test_error if last_test_error else "")).strip() or None
        result_text = call_cptr_agent(task_text, error_feedback=feedback)
        fmt_warning = ""

        if not result_text:
            log("Модель вернула пустой ответ (или сожгла бюджет на reasoning).")
            fmt_warning = (
                "ПРЕДЫДУЩИЙ ОТВЕТ БЫЛ ПУСТЫМ — бюджет токенов ушёл на рассуждения. "
                "План — максимум 2 предложения, сразу выводи полные блоки === FILE: путь ==="
            )
            continue

        applied, parse_err = apply_files_from_response(result_text)
        if not applied:
            log("Не удалось применить изменения: " + str(parse_err))
            fmt_warning = "ОШИБКА ПРИ ПРИМЕНЕНИИ ФАЙЛОВ: " + str(parse_err) + ". Исправь ошибку и выведи файлы заново!"
            continue

        ok, test_out = run_tests()
        if ok:
            log("Тесты успешно пройдены на попытке " + str(attempt) + "!")
            task_passed = True
            break

        log("Тесты провалились на попытке " + str(attempt) + ":\n" + str(test_out))
        last_test_error = test_out

    if not task_passed:
        log("Задача не решена за " + str(MAX_ATTEMPTS) + " попыток. Откат изменений.")
        rollback()
        return

    run(["git", "add", "-A"])
    if not has_staged_code_changes():
        log("Нет фактических изменений.")
        rollback()
        return

    mark_task_done(idx)
    run(["git", "add", "TASKS.md"])
    run(["git", "commit", "-m", "auto: " + task_text[:70]])
    run(["git", "push"])
    log("Успешно закоммичено и отправлено в репозиторий.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("Критическая ошибка: " + str(e))
        rollback()
        sys.exit(1)
