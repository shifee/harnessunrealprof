# Agent Execution Interface

The initial agent integration is transport-neutral and uses `unreal_harness.FileExecutionTransport`.

```python
from unreal_harness import FileExecutionTransport

transport = FileExecutionTransport("/path/to/project")
result = transport.submit({
    "format_version": "1.0",
    "dry_run": True,
    "commands": [{
        "id": "capabilities",
        "action": "system.capabilities",
        "arguments": {},
    }],
})
```

`submit` writes a complete document atomically to `Content/Python/actions.json`, waits for a new `result.json` with the same generated `run_id`, and returns the parsed result envelope. It never interprets Unreal actions or executes code itself.

## Agent loop contract

A future model loop MUST follow this order:

1. Discover exactly one `.uproject` and confirm the target project root.
2. Query `system.capabilities` and `system.describe_actions`.
3. Inspect relevant state before proposing a mutation.
4. Build a complete batch with unique IDs and dependencies.
5. Submit the same batch as `dry_run: true` for broad or ambiguous mutations.
6. Execute only supported actions and explicit persistence.
7. Re-inspect the postcondition and report `changed_objects`, warnings, and stable error codes.

The loop MUST NOT bypass the executor with direct `.uasset`/`.umap` editing, arbitrary Python, shell commands, or concurrent submissions.

## Current transport limitation

The file watcher requires an open Unreal Editor with the installed Python harness. A missing or stale result times out with `UnrealExecutionError`; this is an infrastructure failure, not a successful empty result. Native UE MCP can be added later behind the same interface, but calls remain serial and the JSON result envelope remains the audit contract.
