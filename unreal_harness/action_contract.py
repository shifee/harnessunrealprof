"""Shared, dependency-free action contract, installed alongside the UE executor.

Schemas describe transport values. Reflected property compatibility and native
asset availability still require Unreal. No schema can execute supplied code.
"""
from __future__ import annotations

import copy
import math
import re
from itertools import product
from typing import Any

CONTRACT_VERSION = "1.0"
MAX_COMMANDS = 200


class ActionValidationError(ValueError):
    def __init__(self, message: str, code: str = "validation_error"):
        super().__init__(message)
        self.code = code


def obj(properties=None, required=(), **extra):
    return dict(type="object", properties=properties or {}, required=list(required),
                additionalProperties=False, **extra)


def arr(items, minimum=0, maximum=2000):
    return dict(type="array", items=items, minItems=minimum, maxItems=maximum)


STR = {"type": "string", "minLength": 1}
BOOL = {"type": "boolean"}
NUM = {"type": "number"}
MAP = {"type": "object"}
ANY = {}
PATH = dict(STR, pattern=r"^/")
GAME = dict(STR, pattern=r"^/Game(?:/|$)")
NAME = dict(STR, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
MODE = {"type": "string", "enum": ["fail", "reuse", "update"]}
VECTOR = arr(NUM, 3, 3)
COLOR = arr(NUM, 4, 4)
UNIT = dict(NUM, minimum=0, maximum=1)
LIMIT = {"type": "integer", "minimum": 1, "maximum": 2000}
OFFSET = {"type": "integer", "minimum": 0, "maximum": 1000000}
TRANSFORM = obj({"location": VECTOR, "rotation": VECTOR, "scale": VECTOR})
ENDPOINT = obj({"node": STR, "pin": STR}, ["node", "pin"])
CONFLICT = {"conflict_mode": MODE}
CREATE = {"folder": GAME, "name": NAME, "replace_existing": {"const": False}, **CONFLICT}
BLUEPRINT = {"blueprint": GAME}
GRAPH = {**BLUEPRINT, "graph": STR}
ACTOR = {"actor_name": STR, "actor_label": STR}
ACTOR_CHOICE = [{"required": ["actor_name"]}, {"required": ["actor_label"]}]

OPERATION_SCHEMAS = {
    "add_component": obj({"operation": {"const": "add_component"}, "component_type": PATH,
                          "component_name": NAME, "attach_to": STR, **CONFLICT},
                         ["operation", "component_type", "component_name"]),
    "add_variable": obj({"operation": {"const": "add_variable"}, "name": NAME,
                         "variable_type": {"enum": ["bool", "byte", "int", "int64", "float", "double", "string", "name", "text"]},
                         **CONFLICT}, ["operation", "name", "variable_type"]),
    "set_component_property": obj({"operation": {"const": "set_component_property"}, "component_name": STR,
                                   "property": STR, "value": ANY, **CONFLICT},
                                  ["operation", "component_name", "property", "value"]),
    "set_component_material": obj({"operation": {"const": "set_component_material"}, "component_name": STR,
                                   "slot": {"type": "integer", "minimum": 0}, "material": PATH, **CONFLICT},
                                  ["operation", "component_name", "material"]),
    "set_component_transform": obj({"operation": {"const": "set_component_transform"}, "component_name": STR,
                                    **TRANSFORM["properties"], **CONFLICT}, ["operation", "component_name"]),
    "set_class_property": obj({"operation": {"const": "set_class_property"}, "property": STR, "value": ANY,
                               **CONFLICT}, ["operation", "property", "value"]),
}
NODE_SCHEMAS = {}
for kind in ("function_call", "event", "operator", "custom_event", "branch", "sequence", "reroute", "self",
             "variable_get", "variable_set", "dynamic_cast", "struct_make", "struct_break"):
    fields = {"kind": {"const": kind}}
    required = ["kind"]
    if kind in ("function_call", "event", "operator"):
        fields.update(owner_class=PATH, function=STR); required += ["owner_class", "function"]
    elif kind == "custom_event":
        fields["event_name"] = NAME; required += ["event_name"]
    elif kind in ("variable_get", "variable_set"):
        fields["variable_name"] = STR; required += ["variable_name"]
    elif kind == "dynamic_cast":
        fields["target_class"] = PATH; required += ["target_class"]
    elif kind in ("struct_make", "struct_break"):
        fields["struct_path"] = PATH; required += ["struct_path"]
    NODE_SCHEMAS[kind] = obj(fields, required)

ACTION_SCHEMAS = {}
ACTION_METADATA = {}


def action(name, properties=None, required=(), mutates=False, description="", example=None, **extra):
    # conflict_mode is also injected by the existing declarative recipe expander.
    schema = obj({**(properties or {}), **CONFLICT}, required, **extra)
    ACTION_SCHEMAS[name] = schema
    ACTION_METADATA[name] = {"name": name, "mutates": mutates, "mutating": mutates,
                             "description": description, "arguments_schema": schema,
                             "example": {"id": name.replace(".", "_"), "action": name,
                                         "arguments": example or {}}}


action("system.capabilities")
action("system.describe_actions", description="Discover action argument schemas and mutation flags.")
action("object.describe", {"object": PATH, "limit": LIMIT}, ["object"])
action("object.describe_functions", {"object": PATH, "limit": LIMIT}, ["object"])
action("object.inspect", {"object": PATH, "properties": arr(STR, 1), "depth": {"type": "integer", "minimum": 0, "maximum": 8}},
       ["object", "properties"], description="Read explicitly selected reflected editor properties.")
action("object.set", {"object": PATH, "properties": dict(MAP, minProperties=1)}, ["object", "properties"], True)
action("object.call", {"object": PATH, "function": STR, "arguments": MAP}, ["object", "function"], True,
       "Invoke only runtime-allowlisted functions discovered with object.describe_functions.")
action("content.create_folder", {"path": GAME}, ["path"], True)
action("content.list", {"path": GAME, "recursive": BOOL, "query": {"type": "string"}, "limit": LIMIT})
action("asset.inspect", {"asset": GAME}, ["asset"], description="Inspect asset identity and exists_on_disk (package file existence; does not prove latest in-memory values were saved).")
action("asset.search", {"path": GAME, "recursive": BOOL, "query": {"type": "string"}, "class": STR, "limit": LIMIT, "offset": OFFSET})
action("asset.describe_types", {"limit": LIMIT, "offset": OFFSET})
action("asset.create", {**CREATE, "class": PATH, "asset_class": PATH, "factory": PATH}, ["folder", "name"], True,
       anyOf=[{"required": ["class"]}, {"required": ["asset_class"]}, {"required": ["factory"]}])
action("asset.duplicate", {"source": GAME, "destination": GAME}, ["source", "destination"], True)
action("asset.save", {k: arr(GAME, 1) for k in ("assets", "asset_paths", "packages", "package_paths")}, mutates=True,
       anyOf=[{"required": [k]} for k in ("assets", "asset_paths", "packages", "package_paths")])
action("material.create", {**CREATE, "base_color": COLOR, "metallic": UNIT, "roughness": UNIT}, ["folder", "name"], True,
       "Create a simple material with BaseColor, Metallic and Roughness; creation saves it.",
       {"folder": "/Game/Materials", "name": "M_Example", "base_color": [0.1, 0.2, 0.8, 1], "metallic": 1, "roughness": 0.3})
action("material.inspect", {"material": GAME}, ["material"])
action("material_instance.create", {"folder": GAME, "name": NAME, "parent": GAME}, ["folder", "name", "parent"], True)
action("material_instance.set_parameters", {"material_instance": GAME, "scalar": dict(MAP, additionalProperties=NUM),
       "vector": dict(MAP, additionalProperties=COLOR), "static_switch": dict(MAP, additionalProperties=BOOL)}, ["material_instance"], True)
action("blueprint.create", {**CREATE, "parent_class": PATH}, ["folder", "name", "parent_class"], True,
       "Create a Blueprint asset; components are added using blueprint.edit.",
       {"folder": "/Game/Blueprints", "name": "BP_Example", "parent_class": "/Script/Engine.Actor"})
action("blueprint.inspect", BLUEPRINT, ["blueprint"])
action("blueprint.compile", {**BLUEPRINT, "save": BOOL}, ["blueprint"], True)
action("blueprint.edit", {**BLUEPRINT, "operations": arr({"anyOf": list(OPERATION_SCHEMAS.values())}, 1, 1000),
       "compile": BOOL, "save": BOOL}, ["blueprint", "operations"], True,
       "Edit components or variables using the listed operation schemas; compile and save default to true.",
       {"blueprint": "/Game/Blueprints/BP_Example.BP_Example", "operations": [{"operation": "add_variable", "name": "Count", "variable_type": "int"}]})
action("graph.describe", GRAPH, ["blueprint"])
action("graph.search_nodes", {**GRAPH, "query": {"type": "string"}}, ["blueprint"])
action("graph.describe_node", {"kind": {"enum": list(NODE_SCHEMAS)}}, ["kind"])
action("blueprint.graph.inspect", GRAPH, ["blueprint"])
action("blueprint.graph.add_node", {**GRAPH, "node_id": STR, "node": {"anyOf": list(NODE_SCHEMAS.values())},
       "position": arr(NUM, 2, 2)}, ["blueprint", "node_id", "node"], True)
action("blueprint.graph.connect", {**GRAPH, "from": ENDPOINT, "to": ENDPOINT}, ["blueprint", "from", "to"], True)
action("blueprint.graph.set_pin_value", {**GRAPH, "node": STR, "pin": STR, "value": {"type": "string"}}, ["blueprint", "node", "pin", "value"], True)
action("blueprint.graph.remove_node", {**GRAPH, "node": STR, "node_id": STR}, ["blueprint"], True,
       anyOf=[{"required": ["node"]}, {"required": ["node_id"]}])
action("blueprint.graph.disconnect", {**GRAPH, "from": ENDPOINT, "to": ENDPOINT, "all": BOOL, "node": STR, "pin": STR}, ["blueprint"], True,
       anyOf=[{"required": ["from", "to"]}, {"required": ["all", "node", "pin"], "properties": {"all": {"const": True}}}])
action("level.inspect", {"query": {"type": "string"}, "class": PATH, "selected_only": BOOL,
       "limit": {"type": "integer", "minimum": 1, "maximum": 5000}},
       description="Read current-level actors, including name, label, class, path, location, rotation and scale. Filters are top-level arguments.",
       example={"class": "/Script/Engine.PlayerStart", "limit": 100})
action("level.spawn_actor", {**ACTOR, "class": PATH, "level": STR, "transform": TRANSFORM}, ["class"], True)
action("level.set_actor_transform", {**ACTOR, "transform": TRANSFORM}, ["transform"], True, anyOf=ACTOR_CHOICE)
action("level.set_actor_property", {**ACTOR, "property": STR, "value": ANY}, ["property", "value"], True, anyOf=ACTOR_CHOICE)
action("project.save", {"save_level": BOOL, "save_assets": BOOL}, mutates=True,
       description="Persist level/assets explicitly; do not add this to inspection tasks.")
action("backend.capabilities")
action("backend.describe", {"backend": STR}, ["backend"])
action("backend.operation", {"backend": STR, "operation": {"enum": ["validate", "inspect", "mutate", "diagnostics"]}, "payload": MAP},
       ["backend", "operation"], True)
action("recipe.validate", {"recipe": MAP}, ["recipe"])
action("recipe.execute", {"recipe": MAP}, ["recipe"], True)

def _sample(schema, key=""):
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "object":
        props = dict(schema.get("properties", {}))
        required = list(schema.get("required", []))
        if "anyOf" in schema:
            branch = schema["anyOf"][0]
            props.update(branch.get("properties", {})); required += branch.get("required", [])
        result = {name: _sample(props.get(name, {}), name) for name in required}
        if schema.get("minProperties", 0) and not result:
            result["ExampleProperty"] = None
        return result
    if kind == "array":
        return [_sample(schema.get("items", {})) for _ in range(schema.get("minItems", 0))]
    if kind == "boolean":
        return False
    if kind in {"number", "integer"}:
        return schema.get("minimum", 0)
    if kind == "string":
        if key in {"class", "parent_class", "owner_class", "component_type", "factory", "target_class"}:
            return "/Script/Engine.Actor"
        if schema.get("pattern", "").startswith("^/"):
            return "/Game/Examples/Example.Example"
        return "Example"
    if "anyOf" in schema:
        return _sample(schema["anyOf"][0], key)
    return None


for _name, _meta in ACTION_METADATA.items():
    if not _meta["example"]["arguments"]:
        _meta["example"]["arguments"] = _sample(ACTION_SCHEMAS[_name])


# Compatibility views are generated, never independently maintained.
ACTION_ARGUMENTS = {name: tuple(schema["properties"]) for name, schema in ACTION_SCHEMAS.items()}
MUTATING_ACTIONS = {name for name, meta in ACTION_METADATA.items() if meta["mutates"]}
ACTION_CONTRACT = {name: (meta["mutates"], {k: ACTION_SCHEMAS[name]["properties"][k].get("type", "any")
                  for k in ACTION_SCHEMAS[name]["required"]}, not meta["mutates"]) for name, meta in ACTION_METADATA.items()}


def describe_contract(actions=None):
    names = set(ACTION_METADATA) if actions is None else set(actions)
    return [copy.deepcopy(ACTION_METADATA[n]) for n in sorted(names & set(ACTION_METADATA))]


def is_mutating(command):
    if command.get("action") == "backend.operation":
        return command.get("arguments", {}).get("operation") == "mutate"
    return command.get("action") in MUTATING_ACTIONS


def references(value):
    if isinstance(value, dict):
        if "$ref" in value:
            if set(value) != {"$ref"} or not isinstance(value["$ref"], str) or not value["$ref"]:
                raise ActionValidationError("$ref must be a standalone non-empty string reference", "invalid_reference")
            yield value["$ref"]
        else:
            for item in value.values():
                yield from references(item)
    elif isinstance(value, list):
        for item in value:
            yield from references(item)


def check(value, schema, path, allow_refs=True, depth=0):
    if depth > 40:
        raise ActionValidationError(path + ": nesting limit exceeded", "invalid_arguments")
    if allow_refs and isinstance(value, dict) and set(value) == {"$ref"}:
        return  # Validate reference ordering separately and resolved type before execution.
    if "anyOf" in schema:
        branches = schema["anyOf"]
        if isinstance(value, dict):
            for discriminator in ("operation", "kind"):
                if discriminator in value:
                    matched = [b for b in branches if b.get("properties", {}).get(discriminator, {}).get("const") == value[discriminator]]
                    if matched:
                        branches = matched
        errors = []
        for branch in branches:
            try:
                check(value, branch, path, allow_refs, depth + 1)
                break
            except ActionValidationError as exc:
                errors.append(str(exc))
        else:
            raise ActionValidationError(path + ": no supported alternative; " + " | ".join(errors), "invalid_arguments")
    if "const" in schema and value != schema["const"]:
        raise ActionValidationError(path + ": expected " + repr(schema["const"]), "invalid_arguments")
    if "enum" in schema and value not in schema["enum"]:
        raise ActionValidationError(path + ": expected one of " + repr(schema["enum"]), "invalid_arguments")
    kind = schema.get("type")
    types = {"string": isinstance(value, str), "boolean": isinstance(value, bool), "object": isinstance(value, dict),
             "array": isinstance(value, list), "number": isinstance(value, (int, float)) and not isinstance(value, bool),
             "integer": isinstance(value, int) and not isinstance(value, bool)}
    if kind and not types.get(kind, False):
        raise ActionValidationError(path + ": must be " + kind, "invalid_arguments")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or ("pattern" in schema and not re.search(schema["pattern"], value)):
            raise ActionValidationError(path + ": invalid string or path", "invalid_arguments")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value) or value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf):
            raise ActionValidationError(path + ": number outside supported range", "invalid_arguments")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", 1000000):
            raise ActionValidationError(path + ": invalid array length", "invalid_arguments")
        for i, item in enumerate(value):
            check(item, schema.get("items", {}), path + "[{}]".format(i), allow_refs, depth + 1)
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                raise ActionValidationError(path + ": requires argument " + key, "missing_argument")
        if len(value) < schema.get("minProperties", 0):
            raise ActionValidationError(path + ": must not be empty", "invalid_arguments")
        props = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key not in props and additional is False:
                raise ActionValidationError(path + ": unknown field " + key, "invalid_arguments")
            child = props.get(key, additional if isinstance(additional, dict) else {})
            check(item, child, path + "." + key, allow_refs, depth + 1)


def validate_arguments(action_name, arguments, allow_refs=True):
    if not isinstance(action_name, str) or action_name not in ACTION_SCHEMAS:
        raise ActionValidationError("Unsupported action: " + str(action_name), "unsupported_action")
    check(arguments, ACTION_SCHEMAS[action_name], action_name, allow_refs)


def validate_document(document, *, planner=False, expanded=False, _depth=0):
    if _depth > 8:
        raise ActionValidationError("Recipe nesting limit exceeded", "invalid_recipe")
    if not isinstance(document, dict) or not isinstance(document.get("commands"), list):
        raise ActionValidationError("Document must contain a commands array", "invalid_document")
    if document.get("format_version", "1.0") != "1.0":
        raise ActionValidationError("Unsupported format_version", "unsupported_format")
    allowed = {"format_version", "commands", "dry_run", "run_id", "recipes"}
    if set(document) - allowed:
        raise ActionValidationError("Unknown document fields: " + ", ".join(sorted(set(document) - allowed)), "invalid_document")
    if "dry_run" in document and not isinstance(document["dry_run"], bool):
        raise ActionValidationError("dry_run must be boolean", "invalid_document")
    if planner and document.get("dry_run"):
        raise ActionValidationError("Planner must return an execution plan, not dry_run", "invalid_document")
    if len(document["commands"]) > MAX_COMMANDS:
        raise ActionValidationError("Too many commands; maximum is 200", "too_many_commands")
    commands = document["commands"] if expanded else expand_document_recipes(document)[0]["commands"]
    if len(commands) > MAX_COMMANDS:
        raise ActionValidationError("Too many expanded commands; maximum is 200", "too_many_commands")
    seen = set()
    for command in commands:
        if not isinstance(command, dict):
            raise ActionValidationError("Command must be an object", "invalid_command")
        if set(command) - {"id", "action", "arguments", "depends_on", "condition", "recipe_id", "conflict_mode"}:
            raise ActionValidationError("Unknown command fields", "invalid_command")
        ident, name = command.get("id"), command.get("action")
        if not isinstance(name, str) or name not in ACTION_SCHEMAS:
            raise ActionValidationError("Unsupported action: " + str(name), "unsupported_action")
        if not isinstance(ident, str) or not ident:
            raise ActionValidationError("Command id must be a non-empty string", "invalid_command")
        if ident in seen:
            raise ActionValidationError("Duplicate command id: " + ident, "duplicate_command_id")
        deps = command.get("depends_on", [])
        if not isinstance(deps, list) or any(not isinstance(dep, str) or dep not in seen for dep in deps):
            raise ActionValidationError(ident + ": Dependencies must refer to earlier commands", "invalid_dependencies")
        condition = command.get("condition")
        refs = [] if name in {"recipe.execute", "recipe.validate"} else list(references(command.get("arguments", {})))
        if condition is not None:
            if not isinstance(condition, dict):
                raise ActionValidationError(ident + ": condition must be an object", "invalid_condition")
            try:
                check(condition, obj({"ref": STR, "equals": ANY, "not_equals": ANY, "exists": BOOL}, ["ref"]), ident + ".condition")
            except ActionValidationError as exc:
                raise ActionValidationError(str(exc), "invalid_condition") from exc
            if len(set(condition) & {"equals", "not_equals", "exists"}) > 1:
                raise ActionValidationError(ident + ": condition must use one comparison", "invalid_condition")
            refs += [condition["ref"]] + list(references(condition))
        for ref in refs:
            if ref.split(".", 1)[0] not in seen:
                raise ActionValidationError(ident + ": reference must target an earlier command: " + ref, "invalid_reference")
        try:
            validate_arguments(name, command.get("arguments", {}))
        except ActionValidationError as exc:
            raise ActionValidationError(ident + ": " + str(exc), exc.code) from exc
        if name in {"recipe.execute", "recipe.validate"}:
            nested = recipe_commands(command["arguments"]["recipe"])
            validate_document({"commands": nested}, expanded=True, _depth=_depth + 1)
        seen.add(ident)
    if "recipes" in document:
        if not isinstance(document["recipes"], list):
            raise ActionValidationError("recipes must be an array", "invalid_recipe")
        # Templates are expanded and validated by the UE executor's pure preflight.
        for recipe in document["recipes"]:
            if not isinstance(recipe, dict) or not isinstance(recipe.get("commands", []), list):
                raise ActionValidationError("Invalid recipe template", "invalid_recipe")
    if planner and not document["commands"] and not document.get("recipes"):
        raise ActionValidationError("Empty plan cannot fulfill a task", "empty_plan")
    if planner:
        mutations = [c for c in commands if is_mutating(c)]
        if mutations and all(c["action"] in {"project.save", "asset.save"} for c in mutations):
            raise ActionValidationError("Inspection plans must not add save commands", "read_only_save")
    return document


CONFLICT_MODES = {"fail", "reuse", "update"}
TEMPLATE_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def require(condition, message, code="validation_error"):
    if not condition:
        raise ActionValidationError(message, code)


def conflict_mode(arguments, default="fail"):
    mode = arguments.get("conflict_mode", default)
    require(mode in CONFLICT_MODES, "Invalid conflict_mode: " + str(mode), "invalid_conflict_mode")
    return mode


def substitute_template(value, context):
    if isinstance(value, str):
        exact = TEMPLATE_VARIABLE.fullmatch(value)
        if exact:
            name = exact.group(1)
            require(name in context, "Unknown recipe variable: " + name, "invalid_recipe")
            return context[name]

        def replace(match):
            name = match.group(1)
            require(name in context, "Unknown recipe variable: " + name, "invalid_recipe")
            return str(context[name])

        return TEMPLATE_VARIABLE.sub(replace, value)
    if isinstance(value, list):
        expanded = []
        for item in value:
            item_value = expand_recipe_value(item, context)
            if isinstance(item, dict) and len(item) == 1 and next(iter(item), "") in {
                "$repeat", "$mirror", "$grid"
            }:
                require(isinstance(item_value, list), "Recipe pattern must expand to a list", "invalid_recipe")
                expanded.extend(item_value)
            else:
                expanded.append(item_value)
        return expanded
    if isinstance(value, dict):
        return {key: expand_recipe_value(item, context) for key, item in value.items()}
    return value


def expand_pattern(kind, specification, context):
    require(isinstance(specification, dict), kind + " must be an object", "invalid_recipe")
    require("template" in specification, kind + " requires template", "invalid_recipe")
    template = specification["template"]
    contexts = []

    if kind == "$repeat":
        items = specification.get("items")
        require(isinstance(items, list), "$repeat.items must be an array", "invalid_recipe")
        require(len(items) <= MAX_COMMANDS, "Too many repeat items", "too_many_commands")
        for index, item in enumerate(items):
            require(isinstance(item, dict), "$repeat items must be objects", "invalid_recipe")
            item_context = dict(context)
            item_context.update(item)
            item_context.setdefault("index", index)
            contexts.append(item_context)

    elif kind == "$mirror":
        axis = specification.get("axis")
        item = specification.get("item", {})
        overrides = specification.get("overrides", {})
        require(axis in {"x", "y", "z"}, "$mirror.axis must be x, y, or z", "invalid_recipe")
        require(isinstance(item, dict) and isinstance(overrides, dict), "$mirror item and overrides must be objects", "invalid_recipe")
        require(isinstance(item.get(axis), (int, float)), "$mirror item must contain a numeric axis value", "invalid_recipe")
        original = dict(context)
        original.update(item)
        original["mirror_index"] = 0
        mirrored = dict(original)
        mirrored[axis] = -item[axis]
        mirrored.update(overrides)
        mirrored["mirror_index"] = 1
        contexts.extend((original, mirrored))

    elif kind == "$grid":
        axes = specification.get("axes")
        base = specification.get("base", {})
        require(isinstance(axes, dict) and axes, "$grid.axes must be a non-empty object", "invalid_recipe")
        require(isinstance(base, dict), "$grid.base must be an object", "invalid_recipe")
        axis_names = list(axes)
        axis_values = []
        for name in axis_names:
            values = axes[name]
            require(isinstance(values, list) and values, "$grid axis values must be non-empty arrays", "invalid_recipe")
            axis_values.append(values)
        size = math.prod(len(values) for values in axis_values)
        require(size <= MAX_COMMANDS, "Grid exceeds command limit", "too_many_commands")
        for grid_index, combination in enumerate(product(*axis_values)):
            item_context = dict(context)
            item_context.update(base)
            item_context["index"] = grid_index
            for axis_index, (name, value) in enumerate(zip(axis_names, combination)):
                item_context[name] = value
                item_context[name + "_index"] = axes[name].index(value)
                item_context["axis_{}_index".format(axis_index)] = axes[name].index(value)
            contexts.append(item_context)

    expanded = []
    for item_context in contexts:
        value = expand_recipe_value(template, item_context)
        if isinstance(value, list):
            expanded.extend(value)
        else:
            expanded.append(value)
        require(len(expanded) <= MAX_COMMANDS * 10, "Expanded pattern limit exceeded", "too_many_commands")
    return expanded


def expand_recipe_value(value, context):
    if isinstance(value, dict) and len(value) == 1:
        kind = next(iter(value))
        if kind in {"$repeat", "$mirror", "$grid"}:
            return expand_pattern(kind, value[kind], context)
    return substitute_template(value, context)


def expand_document_recipes(document):
    require(isinstance(document, dict), "Document root must be an object", "invalid_document")
    raw_commands = document.get("commands", [])
    require(isinstance(raw_commands, list), "'commands' must be an array", "invalid_document")
    recipes = document.get("recipes", [])
    require(isinstance(recipes, list), "'recipes' must be an array", "invalid_recipe")
    commands = list(raw_commands)
    summaries = []
    seen_recipe_ids = set()
    for recipe in recipes:
        require(isinstance(recipe, dict), "Recipe must be an object", "invalid_recipe")
        recipe_id = recipe.get("id")
        require(isinstance(recipe_id, str) and recipe_id, "Recipe id must be a non-empty string", "invalid_recipe")
        require(recipe_id not in seen_recipe_ids, "Duplicate recipe id: " + recipe_id, "invalid_recipe")
        seen_recipe_ids.add(recipe_id)
        mode = conflict_mode(recipe)
        expanded = expand_recipe_value(recipe.get("commands", []), {})
        require(isinstance(expanded, list), "Recipe commands must expand to an array", "invalid_recipe")
        command_ids = []
        for command in expanded:
            require(isinstance(command, dict), "Expanded recipe command must be an object", "invalid_recipe")
            command = dict(command)
            command["recipe_id"] = recipe_id
            arguments = dict(command.get("arguments", {}))
            arguments.setdefault("conflict_mode", mode)
            command["arguments"] = arguments
            commands.append(command)
            command_ids.append(command.get("id"))
        summaries.append({"id": recipe_id, "conflict_mode": mode, "commands": command_ids})
    expanded_document = dict(document)
    expanded_document["commands"] = commands
    return expanded_document, summaries


def validate_recipe_parameters(recipe):
    parameters = recipe.get("parameters", {})
    require(isinstance(parameters, dict), "recipe.parameters must be an object", "invalid_recipe")
    schema = recipe.get("parameter_schema", {})
    require(isinstance(schema, dict), "recipe.parameter_schema must be an object", "invalid_recipe")
    required = schema.get("required", [])
    require(isinstance(required, list) and all(isinstance(name, str) for name in required), "parameter_schema.required must be an array of strings", "invalid_recipe")
    missing = [name for name in required if name not in parameters]
    require(not missing, "Missing recipe parameters: " + ", ".join(missing), "invalid_recipe")
    types = schema.get("types", {})
    require(isinstance(types, dict), "parameter_schema.types must be an object", "invalid_recipe")
    for name, expected in types.items():
        require(name in parameters, "Missing recipe parameter: " + name, "invalid_recipe")
        value = parameters[name]
        valid = {"string": isinstance(value, str), "number": isinstance(value, (int, float)) and not isinstance(value, bool), "boolean": isinstance(value, bool), "array": isinstance(value, list), "object": isinstance(value, dict)}.get(expected)
        require(valid is True, "Invalid type for recipe parameter: " + name, "invalid_recipe")
    return parameters


def recipe_commands(recipe):
    validate_recipe_parameters(recipe)
    expanded = expand_recipe_value(recipe.get("commands", []), recipe.get("parameters", {}))
    require(isinstance(expanded, list), "Recipe commands must expand to an array", "invalid_recipe")
    normalized = []
    for command in expanded:
        require(isinstance(command, dict), "Expanded recipe command must be an object", "invalid_recipe")
        normalized.append(dict(command))
    return normalized
