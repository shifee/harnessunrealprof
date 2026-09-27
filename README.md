# Unreal Codex Harness

A portable, safety-focused JSON automation layer and project-local Codex Skill for Unreal Engine 5.8. It watches `Content/Python/actions.json`, validates the whole batch before mutation, executes supported editor operations serially, and atomically writes a structured `result.json` audit record.

This project targets Unreal Engine **5.8**. UE 5.7 is no longer supported.

## Design

- Deterministic JSON batches with dependencies, dry-run plans, bounded output, and stable result envelopes.
- Declarative `$repeat`, `$mirror`, and `$grid` recipe expansion with `fail`, `reuse`, and `update` conflict modes.
- Editor Undo transactions for mutating actions.
- Inspection-first asset, Blueprint, graph, material, and level workflows.
- A small C++ graph bridge for K2 functionality that Unreal Python does not expose reliably.
- No arbitrary Python, shell execution, asset deletion, or silent Blueprint replacement.
- Optional coexistence with UE 5.8's built-in experimental Unreal MCP server.

The design review behind v0.1 is documented in [docs/IMPLEMENTATION_RESEARCH.md](docs/IMPLEMENTATION_RESEARCH.md).

The accepted long-term structure is a universal reflection/asset/graph core with capability discovery, declarative recipes, and backend adapters for specialized Unreal subsystems. See [target architecture](docs/TARGET_ARCHITECTURE.md) and the staged [technical roadmap](docs/ROADMAP.md).

## Requirements

- Unreal Engine 5.8.
- An existing project with one `.uproject` in its root.
- Unreal plugins `PythonScriptPlugin`, `EditorScriptingUtilities`, and the installed `UnrealCodexGraph` project plugin.
- Python 3.9+ for the installer and validator; they use only the standard library.
- A UE 5.8-supported C++ toolchain to build the graph bridge (Visual Studio 2022 on Windows).
- Codex opened at the Unreal project root for project-local skill discovery.

The optional native MCP integration uses the UE 5.8 `ModelContextProtocol` and `AllToolsets` plugins. Unreal MCP is experimental and listens on loopback without authentication by default; do not expose it to a network.

## Install

```powershell
git clone https://github.com/shifee/unreal_harness.git
cd unreal_harness
py scripts/install.py --project "C:/Path/Project/Project.uproject"
```

Project discovery and safe update modes:

```powershell
py scripts/install.py
py scripts/install.py --project-dir "C:/Path/Project"
py scripts/install.py --dry-run --project "C:/Path/Project/Project.uproject"
py scripts/install.py --force --project "C:/Path/Project/Project.uproject"
```

Without an explicit path, the installer checks the current directory, its parents, and a depth-limited subtree. It never scans a whole drive. Existing files are skipped; `--force` creates timestamped sibling backups before replacement.

To let the installer enable the required plugins (with a `.uproject` backup):

```powershell
py scripts/install.py --project "C:/Path/Project/Project.uproject" --enable-plugins
```

To additionally enable UE 5.8's native MCP server and default toolsets:

```powershell
py scripts/install.py --project "C:/Path/Project/Project.uproject" --enable-native-mcp
```

You can also enable plugins manually in **Edit → Plugins**. Restart Unreal after changing plugins.

## One-command Windows launcher

В репозитории есть `run.ps1`. Он устанавливает harness в указанный проект, валидирует установку, запускает Unreal Editor и выполняет read-only `capabilities`:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.run.ps1 -Project "D:\Projects\MyShooter\MyShooter.uproject"
```

Путь к Editor можно передать явно или задать через `UNREAL_EDITOR`:

```powershell
.\run.ps1 `
  -Project "D:\Projects\MyShooter\MyShooter.uproject" `
  -EditorPath "C:\Program Files\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor.exe"
```

После проверки запуска можно сразу передать prompt. Для Ollama/vLLM/hosted API endpoint и model передаются в CLI:

```powershell
$env:UNREAL_HARNESS_LLM_ENDPOINT = "http://127.0.0.1:11434/v1"
$env:UNREAL_HARNESS_LLM_MODEL = "qwen3:14b"

.\run.ps1 `
  -Project "D:\Projects\MyShooter\MyShooter.uproject" `
  -Prompt "Создай базовый Blueprint BP_TestActor в /Game/Test и сохрани его" `
  -Model $env:UNREAL_HARNESS_LLM_MODEL `
  -Endpoint $env:UNREAL_HARNESS_LLM_ENDPOINT `
  -Timeout 120
```

Полезные флаги: `-NoInstall` не повторяет установку, `-NoEditor` не запускает второй Editor, `-SkipValidation` пропускает проверку файлов, `-EnableNativeMcp` включает экспериментальный native MCP. Первый запуск лучше делать без этих флагов. Launcher не может автоматически установить Unreal Engine или модель: это внешние зависимости, требующие отдельной установки и выбора версии/веса.

## First run

1. Open the project in UE 5.8 and allow the `UnrealCodexGraph` Editor plugin to build if prompted.
2. In **Window → Developer Tools → Output Log**, confirm:

   ```text
   [AI WATCHER] Watcher started
   ```

3. Open the project root in Codex and try:

   ```text
   Use $unreal-game-builder to inspect the current level, place three instances of an existing Blueprint actor in a row, and save the level.
   ```

The watcher treats the existing `actions.json` as already seen at startup. A client must write a new complete document to trigger execution.

## CLI

Для фиксированных операций модель не нужна. CLI формирует безопасный JSON-документ и отправляет его в открытый Unreal Editor:

```bash
python -m unreal_harness capabilities --project /path/to/Game.uproject
python -m unreal_harness inspect-level --project /path/to/Game.uproject --limit 100
python -m unreal_harness list-assets --project /path/to/Game.uproject --path /Game/AI --recursive
python -m unreal_harness spawn-actor \
  --project /path/to/Game.uproject \
  --class /Game/AI/BP_Enemy.BP_Enemy_C \
  --location '[100, 200, 0]' \
  --actor-label Enemy_01 \
  --save
```

Доступны команды `capabilities`, `inspect-level`, `list-assets`, `spawn-actor`, `create-blueprint`, `create-material`, `set-property` и `run-json`. CLI не редактирует `.uasset`/`.umap` напрямую: все операции проходят через `actions.json`, executor и audit `result.json`.

Для естественного языка есть команда `ask`. Она использует модель только как planner: модель возвращает declarative JSON-план, после чего harness сам выполняет `capabilities -> dry-run -> execute`. Модель не получает shell/Python-доступ и не вызывает Unreal напрямую.

```bash
export UNREAL_HARNESS_LLM_MODEL=qwen2.5:14b
python -m unreal_harness ask \
  --project /path/to/Game.uproject \
  "Создай Blueprint BP_Enemy в /Game/AI и сохрани его"
```

По умолчанию `ask` подключается к OpenAI-compatible endpoint `http://127.0.0.1:11434/v1`, поэтому подходит для Ollama. Для vLLM или hosted API укажи endpoint и ключ:

```bash
python -m unreal_harness ask \
  --project /path/to/Game.uproject \
  --endpoint https://api.example.com/v1 \
  --model provider/model-name \
  --api-key "$PROVIDER_API_KEY" \
  "Проинспектируй уровень и перечисли найденные акторы"
```

Практичный выбор: Ollama для локальной работы и приватности, vLLM для собственного GPU-сервера и throughput, hosted OpenAI-compatible API для лучшего качества планирования. Для простых операций модель не нужна вообще.

## JSON contract

```json
{
  "format_version": "1.0",
  "dry_run": false,
  "commands": [
    {
      "id": "inspect_level",
      "action": "level.inspect",
      "arguments": {"limit": 100}
    }
  ]
}
```

The executor rejects duplicate IDs, unknown actions, forward/missing dependencies, malformed arguments, and batches larger than 200 commands before any mutation occurs. `dry_run: true` returns the execution plan without running commands.

For repeated constructions, a top-level `recipes` array can generate ordinary commands and nested operation lists with `$repeat`, `$mirror`, and `$grid`. Recipe conflict modes make reruns explicit: `fail` rejects an existing destination, `reuse` keeps it, and `update` applies supported changes without creating duplicates. See `examples/patterned-castle.json` and the installed command reference for the exact schema.

Supported families:

- discovery: `system.capabilities`, `system.describe_actions`
- content: `content.create_folder`, `content.list`
- assets: `asset.inspect`, `asset.search`, `asset.describe_types`, `asset.duplicate`, `asset.save`
- materials: `material.create`, `material.inspect`, `material_instance.create`, `material_instance.set_parameters`
- Blueprints: `blueprint.create`, `blueprint.inspect`, `blueprint.compile`, `blueprint.edit`
- graphs: inspect, add universal node kinds, connect, disconnect, remove nodes, set literal pin values
- levels: inspect actors, spawn actors, set actor transforms and reflected properties
- persistence: `project.save`

Graph node kinds in v0.1 are `function_call`, `event`, `branch`, `sequence`, `reroute`, `self`, `variable_get`, `variable_set`, and `dynamic_cast`. Query `system.capabilities` rather than assuming the list.

`blueprint.compile` now checks Unreal's actual `BlueprintStatus`: a graph rejected by the compiler returns `blueprint_compile_failed` instead of a false successful result.

## Demonstrated capabilities

The checked examples and UE 5.8 validation runs currently demonstrate:

- `examples/numeric-blueprint.json`: create an Actor Blueprint, build `BeginPlay → Print String`, calculate `2 + 3`, connect exec/data pins, compile, save, and spawn it.
- `examples/patterned-castle.json`: expand `$repeat`, `$mirror`, and `$grid`, create/update materials and components, and rerun without actor or component duplicates.
- Blueprint showcase validation: a visible five-mesh Actor whose Event Graph uses `event`, function calls, two sequences, arithmetic, reroute, comparison, branch, self reference, dynamic cast, component variable get, automatic `int → string` conversion, and `SetVisibility`. The validated graph contains 21 nodes and 20 links and compiles as `BS_UP_TO_DATE`.

In practical terms, the harness can inspect content and levels; create simple materials, Actor Blueprints, components, transforms, and level instances; assemble a useful subset of K2 graphs by reflected class/function names; set literal pins; connect compatible pins through Unreal's schema; compile and save assets; and compose repeated work through declarative recipes with explicit conflict handling.

Current boundaries are equally important: there is no generic property codec, factory-driven `asset.create`, contextual Blueprint node catalog, complete recipe runtime, or JSON-controlled PIE session. `variable_set` now validates that the target property exists, is Blueprint-visible, and is not read-only, edit-const, or transient. Automatic metadata validation beyond graph variables remains scheduled for roadmap stage 1.

See the installed `references/actions-schema.md` for exact shapes. `examples/numeric-blueprint.json` remains a complete graph example.

## UE 5.8 native MCP

The harness and native Unreal MCP serve different purposes:

- the JSON harness is the deterministic batch, dependency, Undo, and audit layer;
- native MCP is useful for interactive tool discovery and engine-provided toolsets.

To configure native MCP, enable **Unreal MCP** and **All Toolsets**, enable Auto Start under Editor Preferences, then use the editor console:

```text
ModelContextProtocol.GenerateClientConfig Codex
```

The default endpoint is `http://127.0.0.1:8000/mcp`. Unreal serializes tool execution on the game thread, so clients must not issue overlapping calls.

## Validation

```powershell
py scripts/validate_install.py "C:/Path/Project"
```

Validation checks required files, JSON, Python syntax, skill frontmatter, action-reference drift, forbidden source-machine paths, and required plugins. It does not start Unreal Editor or change the project.

For an end-to-end test in a dedicated UE 5.8 project, first install the current harness and close the interactive editor instance for that project, then run:

```powershell
py scripts/run_ue_smoke_test.py --project "C:/Path/TestProject/TestProject.uproject"
```

The smoke test checks the capability snapshot, creates a uniquely named material and Actor Blueprint, builds and compiles a BeginPlay → Print String graph, spawns the actor in the commandlet world, and verifies the mutation audit. Test assets are intentionally retained under `/Game/CodexHarnessSmoke/<run>` for inspection; the original `actions.json` and `result.json` are restored.

Для локальной проверки можно создать чистый smoke-проект из шаблона:

```bash
python scripts/create_smoke_project.py /tmp/unreal-codex-smoke
python scripts/validate_install.py /tmp/unreal-codex-smoke
python scripts/run_ue_smoke_test.py \
  --project /tmp/unreal-codex-smoke/UnrealCodexHarnessSmoke.uproject \
  --editor-cmd /path/to/UE_5.8/Engine/Binaries/Linux/UnrealEditor-Cmd
```

Скрипт создаёт disposable-проект и не изменяет исходные файлы репозитория. Последняя команда требует реально установленный Unreal Engine 5.8; наличие `.uproject` само по себе не заменяет сборку и запуск Editor.

## Install only the Codex Skill

```text
Use $skill-installer to install from https://github.com/shifee/unreal_harness/tree/main/.agents/skills/unreal-game-builder
```

This installs only the instructions, not the Unreal Python harness or graph plugin. Use `scripts/install.py` for the complete integration.

## Update

```powershell
git pull
py scripts/install.py --project "C:/Path/Project/Project.uproject" --force
py scripts/validate_install.py "C:/Path/Project"
```

Review timestamped backups before removing them.

## Troubleshooting

- No watcher message: confirm the required plugins are enabled, restart Unreal, and inspect the Output Log.
- `result.json` does not change: make sure Unreal Editor is open and `actions.json` is valid JSON.
- Graph bridge unavailable: rebuild the project plugin with UE 5.8, restart, then run `system.capabilities`.
- Multiple projects found: pass the exact `.uproject` with `--project`.
- Native MCP unavailable: check `LogModelContextProtocol`, confirm Auto Start, and keep the endpoint on loopback.

## Safe removal

Close Unreal Editor and review local modifications before removing:

```text
Content/Python/execute_actions.py
Content/Python/init_unreal.py
Content/Python/actions.json
.agents/skills/unreal-game-builder/
Plugins/UnrealCodexGraph/
```

`Content/Python/result.json` is runtime output. Do not remove surrounding directories if they contain unrelated files. Plugin entries in `.uproject` are not removed automatically.

## License

MIT. See [LICENSE](LICENSE).
