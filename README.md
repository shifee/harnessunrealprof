# Unreal Harness

Безопасный JSON-harness для Unreal Engine 5.8. Он позволяет отправлять в открытый Unreal Editor декларативные команды: инспектировать уровень и ассеты, создавать материалы и Blueprint, редактировать поддержанные графы, размещать акторов и сохранять изменения.

Harness **не является твоей игрой и не заменяет твой Git-репозиторий**. Есть два отдельных репозитория:

```text
unreal_harness/       технический harness, CLI, installer и launcher
YourProject/           уже существующий пользовательский Unreal-проект
```

Harness устанавливается в каталог уже существующего Unreal-проекта. Он не создаёт отдельную игру и не требует переименовывать проект.

## Что умеет harness

- принимает полные JSON-документы через `Content/Python/actions.json`;
- валидирует весь batch до изменения проекта;
- выполняет команды последовательно и поддерживает зависимости;
- делает dry-run и пишет audit-результат в `Content/Python/result.json`;
- работает через Undo-транзакции Unreal;
- не предоставляет произвольный Python, shell, удаление ассетов или unrestricted `ProcessEvent`;
- поддерживает детерминированный CLI без LLM;
- может использовать LLM только как planner, который генерирует JSON-план.

Поддерживается **Unreal Engine 5.8**. UE 5.7 не поддерживается.

## Установка на Windows

### Шаг 1. Установить внешние зависимости

На Windows-машине должны быть установлены:

1. Unreal Engine 5.8.
2. Visual Studio 2022 с workload **Game development with C++**.
3. Python 3.9 или новее.
4. Git.
5. Видеодрайвер.
6. Ollama, vLLM или доступ к hosted API — только если нужен режим с LLM.

Проверка:

```powershell
py --version
git --version
```

Unreal Engine и Visual Studio не устанавливаются этим репозиторием.

### Шаг 2. Клонировать свой Unreal-проект

Если проект уже находится в Git:

```powershell
cd D:\Projects
git clone <URL_ТВОЕГО_РЕПОЗИТОРИЯ> YourProject
```

В корне должны находиться, например:

```text
D:\Projects\YourProject\YourProject.uproject
D:\Projects\YourProject\Content\
```

Если проект уже существует локально, этот шаг не нужен. Не добавляй в Git generated-каталоги Unreal:

```text
Binaries/
DerivedDataCache/
Intermediate/
Saved/
```

### Шаг 3. Клонировать harness отдельно

```powershell
cd D:\Tools
git clone <URL_РЕПОЗИТОРИЯ_HARNESS> unreal_harness
cd D:\Tools\unreal_harness
```

`<URL_РЕПОЗИТОРИЯ_HARNESS>` — URL этого harness-репозитория. Это **не** URL твоего Unreal-проекта.

Если harness уже скачан, достаточно:

```powershell
cd D:\Tools\unreal_harness
git pull
```

## Упрощённый запуск одной командой

В harness уже есть `run.ps1`. Он принимает путь к **твоему** `.uproject`, устанавливает в него harness, валидирует файлы, запускает Unreal Editor и делает первую read-only проверку.

Из PowerShell:

```powershell
cd D:\Tools\unreal_harness
Set-ExecutionPolicy -Scope Process Bypass
.\run.ps1 -Project "D:\Projects\YourProject\YourProject.uproject"
```

Что делает команда:

1. находит твой `.uproject`;
2. копирует Python harness, plugin и skill-файлы в твой проект;
3. включает требуемые plugins в `.uproject` с backup;
4. запускает validator;
5. находит `UnrealEditor.exe`;
6. запускает проект;
7. ждёт загрузки Editor;
8. отправляет `system.capabilities`;
9. печатает JSON-результат.

По умолчанию launcher ищет Editor здесь:

```text
C:\Program Files\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor.exe
C:\Program Files\Epic Games\UE_5.8.3\Engine\Binaries\Win64\UnrealEditor.exe
```

Если Unreal установлен в другом месте:

```powershell
.\run.ps1 `
  -Project "D:\Projects\YourProject\YourProject.uproject" `
  -EditorPath "D:\UnrealEngine\Engine\Binaries\Win64\UnrealEditor.exe"
```

Или один раз задай переменную:

```powershell
$env:UNREAL_EDITOR = "D:\UnrealEngine\Engine\Binaries\Win64\UnrealEditor.exe"
.\run.ps1 -Project "D:\Projects\YourProject\YourProject.uproject"
```

После загрузки проекта в Output Log должна появиться строка:

```text
[AI WATCHER] Watcher started
```

Если Unreal попросит собрать plugin или перезапустить Editor, согласись и запусти `run.ps1` повторно.

## Повторный запуск без установки и второго Editor

После первого успешного запуска Unreal Editor уже открыт. Для следующих команд используй:

```powershell
.\run.ps1 `
  -Project "D:\Projects\YourProject\YourProject.uproject" `
  -NoInstall `
  -NoEditor
```

Это повторно проверит capabilities через уже открытый Editor.

Флаги launcher:

```text
-NoInstall          не копировать harness повторно
-NoEditor           не запускать второй Unreal Editor
-SkipValidation     пропустить проверку установленных файлов
-EnableNativeMcp    дополнительно включить экспериментальный UE MCP
-EditorPath PATH    явно указать UnrealEditor.exe
-Python COMMAND     заменить py на python или другой launcher
-Timeout SECONDS    timeout запуска и CLI
```

## Ручная установка без launcher

Если нужен пошаговый контроль, из каталога harness выполни:

```powershell
py scripts\install.py `
  --project "D:\Projects\YourProject\YourProject.uproject" `
  --enable-plugins
```

Проверка без запуска Unreal:

```powershell
py scripts\validate_install.py "D:\Projects\YourProject"
```

Безопасный предварительный просмотр изменений:

```powershell
py scripts\install.py `
  --project "D:\Projects\YourProject\YourProject.uproject" `
  --dry-run
```

`--force` заменяет существующие файлы, предварительно создавая timestamped backup:

```powershell
py scripts\install.py `
  --project "D:\Projects\YourProject\YourProject.uproject" `
  --enable-plugins `
  --force
```

`--enable-native-mcp` необязателен. Для текущего JSON CLI он не требуется:

```powershell
py scripts\install.py `
  --project "D:\Projects\YourProject\YourProject.uproject" `
  --enable-plugins `
  --enable-native-mcp
```

## Проверка CLI без LLM

После того как в Output Log появился watcher:

```powershell
py -m unreal_harness capabilities `
  --project "D:\Projects\YourProject\YourProject.uproject" `
  --timeout 60
```

Инспекция уровня:

```powershell
py -m unreal_harness inspect-level `
  --project "D:\Projects\YourProject\YourProject.uproject" `
  --limit 100 `
  --timeout 60
```

Список ассетов:

```powershell
py -m unreal_harness list-assets `
  --project "D:\Projects\YourProject\YourProject.uproject" `
  --path /Game `
  --recursive `
  --limit 100 `
  --timeout 60
```

Пример мутации:

```powershell
py -m unreal_harness spawn-actor `
  --project "D:\Projects\YourProject\YourProject.uproject" `
  --class "/Game/AI/BP_Enemy.BP_Enemy_C" `
  --location '[100, 200, 0]' `
  --actor-label Enemy_01 `
  --save `
  --timeout 60
```

Доступные deterministic-команды:

```text
capabilities
inspect-level
list-assets
spawn-actor
create-blueprint
create-material
set-property
run-json
```

Для простых операций LLM не нужна вообще.

## Запуск LLM-агента

Команда `ask` передаёт модели текст задачи и доступные Unreal actions. Модель возвращает декларативный JSON-план; она не получает shell, Python или прямой доступ к Unreal. Harness валидирует план и исполняет его через обычный action executor.

Поток выполнения:

```text
задача
  -> при неоднозначности: вопрос пользователю и повторное планирование
  -> LLM строит JSON-план из доступных actions
  -> проверка контракта и dry-run
  -> preview либо подтверждение high-risk действий
  -> исполнение через Unreal JSON executor
  -> read-only verification и postcondition assertions
  -> отчёт об изменённых объектах и проверках
```

Для просмотра и проверки плана без исполнения команд используй `--preview`:

```powershell
py -m unreal_harness ask `
  --project "D:\Projects\YourProject\YourProject.uproject" `
  --preview `
  "Создай Blueprint BP_TestActor в /Game/Test"
```

Рискованные actions требуют явного подтверждения в интерактивном терминале. Если подтверждение недоступно, например при запуске без TTY, выполнение таких actions запрещено. После мутации `ask` выполняет отдельный read-only verification batch и сравнивает assertions с фактическими результатами Unreal. Ошибка проверки возвращается как failure; уже сделанные Unreal изменения автоматически не откатываются.

### Ollama

Установи Ollama отдельно и проверь:

```powershell
ollama --version
ollama list
ollama pull <имя-модели>
```

Запусти сервер, если он не запущен как служба:

```powershell
ollama serve
```

Настрой endpoint:

```powershell
$env:UNREAL_HARNESS_LLM_ENDPOINT = "http://127.0.0.1:11434/v1"
$env:UNREAL_HARNESS_LLM_MODEL = "<точное-имя-модели-из-ollama-list>"
```

Первый prompt через launcher:

```powershell
.\run.ps1 `
  -Project "D:\Projects\YourProject\YourProject.uproject" `
  -NoInstall `
  -NoEditor `
  -Prompt "Проинспектируй текущий уровень и перечисли найденные акторы" `
  -Model $env:UNREAL_HARNESS_LLM_MODEL `
  -Endpoint $env:UNREAL_HARNESS_LLM_ENDPOINT `
  -Timeout 120
```

### vLLM

vLLM должен работать как OpenAI-compatible server. Например:

```powershell
vllm serve <model-name> --host 127.0.0.1 --port 8000
```

Затем:

```powershell
$env:UNREAL_HARNESS_LLM_ENDPOINT = "http://127.0.0.1:8000/v1"
$env:UNREAL_HARNESS_LLM_MODEL = "<model-name>"
```

### Hosted OpenAI-compatible API

```powershell
$env:UNREAL_HARNESS_LLM_ENDPOINT = "https://api.example.com/v1"
$env:UNREAL_HARNESS_LLM_MODEL = "provider/model-name"
$env:UNREAL_HARNESS_LLM_API_KEY = "<api-key>"
```

Запуск:

```powershell
.\run.ps1 `
  -Project "D:\Projects\YourProject\YourProject.uproject" `
  -NoInstall `
  -NoEditor `
  -Prompt "Создай базовый Blueprint BP_TestActor в /Game/Test и сохрани его" `
  -Model $env:UNREAL_HARNESS_LLM_MODEL `
  -Endpoint $env:UNREAL_HARNESS_LLM_ENDPOINT `
  -ApiKey $env:UNREAL_HARNESS_LLM_API_KEY `
  -Timeout 120
```

Рекомендация:

- Ollama — локально, приватно, проще всего на Windows;
- vLLM — собственный GPU-сервер и throughput;
- hosted API — обычно лучшее качество, но сеть, стоимость и передача контекста наружу.

## Как давать большие задачи

Не начинай с:

```text
Сделай шутер.
```

Используй последовательные задачи:

```text
Проинспектируй проект и текущий уровень. Ничего не изменяй. Перечисли доступные ассеты и акторы.
```

```text
Создай базовый Actor Blueprint BP_Player в /Game/Shooter/Blueprints. Скомпилируй и сохрани.
```

```text
Создай Blueprint BP_Weapon в /Game/Shooter/Blueprints, используя только доступные actions и graph node kinds. Сначала выполни dry-run.
```

После каждого изменения проверяй состояние read-only командой. Один prompt не превращает пустой проект в гарантированно готовую игру: поддержанные actions ограничены, а сложный gameplay требует итераций.

## Runtime-файлы и диагностика

В пользовательском проекте harness использует:

```text
Content/Python/actions.json       входной JSON batch
Content/Python/result.json        результат и audit
Content/Python/execute_actions.py executor
Content/Python/init_unreal.py     watcher/startup
Plugins/UnrealCodexGraph/          graph bridge
```

При ошибке смотри:

1. вывод CLI;
2. Unreal Output Log;
3. `Content/Python/result.json`;
4. первый failed command и его error message;
5. `changed_objects`.

Ошибка:

```text
Timed out waiting for Unreal result.json
```

обычно означает, что Editor закрыт, watcher не запустился, проект установлен не тот или plugin ещё не собран. Сначала исправь `capabilities`; модель здесь не поможет.

Если `result.json` не меняется:

- проверь `[AI WATCHER] Watcher started`;
- убедись, что открыт именно указанный `.uproject`;
- проверь, что `actions.json` находится в `Content/Python` этого проекта;
- перезапусти Editor после изменения plugins;
- запусти `scripts/validate_install.py`.

## Обновление harness

```powershell
cd D:\Tools\unreal_harness
git pull
py scripts\install.py `
  --project "D:\Projects\YourProject\YourProject.uproject" `
  --enable-plugins `
  --force
py scripts\validate_install.py "D:\Projects\YourProject"
```

Перед удалением timestamped backup-файлов проверь их содержимое.

## Ограничения безопасности

Harness намеренно не предоставляет:

- произвольное выполнение Python;
- shell и запуск процессов из Unreal;
- прямую запись `.uasset` и `.umap`;
- удаление ассетов;
- unrestricted `ProcessEvent`;
- автоматическую замену Blueprint без явного supported action.

Executor остаётся authoritative boundary: даже план от LLM проходит валидацию, dry-run, выполнение и audit.

## Лицензия

MIT. См. [LICENSE](LICENSE).
