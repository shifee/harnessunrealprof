# План развития и техническое задание

Этот план реализует [целевую архитектуру](TARGET_ARCHITECTURE.md). Каждый этап заканчивается работающим вертикальным срезом, тестами и обновлённой документацией. Переход к следующему этапу допускается только после выполнения всех критериев приёмки текущего.

## Статус реализации

Текущий статус: этапы 0–3 и поддерживаемая часть этапа 5 подтверждены контрактными/native smoke; этап 4 частично закрыт каталогом K2 subset; этапы 6–9 не закрыты как полноценные backend/benchmark stages. Unsupported boundaries описаны явно и не считаются acceptance.
Этап 0.5: [x] безопасный цикл исправления Blueprint-графа.
Этап 1: [x] Reflection и типизированный codec; transport и native Actor property acceptance подтверждены.
Этап 2: [x] универсальный lifecycle ассетов, включая native create/save/reopen и conflict safety.
Этап 3: [x] безопасный вызов allowlisted UFunction с typed arguments и native MapKey acceptance.
Этап 4: [ ] каталог K2 subset реализован; Enhanced Input Action Event, Macro creation, contextual Action Database и restart scenario остаются unsupported.
Этап 5: [ ] recipe validate/execute, expansion, references, conditions и rollback reporting покрыты contract tests; native late-failure transaction cancel и reopen acceptance не выполнены.
Этап 6: [ ] registry и declarative handlers покрыты contract tests; внешний adapter SDK, fixture/schema integration и automatic backend selection не закрыты.
Этап 7: [ ] native acceptance подтверждён для material и базового Blueprint/level subset; Enhanced Input и AI specialized adapters недоступны в текущем safe API.
Этап 8: [ ] Sequencer/Niagara/PCG/Control Rig/Animation Graph/World Partition adapters не экспонируются без подтверждённого safe adapter.
Этап 9: [ ] benchmark runner и трехзадачный reference manifest реализованы; native coverage, 100-task target и 80% threshold не заявляются без фактического benchmark.

Выполнено в этапе 0:

- добавлены стабильные `code`/`error_code` для ошибок;
- добавлен аудит `changed_objects` на уровне команды и всего запуска;
- зафиксированы JSON fixtures всех 22 actions и snapshot capabilities;
- добавлен тест согласованности версии Python executor, C++ bridge, `.uplugin`, package и snapshot;
- добавлен воспроизводимый `run_ue_smoke_test.py`;
- проведён end-to-end тест на UE 5.8 в проекте `Ai_game_test_2`: создание материала, Actor Blueprint, компонента, графа `BeginPlay → Print String`, компиляция, spawn и проверка аудита;
- контрактные тесты, Skill validation, installer validation и сборка C++-плагина пройдены.

Досрочно реализована часть этапа 5:

- декларативная развёртка `$repeat`, `$mirror` и `$grid` до общей валидации batch;
- режимы конфликтов `fail`, `reuse`, `update` для папок, материалов, Blueprint, компонентов и level actors;
- `dry_run` показывает полностью развёрнутые команды и сохраняет `recipe_id` в аудите;
- повторный запуск рецепта может обновлять поддерживаемые объекты без дубликатов.

Этап 5 остаётся незавершённым: ещё нужны параметризованные схемы рецептов, ссылки на результаты предыдущих команд, условия, отдельные `recipe.validate`/`recipe.execute` и библиотека прикладных рецептов.

Выполненная проверочная задача Blueprint-контура:

- [x] В UE 5.8 создан и сохранён `BP_HarnessShowcaseValidated` с пятью mesh-компонентами и четырьмя материалами.
- [x] В одном Event Graph проверены `event`, `function_call`, `sequence`, `branch`, `reroute`, `self`, `dynamic_cast` и `variable_get`.
- [x] Проверены установка literal pin values, соединение exec/data pins, автоматическая конверсия `int → string`, вызов функции компонента и компиляция.
- [x] Граф из 21 ноды и 20 связей получил статус `BS_UP_TO_DATE`, Blueprint сохранён, один экземпляр размещён на уровне.
- [x] Устранён ложный успех `blueprint.compile`: `BS_ERROR` теперь возвращает `blueprint_compile_failed`.
- [x] Добавлен контрактный регрессионный тест; полный набор из 12 тестов проходит.

Проверка также зафиксировала границы: `variable_set` нельзя считать универсально безопасным без проверки metadata свойства, граф нельзя исправить удалением ноды или связи, а запуск PIE пока не входит в JSON-контракт.

## Общие требования ко всем этапам

- Поддерживаемая версия движка: Unreal Engine 5.8.
- Новые мутации участвуют в Editor Undo transaction.
- Любая команда полностью валидируется до изменения проекта.
- `dry_run` не изменяет UObject, package, уровень или файлы проекта.
- Сохранение выполняется только явно.
- Ошибка одной команды останавливает зависимые команды и отражается в `result.json`.
- Новые actions появляются в `system.capabilities`, `system.describe_actions` и справочнике Skill.
- Для каждого action нужны позитивный тест, тест неверного типа, тест отсутствующего объекта и тест `dry_run`.
- Нельзя добавлять произвольное выполнение Python, shell или неограниченный вызов `ProcessEvent`.

## Этап 0 — стабилизация основы 0.1.x

**Статус: выполнено.**

### Назначение

Зафиксировать воспроизводимую базу, относительно которой будет измеряться развитие универсального ядра.

### Добавляемые функции

- Машиночитаемые коды ошибок и поле `changed_objects`.
- Набор эталонных JSON fixtures для существующих 22 actions.
- Автоматический запуск commandlet smoke test на тестовом UE-проекте.
- Снимок `system.capabilities` для обнаружения несовместимых изменений.
- Исправление дрейфа номеров версий в документации и runtime.

### Критерии приёмки

- Все существующие команды имеют контрактные тесты.
- Повторный запуск тестов на чистом проекте даёт одинаковую структуру результата за исключением времени и `run_id`.
- Любая ошибка содержит стабильный `code`, команду и читаемое сообщение.
- CI или локальный единый сценарий запускает Python-тесты, сборку плагина и UE commandlet smoke test.
- Рабочий пример создания Blueprint, материала и актора завершается `success: true`.

## Этап 0.5 — безопасный цикл исправления Blueprint-графа, версия 0.1.1

### Назначение

Сделать итеративное построение графов восстанавливаемым: ошибочную ноду или связь можно исправить в том же Blueprint, а результат компиляции всегда соответствует статусу Unreal.

### Добавляемые функции

- `blueprint.graph.remove_node` по GUID или document-local alias.
- `blueprint.graph.disconnect` для одной связи и безопасный режим отключения всех связей указанного пина.
- Предварительная проверка существования, доступности на чтение/запись и типа свойства для `variable_get`/`variable_set`.
- Структурированный compile result: `status`, warnings/errors и стабильный `blueprint_compile_failed`.
**Статус:** этап 0.5 закрыт на UE 5.8.3. Recovery smoke получил `blueprint_compile_failed` для намеренно ошибочного `dynamic_cast`, удалил ошибочную ноду в том же Blueprint, затем получил `BS_UP_TO_DATE`; `remove_node` использует Undo transaction и отражается в `changed_objects`. Обычный smoke проверяет exec и data connections, compile/save и graph inspection. Флаг `--reopen` запускает второй `UnrealEditor-Cmd`, который повторно загружает сохранённый Blueprint, инспектирует граф и компилирует его.
Команды проверки: `python scripts/run_ue_smoke_test.py --project /tmp/unreal-codex-harness-smoke/UnrealCodexHarnessSmoke.uproject --editor-cmd /mnt/omarchy-data/UnrealEngine-5.8.3/Engine/Binaries/Linux/UnrealEditor-Cmd --reopen` и тот же вызов с `--recovery`; оба завершились `PASS` на UE `5.8.3-58210709`.

### Критерии приёмки

- Попытка создать `variable_set` для read-only свойства отклоняется до изменения графа.
- Намеренно ошибочный граф возвращает `blueprint_compile_failed`, а не успешный result.
- После удаления ошибочной ноды и/или связи тот же Blueprint компилируется как `BS_UP_TO_DATE`; новый ассет создавать не требуется.
- Удаление и disconnect участвуют в Undo transaction и отражаются в `changed_objects`.
- Smoke test создаёт граф с exec/data connections, компилирует, сохраняет, повторно открывает и инспектирует его без ручного вмешательства. Текущий smoke подтверждает exec connection, compile/save/inspect и негативное восстановление; отдельный ручной commandlet-прогон подтвердил reopen, но автоматизированный smoke с data connection и повторным процессом ещё нужен.

## Этап 1 — Reflection и типизированный codec, версия 0.2

### Назначение

Научить харнесс самостоятельно читать типы Unreal и преобразовывать значения между JSON и `FProperty`.

### Добавляемые функции

- `object.describe`.
- `object.inspect` с выбором свойств и ограничением глубины.
- `object.set` для scalar, enum, ссылок, struct, array, set и map.
- Типизированные ссылки `$type: object/class/soft_object/soft_class`.
- Схема ограничений: read-only, edit-const, transient, deprecated и instanced.

- Codec transport envelope covers заявленные typed reference/enum/struct формы и имеет контрактные round-trip/validation тесты.
- Неверный enum, класс ссылки и поле структуры отклоняются до мутации в fake-Unreal контрактных тестах.
- Read-only, EditConst, transient и deprecated metadata flags отклоняются до `modify()`.
 Реальный UE 5.8.3 Actor smoke `Run_20260927_reflection_acceptance_final` прочитал и изменил native `Tags` array через `object.inspect`/`object.set`; намеренный multi-property set с неизвестным `MissingProperty` вернул `property_not_found`, `changed_objects: []`, а независимый post-failure inspect подтвердил сохранение прежнего Tags. Smoke runner принял ожидаемый commandlet exit 1 только при свежем matching result envelope; профиль завершился с `Changed objects: 4`.
 UE Python 5.8 возвращает этот Array wrapper как JSON-массив в result envelope; capability fixture синхронизирован с native action/node catalog, включая backend, graph и object operations.

Текущий статус: этапы 0–1 завершены; базовый универсальный lifecycle и каталог этапа 2 подтверждены native UE 5.8.3.
## Этап 2 — универсальный lifecycle ассетов, версия 0.3

### Назначение

Создавать стандартные ассеты без отдельного action для каждого класса.

### Добавляемые функции

- `asset.search` с фильтром по пути, классу и имени.
- `asset.describe_types` с каталогом классов и фабрик.
- `asset.create` с автоматическим или явным выбором factory.
- `asset.duplicate` без перезаписи назначения.
- Native UE 5.8.3 Material acceptance `Run_20260927_material_v6` прошёл через safe JSON harness: material с тремя parameter expressions, scalar/vector instance mutation, StaticMeshComponent assignment, Blueprint compile/save и отдельный read-only reopen; reopen сообщил `changed_objects:[]`.
- `asset.save` для выбранных пакетов.

- Native UE 5.8.3 `asset.describe_types` completed without mutation and returned 9796 class entries and 288 exposed factory wrappers with bounded pagination metadata; reflected factory CDO metadata is not exposed uniformly, so entries remain discovery data unless a mapping is verified.
- Native UE 5.8.3 lifecycle smoke created and explicitly saved concrete `CurveFloat`, `Material`, `MaterialInstanceConstant`, `InputAction`, and `InputMappingContext`; the latter two used `/Script/InputEditor.InputAction_Factory` and `/Script/InputEditor.InputMappingContext_Factory` via reflected `unreal.new_object` factories.
- Native `asset.duplicate` created a `CurveFloat` copy and rejected a second duplicate into the same destination with `conflict`; `asset.save` reported all six assets saved.
- Native `asset.search` pagination after lifecycle probe returned registered copies through bounded pages with `total` and `has_more` metadata; prior two-page acceptance remains covered.
- A second UE 5.8.3 process reopened the disposable project and found all six saved assets through `asset.search`, confirming persistence across editor restart.
- Constraints factory/class paths and absence of silent replacement are checked before mutation; existing paths are not overwritten.
- `DataAsset` as generic class `/Script/Engine.DataAsset` in UE 5.8.3 is abstract and is not a valid concrete acceptance fixture; concrete `CurveFloat` is used instead.

## Этап 3 — безопасный вызов UFunction, версия 0.4

### Назначение

Заменить семейства однотипных actions контролируемым вызовом публичных Editor API.

### Добавляемые функции

- `object.describe_functions`.
- `object.call` с входными, выходными и return-параметрами — policy-driven subset; structured return/out catalog расширяется только вместе с native fixture.
- Политика разрешения по модулю, классу, функции и metadata.
- Реестр запретов для latent, network, console, file/process и destructive вызовов.
- Отчёт о затронутых объектах и пакетах.

### Статус критериев

- Preflight существования функции (через explicit allowlist), имён аргументов, typed UObject reference, struct shape и policy реализован и native-проверен.
- Value/struct/UObject input и safe `MapKey` Editor call native-проверены.
- `MapKey` return native-проверен: policy catalog явно содержит structured JSON fields `action` (typed UObject), `key` (`Key` struct), `triggers` и `modifiers` (arrays); native result подтвердил их фактическую форму, глубина сериализации ограничена.
- Запрещённый вызов native-проверен с `function_not_allowed` и без побочных эффектов.
- Persistence native-проверен во втором UE process через safe JSON `asset.inspect` и `object.inspect`.
- Transaction cancel при исключении покрыт contract regression.
- Stage 3 считается закрытым как policy-driven subset: поддержка generic enum, произвольных array и out-параметров не заявляется без отдельной allowlisted функции и native fixture; native mutating-throw rollback также не заявляется без безопасного UE API path.
- Stage 4 разрешён к началу: его acceptance не зависит от добавления неподтверждённых generic `object.call` codecs.

### Acceptance

Stage 3 принят для текущего policy-driven subset. Обязательная native функция `MapKey` прошла positive, denied-call, structured-return и persistence проверки; contract suite покрывает preflight, default deny, typed references, serialization и transaction cancellation. Generic enum/array/out и native mutating-throw rollback остаются явно исключёнными из версии 0.4.

### Native UE 5.8.3 evidence

- Positive commandlet `stage3-mapkey-positive` завершился с process exit code `0`, result `success: true`, и выполнил `object.describe_functions`, typed UObject + `Key` struct conversion, native `MapKey`, а затем `asset.save` для `InputAction` и `InputMappingContext`.
- Separate denied-call verification returned exact `error_code: function_not_allowed` and `changed_objects: []`; the denied call did not invoke mutation.
- Separate UE process reopened the saved `InputMappingContext` through `asset.inspect` and `object.describe`, confirming class `/Script/EnhancedInput.InputMappingContext` and `success: true` after restart.
- Safe JSON `object.inspect` of `DefaultKeyMappings` in that reopened process returned the persisted `IA_MapKey_Positive` mapping with `SpaceBar`; no direct asset or map file editing was used.
- Native `stage3-mapkey-return-catalog` через `object.describe_functions` вернул policy catalog с `returns`: `action/object`, `key/struct`, `triggers/array`, `modifiers/array`; commandlet завершился `Success - 0 error(s)`, `success: true`, `changed_objects: []`.


## Этап 4 — каталог Blueprint-нод, версия 0.5

### Назначение

Создавать K2-ноды из того же контекстного каталога, которым пользуется меню Blueprint Editor.

### Добавляемые функции

- `graph.describe`.
- `graph.search_nodes` через Blueprint Action Database/Node Spawners.
- `graph.describe_node` со стабильной `node_spec`.
- Универсальный `graph.add_node` по `node_spec`.
- Унифицированные `graph.connect`, `graph.set_pin_value`, `graph.inspect`.
- Поддержка contextual bindings: asset, class, variable, function, macro и event.

### Критерии приёмки

- Поиск учитывает тип Blueprint, выбранный graph и доступность ноды в контексте.
- Неоднозначный текстовый поиск не выбирает ноду автоматически, а возвращает варианты.
- Создаются и компилируются Function Call, operator, Make/Break Struct, Custom Event, Macro, variable access и Enhanced Input Action Event.
- Пины идентифицируются стабильными именами; отображаемый локализованный текст не используется как идентификатор.
- Тестовый граф `Input Action → Custom Event → Print String` компилируется без ошибок после перезапуска редактора.

### Native UE 5.8.3 evidence for the implemented subset

- Native catalog smoke completed with process exit code `0`, commandlet `Success - 0 error(s), 0 warning(s)`, result `success: true`, and no errors. `graph.describe`, `graph.search_nodes`, and `graph.describe_node` are read-only and returned `changed_objects: []`.
- Fresh-namespace mutation acceptance created `/Game/CodexHarnessStage4/Run_20260927_graph/BP_GraphAcceptance`, added native `Event BeginPlay`, `Branch`, and `PrintString` nodes, set `Condition=true` and `InString="Stage 4 graph acceptance"`, connected execution pins, inspected GUIDs/classes/pins/defaults/links, compiled, explicitly saved the asset, and saved the project.
- A separate UE 5.8.3 process reopened the saved Blueprint through the JSON harness. `asset.inspect`, `graph.describe`, and `blueprint.graph.inspect` succeeded; links and pin defaults persisted; repeat compilation returned `BS_UP_TO_DATE`; result `success: true`, `errors: []`, commandlet `Success - 0 error(s), 0 warning(s)`.
- Fresh-namespace native acceptance for `/Game/CodexHarnessStage4/Run_20260927_custom_event/BP_CustomEventAcceptance` created and saved `K2Node_CustomEvent` named `OnHarnessSignal`, connected it to `PrintString`, set `InString="Custom event acceptance"`, and compiled with `BS_UP_TO_DATE`. A separate UE 5.8.3 process reopened the saved asset; `asset.inspect`, `graph.describe`, and `blueprint.graph.inspect` confirmed the persisted node class, name, GUID, link, and default; read-only commands returned `changed_objects: []`.
- Fresh-namespace native acceptance for `/Game/CodexHarnessStage4/Run_20260927_struct_vector_v2/Run_20260927_struct_vector_v2` created native `K2Node_MakeStruct` and `K2Node_BreakStruct` nodes for `/Script/CoreUObject.Vector`, inspected stable `Vector`, `X`, `Y`, and `Z` pins, connected `Make Vector.Vector` to `Break Vector.Vector`, compiled with `BS_UP_TO_DATE`, explicitly saved, and completed with `success: true`, `errors: []`, and commandlet `Success - 0 error(s), 0 warning(s)`.
- A separate UE 5.8.3 read-only reopen process confirmed the saved Blueprint asset, native Make/Break classes, struct catalog search, and persisted link; every read-only command and the top-level result returned `changed_objects: []`. A separate reopen compile check also returned `BS_UP_TO_DATE`; UE marks the Blueprint dirty during compile even with `save:false`, so that command is not used as the read-only mutation invariant.
- Native operator acceptance for `/Game/CodexHarnessStage4/Run_20260927_operator_add_int.Run_20260927_operator_add_int` created `/Script/BlueprintGraph.K2Node_CommutativeAssociativeBinaryOperator` for the constrained `/Script/Engine.KismetMathLibrary.Add_IntInt` contract. Inspection confirmed stable `A`, `B`, and `ReturnValue` pins and title `int + int`; compile returned `BS_UP_TO_DATE` with no diagnostics, explicit save completed, and the mutation commandlet reported `Success - 0 error(s), 0 warning(s)`. A separate UE 5.8.3 read-only reopen confirmed the persisted native node, operator catalog result, and asset; all read-only commands and the top-level result returned `changed_objects: []`.
- The operator scope is intentionally constrained: arbitrary reflected functions and promotable operators are unsupported. UE 5.8.3 native compilation rejected the attempted Enhanced Input implementation because `K2Node_EnhancedInputAction.h` is not available to this plugin target; therefore Enhanced Input Action Event remains explicitly unsupported, with no capability/catalog claim or fake acceptance. Native evidence is also still missing for contextual Blueprint Action Database/Node Spawner search, Macro, and the requested Input Action -> Custom Event -> Print String restart scenario.

## Этап 5 — рецепты и ссылки на результаты, версия 0.6

### Назначение

Собирать пользовательские сценарии из универсальных примитивов без изменения кода харнесса.

### Добавляемые функции

- `recipe.validate` и `recipe.execute`.
- Параметры рецепта с JSON Schema.
- Ссылки на данные предыдущих результатов.
- Условия только над уже полученными данными, без исполняемого кода.
- Режимы конфликта: `fail`, `reuse`, `update`.
- Библиотека рецептов: Enhanced Input, Actor с компонентами, интерактивный объект и простой AI Controller.

### Критерии приёмки

- Рецепт проходит полную развёртку и валидацию до первой мутации.
- `dry_run` показывает развёрнутые команды и разрешённые ссылки.
- Повторный запуск идемпотентного рецепта не создаёт дубликаты.
- Ошибка шага содержит путь рецепта и исходный action.
- Новый рецепт можно добавить без изменения Python/C++ исполнителя.

## Этап 6 — Backend SDK, версия 0.7

### Назначение

Стандартизировать подключение систем с собственными графами и Editor API.

### Добавляемые функции

- Реестр backend-адаптеров.
- Интерфейсы capability, describe, validate, inspect, mutate и diagnostics.
- Автоматический выбор backend по типу объекта/графа.
- Версионные ограничения UE и зависимостей плагина.
- Контрактный test kit для адаптеров.

### Критерии приёмки

- Тестовый внешний адаптер регистрируется без изменения универсального executor.
- Конфликт двух адаптеров обнаруживается до исполнения.
- Отсутствующий Unreal plugin возвращает `backend_dependency_missing`.
- Результаты стандартного K2 backend и тестового backend имеют одинаковый envelope.
- Отключение адаптера не нарушает запуск базового харнесса.

## Этап 7 — первые прикладные адаптеры, версия 0.8

### Назначение

Проверить архитектуру на системах, отличающихся по устройству данных.

### Добавляемые функции

- **Enhanced Input:** создание actions/contexts, MapKey, modifiers/triggers и рецепт подключения к Controller/Pawn.
- **Material Graph:** поиск и создание expressions, соединение material pins, параметры и компиляция.
- **Behavior Tree/Blackboard:** keys, decorators, services, tasks и связи дерева.
- Общие команды остаются `asset.*`, `object.*`, `graph.*`; адаптеры добавляют только специфические спецификации.

### Критерии приёмки

- Enhanced Input: WASD, Jump и геймпад создаются и подключаются к тестовому PlayerController.
- Material: параметрический металлический материал создаётся, компилируется и назначается мешу.
- AI: Blackboard и Behavior Tree с Sequence, Move To и тестовым Task проходят встроенную валидацию.
- Каждый сценарий воспроизводится из чистого проекта одним JSON-рецептом.
- Не появляется дублирующих верхнеуровневых actions для отдельных нод или клавиш.

## Этап 8 — расширенные адаптеры, версия 0.9

### Назначение

Расширить покрытие на наиболее востребованные специализированные редакторы.

### Добавляемые функции

- Sequencer.
- Niagara.
- PCG.
- Control Rig и Animation Graph.
- World Partition и Data Layers в пределах публичного безопасного Editor API.
- SDK-пример интеграции стороннего Unreal-плагина.

### Критерии приёмки

- Для каждого backend существует минимум один end-to-end рецепт, inspection snapshot и негативный тест.
- Адаптер явно сообщает поддерживаемую версию UE и необходимые плагины.
- После сохранения и перезапуска созданные графы проходят собственную компиляцию/валидацию подсистемы.
- Недоступные private API документируются; UI-автоматизация не маскируется под детерминированный backend.

## Этап 9 — измеряемая стабильность, версия 1.0

### Назначение

Подтвердить покрытие реальными сценариями и стабилизировать публичный контракт.

### Добавляемые функции

- Набор минимум из 100 эталонных редакторских задач.
- Матрица покрытия по подсистемам и backend.
- Машиночитаемый compatibility report.
- Миграции формата команд и политика deprecation.
- Руководство по разработке адаптеров и рецептов.

### Критерии приёмки

- Не менее 80% утверждённого набора типовых задач выполняется без ручного вмешательства; 90% остаётся целевой, но не объявляется достигнутым без результатов теста.
- Все поддерживаемые сценарии проходят на чистом UE 5.8-проекте и после повторного открытия редактора.
- Публичный JSON contract имеет semantic version и проверенную миграцию с 0.1.
- В отчёте отдельно показаны полная поддержка, поддержка через адаптер, частичная поддержка и недоступные операции.
- Релиз не содержит критических ошибок сохранения, Undo или повреждения ассетов.

## Приоритет ближайшей реализации

Следующие обязательные шаги до заявления о полноценном harness:

1. Завершить native recipe late-failure acceptance: доказать cancel транзакции после вложенной мутации, отсутствие aggregate `changed_objects` и сохранение исходного состояния отдельным reopen процессом.
2. Довести Backend SDK до внешнего контракта: schema/fixture integration, adapter test kit, deterministic backend selection/conflict resolution, dependency/version negotiation и единый result envelope.
3. Создать native-safe Blueprint Action Database/contextual search и Macro workflows; сохранить Enhanced Input Action Event как unsupported, пока UE target не предоставляет подтверждённый API.
4. Закрыть specialized vertical slices отдельно: Enhanced Input, AI/Behavior Tree и material graph extensions; каждый требует mutation, compile/validation, save и отдельного reopen.
5. Реализовать benchmark на утверждённом наборе задач и формировать compatibility report только из фактических runs; затем закрыть migration/deprecation и operational/security hardening.

Level spawn/inspection и Blueprint compile/save/reopen подтверждены существующим UE 5.8.3 smoke; это не является специализированным level/runtime adapter acceptance.
Sequencer, Niagara, PCG, Control Rig, Animation Graph и World Partition остаются за границей поддержки до появления конкретного безопасного backend и native evidence.

## Ограничения и результаты проверки Unreal Engine

Native UE 5.8.3 smoke `Run_20260927_material_final` завершился успешно: 4 changed objects, отдельный read-only reopen passed. Smoke `Run_20260927_level_final` также завершился успешно: Blueprint/level сценарий, 4 changed objects и отдельный reopen passed. Эти проверки подтверждают соответствующие вертикальные сценарии, но не доказывают recipe rollback после поздней nested failure или специализированные AI/Input adapters.

`python -m py_compile` завершился успешно для изменённых Python-модулей. `python -m unittest discover -s tests -q` завершился: 56 tests, `OK`. Benchmark dry-run report теперь помечен mode `contract`, все три примера считаются `contract_only`, а `coverage_percent` отсутствует; native percentage формируется только из фактически выполненных native tasks.
