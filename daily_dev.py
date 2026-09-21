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

_raw_ep = os.environ.get("CPTR_ENDPOINT", "http://127.0.0.1:11434/v1/chat/completions").strip()
if "](" in _raw_ep:
    _raw_ep = _raw_ep.split("](")[-1]
CPTR_ENDPOINT = _raw_ep.strip("[]()\"' \t\r\n")

CPTR_MODEL = os.environ.get("CPTR_MODEL", "gemma-coder:latest").strip()
CPTR_API_KEY = os.environ.get("CPTR_API_KEY", "").strip()

MAX_ATTEMPTS = 3
IGNORED_SYSTEM_FILES = {"TASKS.md", "daily_dev.log", "daily_dev.py", "daily_dev_2.py", "TODO_FAILING.md", "test_probe.txt", "CHECKLIST.md"}
IGNORED_DIRS = {"__pycache__", ".pytest_cache", ".git", "venv", ".venv"}

# Ограничение контекста репозитория. Без этого get_repo_context() рано или
# поздно (когда файлов станет много) съест весь num_ctx ещё до того, как
# модель начнёт писать код — и finish_reason=length будет происходить уже
# без всякого reasoning-loop, просто от объёма исходников.
MAX_FILE_CONTEXT_CHARS = 6000

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

def get_repo_context():
    context = []
    total_chars = 0
    for p in sorted(REPO_DIR.rglob("*.py")):
        rel = p.relative_to(REPO_DIR)
        if rel.name in IGNORED_SYSTEM_FILES or any(part.startswith(".") or part in IGNORED_DIRS for part in rel.parts):
            continue
        text = p.read_text(encoding="utf-8")
        if len(text) > MAX_FILE_CONTEXT_CHARS:
            text = text[:MAX_FILE_CONTEXT_CHARS] + f"\n# ... (обрезано, файл больше {MAX_FILE_CONTEXT_CHARS} симв.) ..."
        total_chars += len(text)
        context.append(f"--- Файл: {rel} ---\n{text}")
    log(f"Контекст репозитория: {len(context)} файлов, {total_chars} символов")
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
        log(f"Защита пути: отклонена запись вне репозитория ({rel_path})")
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
        return False, "Ответ модели пуст"

    matches = list(re.finditer(r"=== FILE:\s*([^\r\n]+)\s*===\r?\n(.*?)(?:=== END FILE ===|(?==== FILE:)|\Z)", content, re.DOTALL))
    if not matches:
        matches = list(re.finditer(r"```(?:python:)?([a-zA-Z0-9_\-\./]+\.py)\r?\n(.*?)```", content, re.DOTALL))

    if not matches:
        return False, "Блоки файлов === FILE: ... === не найдены в ответе"

    # Валидация всей пачки перед записью (транзакционность)
    staged = []
    for m in matches:
        rel_path = m.group(1).strip("`* \t\r\n").lstrip("/\\")
        file_code = sanitize_code(m.group(2))
        if rel_path.endswith(".py"):
            try:
                ast.parse(file_code)
            except SyntaxError as e:
                return False, f"Синтаксическая ошибка AST в {rel_path} на строке {e.lineno}: {e.msg}"
        staged.append((rel_path, file_code))

    for rel_path, file_code in staged:
        target = (REPO_DIR / rel_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(file_code + "\n", encoding="utf-8")
        log(f"Обновлен файл: {rel_path}")

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

def run_tests():
    if not (REPO_DIR / "tests").exists():
        return True, "Тестов нет"
    res = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=REPO_DIR, capture_output=True, text=True)
    return res.returncode == 0, res.stdout + res.stderr

def call_cptr_agent(task_text: str, error_feedback: str | None = None):
    repo_files = get_repo_context()

    system_prompt = (
        "Ты — ведущий Python-инженер проекта. Твоя цель — надежная и чистая реализация функционала.\n\n"
        "РЕГЛАМЕНТ РАБОТЫ:\n"
        "1. Рассуждения (CoT): СТРОГО КРАТКО (до 50-100 слов). Зафиксируй ключевые сущности и СРАЗУ переходи к блокам кода.\n"
        "2. Вывод кода: закончив план, СРАЗУ переходи к коду в блоках:\n\n"
        "=== FILE: путь/к/файлу.py ===\n"
        "# полный рабочий код файла без сокращений\n"
        "=== END FILE ===\n\n"
        "Требования:\n"
        "- МИНИМАЛЬНЫЙ ДИФФ: выводи блоки === FILE: ... === ТОЛЬКО для файлов, которые ты создаешь или модифицируешь по текущей задаче. ЗАПРЕЩЕНО трогать или выводить scanner.py, search_service.py и существующие тесты.\n"
        "- НЕ МЕНЯЙ КОНТРАКТ: запрещено менять существующие HTTP-статусы, коды ответов, сигнатуры "
        "уже работающих эндпоинтов и их поведение, если это явно не требуется текущей задачей. "
        "Если нужно расширить поведение — делай это как дополнительный слой поверх старого "
        "контракта, а не заменой (например: не найдено в кэше -> старое поведение остаётся "
        "рабочим, кэш добавляется сверху, а не вместо).\n"
        "- В SQL-запросах для пустых значений используй исключительно NULL, а не Python None.\n"
        "- Явные импорты в начале каждого файла.\n" 
        "- Никаких заглушек pass или ... Только готовая реализация.\n"
        "- Полноценные pytest-тесты."
    )

    feedback_text = ""
    if error_feedback:
        feedback_text = (
            f"\nПРЕДЫДУЩАЯ ПОПЫТКА УПАЛА С ОШИБКОЙ В ТЕСТАХ:\n{error_feedback}\n"
            "ВНИМАНИЕ: тесты упали — значит архитектура в целом верна, чинить нужно точечно. "
            "Твой план должен состоять МАКСИМУМ из 2 предложений. Запрещено рассуждать об "
            "устройстве FastAPI/lifespan/mock и прочей архитектуре — сразу пиши блоки "
            "исправленных файлов. Запрещено менять статус-коды и поведение существующих "
            "эндпоинтов, если это не требуется для исправления именно этой ошибки.\n"
        )

    user_prompt = f"Задача: {task_text}\n{feedback_text}\nТЕКУЩИЙ КОД РЕПОЗИТОРИЯ:\n{repo_files}\n\nСоставь краткий план (до 250 слов) и выведи файлы в формате === FILE: ... ==="

    headers = {"Content-Type": "application/json"}
    if CPTR_API_KEY:
        headers["Authorization"] = f"Bearer {CPTR_API_KEY}"

    payload = {
        "model": CPTR_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 24576,
        "options": {
            "num_ctx": 32768,
            "num_predict": 24576,
        },
    }

    log(f"Отправка запроса в Ollama ({CPTR_MODEL})...")
    resp = requests.post(CPTR_ENDPOINT, headers=headers, json=payload, timeout=(15, 2400))
    resp.raise_for_status()
    data = resp.json()

    choices = data.get("choices", [])
    if not choices:
        log(f"Пустой ответ API: {data}")
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

    log(f"Ollama ответ (finish_reason={finish_reason}): content={len(content)} симв., reasoning={len(reasoning)} симв.")

    if reasoning.strip():
        log("--- Ход мыслей модели (Reasoning) ---")
        for r_line in reasoning.strip().splitlines()[:15]:
            log(f"CoT: {r_line}")

    # Reasoning-loop: модель сожгла бюджет генерации на размышления и не
    # успела вывести ни одного блока с кодом. Раньше сюда подставлялся сырой
    # текст reasoning в качестве "ответа" — apply_files_from_response всё
    # равно не находил в нём === FILE: === и падал с малопонятной ошибкой
    # "структура файлов не найдена". Теперь считаем это явным пустым ответом,
    # чтобы main() сразу дал модели жёсткий запрет на рассуждения на retry.
    if not content.strip() and len(reasoning) > 2000:
        log("Похоже на reasoning-loop: контент пуст, весь бюджет ушёл на CoT.")
        return ""

    return content.strip()

def main():
    REPO_DIR.mkdir(parents=True, exist_ok=True)
    run(["git", "pull", "--rebase", "--autostash"], check=False)

    idx, task_text = get_next_task()
    if task_text is None:
        log("Все задачи в TASKS.md выполнены.")
        return

    log(f"Начало работы над задачей: {task_text}")
    last_test_error = None
    fmt_warning = ""
    task_passed = False

    for attempt in range(1, MAX_ATTEMPTS + 1):
        log(f"--- Попытка {attempt} из {MAX_ATTEMPTS} ---")
        feedback = (fmt_warning + ("\n" + last_test_error if last_test_error else "")).strip() or None
        result_text = call_cptr_agent(task_text, error_feedback=feedback)
        fmt_warning = ""

        if not result_text:
            log("Модель вернула пустой ответ (или сожгла бюджет на reasoning).")
            fmt_warning = (
                "ПРЕДЫДУЩИЙ ОТВЕТ БЫЛ ПУСТЫМ — весь бюджет токенов ушёл на рассуждения, а не на "
                "код. НЕ АНАЛИЗИРУЙ архитектуру заново, она не изменилась. План — максимум 2 "
                "предложения, сразу выводи блоки === FILE: путь ==="
            )
            continue

        applied, parse_err = apply_files_from_response(result_text)
        if not applied:
            log("Не удалось извлечь файлы из ответа.")
            fmt_warning = f"ОШИБКА В СТРУКТУРЕ ФАЙЛОВ: {parse_err}. Исправь синтаксис!"
            continue

        ok, test_out = run_tests()
        if ok:
            log(f"Тесты успешно пройдены на попытке {attempt}!")
            task_passed = True
            break

        log(f"Тесты провалились на попытке {attempt}:\n{test_out}")
        last_test_error = test_out

    if not task_passed:
        log(f"Задача не решена за {MAX_ATTEMPTS} попыток. Откат изменений.")
        rollback()
        return

    run(["git", "add", "-A"])
    if not has_staged_code_changes():
        log("Нет фактических изменений.")
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
