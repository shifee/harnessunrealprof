"""Persistent, guided entry point for people using the harness locally."""
import argparse
import getpass
import json
import os
import subprocess
import sys
from pathlib import Path

from .providers import connection_settings, list_models
from .health import inspect_project, summarize
from .task_store import list_tasks

ROOT = Path(__file__).resolve().parents[1]


def config_path():
    base = Path(os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "UnrealHarness" / "settings.json"


def load_settings():
    path = config_path()
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Неверный формат настроек: " + str(path))
    for key in ("project", "endpoint", "model", "editor"):
        if key in data and not isinstance(data[key], str):
            raise ValueError("Неверный параметр настроек: " + key)
    if type(data.get("auto_editor", True)) is not bool:
        raise ValueError("auto_editor должен быть boolean")
    return {key: data[key] for key in ("project", "endpoint", "model", "editor", "auto_editor") if key in data}


def save_settings(settings):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    persisted = {key: settings[key] for key in ("project", "endpoint", "model", "editor", "auto_editor") if key in settings}
    temporary.write_text(json.dumps(dict(persisted, version=1), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def project_file(value):
    path = Path(value.strip().strip('"')).expanduser().resolve()
    candidates = list(path.glob("*.uproject")) if path.is_dir() else [path]
    if len(candidates) != 1 or not candidates[0].is_file() or candidates[0].suffix.lower() != ".uproject":
        raise ValueError("Выберите существующий .uproject или папку с одним проектом.")
    data = json.loads(candidates[0].read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(".uproject должен содержать JSON object")
    association = str(data.get("EngineAssociation", ""))
    if association[:1].isdigit() and association.split(".")[:2] != ["5", "8"]:
        raise ValueError("Нужен проект Unreal 5.8.")
    return str(candidates[0])


def discover_projects():
    candidates = list(Path.cwd().glob("*.uproject"))
    candidates += list((Path.home() / "Documents/Unreal Projects").glob("*/*.uproject"))
    return sorted({str(path.resolve()) for path in candidates})


def default_editor():
    explicit = os.environ.get("UNREAL_EDITOR")
    candidates = [Path(explicit)] if explicit else []
    base = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Epic Games"
    candidates += sorted(base.glob("UE_5.8*/Engine/Binaries/Win64/UnrealEditor.exe"))
    return str(next((path for path in candidates if path.is_file()), ""))


def prompt(label, default=""):
    value = input(label + (" [" + default + "]" if default else "") + ": ").strip()
    return value or default


def configure(settings):
    updated = dict(settings)
    print("\nНастройка. Enter оставляет значение в скобках; Ctrl+C отменяет.")
    projects = discover_projects()
    for index, project in enumerate(projects, 1):
        print(str(index) + ". " + project)
    default = updated.get("project") or (projects[0] if len(projects) == 1 else "")
    while True:
        value = prompt("Проект: номер или путь к .uproject", default)
        try:
            updated["project"] = project_file(projects[int(value) - 1] if value.isdigit() and 1 <= int(value) <= len(projects) else value)
            break
        except (ValueError, OSError) as exc:
            print(str(exc))
    while True:
        endpoint = prompt("API модели (base URL)", updated.get("endpoint") or connection_settings()[0])
        try:
            updated["endpoint"] = connection_settings(endpoint)[0]
            break
        except (ValueError, RuntimeError) as exc:
            print(str(exc))
    try:
        models = list_models(endpoint=updated["endpoint"], api_key=updated.get("api_key"), timeout=3)
        for index, model in enumerate(models, 1):
            print(str(index) + ". " + model)
    except (ValueError, OSError, RuntimeError) as exc:
        models = []
        print("API пока недоступен: " + str(exc))
        print("Можно сохранить настройки сейчас и подключиться позже через /setup.")
    default = updated.get("model") or os.environ.get("UNREAL_HARNESS_LLM_MODEL", "") or (models[0] if len(models) == 1 else "")
    value = prompt("Модель: номер или точный model ID (можно позже)", default)
    updated["model"] = models[int(value) - 1] if value.isdigit() and 1 <= int(value) <= len(models) else value
    editor = prompt("UnrealEditor: путь (пусто — запуск вручную)", updated.get("editor") or default_editor()).strip('"')
    if editor and not Path(editor).is_file():
        raise ValueError("UnrealEditor не найден: " + editor)
    updated["editor"] = editor
    updated["auto_editor"] = prompt("Открывать Unreal автоматически? да/нет", "да" if updated.get("auto_editor", True) else "нет").lower() in {"да", "yes", "y", "д"}
    save_settings(updated)
    print("Настройки сохранены: " + str(config_path()))
    return updated


def editor_running(project):
    if os.name != "nt":
        return False
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); @(Get-CimInstance Win32_Process -Filter \"Name='UnrealEditor.exe'\" | Select-Object -ExpandProperty CommandLine) | ConvertTo-Json -Compress"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise RuntimeError("Не удалось проверить процессы Unreal. Откройте Editor вручную или используйте --no-editor.")
    rows = json.loads(result.stdout.strip() or "[]")
    rows = [rows] if isinstance(rows, str) else rows or []
    target = str(Path(project).resolve()).replace("/", "\\").casefold()
    return any(isinstance(row, str) and target in row.replace("/", "\\").casefold() for row in rows)


def open_editor(settings):
    project = project_file(settings["project"])
    if editor_running(project):
        print("Этот проект уже открыт в Unreal.")
        return
    editor = settings.get("editor") or default_editor()
    if not editor or not Path(editor).is_file():
        raise ValueError("Укажите UnrealEditor в /setup или откройте проект вручную.")
    subprocess.Popen([editor, project], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    print("Unreal запускается. Первая задача подождёт ответа Editor до 120 секунд.")


def install_runtime(settings):
    installer = ROOT / "scripts/install.py"
    if not installer.is_file():
        raise ValueError("Для установки runtime откройте Start.cmd из исходного проекта; wheel содержит только клиент.")
    project = project_file(settings["project"])
    if editor_running(project):
        raise ValueError("Перед установкой закройте Unreal, затем выполните /install.")
    if (Path(project).parent / "Content/Python/execute_actions.py").exists():
        updated = subprocess.run([sys.executable, str(installer), "--project", project, "--runtime-only"], cwd=ROOT, timeout=120)
        if updated.returncode:
            raise RuntimeError("Не удалось обновить runtime; детали выше.")
    arguments = [sys.executable, str(installer), "--project", project, "--enable-plugins"]
    result = subprocess.run(arguments, cwd=ROOT, timeout=120)
    if result.returncode:
        raise RuntimeError("Установка не завершена. Исходные конфликты сохраняются; детали выше.")
    print("Runtime установлен. При первом открытии Unreal может предложить собрать C++-плагин.")


HELP = """Пишите задачу обычными словами и нажимайте Enter.
/new        начать новую задачу без предыдущего контекста
/tasks      список задач
/resume ID  продолжить остановленную задачу
/setup      изменить проект, API, модель и запуск Unreal
/key        ввести API-ключ скрыто, только для текущего сеанса
/status     проверить файлы проекта и доступность API
/editor     открыть Unreal (сервер модели запускается отдельно)
/install    установить runtime в закрытый проект
/help       эта справка
/exit       выйти"""


def show_progress(event):
    kind = event.get("kind")
    if kind == "model_request":
        print("  Модель обдумывает следующий шаг…", flush=True)
    elif kind == "unreal_request":
        document = event["document"]
        print("  " + ("Проверка плана" if document.get("dry_run") else "Unreal") + ": " +
              ", ".join(command["action"] for command in document["commands"]), flush=True)
    elif kind == "agent_step" and event.get("step", {}).get("reason"):
        print("  " + event["step"]["reason"], flush=True)


def launch(argv=None, *, cli_main=None):
    from .cli import main
    cli_main = cli_main or main
    parser = argparse.ArgumentParser(prog="unreal-harness start", description="Открыть интерактивный Harness")
    parser.add_argument("--project")
    parser.add_argument("--endpoint")
    parser.add_argument("--model")
    parser.add_argument("--no-editor", action="store_true")
    parser.add_argument("--setup", action="store_true")
    args = parser.parse_args(argv)
    print("\nUnreal Harness — интерактивный CLI\n")
    try:
        try:
            settings = load_settings()
        except (ValueError, OSError) as exc:
            print("Не удалось прочитать настройки: " + str(exc) + ". Настроим CLI заново.")
            settings = {}
        for name in ("project", "endpoint", "model"):
            if getattr(args, name):
                settings[name] = getattr(args, name)
        if args.setup or not settings.get("project"):
            settings = configure(settings)
        settings["project"] = project_file(settings["project"])
    except (EOFError, KeyboardInterrupt):
        print("Настройка отменена.")
        return 0
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print("Ошибка настройки: " + str(exc))
        print("Повторите запуск с start --setup для изменения настроек.")
        return 2
    print("Проект: " + settings["project"])
    print("Модель: " + (settings.get("model") or "не выбрана — /setup"))
    print("API: " + (settings.get("endpoint") or connection_settings()[0]))
    try:
        if not (Path(settings["project"]).parent / "Content/Python/execute_actions.py").is_file():
            install_runtime(settings)
        _, checks = inspect_project(settings["project"])
        failures = [check for check in checks if check["status"] == "fail"]
        if failures:
            print("Проект требует настройки: /status покажет детали, /install установит harness.")
        elif settings.get("auto_editor", True) and not args.no_editor:
            open_editor(settings)
    except (ValueError, OSError, RuntimeError) as exc:
        print("Подготовка: " + str(exc))
    print("\n" + HELP)
    session = None
    while True:
        try:
            task = input("\nВы> ").strip()
            if not task:
                continue
            if task in {"/exit", "/quit"}:
                return 0
            if task == "/help":
                print(HELP)
                continue
            if task == "/new":
                session = None
                print("Контекст очищен.")
                continue
            if task == "/setup":
                settings = configure(settings)
                session = None
                continue
            if task == "/editor":
                open_editor(settings)
                continue
            if task == "/key":
                settings["api_key"] = getpass.getpass("API-ключ (пусто — очистить): ")
                print("Ключ задан только для этого сеанса и не сохраняется в настройках.")
                continue
            if task == "/install":
                install_runtime(settings)
                continue
            if task == "/tasks":
                print(json.dumps(list_tasks(settings["project"]), ensure_ascii=False, indent=2))
                continue
            if task == "/status":
                root, checks = inspect_project(settings["project"])
                print(summarize({"project": str(root), "checks": checks, "offline": True,
                                 "success": not any(c["status"] == "fail" for c in checks)}))
                print("Модели API: " + ", ".join(list_models(endpoint=settings.get("endpoint"), api_key=settings.get("api_key"), timeout=3)))
                continue
            if task.startswith("/") and not task.startswith("/resume "):
                print("Неизвестная команда. /help — справка.")
                continue
            if not settings.get("model"):
                print("Сначала выберите модель через /setup.")
                continue
            models = list_models(endpoint=settings.get("endpoint"), api_key=settings.get("api_key"), timeout=3)
            if settings["model"] not in models:
                print("Выбранная модель отсутствует в API. Выполните /setup.")
                continue
            options = ["--project", settings["project"], "--model", settings["model"], "--timeout", "120", "--task-timeout", "600"]
            if settings.get("endpoint"):
                options += ["--endpoint", settings["endpoint"]]
            if "api_key" in settings:
                options += ["--api-key", settings["api_key"]]
            captured = []
            def remember(result):
                if result.get("audit_path") and result.get("task_id"):
                    captured.append(result["task_id"])
            if task.startswith("/resume "):
                    cli_main(["resume", task[len("/resume "):].strip(), *options], result_callback=remember, progress_callback=show_progress)
            else:
                cli_main(["ask", task, *options, *(["--session", session] if session else [])], result_callback=remember, progress_callback=show_progress)
            if captured:
                session = captured[-1]
        except EOFError:
            return 0
        except KeyboardInterrupt:
            print("\nПрервано. Если задача была отправлена, проверьте /tasks и её результат перед повтором.")
        except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
            print("Не удалось выполнить: " + str(exc))
            print("/status — диагностика; /setup — настройки. CLI остаётся открытым.")
