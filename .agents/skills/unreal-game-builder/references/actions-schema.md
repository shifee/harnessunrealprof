# Unreal Harness Command Reference (UE 5.8)

`Content/Python/execute_actions.py` and its `ACTION_HANDLERS` mapping are authoritative. Use `system.describe_actions` for runtime discovery. Public fields use `snake_case`; Unreal virtual paths begin with `/Game` unless an engine asset is explicitly accepted.

## Document contract

```json
{
  "format_version": "1.0",
  "dry_run": false,
  "commands": [
    {"id": "unique_id", "action": "system.capabilities", "depends_on": [], "arguments": {}}
  ]
}
```

The executor validates the complete document before making changes. IDs must be unique, dependencies must reference earlier commands, and the limit is 200 commands. With `dry_run: true`, the result contains a validated plan and performs no commands.

## Declarative recipes

Recipes support `parameter_schema` with `required` and primitive `types` (`string`, `number`, `boolean`, `array`, `object`). `recipe.validate` expands and validates a recipe without mutation; `recipe.execute` runs its expanded commands through the normal transaction and audit envelope. Command arguments may contain `{"$ref":"command_id.data.field"}` references to earlier command results. A command may declare `condition` with `ref` and one of `equals`, `not_equals`, or `exists`; false conditions produce a successful skipped command with no changed objects. References and conditions cannot execute code.


Top-level `recipes` expand into ordinary commands before dependency validation or mutation. A recipe has a unique `id`, one conflict mode, and a `commands` template. Exact `${name}` substitutions preserve the JSON value type; substitutions embedded in a larger string produce a string.

```json
{
  "format_version": "1.0",
  "dry_run": true,
  "commands": [],
  "recipes": [{
    "id": "layout",
    "conflict_mode": "reuse",
  "commands": [
      {"$repeat": {
        "items": [{"name":"A","x":0}, {"name":"B","x":200}],
        "template": {"id":"spawn_${name}","action":"level.spawn_actor","arguments":{"level":"current","class":"/Game/AI/BP_Block.BP_Block_C","actor_label":"Block ${name}","transform":{"location":["${x}",0,100]}}}
      }}
    ]
  }]
}
```

- `fail`: stop if the destination already exists.
- `reuse`: use the existing compatible object without replacing it.
- `update`: reuse the object and apply supported properties/operations. For `material.create`, this rebuilds the simple material expressions; for Blueprint components and level actors it updates later fields/transforms without creating duplicates.

`dry_run` returns the fully expanded command arguments. Recipes are bounded by the same 200-command limit. They do not add scripting, conditions, or arbitrary evaluation.

Every result uses a stable audit envelope:

```json
{
  "format_version": "1.0",
  "harness_version": "0.1.0",
  "run_id": "uuid",
  "success": true,
  "commands": [
    {
      "id": "unique_id",
      "action": "content.create_folder",
      "success": true,
      "data": {},
      "changed_objects": ["/Game/AI"]
    }
  ],
  "errors": [],
  "changed_objects": ["/Game/AI"]
}
```

Failed commands preserve the readable `error` field and add a stable `error_code`. Entries in the top-level `errors` array contain `command_id`, `code`, `message`, and a traceback for diagnostics. `changed_objects` is present on every executed command and at the top level; inspect it before retrying a failed mutating batch.

## Typed values

Transport values may use explicit typed envelopes; the executor never guesses an Unreal object, class, soft reference, or enum from a plain string:

```json
{"$type":"object","path":"/Game/AI/M_Iron.M_Iron"}
{"$type":"class","path":"/Script/Engine.Actor"}
{"$type":"soft_object","path":"/Game/AI/M_Iron.M_Iron"}
{"$type":"soft_class","path":"/Script/Engine.Actor"}
{"$type":"enum","name":"Visible"}
```

Typed envelopes accept only their documented fields. Object existence, class compatibility, property reflection, and enum-member validity remain Unreal-side responsibilities; transport-side codec validation only checks envelope shape.

## `object.describe`

Returns the reflected class and bounded editor-property names for a loaded UObject.

```json
{"id":"describe_object","action":"object.describe","arguments":{"object":"/Game/AI/M_Iron.M_Iron","limit":200}}
```

## `object.inspect`

Reads explicitly selected editor properties with a bounded recursive depth. `properties` is required and no implicit bulk dump is performed.

```json
{"id":"inspect_object","action":"object.inspect","arguments":{"object":"/Game/AI/M_Iron.M_Iron","properties":["NaniteSettings"],"depth":2}}
```

## `object.set`

Sets explicitly selected editor properties inside the normal harness transaction. The target is reported in `changed_objects`; Unreal remains authoritative for reflected type compatibility and property metadata.

```json
{"id":"set_object","action":"object.set","arguments":{"object":"/Game/AI/M_Iron.M_Iron","properties":{"TwoSided":true}}}
```


Reflection failures use stable codes: `object_not_found` when the UObject cannot be loaded, `reflection_unavailable` when editor property metadata is unavailable, `property_not_found` for an unknown selected property, `property_read_failed` for a failed read, and `property_write_failed` for a failed write. `object.set` validates every property before calling `modify()` or changing any value.
`object.set` accepts the same explicit typed envelopes as the transport codec. `$type: object` and `$type: class` resolve loaded references before mutation; `$type: soft_object` and `$type: soft_class` preserve soft paths; `$type: enum` passes the explicit member name to Unreal. Nested arrays and maps are decoded recursively. Unknown envelope fields and unresolved hard references fail before `modify()`.
When Unreal exposes property metadata, `object.set` rejects `ReadOnly`, `EditConst`, `Transient`, and `Deprecated` flags before opening a mutation. Fake/runtime metadata may declare `type` as `bool`, `number`, `string`, `array`, `set`, `map`, or `enum`; mismatches return `property_type_mismatch`. Unreal remains authoritative for native `FProperty` compatibility when metadata is unavailable.
Metadata may additionally declare enum `values`, reference `class`, or struct `fields` and `required` lists. Invalid enum members return `enum_value_invalid`; incompatible references return `reference_type_mismatch`; unknown or missing struct fields return `struct_field_invalid`. These checks are pre-mutation checks.

## `object.describe_functions`

Returns the explicitly allowlisted callable functions for a loaded object class. This is a policy catalog, not a complete Unreal `UFunction` reflection dump. Unknown classes and functions are denied.

```json
{"id":"functions","action":"object.describe_functions","arguments":{"object":"/Game/Input/IMC_Default.IMC_Default"}}
```

## `object.call`

Invokes one explicitly allowlisted reflected function after validating the target, argument names, typed references, and policy. Calls run inside the normal editor transaction. A denied or invalid call is rejected before `modify()` and reports no changed objects.

```json
{"id":"map_key","action":"object.call","arguments":{"object":"/Game/Input/IMC_Default.IMC_Default","function":"MapKey","arguments":{"action":{"$type":"object","path":"/Game/Input/IA_Jump.IA_Jump"},"to_key":{"$type":"struct","class":"Key","value":{"key_name":"SpaceBar"}}}}}
```

The initial allowlist contains `UInputMappingContext::MapKey(const UInputAction*, FKey)`. Its catalog declares the reflected return fields `action` (typed UObject), `key` (struct `Key`), `triggers` (array), and `modifiers` (array). Return values are serialized through the reflected Python wrapper; mutating calls are transaction-scoped. In the validated UE 5.8.3 wrapper, `MapKey` returns these fields as bounded structured JSON. Generic enum, array, and out-parameter policy entries require separate native fixtures and are not implied by this function.




## `system.capabilities`

Returns harness and engine versions, supported actions, graph-bridge availability, and graph node kinds.

```json
{"id":"capabilities","action":"system.capabilities","arguments":{}}
```

## `system.describe_actions`

Returns the action catalog and whether each action mutates editor state.

```json
{"id":"describe","action":"system.describe_actions","arguments":{}}
```

## `recipe.validate`

Expands and validates a recipe without executing its commands.

```json
{"id":"check_recipe","action":"recipe.validate","arguments":{"recipe":{"id":"layout","commands":[]}}}
```

## `recipe.execute`

Expands and executes a validated recipe through the normal transaction and audit envelope. The result includes nested command results.

```json
{"id":"run_recipe","action":"recipe.execute","arguments":{"recipe":{"id":"layout","commands":[]}}}
```

## `backend.capabilities`

Lists registered backends and their supported operations.

```json
{"id":"backends","action":"backend.capabilities","arguments":{}}
```

## `backend.describe`

Returns the descriptor for one registered backend.

```json
{"id":"backend","action":"backend.describe","arguments":{"backend":"backend_id"}}
```

## `backend.operation`

Invokes a registered backend operation. `operation` must be one of `validate`, `inspect`, `mutate`, or `diagnostics`; `payload` is an object and defaults to `{}`. Discover registered backends with `backend.capabilities` first.

```json
{"id":"inspect_backend","action":"backend.operation","arguments":{"backend":"backend_id","operation":"inspect","payload":{}}}
```

## `content.create_folder`

```json
{"id":"folder","action":"content.create_folder","arguments":{"path":"/Game/AI"}}
```

## `content.list`

Lists asset paths with bounded output. Arguments: `path` (default `/Game`), `recursive`, `query`, and `limit` (1–2000).

```json
{"id":"list","action":"content.list","arguments":{"path":"/Game/AI","recursive":true,"query":"BP_","limit":200}}
```

## `asset.inspect`

```json
{"id":"asset","action":"asset.inspect","arguments":{"asset":"/Game/AI/BP_Test.BP_Test"}}
```

## `asset.search`

Lists registered assets under a virtual path without loading them. Supports `path`, `recursive`, optional `query`, optional asset `class`, and bounded `limit` (1–2000) plus `offset` (0–1000000). The response includes the full matching `total`, returned page `assets`, `has_more`, `truncated`, `offset`, and `limit`.

```json
{"id":"search_page","action":"asset.search","arguments":{"path":"/Game/AI","recursive":true,"limit":100,"offset":100}}
```

## `asset.describe_types`

Returns asset classes and editor factory wrappers discoverable through AssetRegistry and the exposed AssetTools/Python API. It does not mutate project state. Results are bounded with `limit` (1–2000) and `offset` (0–1000000); classes and factories each include independent totals, offsets, and `has_more` metadata.

```json
{"id":"types","action":"asset.describe_types","arguments":{"limit":100,"offset":0}}
```

## `asset.create`

Creates an asset through Unreal Asset Tools. `folder` and `name` are required, along with a native `class` (or `asset_class`) or `factory` path. Existing assets can only be reused when compatible; replacement is disabled. Save the new asset explicitly with `asset.save` or `project.save`.

```json
{"id":"create_asset","action":"asset.create","arguments":{"folder":"/Game/AI","name":"Curve_Test","class":"/Script/Engine.CurveFloat"}}
```

## `asset.duplicate`

Duplicates one existing asset to a new package path. The destination must not already exist; replacement and deletion are not supported.

```json
{"id":"copy","action":"asset.duplicate","arguments":{"source":"/Game/AI/M_Source.M_Source","destination":"/Game/AI/M_Copy.M_Copy"}}
```

## `asset.save`

Saves only explicitly listed loaded assets or packages. At least one of `assets` or `packages` is required.

```json
{"id":"save_asset","action":"asset.save","arguments":{"assets":["/Game/AI/M_Copy.M_Copy"]}}
```

## `material.create`

Creates a simple Material with Base Color, Metallic, and Roughness expressions. Existing assets are never replaced.

```json
{"id":"iron","action":"material.create","arguments":{"folder":"/Game/AI/Materials","name":"M_Iron","base_color":[0.16,0.18,0.21,1.0],"metallic":1.0,"roughness":0.3}}
```

## `material.inspect`

Returns class, parent, expression count, and discoverable scalar/vector/static-switch parameters.

```json
{"id":"inspect_material","action":"material.inspect","arguments":{"material":"/Game/AI/Materials/M_Iron.M_Iron"}}
```

## `material_instance.create`

```json
{"id":"instance","action":"material_instance.create","arguments":{"folder":"/Game/AI/Materials","name":"MI_IronDark","parent":"/Game/AI/Materials/M_Iron.M_Iron"}}
```

## `material_instance.set_parameters`

Parameter maps are optional. Parameter names must exist in the parent material.

```json
{"id":"params","action":"material_instance.set_parameters","arguments":{"material_instance":"/Game/AI/Materials/MI_IronDark.MI_IronDark","scalar":{"Roughness":0.4},"vector":{"Tint":[0.1,0.12,0.15,1.0]},"static_switch":{"UseDetail":true}}}
```

## `blueprint.create`

Existing Blueprints are never replaced, even if `replace_existing` is supplied.

```json
{"id":"create_bp","action":"blueprint.create","arguments":{"folder":"/Game/AI/Blueprints","name":"BP_Test","parent_class":"/Script/Engine.Actor"}}
```

## `blueprint.inspect`

Returns parent/generated classes, graph names, components, transforms, meshes, and material slots.

```json
{"id":"inspect_bp","action":"blueprint.inspect","arguments":{"blueprint":"/Game/AI/Blueprints/BP_Test.BP_Test"}}
```

## `blueprint.compile`

Compilation success follows Unreal's `BlueprintStatus`, not merely completion of the editor call. Successful data includes `compile_status`; `BS_ERROR` returns the stable error code `blueprint_compile_failed` and does not report a successful command.

```json
{"id":"compile","action":"blueprint.compile","arguments":{"blueprint":"/Game/AI/Blueprints/BP_Test.BP_Test","save":false}}
```

## `blueprint.edit`

Operations: `add_component`, `add_variable`, `set_component_property`, `set_component_material`, `set_component_transform`, and `set_class_property`.

```json
{
  "id":"edit_bp","action":"blueprint.edit","arguments":{
    "blueprint":"/Game/AI/Blueprints/BP_Test.BP_Test",
    "operations":[
      {"operation":"add_component","component_type":"/Script/Engine.StaticMeshComponent","component_name":"Body","attach_to":"DefaultSceneRoot"},
      {"operation":"set_component_property","component_name":"Body","property":"static_mesh","value":"/Engine/BasicShapes/Sphere.Sphere"},
      {"operation":"set_component_material","component_name":"Body","slot":0,"material":"/Game/AI/Materials/M_Iron.M_Iron"},
      {"operation":"set_component_transform","component_name":"Body","location":[0,0,50],"rotation":[0,0,0],"scale":[1,1,1]}
    ],"compile":true,"save":false
  }
}
```

## `graph.describe`

Returns read-only context for an existing Blueprint graph: canonical graph name, node count, parent class, and generated class.

```json
{"id":"graph_context","action":"graph.describe","arguments":{"blueprint":"/Game/AI/Blueprints/BP_Test.BP_Test","graph":"EventGraph"}}
```

## `graph.search_nodes`

Searches the native graph bridge catalog for supported node kinds in the selected Blueprint graph. Results are candidates only; `ambiguous: true` means the caller must choose a returned `kind` and no node is selected automatically.

```json
{"id":"node_search","action":"graph.search_nodes","arguments":{"blueprint":"/Game/AI/Blueprints/BP_Test.BP_Test","graph":"EventGraph","query":"branch"}}
```

## `graph.describe_node`

Returns the stable `node_spec` for one supported node kind. The spec uses machine identifiers (`kind`, parameter names), not localized display text.

```json
{"id":"node_spec","action":"graph.describe_node","arguments":{"kind":"function_call"}}
```

Catalog failures use `blueprint_not_found`, `graph_not_found`, and `node_kind_not_found`.

## `blueprint.graph.inspect`

Returns node GUIDs, classes, titles, positions, pins, defaults, types, and links.

```json
{"id":"graph","action":"blueprint.graph.inspect","arguments":{"blueprint":"/Game/AI/Blueprints/BP_Test.BP_Test","graph":"EventGraph"}}
```

## `blueprint.graph.add_node`

Supported `node.kind` values are reported by `system.capabilities`: `function_call`, `event`, `operator`, `custom_event`, `branch`, `sequence`, `reroute`, `self`, `variable_get`, `variable_set`, `dynamic_cast`, `struct_make`, and `struct_break`. `node_id` is a document-local alias usable by later graph commands.

```json
{"id":"add_int","action":"blueprint.graph.add_node","arguments":{"blueprint":"/Game/AI/Blueprints/BP_Test.BP_Test","graph":"EventGraph","node_id":"add_int","node":{"kind":"operator","owner_class":"/Script/Engine.KismetMathLibrary","function":"Add_IntInt"},"position":[300,0]}}
```

Kind-specific fields:
- `function_call` and `event`: `owner_class`, `function`.
- `operator`: `owner_class` and `function`; the current safe subset accepts only `/Script/Engine.KismetMathLibrary.Add_IntInt` and creates the native integer commutative operator node.
- `custom_event`: `event_name`; it must be a valid, non-duplicate custom event name and the graph must be an Event Graph.
- `variable_get` and `variable_set`: `variable_name` (the variable must already exist).
- `dynamic_cast`: `target_class`.
- `struct_make` and `struct_break`: `struct_path`, a fully-qualified native `UScriptStruct` object path such as `/Script/CoreUObject.Vector` or `/Script/CoreUObject.Transform`.

`blueprint.edit` `add_variable` accepts `name` and one of the safe basic `variable_type` values: `bool`, `byte`, `int`, `int64`, `float`, `double`, `string`, `name`, or `text`. Variables must not already exist.

`node_id` is a document-local alias usable by later graph commands.
 

## `blueprint.graph.connect`

Unreal's K2 schema validates compatibility and may insert conversions. Pin matching ignores case, spaces, and underscores.

```json
{"id":"wire","action":"blueprint.graph.connect","arguments":{"blueprint":"/Game/AI/Blueprints/BP_Test.BP_Test","graph":"EventGraph","from":{"node":"begin_play","pin":"then"},"to":{"node":"branch","pin":"execute"}}}
```

## `blueprint.graph.set_pin_value`

Values are passed as strings to Unreal's graph schema.

```json
{"id":"condition","action":"blueprint.graph.set_pin_value","arguments":{"blueprint":"/Game/AI/Blueprints/BP_Test.BP_Test","graph":"EventGraph","node":"branch","pin":"condition","value":"true"}}
```


## `blueprint.graph.remove_node`

Removes a node identified by document-local `node` alias or node GUID. The graph defaults to `EventGraph`.

```json
{"id":"remove","action":"blueprint.graph.remove_node","arguments":{"blueprint":"/Game/AI/Blueprints/BP_Test.BP_Test","graph":"EventGraph","node":"branch"}}
```

## `blueprint.graph.disconnect`

Disconnects one link using `from` and `to` endpoint objects, or every link on a specified `node`/`pin` when `all` is true.

```json
{"id":"unwire","action":"blueprint.graph.disconnect","arguments":{"blueprint":"/Game/AI/Blueprints/BP_Test.BP_Test","from":{"node":"begin_play","pin":"then"},"to":{"node":"branch","pin":"execute"}}}
```

```json
{"id":"unwire_pin","action":"blueprint.graph.disconnect","arguments":{"blueprint":"/Game/AI/Blueprints/BP_Test.BP_Test","node":"branch","pin":"execute","all":true}}
```
## `level.inspect`

Arguments: `query`, exact `class`, `selected_only`, and `limit` (1–5000).

```json
{"id":"level","action":"level.inspect","arguments":{"query":"Enemy","limit":200}}
```

## `level.spawn_actor`

Blueprint generated-class paths end in `_C`. Loading a non-current level is intentional and should be explicit.

```json
{"id":"spawn","action":"level.spawn_actor","arguments":{"level":"current","class":"/Game/AI/Blueprints/BP_Test.BP_Test_C","actor_label":"Test Actor","transform":{"location":[0,0,100],"rotation":[0,0,0],"scale":[1,1,1]}}}
```

## `level.set_actor_transform`

Identify exactly one actor by `actor_name` or `actor_label`. Omitted transform fields retain their current values.

```json
{"id":"move","action":"level.set_actor_transform","arguments":{"actor_label":"Test Actor","transform":{"location":[100,200,100]}}}
```

## `level.set_actor_property`

Sets one reflected editor property on exactly one actor.

```json
{"id":"tag","action":"level.set_actor_property","arguments":{"actor_label":"Test Actor","property":"tags","value":["Codex"]}}
```

## `project.save`

Mutating commands participate in editor Undo transactions. Persistence is explicit through this action unless a command's own `save` option is enabled.

```json
{"id":"save","action":"project.save","depends_on":["spawn"],"arguments":{"save_level":true,"save_assets":true}}
```

## Deliberate exclusions

The harness does not expose arbitrary Python, shell execution, asset deletion, Blueprint replacement, or bulk destructive operations. UE 5.8's native Unreal MCP can be enabled alongside the harness for interactive tool discovery, but the JSON executor remains the deterministic batch and audit layer.
- `blueprint.edit` supports `add_variable` for native Blueprint member variables. Required fields: `name` and `variable_type`. Supported basic types: `bool`, `byte`, `int`, `int64`, `float`, `double`, `string`, `name`, `text`. The operation rejects empty names, unsupported types, and native insertion failures before reporting success; variable access nodes require the variable to exist and be Blueprint-visible.
