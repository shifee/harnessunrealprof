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
- использует LLM для исследования и выбора следующих JSON-команд в ограниченном агентном цикле.

Поддерживается **Unreal Engine 5.8**. UE 5.7 не поддерживается.

## Просто открыть CLI

На Windows откройте **`Start.cmd` двойным щелчком**. Из PowerShell можно запустить `./run.ps1` без аргументов. После установки клиентского wheel команда `unreal-harness` без аргументов открывает тот же режим.

Первый запуск предложит выбрать `.uproject`, URL API модели и model ID из списка доступных моделей. Настройки сохраняются в `%APPDATA%/UnrealHarness/settings.json` (Linux: `$XDG_CONFIG_HOME/UnrealHarness` или `~/.config/UnrealHarness`). При следующих запусках снова вводить параметры не нужно.

Далее пишите обычный текст в строку `Вы>`:

```text
Создай материал синий металлик в /Game/Materials и сохрани его
Сделай его немного темнее
```

Следующий промпт получает контекст предыдущей задачи. `/new` очищает контекст, `/tasks` показывает задачи, `/resume ID` продолжает остановленную задачу. `/setup` меняет настройки, `/status` проверяет подключение и проект, `/editor` открывает Unreal, `/install` устанавливает или обновляет runtime с резервными копиями. Перед установкой закройте Editor. `/help` показывает команды, `/exit` закрывает CLI.

API-ключ можно ввести скрыто через `/key` на текущий сеанс либо задать `UNREAL_HARNESS_LLM_API_KEY`. Ключ не записывается в файл настроек. CLI подключается к работающему API и **не запускает сервер модели или LM Studio**. При недоступном API окно остаётся открытым.

При первом запуске из checkout отсутствующий runtime устанавливается автоматически. Автоматическое открытие Unreal можно отключить при настройке или командой `unreal-harness start --no-editor`. Для первой сборки C++-плагина всё ещё нужны инструменты Visual Studio; Unreal может предложить собрать модуль. Wheel предоставляет клиент; установщик runtime доступен из исходного репозитория.

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

Команда `ask` использует модель для исследования проекта, определения критериев и выбора следующих пакетов команд. Модель не получает shell, Python или прямой доступ к Unreal.

Поток выполнения:

```text
prompt
  -> проверка проекта и capabilities
  -> исследование состояния через команды чтения
  -> фиксированные критерии успеха
  -> следующий JSON-пакет: валидация, хуки, dry-run, execute
  -> результаты и ошибки возвращаются модели
  -> проверка критериев, отчёт и checkpoint
```

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

## Агентный цикл и проверка пользовательской задачи

По умолчанию `ask` использует `TaskAgent`:

1. Получает capabilities и исследует нужные объекты проекта настоящими командами чтения.
2. По исходному запросу и наблюдениям составляет критерии успеха. Каждый критерий — действие чтения плюс проверяемое значение. Критерии фиксируются до изменений.
3. Выбирает следующий небольшой пакет команд, проверяет его через dry-run и выполняет.
4. Возвращает модели результаты и ошибки. После известной ошибки исполнения следующий шаг допускает только чтение фактического состояния.
5. Когда модель предлагает завершение, host выполняет критерии. При несовпадении результатов цикл продолжается в пределах бюджета.

Повтор одинаковой команды изменения между шагами блокируется. При таймауте или другом неизвестном статусе выполнения агент останавливается. Частичные изменения сохраняются в отчёте; общий rollback не добавлен. `$ref` работает внутри одного пакета, между шагами используются фактические пути из наблюдений. Для неподдерживаемого результата или недоступной проверки агент сообщает `unsupported`.

По умолчанию: 8 шагов модели, 80 отправленных команд (включая dry-run, исследование и критерии), общий бюджет 300 секунд. Настройки: `--max-steps`, `--max-commands`, `--task-timeout`; в `run.ps1` — `-MaxSteps`, `-MaxCommands`, `-TaskTimeout`. Время проверяется между операциями и ограничивает ожидание сетевых запросов. `--single-pass` / `-SinglePass` включает прежний одноплановый `AgentRun`.

Трасса всей задачи сохраняется в `Saved/HarnessTasks`: исходный текст, запросы и ответы модели, критерии, результаты Unreal, ошибки и время. API-ключи и HTTP-заголовки в трассу не записываются. `Saved/HarnessAudit` продолжает хранить отдельные обмены с executor. Новые трассы содержат checkpoint для явного продолжения через `resume`; старые трассы без версии checkpoint продолжать нельзя.

Критерии составляет модель, поэтому их полнота зависит от понимания запроса. Они проверяются программно и не меняются после начала изменений. Для независимой оценки добавлен набор эталонных промптов:

```powershell
# Проверить только структуру набора, без запуска модели и Unreal
py scripts/run_agent_evals.py --validate-only

# Выполнить реальные промпты в открытом тестовом Unreal-проекте
py scripts/run_agent_evals.py --project "D:\Projects\YourProject\YourProject.uproject" `
  --endpoint "http://127.0.0.1:1234/v1" --model "YOUR_MODEL" --trials 3
```

[Набор](evals/agent-tasks.json) содержит пять задач и независимые эталонные проверки: папка, синий металлик, чтение материала без изменений, правка существующего материала и отказ от неподдерживаемой генерации геометрии. Живой прогон создаёт тестовые ассеты в отдельной папке `/Game/HarnessAgentEvals/<run>/<trial>/<case>` и оставляет их для проверки. Отчёт с долей успешных промптов сохраняется в `Saved/HarnessEvals`; незавершённый запрос останавливает прогон. Проверка `asset.inspect.exists_on_disk` подтверждает существование файла пакета, но не доказывает сохранение всех последних значений в памяти.

## Проверка планов и исправление ошибок модели

CLI и Unreal используют один контракт: `unreal_harness/action_contract.py`. Установщик копирует его в `Content/Python/action_contract.py`. Контракт содержит схемы всех аргументов, вложенных Blueprint-операций, ограничения и примеры. `system.describe_actions` возвращает эти схемы; [полный JSON-каталог](docs/action-contract.json) генерируется командой `py scripts/export_action_contract.py`. Проверка актуальности: `py scripts/export_action_contract.py --check`.

В `ask` большой каталог сначала сокращается отдельным запросом к модели, затем ей передаются полные схемы выбранных действий. Неверный JSON или аргументы отправляются модели вместе с предыдущим ответом и точной ошибкой. По умолчанию разрешены две попытки исправления (`--max-corrections 0`, `1` или `2`). CLI и Unreal валидируют весь пакет, включая раскрытые рецепты, до исполнения. Dry-run проверяет план, но не читает уровень и не гарантирует существование всех native объектов: чтение выполняется отдельным настоящим вызовом.

Для запросов только на чтение можно принудительно запретить изменения:

```powershell
py -m unreal_harness ask --project "D:\Projects\YourProject\YourProject.uproject" `
  --endpoint "http://127.0.0.1:1234/v1" --model "YOUR_MODEL" --read-only `
  "Найди PlayerStart и перечисли координаты"
```

По умолчанию `ask` печатает краткий итог и путь к аудиту. `--json` выводит полный JSON-отчёт для интеграций. Детерминированные команды, включая `run-json`, продолжают выводить JSON. Каждый ответ Unreal и отправленный ему документ сохраняются в `Saved/HarnessAudit`, поэтому последующая проверка не затирает сведения о выполненных изменениях.

После runtime-ошибки зависимые команды пропускаются, а дальнейшие изменения и сохранение блокируются даже без явно объявленной зависимости. Ранее успешные команды могут оставить изменения: смотри `changed_objects`, `partial_changes` и аудит. Отмена отдельной Undo-транзакции не является гарантией отката уже сохранённых файлов.

В режиме `--single-pass` после изменения проверяются значения параметров материалов, существование и родительский класс Blueprint, свойства UObject, положение акторов, компоненты Blueprint и параметры Material Instance. Для графов, backend-операций, функций, рецептов и некоторых Blueprint-полей эта автоматическая проверка неполная; результат сообщает об этом с `success: false`. В обычном цикле проверяются фиксированные критерии пользовательской задачи. Дополнительный непустой документ чтения можно передать через `--verify-json PATH`; он должен содержать только read-only действия. Успешное чтение дополнительного документа само по себе не проверяет значения, заданные пользователем.

Поддержка structured output согласуется с сервером: `json_object`, затем при явном отказе `json_schema`, затем обычный JSON-ответ без параметра формата. Содержимое всегда проверяется локально. HTTP-ошибки включают текст ответа сервера.

После обновления исходников обнови runtime в Unreal-проекте по инструкции ниже: старый установленный executor не получает исправления автоматически. Генерация новой геометрии и анимаций не добавлена; модель может использовать только действия текущего каталога.

## Runtime-файлы и диагностика

### Проверка перед промптом

```powershell
# Только файлы проекта: Editor и сервер модели не нужны
py -m unreal_harness doctor --project "D:\Projects\YourProject\YourProject.uproject" --offline

# Runtime, ответ watcher и доступность выбранной модели
py -m unreal_harness doctor --project "D:\Projects\YourProject\YourProject.uproject" `
  --endpoint "http://127.0.0.1:1234/v1" --model "MODEL_ID" --timeout 10

# Точные имена моделей, которые сообщает сервер
py -m unreal_harness models --endpoint "http://127.0.0.1:1234/v1"
```

`doctor` выдаёт отдельный статус и следующий шаг для каждой проблемы. `--json` доступен для обеих команд. Проверка списка моделей подтверждает доступность `/models`, но не проверяет генерацию и качество планов. Живой `doctor` отправляет одно действие чтения `system.capabilities`; offline-режим не пишет в канал harness. При незавершённом запросе диагностика не отправляет новый запрос Editor.

Перед `ask` автоматически проверяются путь проекта, plugins, наличие и актуальность runtime и незавершённый запрос. При ошибке промпт модели не отправляется. Для недоступного graph bridge графовые действия исключаются из каталога планирования.

Ожидание Editor и модели задаётся отдельно: `--timeout 30 --llm-timeout 180` для `ask`, либо `-Timeout 30 -ModelTimeout 180` для `run.ps1`. Это время на каждый запрос, а не ограничение общей длительности задачи. Команды чтения через `ask` выводят данные, включая свойства объектов и параметры материалов.

CLI резервирует файловый канал проекта на весь цикл `ask`, включая ожидание модели и проверку результата. Второй запуск для того же проекта сразу сообщает, что канал занят. Блокировка освобождается при завершении процесса.

При таймауте `Content/Python/harness.pending.json` сохраняет отправленный документ. Таймаут означает неизвестный статус выполнения. Новый запрос блокируется, пока в `result.json` не появится ответ с тем же `run_id`; поздний ответ автоматически попадает в историю при следующем запуске. После запуска Editor watcher подхватывает незавершённые запросы чтения и dry-run. Команды изменения с неизвестным результатом автоматически не повторяются.

Если ответа нет и требуется вручную отменить запрос: закрой Editor и все CLI-запуски этого проекта, сохрани копии `actions.json`, `harness.pending.json` и `result.json`, проверь изменённые объекты. Перемести `harness.pending.json` из `Content/Python` и замени `actions.json` на `{"format_version":"1.0","dry_run":true,"commands":[]}` перед запуском Editor. Это прекращает ожидание запроса; выполненные изменения не откатываются.

`run.ps1` проверяет готовность через `capabilities` сразу после запуска Editor. Фиксированная пауза больше не нужна. Для промптов доступны `-ReadOnly`, `-Json`, `-VerifyJson PATH` и `-MaxCorrections 0|1|2`.

В пользовательском проекте harness использует:

```text
Content/Python/actions.json       входной JSON batch
Content/Python/result.json        результат и audit
Content/Python/execute_actions.py executor
Content/Python/init_unreal.py     watcher/startup
Content/Python/action_contract.py общий контракт
Content/Python/backend_registry.py backend registry
Saved/HarnessAudit/               история запросов и ответов
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

Для обновления только Python runtime с резервными копиями:

```powershell
py scripts\install.py --project "D:\Projects\YourProject\YourProject.uproject" --runtime-only
py scripts\validate_install.py "D:\Projects\YourProject"
```

Этот режим обновляет четыре Python-файла. `actions.json`, настройки проекта, skill и C++ plugin сохраняются. Повторное обновление одинаковых файлов не создаёт новых копий. Validator проверяет актуальность executor, watcher, backend registry и общего контракта. Перезапусти Editor после обновления watcher.

Полное обновление, включая plugin и skill:

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

## Сохранённые задачи и следующие промпты

CLI выводит `Task ID` и сохраняет checkpoint в `Saved/HarnessTasks/<id>.json`. Просмотр работает без Editor и модели:

```powershell
py -m unreal_harness tasks --project "C:\Users\grishka\Documents\Unreal Projects\testharness\testharness.uproject"
py -m unreal_harness tasks --project "C:\Users\grishka\Documents\Unreal Projects\testharness\testharness.uproject" --task-id TASK_ID
```

Продолжить остановленную задачу, например после исчерпания бюджета:

```powershell
.\run.ps1 -Project "C:\Users\grishka\Documents\Unreal Projects\testharness\testharness.uproject" `
  -ResumeTask TASK_ID -Model YOUR_MODEL -Endpoint "http://127.0.0.1:1234/v1" -NoEditor -NoInstall
```

Новый запрос с контекстом предыдущей задачи:

```powershell
.\run.ps1 -Project "C:\Users\grishka\Documents\Unreal Projects\testharness\testharness.uproject" `
  -Prompt "Сделай этот материал темнее" -Session TASK_ID `
  -Model YOUR_MODEL -Endpoint "http://127.0.0.1:1234/v1" -NoEditor -NoInstall
```

Прямой CLI: `py -m unreal_harness resume TASK_ID --project PROJECT --model YOUR_MODEL`; для нового запроса — `py -m unreal_harness ask "Сделай этот материал темнее" --session TASK_ID --project PROJECT --model YOUR_MODEL`. При необходимости добавь `--endpoint`.

`resume` сохраняет исходный запрос, критерии успеха, режим read-only и явную проверку. Каждая попытка получает новый ID, ссылку на родителя и новый бюджет; оригинальная трасса остаётся доступной. Перед действиями агент читает актуальное состояние. Идентичные изменения из предыдущих попыток блокируются. Завершённую задачу продолжать нельзя: используй новый `ask --session`.

Если запрос к Unreal остался без подтверждения, `resume` и `ask --session` ищут ответ с тем же `run_id` в `result.json` и архиве обменов. Без подтверждённого результата продолжение блокируется, даже если pending-файл был удалён вручную. Команды не переотправляются. После позднего ответа агент сверяет текущее состояние. Общий rollback и автоматический повтор незавершённых изменений не реализованы.

`--session` использует прошлый запрос, критерии и наблюдения как контекст выбора целей. Для нового запроса формируются новые критерии, а цели повторно исследуются. Это явная связь задач; Для ввода последовательных промптов в одной консоли теперь доступен `chat` (см. ниже).

## Хуки жизненного цикла

Для `ask` и `resume` подключаются локальные обработчики через `--hooks CONFIG.json`. В PowerShell — `-Hooks CONFIG.json`:

```powershell
.\run.ps1 -Project "C:\Users\grishka\Documents\Unreal Projects\testharness\testharness.uproject" `
  -Prompt "Создай синий материал в /Game/Materials и сохрани" `
  -Model YOUR_MODEL -Endpoint "http://127.0.0.1:1234/v1" `
  -Hooks .\examples\hooks.json -NoEditor -NoInstall
```

Пример конфигурации — [examples/hooks.json](examples/hooks.json), обработчиков — [examples/hooks.py](examples/hooks.py). Пример запрещает общие вызовы `object.call`, `backend.operation`, `recipe.execute` и пишет итоги локально в `Saved/HarnessHooks/events.jsonl`.

| Событие | Когда вызывается | Поведение при ошибке |
| --- | --- | --- |
| `before_action` | Перед отправкой каждого пакета, включая dry-run и проверки | Запрет или ошибка останавливают задачу до отправки |
| `after_action` | После подтверждённого ответа Unreal | Задача останавливается; ответ и сделанные изменения сохраняются |
| `on_error` | При известной ошибке пакета (`scope: batch`) и при неуспешном итоге (`scope: task`) | Ошибка обработчика записывается, исходная причина сохраняется |
| `on_task_complete` | После успешного или неуспешного завершения попытки | Ошибка делает успешный итог неуспешным, сохраняя результат проверки |

Хук вызывается один раз на пакет, а не отдельно для каждой команды. События выполняются последовательно в порядке конфигурации. Фильтр `actions` использует glob-шаблоны (например, `material.*`); `mutating_only: true` отбирает команды изменения. Если указаны оба фильтра, им должна соответствовать одна и та же команда. Для терминальных событий фильтры не поддерживаются.

Обработчик — локальная программа: `command` задаётся массивом аргументов, без shell-интерполяции. Рабочая папка — папка конфигурации. На stdin приходит UTF-8 JSON с `event`, `task_id`, исходным запросом, проектом, критериями и данными события (`document`, `phase`, `result` либо `report`). На stdout `before_action` обязан вернуть `{"allow": true}` или `{"allow": false, "reason": "Причина"}`; остальные обработчики могут ничего не выводить или вернуть JSON. Сообщения диагностики направляй в stderr.

Ненулевой exit code, неверный ответ и таймаут считаются ошибкой. `timeout` по умолчанию 10 секунд, максимум 60; ожидание ограничивается оставшимся бюджетом задачи. При исчерпании бюджета последующие обработчики не запускаются, ошибка записывается. stdout ограничен 64 KiB. Таймаут завершает непосредственно запущенный процесс; обработчики не должны оставлять фоновые процессы.

Настройки хуков сохраняются в checkpoint и восстанавливаются при `resume`. Явный `--hooks` выбирает новую конфигурацию для этой попытки; пустой `{"version": 1, "hooks": []}` явно отключает хуки. Новый `ask --session` подключает хуки через собственный `--hooks`. Старые checkpoint без настроек работают без хуков.

Обработчики запускаются только из выбранной пользователем конфигурации или её сохранённых настроек при `resume`; модель не может подключать их через план. Это доверенные локальные программы с правами пользователя, а не sandbox. Их ответы не меняют команды, критерии или результаты Unreal. Хуки не обходят контракт, dry-run и проверки. Результаты каждого запуска записываются как `hook_result` в трассу задачи. Режим `--single-pass` эти хуки не поддерживает.

## Проверка готовности

Все автоматические проверки запускаются одной командой:

```powershell
py scripts/check_release.py --project "D:\Projects\YourProject\YourProject.uproject"
```

Она проверяет тесты, актуальность action-каталога, структуру eval-набора, установленный runtime и офлайн-диагностику. Отчёт — `Saved/HarnessReadiness/host.json` в репозитории. С `--build` дополнительно собираются wheel и sdist, выполняются проверки из распакованного исходного архива и проверяется установленный wheel в чистом окружении вне checkout (`pip install build`); `--lint` проверяет неопределённые имена (`pip install ruff`). GitHub Actions выполняет тесты, статическую проверку, сборку с проверкой дистрибутива и проверку PowerShell на Windows/Linux с Python 3.9/3.14. Локальный прогон не доказывает успешность удалённых CI-запусков.

Для уже открытого тестового Editor:

```powershell
py scripts/check_release.py --project "D:\Projects\YourProject\YourProject.uproject" --native
```

Этот режим использует заранее заданные шаги, настоящий executor и изолированную папку `/Game/CodexHarnessSmoke/Acceptance_<id>`. Проверяет материал, компоненты Blueprint, граф, компиляцию, сохранение выбранных ассетов, остановку по бюджету, продолжение и read-only запрос с контекстом. Не запускает Editor, LM Studio или другую модель. Тестовые ассеты остаются для проверки; отчёт находится в `Saved/HarnessReadiness` Unreal-проекта.

Для проверки сохранения после нового процесса Unreal закрой Editor тестового проекта и используй `scripts/run_ue_smoke_test.py --project PROJECT --material --reopen`. Проверяются значения параметров Material Instance и наличие материалов на компонентах Blueprint после открытия. Вариант без `--material` проверяет графы, компиляцию и акторов в тестовом commandlet; уровень не сохраняется. Скрипт временно использует mailbox и предназначен для отдельного тестового проекта без параллельных клиентов.

`unreal-harness` — консольная команда после установки wheel, эквивалентная `py -m unreal_harness`. Для установки runtime и сборки C++-плагина нужен checkout этого репозитория и инструменты Unreal; wheel предоставляет клиент CLI.

[Статус готовности и ограничения](docs/RELEASE_READINESS.md) отделяет проверки кода и реального Unreal от качества конкретной модели.

## Интерактивный CLI

```powershell
.\run.ps1 -Project "D:\Projects\YourProject\YourProject.uproject" `
  -Chat -NoEditor -NoInstall -Model YOUR_MODEL -Endpoint "http://127.0.0.1:1234/v1"
```

Или `py -m unreal_harness chat --project PROJECT --model YOUR_MODEL --endpoint ENDPOINT`.

В приглашении `harness>` вводи обычные промпты. После каждого запуска контекст автоматически связывается с предыдущей задачей; результаты и ID выводятся в консоль. Поддерживаются:

- `/new` — следующий промпт без контекста предыдущего;
- `/tasks` — сохранённые задачи;
- `/resume TASK_ID` — продолжение остановленной задачи;
- `/exit` — выход; EOF и Ctrl+C в приглашении также закрывают чат.

`--read-only` запрещает изменения для всех новых задач и продолжение задач, разрешающих изменения. `--session TASK_ID` задаёт начальный контекст; `--hooks` подключает обработчики. Каждый промпт получает собственный бюджет шагов, команд и времени. `--single-pass` и `--json` для интерактивного режима не поддерживаются. Перед каждой задачей повторяется проверка runtime и pending-файла. Прерывание во время задачи оставляет checkpoint для последующего разбора; оно не означает отмену уже отправленной команды Unreal.

`chat` не запускает сервер модели: он подключается к уже доступному endpoint.

Проверка восстановления Blueprint: `py scripts/run_ue_smoke_test.py --project PROJECT --recovery --reopen`. Ошибка компиляции и исправление разделены на два пакета. Между ними граф инспектируется, а узел выбирается по фактическому GUID; сохранение в неуспешном пакете должно быть заблокировано.
