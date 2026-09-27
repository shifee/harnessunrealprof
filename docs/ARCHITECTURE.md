# Harness Architecture

```text
User task
   |
Agent layer: context + project instructions + tools + trace
   |
Execution interface: capabilities / inspect / dry-run / execute / verify
   |
JSON batch contract
   |
Unreal executor: validation + dependencies + transactions + audit
   |
Python Editor API + C++ graph bridge
   |
Unreal Editor 5.8
```

## Agent layer

The agent owns intent interpretation, not Unreal state. It must discover the project and capabilities, inspect before mutation, construct a complete batch, and verify the postcondition. Its initial tools are deliberately narrow: project discovery, bounded file reads, capability query, batch submission, result retrieval, and user clarification for ambiguous or risky requests.

Context is budgeted. Large Unreal observations are bounded at the executor and summarised before being fed back to the model. Every run has a trace containing the task, tool calls, command IDs, result envelope, and verification outcome.

## Execution interface

The agent talks to one transport-neutral interface:

- `capabilities()` — discover engine, harness, plugin, actions, and limits;
- `inspect(request)` — execute read-only actions and return bounded data;
- `plan(batch)` — validate and expand recipes without mutation;
- `execute(batch)` — run a validated batch serially;
- `verify(request)` — perform postcondition inspections.

The first implementation uses the existing file watcher (`actions.json` / `result.json`). A later transport MAY use UE 5.8 native MCP, but it must preserve the same command/result contract and serial execution rule.

## Unreal execution layer

The executor validates the entire document before invoking an action. It resolves dependencies and document-local graph aliases, runs mutating operations in editor transactions, records changed objects, and writes the result atomically. It does not expose arbitrary Python, shell, deletion, or silent replacement.

Python handles reflected editor operations and orchestration. The C++ plugin is limited to K2 graph operations that Unreal Python does not expose reliably. Bridge responses are JSON objects with stable success/error fields; localized editor display names are never identifiers.

## Extension policy

Prefer existing universal primitives over new top-level actions. Add a specialized backend only when the subsystem has its own graph/data model or public Editor API that cannot be represented by `object.*`, `asset.*`, or `graph.*`. Every extension declares capabilities, version/plugin requirements, bounded inspection, validation, mutation semantics, diagnostics, and a smoke scenario.

## Verification

Contract tests run without Unreal and prove document validation, dry-run, error codes, and deterministic expansion. UE smoke tests run against a dedicated UE 5.8 project and prove actual UObject/package/graph behaviour, compilation, saving, and post-restart inspection where applicable.
