# Priority Unreal Adapters

The v0.1 executor exposes universal asset and Blueprint graph actions first. Specialized adapters remain intentionally narrow:

- Asset lifecycle: `asset.search`, `asset.inspect`, `asset.describe_types`, `asset.duplicate`, `asset.save`.
- Blueprint recovery: `blueprint.graph.inspect`, `add_node`, `connect`, `disconnect`, `remove_node`, `set_pin_value`.
- Level operations: inspect, spawn, transform, property update.

All adapters use the same document validator, dry-run planner, serialized executor, transaction boundary, and `changed_objects` audit field. New domain support must reuse these boundaries rather than expose arbitrary Python or shell execution.

The internal backend registry exposes only declarative registered handlers:

- `backend.capabilities` lists registered backend metadata and allowed operations.
- `backend.describe` returns one backend descriptor.
- `backend.operation` invokes a registered `validate`, `inspect`, `mutate`, `diagnostics`, `capabilities`, or `describe` handler.

Unknown, disabled, conflicting, dependency-missing, and failed backends return stable `error_code` values. JSON cannot provide Python, shell, callables, or unrestricted Unreal invocation. The registry is not yet advertised as a complete external adapter SDK.

UE-specific API availability is reported as `api_unavailable`; the harness does not silently emulate unavailable editor operations.
