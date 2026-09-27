# Unreal AI Harness: Product Scope

## Goal

Provide a safe, auditable agent interface for Unreal Engine 5.8 editor work. A user describes an editor task; the harness inspects the project, plans a bounded batch, executes it through the Unreal JSON executor, verifies the result, and reports changed objects and diagnostics.

The product is two layers:

1. **Agent layer** — natural-language task handling, project instructions, context budget, tool selection, planning, retry policy, and trace output.
2. **Unreal execution layer** — validated JSON batches, inspection, mutations, Blueprint graph operations, transactions, explicit saving, and atomic result auditing.

The agent never receives arbitrary Python or shell execution inside Unreal. The execution layer remains authoritative for capabilities and validation.

## Initial vertical slice

The first usable slice supports these scenarios:

- inspect the current level and bounded content/asset metadata;
- place an existing Blueprint actor on the current level and set its transform;
- create an Actor Blueprint with a small Event Graph;
- compile, save, and re-inspect the generated Blueprint;
- return a stable success/error envelope and `changed_objects` audit.

Every mutation follows: inspect, validate, dry-run when useful, mutate inside an Undo transaction, explicitly save, then verify.

## Safety boundaries

- UE 5.8 is the supported engine version.
- All commands are validated before the first mutation.
- Mutations are serialised on the Unreal editor thread and expose stable error codes.
- Destructive deletion, silent replacement, arbitrary code execution, remote binding, and unrestricted process execution are out of scope for the core.
- Existing objects require explicit conflict behaviour: `fail`, `reuse`, or `update` where supported.
- Results are bounded and written atomically.

## Delivery stages

1. **Foundation** — establish the imported Unreal execution baseline, contract tests, scope, and architecture.
2. **Blueprint recovery** — add graph node removal, link disconnection, and safe reflected-property checks so an agent can repair an existing graph instead of rebuilding it.
3. **Agent layer** — define a narrow execution interface and implement a minimal loop that uses capabilities, inspection, dry-run, mutation, and verification.
4. **Universal core** — add typed reflection, generic asset lifecycle, and declarative recipes only where existing primitives cannot express the scenario.
5. **Expansion** — add priority subsystem adapters, benchmark tasks, compatibility reporting, and a stable version-one contract.

Each stage ends with an observable vertical scenario and contract evidence. Unreal Editor smoke tests are required for changes that cross the Python/C++ boundary; Python contract tests cover validation and dry-run behaviour without requiring the editor.

## Success criteria for version one

A clean UE 5.8 test project can be driven from a natural-language task through inspection, a reviewed/dry-run plan, deterministic mutation, compilation or subsystem validation, explicit persistence, and post-mutation inspection. The final report identifies the commands run, changed objects, warnings, and the first actionable failure when a batch cannot complete.
