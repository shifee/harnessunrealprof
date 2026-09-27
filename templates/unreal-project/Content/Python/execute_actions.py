import json
import os
import re
import traceback
import uuid
from itertools import product
from datetime import datetime, timezone
import unreal

try:
    from backend_registry import BackendError, BackendRegistry
except ImportError:
    class BackendError(RuntimeError):
        def __init__(self, code, message, details=None):
            super().__init__(message)
            self.code, self.details = code, (details or {})

    class BackendRegistry:
        def __init__(self):
            self._adapters = {}

        def capabilities(self):
            return []

        def describe(self, name):
            raise BackendError("backend_not_found", "Unknown backend: " + str(name))

        def invoke(self, name, operation, payload):
            raise BackendError("backend_not_found", "Unknown backend: " + str(name))

        def validate(self, name, payload):
            return self.invoke(name, "validate", payload)

        def inspect(self, name, payload):
            return self.invoke(name, "inspect", payload)

        def mutate(self, name, payload):
            return self.invoke(name, "mutate", payload)

        def diagnostics(self, name, payload):
            return self.invoke(name, "diagnostics", payload)

BACKEND_REGISTRY = BackendRegistry()


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ACTIONS_FILE = os.path.join(SCRIPT_DIR, "actions.json")
RESULT_FILE = os.path.join(SCRIPT_DIR, "result.json")
HARNESS_VERSION = "0.1.0"
MAX_COMMANDS = 200
CURRENT_CHANGED_OBJECTS = []
# The recipe transaction owner groups all nested mutating commands.  Nested
# execute_command calls must not open/cancel their own transactions.
ACTIVE_RECIPE_TRANSACTION = None
CONFLICT_MODES = {"fail", "reuse", "update"}
TEMPLATE_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class HarnessError(RuntimeError):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def log(message):
    unreal.log("[AI EXECUTOR] " + str(message))


def log_error(message):
    unreal.log_error("[AI EXECUTOR] " + str(message))


def require(condition, message, code="validation_error"):
    if not condition:
        raise HarnessError(code, message)


def error_record(error, command_id=None, include_traceback=False):
    record = {
        "command_id": command_id,
        "code": getattr(error, "code", "execution_failed"),
        "message": str(error),
    }
    details = getattr(error, "details", None)
    if details:
        record["details"] = details
    if include_traceback:
        record["traceback"] = traceback.format_exc()
    return record


def mark_changed(*values):
    for value in values:
        if value is None:
            continue
        path = value if isinstance(value, str) else object_path(value)
        if path and path not in CURRENT_CHANGED_OBJECTS:
            CURRENT_CHANGED_OBJECTS.append(path)


def merge_changed(target, values):
    for value in values:
        if value not in target:
            target.append(value)


def validate_game_path(path):
    require(isinstance(path, str), "Path must be a string")
    require(path.startswith("/Game"), "Path must start with /Game")
    require(".." not in path, "Path cannot contain '..'")
    return path.rstrip("/")


def validate_name(name):
    require(isinstance(name, str), "Name must be a string")
    require(
        re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name) is not None,
        "Invalid Unreal name: " + name,
    )
    return name


def asset_package_path(asset_path):
    return asset_path.split(".")[0]


def make_vector(values, default):
    values = values if values is not None else default
    require(
        isinstance(values, list) and len(values) == 3,
        "Vector must contain exactly three numbers",
    )
    return unreal.Vector(float(values[0]), float(values[1]), float(values[2]))


def make_rotator(values):
    values = values if values is not None else [0.0, 0.0, 0.0]
    require(
        isinstance(values, list) and len(values) == 3,
        "Rotation must contain [pitch, yaw, roll]",
    )
    return unreal.Rotator(
        pitch=float(values[0]), yaw=float(values[1]), roll=float(values[2])
    )


def make_linear_color(values, default):
    values = values if values is not None else default
    require(
        isinstance(values, list) and len(values) in (3, 4),
        "Color must contain [r, g, b] or [r, g, b, a]",
    )
    alpha = float(values[3]) if len(values) == 4 else 1.0
    return unreal.LinearColor(
        float(values[0]), float(values[1]), float(values[2]), alpha
    )
def vector_values(value):
    return [float(value.x), float(value.y), float(value.z)]

def rotator_values(value):
    return [float(value.pitch), float(value.yaw), float(value.roll)]


def load_unreal_class(class_path):
    require(isinstance(class_path, str), "Class path must be a string")
    unreal_class = unreal.load_class(None, class_path)
    require(unreal_class is not None, "Could not load Unreal class: " + class_path)
    return unreal_class


def load_json_file(path):
    require(os.path.isfile(path), "File not found: " + path)
    with open(path, "r", encoding="utf-8-sig") as file:
        return json.load(file)


def save_json_file(path, data):
    temporary_path = path + ".tmp"
    with open(temporary_path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    os.replace(temporary_path, path)


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


RESULT_CONTEXT = {}


def resolve_result_reference(value):
    if isinstance(value, dict) and set(value) == {"$ref"}:
        reference = value["$ref"]
        require(isinstance(reference, str) and reference, "$ref must be a non-empty string", "invalid_reference")
        parts = reference.split(".")
        command_id = parts.pop(0)
        require(command_id in RESULT_CONTEXT, "Unknown result reference: " + command_id, "invalid_reference")
        current = RESULT_CONTEXT[command_id]
        for part in parts:
            require(isinstance(current, dict) and part in current, "Missing result reference path: " + reference, "invalid_reference")
            current = current[part]
        return current
    if isinstance(value, list):
        return [resolve_result_reference(item) for item in value]
    if isinstance(value, dict):
        return {key: resolve_result_reference(item) for key, item in value.items()}
    return value


def evaluate_condition(condition):
    require(isinstance(condition, dict), "condition must be an object", "invalid_condition")
    require(set(condition).issubset({"ref", "equals", "not_equals", "exists"}), "Unsupported condition fields", "invalid_condition")
    reference = condition.get("ref")
    require(isinstance(reference, str) and reference, "condition.ref is required", "invalid_condition")
    value = resolve_result_reference({"$ref": reference})
    if "equals" in condition:
        return value == resolve_result_reference(condition["equals"])
    if "not_equals" in condition:
        return value != resolve_result_reference(condition["not_equals"])
    if "exists" in condition:
        return bool(value) is bool(condition["exists"])
    return bool(value)


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
def recipe_references(value):
    if isinstance(value, dict) and set(value) == {"$ref"}:
        reference = value["$ref"]
        require(isinstance(reference, str) and reference, "$ref must be a non-empty string", "invalid_reference")
        yield reference
        return
    if isinstance(value, dict):
        for item in value.values():
            for reference in recipe_references(item):
                yield reference
    elif isinstance(value, list):
        for item in value:
            for reference in recipe_references(item):
                yield reference




def validate_recipe_commands(commands):
    require(len(commands) <= MAX_COMMANDS, "Too many recipe commands; maximum is " + str(MAX_COMMANDS), "too_many_commands")
    seen = set()
    for index, command in enumerate(commands):
        require(isinstance(command, dict), "Recipe command at index {} must be an object".format(index), "invalid_recipe")
        command_id = command.get("id")
        action = command.get("action")
        require(isinstance(command_id, str) and command_id, "Recipe command id is required", "invalid_recipe")
        require(command_id not in seen, "Duplicate recipe command id: " + command_id, "duplicate_command_id")
        require(action in ACTION_HANDLERS, "Unsupported recipe action: " + str(action), "unsupported_action")
        require(isinstance(command.get("arguments", {}), dict), "arguments must be an object: " + command_id, "invalid_arguments")
        dependencies = command.get("depends_on", [])
        require(isinstance(dependencies, list) and all(isinstance(item, str) for item in dependencies), "depends_on must be an array of strings", "invalid_dependencies")
        missing = [item for item in dependencies if item not in seen]
        require(not missing, "Dependencies must refer to earlier recipe commands: " + ", ".join(missing), "invalid_dependencies")
        if "condition" in command:
            condition = command["condition"]
            require(isinstance(condition, dict), "condition must be an object", "invalid_condition")
            require(set(condition).issubset({"ref", "equals", "not_equals", "exists"}), "Unsupported condition fields", "invalid_condition")
            reference = condition.get("ref")
            require(isinstance(reference, str) and reference, "condition.ref is required", "invalid_condition")
            require(reference.split(".", 1)[0] in seen, "Condition must target an earlier command: " + reference, "invalid_reference")
            if "exists" in condition:
                require(isinstance(condition["exists"], bool), "condition.exists must be a boolean", "invalid_condition")
        for value in (command.get("arguments", {}), command.get("condition")):
            if value is not None:
                for reference in recipe_references(value):
                    referenced_id = reference.split(".", 1)[0]
                    require(referenced_id in seen, "Recipe reference must target an earlier command: " + reference, "invalid_reference")
        seen.add(command_id)
    return commands

def execute_recipe_validate(arguments):
    recipe = arguments.get("recipe")
    require(isinstance(recipe, dict), "recipe is required", "invalid_recipe")
    expanded = recipe_commands(recipe)
    validate_recipe_commands(expanded)
    return {"valid": True, "command_count": len(expanded), "commands": expanded}


def execute_recipe_execute(arguments):
    global ACTIVE_RECIPE_TRANSACTION, CURRENT_CHANGED_OBJECTS
    recipe = arguments.get("recipe")
    require(isinstance(recipe, dict), "recipe is required", "invalid_recipe")
    expanded = recipe_commands(recipe)
    validate_recipe_commands(expanded)
    results = []
    changed_objects = []
    recipe_id = recipe.get("id", "<anonymous>")
    owner = ACTIVE_RECIPE_TRANSACTION is None
    transaction = None
    if owner:
        transaction = unreal.ScopedEditorTransaction("Codex: recipe " + str(recipe_id))
        ACTIVE_RECIPE_TRANSACTION = transaction
    try:
        for command in expanded:
            try:
                command_result = execute_command(command)
            except Exception as error:
                # Keep successful nested command envelopes (including their
                # changed_objects) while making the failed recipe atomic.
                failure = HarnessError(
                    "recipe_command_failed",
                    "Recipe {} failed at command {}: {}".format(
                        recipe_id, command.get("id", "<missing id>"), str(error)
                    ),
                    {
                        "recipe_id": recipe_id,
                        "command_id": command.get("id"),
                        "action": command.get("action"),
                        "command_error_code": getattr(error, "code", "execution_failed"),
                        "commands": results,
                    },
                )
                # A cancelled transaction has no aggregate changed objects.
                CURRENT_CHANGED_OBJECTS = []
                raise failure
            results.append(command_result)
            merge_changed(changed_objects, command_result.get("changed_objects", []))
        mark_changed(*changed_objects)
        return {"executed": True, "command_count": len(results), "commands": results, "changed_objects": changed_objects}
    except Exception:
        if owner:
            transaction.cancel()
        raise
    finally:
        if owner:
            ACTIVE_RECIPE_TRANSACTION = None
            del transaction


def object_path(value):
    return value.get_path_name() if value is not None else None


def execute_create_folder(arguments):
    path = validate_game_path(arguments["path"])
    if unreal.EditorAssetLibrary.does_directory_exist(path):
        mode = conflict_mode(arguments, "reuse")
        require(mode != "fail", "Directory already exists: " + path, "conflict")
        return {"created": False, "reused": True, "path": path}
    success = unreal.EditorAssetLibrary.make_directory(path)
    require(success, "Could not create directory: " + path)
    mark_changed(path)
    return {"created": True, "path": path}


def execute_list_content(arguments):
    path = validate_game_path(arguments.get("path", "/Game"))
    recursive = bool(arguments.get("recursive", True))
    limit = int(arguments.get("limit", 200))
    require(1 <= limit <= 2000, "limit must be between 1 and 2000")
    assets = [str(asset) for asset in unreal.EditorAssetLibrary.list_assets(path, recursive, False)]
    query = str(arguments.get("query", "")).lower().strip()
    if query:
        assets = [asset for asset in assets if query in str(asset).lower()]
    total = len(assets)
    return {"path": path, "total": total, "truncated": total > limit, "assets": assets[:limit]}


def execute_inspect_asset(arguments):
    path = validate_game_path(asset_package_path(arguments["asset"]))
    asset = unreal.load_asset(path)
    require(asset is not None, "Asset not found: " + path)
    asset_data = unreal.EditorAssetLibrary.find_asset_data(path)
    return {
        "path": object_path(asset),
        "name": asset.get_name(),
        "class": asset.get_class().get_path_name(),
        "package": asset.get_outermost().get_path_name(),
        "asset_class": str(asset_data.asset_class_path) if asset_data else None,
    }

def load_reflected_object(path):
    require(isinstance(path, str) and path, "object must be a non-empty path")
    require(path.startswith(("/Game/", "/Script/", "/Temp/")), "Object path must start with /Game, /Script, or /Temp")
    value = unreal.load_object(None, path)
    require(value is not None, "Object not found: " + path, "object_not_found")
    require(hasattr(value, "get_class"), "Resolved value is not a UObject", "invalid_object")
    return value


def reflected_property_names(value):
    getter = getattr(value, "get_editor_property_names", None)
    if getter is None:
        return []
    try:
        return sorted(str(name) for name in getter())
    except Exception:
        return []

def reflected_property_metadata(value, name):
    getter = getattr(value, "get_editor_property_metadata", None)
    if getter is None:
        return {}
    try:
        metadata = getter(name)
    except Exception:
        return {}
    return dict(metadata) if isinstance(metadata, dict) else {}


def validate_property_write(value, name, new_value):
    metadata = reflected_property_metadata(value, name)
    flags = {str(flag).lower() for flag in metadata.get("flags", [])}
    blocked = flags.intersection({"readonly", "editconst", "transient", "deprecated"})
    require(not blocked, "Property is not writable: {} ({})".format(name, ", ".join(sorted(blocked))), "property_not_writable")
    expected = metadata.get("type")
    if expected == "bool":
        require(isinstance(new_value, bool), "Property requires a boolean: " + name, "property_type_mismatch")
    elif expected == "number":
        require(isinstance(new_value, (int, float)) and not isinstance(new_value, bool), "Property requires a number: " + name, "property_type_mismatch")
    elif expected == "string":
        require(isinstance(new_value, str), "Property requires a string: " + name, "property_type_mismatch")
    elif expected == "array":
        require(isinstance(new_value, list), "Property requires an array: " + name, "property_type_mismatch")
    elif expected == "set":
        require(isinstance(new_value, list) and len(new_value) == len({repr(item) for item in new_value}), "Property requires unique array values: " + name, "property_type_mismatch")
    elif expected == "map":
        require(isinstance(new_value, dict), "Property requires an object map: " + name, "property_type_mismatch")
    elif expected == "enum":
        member = new_value.get("name") if isinstance(new_value, dict) and new_value.get("$type") == "enum" else new_value
        allowed = metadata.get("values")
        require(isinstance(member, str), "Property requires an enum member: " + name, "property_type_mismatch")
        if isinstance(allowed, list):
            require(member in allowed, "Unknown enum member for property {}: {}".format(name, member), "enum_value_invalid")
    elif expected in {"object", "class"}:
        require(new_value is not None, "Property requires a non-null {} reference: {}".format(expected, name), "property_type_mismatch")
        class_value = new_value.get_class() if expected == "object" and hasattr(new_value, "get_class") else new_value
        actual_class = class_value.get_path_name() if hasattr(class_value, "get_path_name") else None
        require(actual_class is not None, "Property requires a typed {} reference: {}".format(expected, name), "property_type_mismatch")
        allowed_class = metadata.get("class")
        if allowed_class:
            require(actual_class == allowed_class or actual_class.startswith(allowed_class + "/"), "Reference class is incompatible for property: " + name, "reference_type_mismatch")
    elif expected == "struct":
        require(isinstance(new_value, dict), "Property requires a struct object: " + name, "property_type_mismatch")
        fields = metadata.get("fields")
        if isinstance(fields, list):
            unknown = set(new_value) - set(fields)
            required = set(metadata.get("required", [])) - set(new_value)
            require(not unknown, "Unknown struct fields: " + ", ".join(sorted(unknown)), "struct_field_invalid")
            require(not required, "Missing struct fields: " + ", ".join(sorted(required)), "struct_field_invalid")


def serialize_reflected_value(value, depth=0, max_depth=4):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if depth >= max_depth:
        return {"$truncated": True}
    if isinstance(value, (list, tuple)):
        return [serialize_reflected_value(item, depth + 1, max_depth) for item in value]
    if type(value).__name__ == "Array":
        try:
            return [serialize_reflected_value(value[index], depth + 1, max_depth) for index in range(len(value))]
        except Exception:
            return str(value)
    if isinstance(value, dict):
        return {str(key): serialize_reflected_value(item, depth + 1, max_depth) for key, item in value.items()}
    try:
        path = object_path(value)
    except Exception:
        path = None
    if path:
        return {"$type": "object", "path": path}
    fields = {}
    field_names = (
        "x", "y", "z", "pitch", "yaw", "roll", "r", "g", "b", "a",
        "action", "key", "triggers", "modifiers", "key_name",
    )
    getter = getattr(value, "get_editor_property", None)
    for name in field_names:
        try:
            if getter is not None:
                fields[name] = serialize_reflected_value(getter(name), depth + 1, max_depth)
            elif hasattr(value, name):
                fields[name] = serialize_reflected_value(getattr(value, name), depth + 1, max_depth)
        except Exception:
            continue
    return fields if fields else str(value)


FUNCTION_POLICIES = {
    "/Script/EnhancedInput.InputMappingContext": {
        "MapKey": {
            "parameters": {
                "action": {"type": "object", "class": "/Script/EnhancedInput.InputAction"},
                "to_key": {"type": "struct", "class": "Key"},
            },
            "returns": [
                {"name": "action", "type": "object", "class": "/Script/EnhancedInput.InputAction"},
                {"name": "key", "type": "struct", "class": "Key"},
                {"name": "triggers", "type": "array"},
                {"name": "modifiers", "type": "array"},
            ],
            "metadata": {"editor_safe": True, "mutates": True},
        },
    },
}

FORBIDDEN_FUNCTION_NAMES = {
    "process_event", "exec", "call_function", "load", "save", "delete",
    "rename", "duplicate", "destroy", "quit", "exit",
}


def function_policy(value, function_name):
    class_path = value.get_class().get_path_name()
    class_policy = FUNCTION_POLICIES.get(class_path, {})
    policy = class_policy.get(function_name)
    if function_name in FORBIDDEN_FUNCTION_NAMES or policy is None:
        raise HarnessError(
            "function_not_allowed",
            "Function is not allowed: {}.{}".format(class_path, function_name),
        )
    return class_path, policy


def make_function_argument(specification, value):
    expected = specification.get("type")
    if expected == "struct":
        if isinstance(value, dict) and value.get("$type") == "struct":
            require(
                set(value) <= {"$type", "class", "value"},
                "Typed struct has unknown fields",
                "invalid_typed_value",
            )
            struct_name = value.get("class") or specification.get("class")
            value = value.get("value")
        else:
            struct_name = specification.get("class")
        value = decode_property_value(value)
        require(isinstance(value, dict), "Function argument requires a struct object", "function_argument_invalid")
        constructor = getattr(unreal, struct_name, None) if struct_name else None
        require(constructor is not None and callable(constructor), "Struct API is unavailable: " + str(struct_name), "function_argument_invalid")
        try:
            return constructor(**value)
        except Exception:
            try:
                instance = constructor()
                for field_name, field_value in value.items():
                    if hasattr(instance, "set_editor_property"):
                        instance.set_editor_property(field_name, field_value)
                    else:
                        setattr(instance, field_name, field_value)
                return instance
            except Exception:
                try:
                    if set(value) == {"key_name"}:
                        return constructor(value["key_name"])
                except Exception:
                    pass
                raise HarnessError("function_argument_invalid", "Could not construct struct argument")
    value = decode_property_value(value)
    if expected == "object":
        if isinstance(value, dict) and value.get("$type") == "object":
            value = load_reflected_object(value.get("path"))
        require(hasattr(value, "get_class"), "Function argument requires a UObject", "function_argument_invalid")
        expected_class = specification.get("class")
        if expected_class:
            actual_class = value.get_class().get_path_name()
            require(
                actual_class == expected_class,
                "Function argument class mismatch: {}".format(actual_class),
                "function_argument_invalid",
            )
    elif expected == "enum":
        require(isinstance(value, str), "Function argument requires an enum name", "function_argument_invalid")
    elif expected == "array":
        require(isinstance(value, list), "Function argument requires an array", "function_argument_invalid")
    return value


def execute_object_describe_functions(arguments):
    value = load_reflected_object(arguments.get("object"))
    class_path = value.get_class().get_path_name()
    policies = FUNCTION_POLICIES.get(class_path, {})
    limit = arguments.get("limit", 200)
    require(isinstance(limit, int) and not isinstance(limit, bool) and 1 <= limit <= 2000,
            "limit must be an integer between 1 and 2000")
    functions = []
    for name in sorted(policies):
        policy = policies[name]
        functions.append({
            "name": name,
            "owner_class": class_path,
            "parameters": [
                {"name": parameter, **specification}
                for parameter, specification in policy.get("parameters", {}).items()
            ],
            "returns": list(policy.get("returns", [])),
            "metadata": dict(policy.get("metadata", {})),
        })
    return {
        "object": object_path(value),
        "class": class_path,
        "functions": functions[:limit],
        "truncated": len(functions) > limit,
    }


def execute_object_call(arguments):
    path = arguments.get("object")
    value = load_reflected_object(path)
    function_name = arguments.get("function")
    require(isinstance(function_name, str) and function_name, "function must be a non-empty string")
    _, policy = function_policy(value, function_name)
    supplied = arguments.get("arguments", {})
    require(isinstance(supplied, dict), "arguments must be an object", "function_argument_invalid")
    specifications = policy.get("parameters", {})
    unknown = set(supplied) - set(specifications)
    require(not unknown, "Unknown function arguments: " + ", ".join(sorted(unknown)), "function_argument_invalid")
    missing = set(specifications) - set(supplied)
    require(not missing, "Missing function arguments: " + ", ".join(sorted(missing)), "function_argument_invalid")
    decoded = {
        name: make_function_argument(specifications[name], supplied[name])
        for name in specifications
    }
    value.modify() if policy.get("metadata", {}).get("mutates") and hasattr(value, "modify") else None
    try:
        result = value.call_method(function_name, kwargs=decoded)
    except HarnessError:
        raise
    except Exception as error:
        raise HarnessError("function_call_failed", "Function call failed: {}".format(function_name)) from error
    if policy.get("metadata", {}).get("mutates"):
        mark_changed(path)
    return {
        "object": object_path(value) or path,
        "function": function_name,
        "return": serialize_reflected_value(result),
    }
def execute_object_describe(arguments):
    value = load_reflected_object(arguments.get("object"))
    class_object = value.get_class()
    names = reflected_property_names(value)
    limit = int(arguments.get("limit", 200))
    require(1 <= limit <= 2000, "limit must be between 1 and 2000")
    return {
        "object": object_path(value),
        "class": class_object.get_path_name(),
        "properties": [{"name": name} for name in names[:limit]],
        "truncated": len(names) > limit,
    }


def reflected_property_available(value, name, available=None):
    if available:
        return name in available
    try:
        value.get_editor_property(name)
    except Exception:
        return False
    return True


def execute_object_inspect(arguments):
    value = load_reflected_object(arguments.get("object"))
    properties = arguments.get("properties", [])
    require(isinstance(properties, list) and properties, "properties must be a non-empty array")
    depth = int(arguments.get("depth", 2))
    require(0 <= depth <= 8, "depth must be between 0 and 8")
    available = set(reflected_property_names(value))
    result = {}
    for name in properties:
        require(isinstance(name, str) and name, "property names must be non-empty strings")
        require(reflected_property_available(value, name, available), "Unknown property: " + name, "property_not_found")
        try:
            result[name] = serialize_reflected_value(value.get_editor_property(name), 0, depth)
        except Exception as error:
            raise HarnessError("property_read_failed", "Could not read property: {}".format(name)) from error
    return {"object": object_path(value), "class": value.get_class().get_path_name(), "properties": result, "depth": depth}


def execute_object_set(arguments):
    path = arguments.get("object")
    value = load_reflected_object(path)
    properties = arguments.get("properties")
    require(isinstance(properties, dict) and properties, "properties must be a non-empty object")
    available = set(reflected_property_names(value))
    decoded = {}
    for name, new_value in properties.items():
        require(reflected_property_available(value, name, available), "Unknown property: " + name, "property_not_found")
        require(not name.startswith("_"), "Private properties are not writable", "property_not_writable")
        decoded[name] = decode_property_value(new_value)
        validate_property_write(value, name, decoded[name])
    value.modify() if hasattr(value, "modify") else None
    changed = []
    for name, new_value in decoded.items():
        try:
            value.set_editor_property(name, new_value)
        except Exception as error:
            raise HarnessError("property_write_failed", "Could not write property: {}".format(name)) from error
        changed.append(name)
    mark_changed(path)
    return {"object": object_path(value) or path, "changed_properties": changed}
def asset_registry():
    helpers = getattr(unreal, "AssetRegistryHelpers", None)
    require(helpers is not None, "AssetRegistryHelpers is unavailable", "api_unavailable")
    registry = helpers.get_asset_registry()
    require(registry is not None, "Asset registry is unavailable", "api_unavailable")
    return registry



def asset_data_path(data):
    package_name = str(getattr(data, "package_name", ""))
    asset_name = str(getattr(data, "asset_name", ""))
    return package_name + "." + asset_name if asset_name else package_name


def execute_asset_search(arguments):
    path = validate_game_path(arguments.get("path", "/Game"))
    query = arguments.get("query", "")
    require(isinstance(query, str), "query must be a string")
    class_name = arguments.get("class")
    require(class_name is None or isinstance(class_name, str), "class must be a string")
    limit = arguments.get("limit", 200)
    offset = arguments.get("offset", 0)
    require(isinstance(limit, int) and not isinstance(limit, bool) and 1 <= limit <= 2000,
            "limit must be an integer between 1 and 2000")
    require(isinstance(offset, int) and not isinstance(offset, bool) and 0 <= offset <= 1000000,
            "offset must be an integer between 0 and 1000000")
    registry = asset_registry()
    filter_type = getattr(unreal, "ARFilter", None)
    require(filter_type is not None, "ARFilter is unavailable", "api_unavailable")
    try:
        filter_kwargs = {
            "package_paths": [path],
            "recursive_paths": bool(arguments.get("recursive", True)),
        }
        if class_name:
            filter_kwargs["class_names"] = [class_name.rsplit("/", 1)[-1].rsplit(".", 1)[-1]]
        ar_filter = filter_type(**filter_kwargs)
    except Exception:
        ar_filter = filter_type()
        ar_filter.package_paths = [path]
        ar_filter.recursive_paths = bool(arguments.get("recursive", True))
        if class_name:
            ar_filter.class_names = [class_name.rsplit("/", 1)[-1].rsplit(".", 1)[-1]]
    records = []
    total = 0
    page_end = offset + limit
    for data in registry.get_assets(ar_filter):
        record_path = asset_data_path(data)
        if query.lower() not in record_path.lower():
            continue
        if total >= offset and total < page_end:
            records.append({
                "path": record_path,
                "package": str(getattr(data, "package_name", "")),
                "name": str(getattr(data, "asset_name", "")),
                "class": str(getattr(data, "asset_class_path", getattr(data, "asset_class", ""))),
            })
        total += 1
    return {
        "path": path,
        "offset": offset,
        "limit": limit,
        "total": total,
        "has_more": offset + len(records) < total,
        "truncated": offset + len(records) < total,
        "assets": records,
    }


def execute_asset_describe_types(arguments):
    limit = arguments.get("limit", 200)
    offset = arguments.get("offset", 0)
    require(isinstance(limit, int) and not isinstance(limit, bool) and 1 <= limit <= 2000,
            "limit must be an integer between 1 and 2000")
    require(isinstance(offset, int) and not isinstance(offset, bool) and 0 <= offset <= 1000000,
            "offset must be an integer between 0 and 1000000")
    registry = asset_registry()
    get_derived = getattr(registry, "get_derived_class_names", None)
    require(get_derived is not None, "AssetRegistry class discovery is unavailable", "api_unavailable")
    try:
        classes = []
        path_type = getattr(unreal, "TopLevelAssetPath", None)
        bases = [path_type("/Script/CoreUObject", base) for base in ("Object", "UObject")] if path_type else ["Object", "UObject"]
        for base in bases:
            derived = get_derived([base], set())
            classes.extend(str(name) for name in derived)
        classes = sorted(set(classes))
    except Exception as error:
        raise HarnessError("catalog_query_failed", "Could not query asset classes") from error
    helpers = getattr(unreal, "AssetToolsHelpers", None)
    require(helpers is not None, "Asset tools are unavailable", "api_unavailable")
    try:
        tools = helpers.get_asset_tools()
        require(tools is not None, "Asset tools are unavailable", "api_unavailable")
        factories = []
        for name in dir(unreal):
            if not name.endswith("Factory") and not name.endswith("FactoryNew"):
                continue
            factory_type = getattr(unreal, name, None)
            try:
                factory_class = factory_type.static_class()
                cdo = factory_class.get_default_object()
                supported = cdo.get_editor_property("supported_class")
                factories.append({
                    "class": supported.get_path_name() if supported else None,
                    "factory": factory_class.get_path_name(),
                })
            except Exception:
                continue
        require(factories, "No asset factories are exposed", "catalog_query_failed")
    except HarnessError:
        raise
    except Exception as error:
        raise HarnessError("catalog_query_failed", "Could not query asset factories") from error
    factory_offset = min(offset, len(factories))
    class_offset = min(offset, len(classes))
    return {
        "classes": classes[class_offset:class_offset + limit],
        "classes_total": len(classes),
        "classes_offset": class_offset,
        "classes_has_more": class_offset + limit < len(classes),
        "factories": factories[factory_offset:factory_offset + limit],
        "factories_total": len(factories),
        "factories_offset": factory_offset,
        "factories_has_more": factory_offset + limit < len(factories),
        "limit": limit,
    }

def load_asset_creation_class(path, label):
    """Load only reflected UE classes allowed for asset creation."""
    require(isinstance(path, str) and path, label + " must be a non-empty class path")
    require(
        path.startswith(("/Script/", "/Engine/")),
        label + " must use an approved Unreal path: " + path,
        "invalid_asset_class",
    )
    value = unreal.load_class(None, path)
    require(value is not None, "Could not load " + label + ": " + path, "asset_class_not_found")
    return value


def execute_asset_create(arguments):
    folder = validate_game_path(arguments.get("folder"))
    name = validate_name(arguments.get("name"))
    asset_path = folder + "/" + name
    mode = conflict_mode(arguments)
    class_path = arguments.get("class", arguments.get("asset_class"))
    factory_path = arguments.get("factory")
    require(class_path is not None or factory_path is not None,
            "asset.create requires class or factory", "invalid_asset_create")
    require(class_path is None or isinstance(class_path, str),
            "class must be a string", "invalid_asset_create")
    require(factory_path is None or isinstance(factory_path, str),
            "factory must be a string", "invalid_asset_create")

    # Resolve all requested reflected types before touching folders or assets.
    asset_class = load_asset_creation_class(class_path, "class") if class_path else None
    factory = None
    if factory_path:
        factory_class = load_asset_creation_class(factory_path, "factory")
        try:
            factory = unreal.new_object(factory_class)
        except Exception as error:
            raise HarnessError("invalid_asset_factory", "Could not instantiate factory: " + factory_path) from error

    exists = unreal.EditorAssetLibrary.does_asset_exist(asset_path)
    if arguments.get("replace_existing", False):
        raise HarnessError("replacement_disabled", "Automatic replacement is disabled for safety: " + asset_path)
    if exists:
        require(mode == "reuse", "Asset already exists: " + asset_path, "conflict")
        asset = unreal.load_asset(asset_path)
        require(asset is not None, "Could not load existing asset: " + asset_path, "asset_not_found")
        actual_class = asset.get_class().get_path_name()
        if class_path:
            require(actual_class == class_path,
                    "Existing asset class does not match requested class: " + asset_path,
                    "type_mismatch")
        result_path = object_path(asset) or asset_path + "." + name
        return {"path": result_path, "asset_path": result_path, "name": name,
                "class": actual_class, "created": False, "reused": True}

    if not unreal.EditorAssetLibrary.does_directory_exist(folder):
        require(unreal.EditorAssetLibrary.make_directory(folder),
                "Could not create directory: " + folder)
    tools_helpers = getattr(unreal, "AssetToolsHelpers", None)
    require(tools_helpers is not None, "Asset tools are unavailable", "api_unavailable")
    tools = tools_helpers.get_asset_tools()
    asset = tools.create_asset(name, folder, asset_class, factory)
    require(asset is not None, "Could not create asset: " + asset_path)
    result_path = object_path(asset) or asset_path + "." + name
    mark_changed(asset)
    actual_class = asset.get_class().get_path_name() if hasattr(asset, "get_class") else class_path
    return {"path": result_path, "asset_path": result_path, "name": name,
            "class": actual_class, "created": True, "reused": False}


def execute_asset_duplicate(arguments):
    source = validate_game_path(asset_package_path(arguments.get("source", "")))
    destination = validate_game_path(asset_package_path(arguments.get("destination", "")))
    require(source != destination, "Source and destination must differ")
    require(unreal.EditorAssetLibrary.does_asset_exist(source), "Source asset not found: " + source)
    require(not unreal.EditorAssetLibrary.does_asset_exist(destination), "Destination already exists: " + destination, "conflict")
    duplicate = unreal.EditorAssetLibrary.duplicate_asset(source, destination)
    require(duplicate is not None, "Could not duplicate asset: " + source)
    mark_changed(object_path(duplicate) or destination)
    return {"source": source, "asset_path": object_path(duplicate) or destination, "replaced": False}


def execute_asset_save(arguments):
    assets = arguments.get("assets", arguments.get("asset_paths", []))
    packages = arguments.get("packages", arguments.get("package_paths", []))
    require(isinstance(assets, list) and isinstance(packages, list), "assets and packages must be arrays")
    require(assets or packages, "At least one asset or package path is required")
    asset_objects = []
    package_objects = []
    for path in assets:
        path = validate_game_path(asset_package_path(path))
        asset = unreal.load_asset(path)
        require(asset is not None, "Asset not found: " + path)
        asset_objects.append(asset)
    for path in packages:
        path = validate_game_path(asset_package_path(path))
        package = unreal.load_package(path)
        require(package is not None, "Package not found: " + path)
        package_objects.append(package)
    if asset_objects:
        saver = getattr(unreal, "EditorLoadingAndSavingUtils", None)
        require(saver is not None and hasattr(saver, "save_dirty_packages"), "Package save API is unavailable", "api_unavailable")
        try:
            saver.save_dirty_packages(True, True)
        except Exception as error:
            raise HarnessError("validation_error", "Could not save selected asset packages") from error
        for asset in asset_objects:
            mark_changed(object_path(asset))
    if package_objects:
        saver = getattr(unreal, "EditorLoadingAndSavingUtils", None)
        require(saver is not None and hasattr(saver, "save_packages"), "Package save API is unavailable", "api_unavailable")
        require(saver.save_packages(package_objects, False), "Could not save selected packages")
        for package in package_objects:
            mark_changed(package.get_path_name())
    return {"assets_saved": [object_path(asset) for asset in asset_objects], "packages_saved": [package.get_path_name() for package in package_objects]}


def execute_create_blueprint(arguments):
    folder = validate_game_path(arguments["folder"])
    name = validate_name(arguments["name"])
    asset_path = folder + "/" + name
    object_path = asset_path + "." + name

    if unreal.EditorAssetLibrary.does_asset_exist(asset_path):
        if arguments.get("replace_existing", False):
            raise HarnessError("replacement_disabled", "Automatic replacement is disabled for safety: " + asset_path)
        mode = conflict_mode(arguments)
        require(mode != "fail", "Blueprint already exists: " + asset_path, "conflict")
        blueprint = unreal.load_asset(asset_path)
        require(isinstance(blueprint, unreal.Blueprint), "Existing asset is not a Blueprint: " + asset_path, "type_mismatch")
        return {
            "asset_path": object_path,
            "generated_class_path": object_path + "_C",
            "created": False,
            "reused": True,
        }

    if not unreal.EditorAssetLibrary.does_directory_exist(folder):
        require(
            unreal.EditorAssetLibrary.make_directory(folder),
            "Could not create directory: " + folder,
        )

    parent_class = load_unreal_class(arguments["parent_class"])
    factory = unreal.BlueprintFactory()
    factory.set_editor_property("parent_class", parent_class)
    blueprint = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        name, folder, None, factory
    )
    require(blueprint is not None, "Could not create Blueprint: " + asset_path)
    unreal.BlueprintEditorLibrary.compile_blueprint(blueprint)
    unreal.EditorAssetLibrary.save_loaded_asset(blueprint, False)
    mark_changed(blueprint)
    return {
        "asset_path": object_path,
        "generated_class_path": object_path + "_C",
        "created": True,
        "reused": False,
    }


def configure_simple_material(material, arguments, clear_existing=False):
    base_color = make_linear_color(
        arguments.get("base_color"), [0.18, 0.20, 0.22, 1.0]
    )
    metallic = float(arguments.get("metallic", 0.0))
    roughness = float(arguments.get("roughness", 0.5))
    require(0.0 <= metallic <= 1.0, "metallic must be between 0 and 1")
    require(0.0 <= roughness <= 1.0, "roughness must be between 0 and 1")
    if clear_existing:
        unreal.MaterialEditingLibrary.delete_all_material_expressions(material)

    base_expression = unreal.MaterialEditingLibrary.create_material_expression(
        material, unreal.MaterialExpressionVectorParameter, -400, -120
    )
    require(base_expression is not None, "Could not create Base Color expression")
    base_expression.set_editor_property("parameter_name", "BaseColor")
    base_expression.set_editor_property("default_value", base_color)
    require(
        unreal.MaterialEditingLibrary.connect_material_property(
            base_expression, "", unreal.MaterialProperty.MP_BASE_COLOR
        ),
        "Could not connect Base Color",
    )

    metallic_expression = unreal.MaterialEditingLibrary.create_material_expression(
        material, unreal.MaterialExpressionScalarParameter, -400, 40
    )
    require(metallic_expression is not None, "Could not create Metallic expression")
    metallic_expression.set_editor_property("parameter_name", "Metallic")
    metallic_expression.set_editor_property("default_value", metallic)
    require(
        unreal.MaterialEditingLibrary.connect_material_property(
            metallic_expression, "", unreal.MaterialProperty.MP_METALLIC
        ),
        "Could not connect Metallic",
    )

    roughness_expression = unreal.MaterialEditingLibrary.create_material_expression(
        material, unreal.MaterialExpressionScalarParameter, -400, 180
    )
    require(roughness_expression is not None, "Could not create Roughness expression")
    roughness_expression.set_editor_property("parameter_name", "Roughness")
    roughness_expression.set_editor_property("default_value", roughness)
    require(
        unreal.MaterialEditingLibrary.connect_material_property(
            roughness_expression, "", unreal.MaterialProperty.MP_ROUGHNESS
        ),
        "Could not connect Roughness",
    )

    unreal.MaterialEditingLibrary.recompile_material(material)
    return base_color, metallic, roughness


def execute_create_material(arguments):
    folder = validate_game_path(arguments["folder"])
    name = validate_name(arguments["name"])
    asset_path = folder + "/" + name
    result_path = asset_path + "." + name
    mode = conflict_mode(arguments)
    exists = unreal.EditorAssetLibrary.does_asset_exist(asset_path)

    if arguments.get("replace_existing", False):
        raise HarnessError("replacement_disabled", "Automatic replacement is disabled for safety: " + asset_path)
    if exists:
        require(mode != "fail", "Material already exists: " + asset_path, "conflict")
        material = unreal.load_asset(asset_path)
        require(isinstance(material, unreal.Material), "Existing asset is not a Material: " + asset_path, "type_mismatch")
        if mode == "reuse":
            return {"asset_path": object_path(material), "created": False, "reused": True, "updated": False}
    else:
        if not unreal.EditorAssetLibrary.does_directory_exist(folder):
            require(
                unreal.EditorAssetLibrary.make_directory(folder),
                "Could not create directory: " + folder,
            )
        material = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            name, folder, unreal.Material, unreal.MaterialFactoryNew()
        )
        require(material is not None, "Could not create Material: " + asset_path)

    base_color, metallic, roughness = configure_simple_material(
        material, arguments, clear_existing=exists and mode == "update"
    )
    require(
        unreal.EditorAssetLibrary.save_loaded_asset(material, False),
        "Could not save Material: " + asset_path,
    )
    mark_changed(material)
    return {
        "asset_path": result_path,
        "base_color": [base_color.r, base_color.g, base_color.b, base_color.a],
        "metallic": metallic,
        "roughness": roughness,
        "created": not exists,
        "reused": False,
        "updated": exists and mode == "update",
    }


def execute_inspect_material(arguments):
    path = validate_game_path(asset_package_path(arguments["material"]))
    material = unreal.load_asset(path)
    require(material is not None, "Material not found: " + path)
    editing = unreal.MaterialEditingLibrary
    result = {
        "path": object_path(material),
        "class": material.get_class().get_path_name(),
        "scalar_parameters": [],
        "vector_parameters": [],
        "static_switch_parameters": [],
    }
    for parameter in editing.get_scalar_parameter_names(material):
        name = str(parameter)
        item = {"name": name}
        if isinstance(material, unreal.MaterialInstanceConstant):
            item["value"] = float(
                editing.get_material_instance_scalar_parameter_value(material, parameter)
            )
        result["scalar_parameters"].append(item)
    for parameter in editing.get_vector_parameter_names(material):
        name = str(parameter)
        item = {"name": name}
        if isinstance(material, unreal.MaterialInstanceConstant):
            color = editing.get_material_instance_vector_parameter_value(material, parameter)
            item["value"] = [color.r, color.g, color.b, color.a]
        result["vector_parameters"].append(item)
    for parameter in editing.get_static_switch_parameter_names(material):
        name = str(parameter)
        item = {"name": name}
        if isinstance(material, unreal.MaterialInstanceConstant):
            item["value"] = bool(
                editing.get_material_instance_static_switch_parameter_value(material, parameter)
            )
        result["static_switch_parameters"].append(item)
    if isinstance(material, unreal.MaterialInstanceConstant):
        result["parent"] = object_path(material.get_editor_property("parent"))
    elif isinstance(material, unreal.Material):
        result["expression_count"] = int(editing.get_num_material_expressions(material))
    return result


def execute_create_material_instance(arguments):
    folder = validate_game_path(arguments["folder"])
    name = validate_name(arguments["name"])
    parent_path = validate_game_path(asset_package_path(arguments["parent"]))
    asset_path = folder + "/" + name
    require(
        not unreal.EditorAssetLibrary.does_asset_exist(asset_path),
        "Material instance already exists: " + asset_path,
    )
    parent = unreal.load_asset(parent_path)
    require(parent is not None, "Parent material not found: " + parent_path)
    if not unreal.EditorAssetLibrary.does_directory_exist(folder):
        require(unreal.EditorAssetLibrary.make_directory(folder), "Could not create directory: " + folder)
    instance = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        name, folder, unreal.MaterialInstanceConstant, unreal.MaterialInstanceConstantFactoryNew()
    )
    require(instance is not None, "Could not create Material Instance: " + asset_path)
    unreal.MaterialEditingLibrary.set_material_instance_parent(instance, parent)
    unreal.EditorAssetLibrary.save_loaded_asset(instance, False)
    mark_changed(instance)
    return {"asset_path": object_path(instance), "parent": object_path(parent)}


def execute_set_material_instance_parameters(arguments):
    path = validate_game_path(asset_package_path(arguments["material_instance"]))
    instance = unreal.load_asset(path)
    require(
        isinstance(instance, unreal.MaterialInstanceConstant),
        "Material Instance Constant not found: " + path,
    )
    editing = unreal.MaterialEditingLibrary
    changed = {"scalar": [], "vector": [], "static_switch": []}
    for name, value in arguments.get("scalar", {}).items():
        editing.set_material_instance_scalar_parameter_value(instance, unreal.Name(str(name)), float(value))
        actual = editing.get_material_instance_scalar_parameter_value(instance, unreal.Name(str(name)))
        require(abs(float(actual) - float(value)) < 0.0001, "Scalar parameter not found: " + str(name))
        changed["scalar"].append(str(name))
    for name, value in arguments.get("vector", {}).items():
        editing.set_material_instance_vector_parameter_value(
            instance, unreal.Name(str(name)), make_linear_color(value, [0.0, 0.0, 0.0, 1.0])
        )
        actual = editing.get_material_instance_vector_parameter_value(instance, unreal.Name(str(name)))
        expected = make_linear_color(value, [0.0, 0.0, 0.0, 1.0])
        require(all(abs(float(getattr(actual, channel)) - float(getattr(expected, channel))) < 0.0001 for channel in ("r", "g", "b", "a")), "Vector parameter not found: " + str(name))
        changed["vector"].append(str(name))
    for name, value in arguments.get("static_switch", {}).items():
        require(
            editing.set_material_instance_static_switch_parameter_value(instance, str(name), bool(value)),
            "Static switch parameter not found: " + str(name),
        )
        changed["static_switch"].append(str(name))
    unreal.EditorAssetLibrary.save_loaded_asset(instance, False)
    mark_changed(instance)
    changed["material_instance"] = object_path(instance)
    return changed


def get_blueprint_components(blueprint):
    subsystem = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
    handles = subsystem.k2_gather_subobject_data_for_blueprint(blueprint)
    require(len(handles) > 0, "Blueprint does not contain subobject data")
    library = unreal.SubobjectDataBlueprintFunctionLibrary
    components = {}
    component_handles = {}
    for handle in handles:
        try:
            data = library.get_data(handle)
            display_name = str(library.get_display_name(data))
            variable_name = str(library.get_variable_name(data))
            component = library.get_object_for_blueprint(data, blueprint)
            if component is not None:
                components[display_name] = component
                components[variable_name] = component
                components[component.get_name()] = component
            component_handles[display_name] = handle
            component_handles[variable_name] = handle
        except Exception:
            pass
    return subsystem, handles[0], components, component_handles


def execute_inspect_blueprint(arguments):
    blueprint_path = validate_game_path(asset_package_path(arguments["blueprint"]))
    blueprint = unreal.load_asset(blueprint_path)
    require(isinstance(blueprint, unreal.Blueprint), "Blueprint not found: " + blueprint_path)
    _, _, components, _ = get_blueprint_components(blueprint)
    unique_components = []
    seen = set()
    for component in components.values():
        identity = object_path(component)
        if identity in seen:
            continue
        seen.add(identity)
        item = {
            "name": component.get_name(),
            "class": component.get_class().get_path_name(),
        }
        if isinstance(component, unreal.SceneComponent):
            item["relative_location"] = vector_values(component.get_editor_property("relative_location"))
            item["relative_rotation"] = rotator_values(component.get_editor_property("relative_rotation"))
            item["relative_scale"] = vector_values(component.get_editor_property("relative_scale3d"))
        if isinstance(component, unreal.MeshComponent):
            item["materials"] = [
                object_path(component.get_material(slot))
                for slot in range(component.get_num_materials())
            ]
        if isinstance(component, unreal.StaticMeshComponent):
            item["static_mesh"] = object_path(component.get_editor_property("static_mesh"))
        unique_components.append(item)
    graphs = []
    parent_class = None
    generated_class = object_path(blueprint.generated_class())
    bridge = getattr(unreal, "UnrealCodexGraphLibrary", None)
    if bridge is not None:
        graph_data = parse_graph_result(bridge.list_graphs(object_path(blueprint)))
        graphs = graph_data["graphs"]
        parent_class = graph_data.get("parent_class")
        generated_class = graph_data.get("generated_class", generated_class)
    return {
        "path": object_path(blueprint),
        "parent_class": parent_class,
        "generated_class": generated_class,
        "components": unique_components,
        "graphs": graphs,
    }


def blueprint_status_name(status):
    """Return the stable Unreal enum member name, without repr formatting."""
    name = getattr(status, "name", None)
    if isinstance(name, str) and name:
        return name.rsplit(".", 1)[-1]
    text = str(status)
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    if ":" in text:
        text = text.split(":", 1)[0]
    return text.strip(" <>'")


def blueprint_compile_diagnostics(blueprint):
    """Return the supported diagnostic shape.

    UE 5.8's Unreal Python BlueprintEditorLibrary compile call exposes no
    compiler-message collection.  Do not scrape editor logs: report the
    limitation while keeping a stable, structured envelope for callers.
    """
    return {
        "warnings": [],
        "errors": [],
        "available": False,
        "limitation": "Unreal Python exposes Blueprint status but not Kismet compiler messages",
    }


def compile_blueprint_checked(blueprint):
    unreal.BlueprintEditorLibrary.compile_blueprint(blueprint)
    status = blueprint.get_editor_property("status")
    compile_status = blueprint_status_name(status)
    diagnostics = blueprint_compile_diagnostics(blueprint)
    require(
        status != unreal.BlueprintStatus.BS_ERROR,
        "Blueprint compilation failed: " + object_path(blueprint),
        "blueprint_compile_failed",
    )
    return compile_status, diagnostics


def execute_compile_blueprint(arguments):
    blueprint_path = validate_game_path(asset_package_path(arguments["blueprint"]))
    blueprint = unreal.load_asset(blueprint_path)
    require(isinstance(blueprint, unreal.Blueprint), "Blueprint not found: " + blueprint_path)
    compile_status, diagnostics = compile_blueprint_checked(blueprint)
    if arguments.get("save", False):
        unreal.EditorAssetLibrary.save_loaded_asset(blueprint, False)
    mark_changed(blueprint)
    return {
        "blueprint": object_path(blueprint),
        "compiled": True,
        "compile_status": compile_status,
        "diagnostics": diagnostics,
        "saved": bool(arguments.get("save", False)),
    }


def add_component(blueprint, subsystem, root_handle, components, component_handles, operation):
    component_name = validate_name(operation["component_name"])
    existing = find_component(components, component_name)
    if existing is not None:
        mode = conflict_mode(operation)
        require(mode != "fail", "Component already exists: " + component_name, "conflict")
        return {
            "operation": "add_component",
            "component": component_name,
            "created": False,
            "reused": True,
        }
    component_class = load_unreal_class(operation["component_type"])
    parent_handle = component_handles.get(operation.get("attach_to"), root_handle)
    params = unreal.AddNewSubobjectParams(
        parent_handle=parent_handle,
        new_class=component_class,
        blueprint_context=blueprint,
    )
    new_handle, fail_reason = subsystem.add_new_subobject(params)
    require(not str(fail_reason).strip(), "Could not add component: " + str(fail_reason))
    require(
        subsystem.rename_subobject(handle=new_handle, new_name=unreal.Text(component_name)),
        "Could not rename component to: " + component_name,
    )
    library = unreal.SubobjectDataBlueprintFunctionLibrary
    component = library.get_object_for_blueprint(library.get_data(new_handle), blueprint)
    require(component is not None, "Could not access created component: " + component_name)
    components[component_name] = component
    component_handles[component_name] = new_handle
    return {
        "operation": "add_component",
        "component": component_name,
        "created": True,
        "reused": False,
    }


def decode_property_value(value):
    if isinstance(value, list):
        return [decode_property_value(item) for item in value]
    if isinstance(value, dict):
        kind = value.get("$type")
        if kind is not None:
            require(set(value) <= {"$type", "path", "name"}, "Typed value has unknown fields", "invalid_typed_value")
            if kind in {"object", "class", "soft_object", "soft_class"}:
                path = value.get("path")
                require(isinstance(path, str) and path, "Typed reference requires a path", "invalid_typed_value")
                if kind == "object":
                    resolved = unreal.load_object(None, path)
                    require(resolved is not None, "Object reference not found: " + path, "reference_not_found")
                    return resolved
                if kind == "class":
                    resolved = unreal.load_class(None, path)
                    require(resolved is not None, "Class reference not found: " + path, "reference_not_found")
                    return resolved
                constructor = getattr(unreal, "SoftObjectPath", None) if kind == "soft_object" else getattr(unreal, "SoftClassPath", None)
                require(constructor is not None, "Soft reference API is unavailable", "api_unavailable")
                return constructor(path)
            if kind == "enum":
                name = value.get("name")
                require(isinstance(name, str) and name, "Typed enum requires a name", "invalid_typed_value")
                return name
            raise HarnessError("invalid_typed_value", "Unsupported typed value: " + str(kind))
        return {str(key): decode_property_value(item) for key, item in value.items()}
    return value


def convert_property_value(property_name, value):
    value = decode_property_value(value)
    asset_properties = {"static_mesh", "skeletal_mesh", "material", "child_actor_class"}
    if property_name in asset_properties and isinstance(value, str) and value.startswith("/"):
        asset = unreal.load_asset(value)
        require(asset is not None, "Could not load asset: " + value)
        return asset
    return value


def find_component(components, component_name):
    component = components.get(component_name)
    if component is not None:
        return component
    normalized = re.sub(r"[^a-z0-9]", "", str(component_name).lower())
    normalized = normalized.removesuffix("genvariable")
    matches = []
    for candidate_name, candidate in components.items():
        candidate_normalized = re.sub(r"[^a-z0-9]", "", str(candidate_name).lower())
        candidate_normalized = candidate_normalized.removesuffix("genvariable")
        if candidate_normalized == normalized and candidate not in matches:
            matches.append(candidate)
    require(
        len(matches) <= 1,
        "Component name is ambiguous: " + component_name,
    )
    return matches[0] if matches else None


def set_component_property(components, operation):
    component_name = operation["component_name"]
    component = find_component(components, component_name)
    require(component is not None, "Component not found: " + component_name)
    property_name = operation["property"]
    component.set_editor_property(
        property_name, convert_property_value(property_name, operation["value"])
    )
    return {
        "operation": "set_component_property",
        "component": component_name,
        "property": property_name,
    }


def set_component_material(components, operation):
    component_name = operation["component_name"]
    component = find_component(components, component_name)
    require(component is not None, "Component not found: " + component_name)
    slot = int(operation.get("slot", 0))
    require(slot >= 0, "Material slot must be zero or greater")
    material_path = operation["material"]
    material = unreal.load_asset(material_path)
    require(material is not None, "Could not load material: " + material_path)
    component.set_material(slot, material)
    return {
        "operation": "set_component_material",
        "component": component_name,
        "slot": slot,
        "material": material_path,
    }


def set_component_transform(components, operation):
    component_name = operation["component_name"]
    component = find_component(components, component_name)
    require(component is not None, "Component not found: " + component_name)
    component.set_editor_property(
        "relative_location", make_vector(operation.get("location"), [0.0, 0.0, 0.0])
    )
    component.set_editor_property("relative_rotation", make_rotator(operation.get("rotation")))
    component.set_editor_property(
        "relative_scale3d", make_vector(operation.get("scale"), [1.0, 1.0, 1.0])
    )
    return {"operation": "set_component_transform", "component": component_name}


def add_variable(blueprint, operation):
    name = operation.get("name")
    require(isinstance(name, str) and name, "Variable name must be a non-empty string")
    variable_type = operation.get("variable_type", "bool")
    require(isinstance(variable_type, str), "Variable type must be a string")
    basic_types = {"bool", "byte", "int", "int64", "float", "double", "string", "name", "text"}
    normalized_type = variable_type.strip().lower()
    require(normalized_type in basic_types, "Unsupported Blueprint variable type: " + variable_type)
    pin_type = unreal.BlueprintEditorLibrary.get_basic_type_by_name(normalized_type)
    require(pin_type is not None, "Could not resolve Blueprint variable type: " + variable_type)
    added = unreal.BlueprintEditorLibrary.add_member_variable(blueprint, name, pin_type)
    require(added, "Failed to add Blueprint variable: " + name)
    return {"operation": "add_variable", "name": name, "variable_type": normalized_type}


def set_class_property(blueprint, operation):
    generated_class = blueprint.generated_class()
    require(generated_class is not None, "Blueprint does not have a generated class")
    property_name = operation["property"]
    unreal.get_default_object(generated_class).set_editor_property(
        property_name, operation["value"]
    )
    return {"operation": "set_class_property", "property": property_name}


def execute_edit_blueprint(arguments):
    blueprint_path = asset_package_path(arguments["blueprint"])
    blueprint = unreal.load_asset(blueprint_path)
    require(blueprint is not None, "Blueprint not found: " + blueprint_path)
    subsystem, root_handle, components, component_handles = get_blueprint_components(blueprint)
    operation_results = []

    for operation in arguments.get("operations", []):
        operation = dict(operation)
        operation.setdefault("conflict_mode", arguments.get("conflict_mode", "fail"))
        name = operation.get("operation")
        if name == "add_component":
            result = add_component(
                blueprint, subsystem, root_handle, components, component_handles, operation
            )
        elif name == "set_component_property":
            result = set_component_property(components, operation)
        elif name == "set_component_material":
            result = set_component_material(components, operation)
        elif name == "set_component_transform":
            result = set_component_transform(components, operation)
        elif name == "set_class_property":
            result = set_class_property(blueprint, operation)
        elif name == "add_variable":
            result = add_variable(blueprint, operation)
        else:
            raise RuntimeError("Unsupported Blueprint operation: " + str(name))
        operation_results.append(result)

    compile_status = None
    diagnostics = None
    if arguments.get("compile", True):
        compile_status, diagnostics = compile_blueprint_checked(blueprint)
    if arguments.get("save", True):
        unreal.EditorAssetLibrary.save_loaded_asset(blueprint, False)
    mark_changed(blueprint)
    return {
        "compile_diagnostics": diagnostics,
        "blueprint": blueprint_path,
        "operations": operation_results,
        "compiled": arguments.get("compile", True),
        "compile_status": compile_status,
        "saved": arguments.get("save", True),
    }


def execute_spawn_actor(arguments):
    level_path = arguments.get("level", "current")
    if level_path != "current":
        level_path = validate_game_path(level_path)
        loaded = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).load_level(level_path)
        require(loaded, "Could not load level: " + level_path)

    class_path = arguments["class"]
    actor_class = load_unreal_class(class_path)
    transform = arguments.get("transform", {})
    location = make_vector(transform.get("location"), [0.0, 0.0, 0.0])
    rotation = make_rotator(transform.get("rotation"))
    scale = make_vector(transform.get("scale"), [1.0, 1.0, 1.0])
    actor_label = arguments.get("actor_label")
    actor_name = arguments.get("actor_name")
    mode = arguments.get("conflict_mode")
    if mode is not None and (actor_label or actor_name):
        mode = conflict_mode(arguments)
        actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
        matches = [
            candidate for candidate in actors
            if (actor_name and candidate.get_name() == actor_name)
            or (actor_label and candidate.get_actor_label() == actor_label)
        ]
        require(len(matches) <= 1, "Actor reference is ambiguous: " + str(actor_name or actor_label), "conflict")
        if matches:
            require(mode != "fail", "Actor already exists: " + str(actor_name or actor_label), "conflict")
            actor = matches[0]
            require(actor.get_class().get_path_name() == class_path, "Existing actor class does not match: " + class_path, "type_mismatch")
            if mode == "update":
                actor.set_actor_location_and_rotation(location, rotation, False, False)
                actor.set_actor_scale3d(scale)
                mark_changed(actor)
            return {
                "actor_name": actor.get_name(),
                "actor_label": actor.get_actor_label(),
                "class": class_path,
                "created": False,
                "reused": True,
                "updated": mode == "update",
            }
    actor = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).spawn_actor_from_class(
        actor_class, location, rotation
    )
    require(actor is not None, "Could not spawn actor")
    actor.set_actor_scale3d(scale)
    actor.set_actor_label(
        arguments.get("actor_label", arguments.get("actor_name", actor.get_name()))
    )
    mark_changed(actor)
    return {
        "actor_name": actor.get_name(),
        "actor_label": actor.get_actor_label(),
        "class": class_path,
        "created": True,
        "reused": False,
        "updated": False,
    }


def actor_record(actor):
    return {
        "name": actor.get_name(),
        "label": actor.get_actor_label(),
        "class": actor.get_class().get_path_name(),
        "path": actor.get_path_name(),
        "location": vector_values(actor.get_actor_location()),
        "rotation": rotator_values(actor.get_actor_rotation()),
        "scale": vector_values(actor.get_actor_scale3d()),
        "hidden": bool(actor.is_hidden_ed()),
    }


def execute_inspect_level(arguments):
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
    query = str(arguments.get("query", "")).lower().strip()
    class_path = arguments.get("class")
    selected_only = bool(arguments.get("selected_only", False))
    if selected_only:
        selected = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_selected_level_actors()
        selected_paths = {actor.get_path_name() for actor in selected}
        actors = [actor for actor in actors if actor.get_path_name() in selected_paths]
    if query:
        actors = [
            actor for actor in actors
            if query in actor.get_name().lower() or query in actor.get_actor_label().lower()
        ]
    if class_path:
        actors = [actor for actor in actors if actor.get_class().get_path_name() == class_path]
    limit = int(arguments.get("limit", 500))
    require(1 <= limit <= 5000, "limit must be between 1 and 5000")
    total = len(actors)
    return {
        "total": total,
        "truncated": total > limit,
        "actors": [actor_record(actor) for actor in actors[:limit]],
    }


def find_level_actor(arguments):
    name = arguments.get("actor_name")
    label = arguments.get("actor_label")
    require(name or label, "Provide actor_name or actor_label")
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
    matches = [
        actor for actor in actors
        if (name and actor.get_name() == name) or (label and actor.get_actor_label() == label)
    ]
    require(matches, "Actor not found: " + str(name or label))
    require(len(matches) == 1, "Actor reference is ambiguous: " + str(name or label))
    return matches[0]


def execute_set_actor_transform(arguments):
    actor = find_level_actor(arguments)
    transform = arguments.get("transform", {})
    location = make_vector(transform.get("location"), vector_values(actor.get_actor_location()))
    rotation = make_rotator(transform.get("rotation", rotator_values(actor.get_actor_rotation())))
    scale = make_vector(transform.get("scale"), vector_values(actor.get_actor_scale3d()))
    actor.set_actor_location_and_rotation(location, rotation, False, False)
    actor.set_actor_scale3d(scale)
    mark_changed(actor)
    return actor_record(actor)


def execute_set_actor_property(arguments):
    actor = find_level_actor(arguments)
    property_name = validate_name(arguments["property"])
    actor.set_editor_property(property_name, arguments["value"])
    mark_changed(actor)
    return {"actor": actor.get_path_name(), "property": property_name}


def execute_save_project(arguments):
    if arguments.get("save_level", True):
        unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).save_current_level()
    if arguments.get("save_assets", True):
        unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
    return {
        "level_saved": arguments.get("save_level", True),
        "assets_saved": arguments.get("save_assets", True),
    }


GRAPH_NODE_ALIASES = {}


def graph_bridge():
    bridge = getattr(unreal, "UnrealCodexGraphLibrary", None)
    require(
        bridge is not None,
        "UnrealCodexGraph plugin is not loaded. Install it, enable it, and restart Unreal Editor.",
    )
    return bridge


def parse_graph_result(raw_result):
    try:
        result = json.loads(str(raw_result))
    except (TypeError, ValueError) as error:
        raise RuntimeError("Invalid response from UnrealCodexGraph: " + str(error))
    require(isinstance(result, dict), "UnrealCodexGraph response must be an object")
    require(result.get("success") is True, result.get("error", "UnrealCodexGraph operation failed"))
    return result


def graph_alias_key(blueprint_path, graph_name, node_id):
    return (blueprint_path, graph_name, node_id)


def resolve_graph_node(blueprint_path, graph_name, node_reference):
    require(isinstance(node_reference, str) and node_reference, "Node reference must be a string")
    return GRAPH_NODE_ALIASES.get(
        graph_alias_key(blueprint_path, graph_name, node_reference),
        node_reference,
    )


def graph_position(arguments):
    position = arguments.get("position", [0.0, 0.0])
    require(isinstance(position, list) and len(position) == 2, "Position must contain [x, y]")
    return unreal.Vector2D(float(position[0]), float(position[1]))


def execute_system_capabilities(arguments):
    bridge = getattr(unreal, "UnrealCodexGraphLibrary", None)
    data = {
        "harness_version": HARNESS_VERSION,
        "engine_version": str(unreal.SystemLibrary.get_engine_version()),
        "graph_bridge_available": bridge is not None,
        "actions": sorted(ACTION_HANDLERS),
    }
    if bridge is not None:
        data["graph_bridge"] = parse_graph_result(bridge.get_capabilities())
    return data


def execute_describe_actions(arguments):
    return {
        "harness_version": HARNESS_VERSION,
        "actions": [
            {"name": name, "mutating": name in MUTATING_ACTIONS}
            for name in sorted(ACTION_HANDLERS)
        ],
        "document_options": {
            "dry_run": "Validate and return the expanded execution plan without running commands",
            "recipes": "Expand declarative $repeat, $mirror, and $grid patterns before validation",
            "conflict_modes": sorted(CONFLICT_MODES),
        },
    }


def execute_graph_inspect(arguments):
    return parse_graph_result(
        graph_bridge().inspect_graph(arguments["blueprint"], arguments.get("graph", "EventGraph"))
    )
def execute_graph_describe(arguments):
    return parse_graph_result(
        graph_bridge().describe_graph(arguments["blueprint"], arguments.get("graph", "EventGraph"))
    )


def execute_graph_search_nodes(arguments):
    require(isinstance(arguments.get("query", ""), str), "query must be a string")
    return parse_graph_result(
        graph_bridge().search_node_specs(
            arguments["blueprint"], arguments.get("graph", "EventGraph"), arguments.get("query", "")
        )
    )


def execute_graph_describe_node(arguments):
    require(isinstance(arguments.get("kind"), str) and arguments["kind"], "kind must be a non-empty string")
    return parse_graph_result(graph_bridge().describe_node_spec(arguments["kind"]))




def execute_graph_add_node(arguments):
    blueprint_path = arguments["blueprint"]
    graph_name = arguments.get("graph", "EventGraph")
    node_id = arguments["node_id"]
    node = arguments["node"]
    kind = node.get("kind")
    position = graph_position(arguments)
    require(isinstance(kind, str) and kind, "node.kind must be a non-empty string")
    raw_result = graph_bridge().add_node(
        blueprint_path,
        graph_name,
        kind,
        json.dumps(node, ensure_ascii=False),
        position,
    )
    result = parse_graph_result(raw_result)
    GRAPH_NODE_ALIASES[graph_alias_key(blueprint_path, graph_name, node_id)] = result["node_guid"]
    mark_changed(blueprint_path)
    result["node_id"] = node_id
    return result


def execute_graph_connect(arguments):
    blueprint_path = arguments["blueprint"]
    graph_name = arguments.get("graph", "EventGraph")
    source = arguments["from"]
    target = arguments["to"]
    result = parse_graph_result(
        graph_bridge().connect_pins(
            blueprint_path,
            graph_name,
            resolve_graph_node(blueprint_path, graph_name, source["node"]),
            source["pin"],
            resolve_graph_node(blueprint_path, graph_name, target["node"]),
            target["pin"],
        )
    )
    mark_changed(blueprint_path)
    return result


def execute_graph_set_pin_value(arguments):
    blueprint_path = arguments["blueprint"]
    graph_name = arguments.get("graph", "EventGraph")
    result = parse_graph_result(
        graph_bridge().set_pin_default_value(
            blueprint_path,
            graph_name,
            resolve_graph_node(blueprint_path, graph_name, arguments["node"]),
            arguments["pin"],
            str(arguments["value"]),
        )
    )
    mark_changed(blueprint_path)
    return result

def execute_graph_remove_node(arguments):
    blueprint_path = arguments["blueprint"]
    graph_name = arguments.get("graph", "EventGraph")
    result = parse_graph_result(graph_bridge().remove_node(
        blueprint_path, graph_name,
        resolve_graph_node(blueprint_path, graph_name, arguments.get("node", arguments.get("node_id")))
    ))
    mark_changed(blueprint_path)
    return result


def execute_graph_disconnect(arguments):
    blueprint_path = arguments["blueprint"]
    graph_name = arguments.get("graph", "EventGraph")
    source = arguments.get("from", {})
    target = arguments.get("to", {})
    all_links = arguments.get("all", False)
    require(isinstance(all_links, bool), "all must be a boolean")
    node = arguments.get("node")
    if all_links:
        require(isinstance(node, str), "node is required when all is true")
        from_node = resolve_graph_node(blueprint_path, graph_name, node)
        from_pin = arguments["pin"]
        to_node, to_pin = "", ""
    else:
        from_node = resolve_graph_node(blueprint_path, graph_name, source["node"])
        from_pin = source["pin"]
        to_node = resolve_graph_node(blueprint_path, graph_name, target["node"])
        to_pin = target["pin"]
    result = parse_graph_result(graph_bridge().disconnect_pins(
        blueprint_path, graph_name, from_node, from_pin, to_node, to_pin, all_links
    ))
    mark_changed(blueprint_path)
    return result


def execute_backend_capabilities(arguments):
    return {"backends": BACKEND_REGISTRY.capabilities()}


def execute_backend_describe(arguments):
    backend_id = arguments.get("backend")
    require(isinstance(backend_id, str) and backend_id, "backend must be a non-empty string", "invalid_backend")
    return BACKEND_REGISTRY.describe(backend_id)


def execute_backend_operation(arguments):
    backend_id = arguments.get("backend")
    operation = arguments.get("operation")
    require(isinstance(backend_id, str) and backend_id, "backend must be a non-empty string", "invalid_backend")
    require(operation in BackendRegistry.OPERATIONS[2:], "Unsupported backend operation: " + str(operation), "unsupported_backend_operation")
    payload = arguments.get("payload", {})
    require(isinstance(payload, dict), "payload must be an object", "invalid_backend")
    return BACKEND_REGISTRY.invoke(backend_id, operation, payload)
ACTION_HANDLERS = {
    "system.capabilities": execute_system_capabilities,
    "system.describe_actions": execute_describe_actions,
    "object.describe": execute_object_describe,
    "object.describe_functions": execute_object_describe_functions,
    "object.call": execute_object_call,
    "object.inspect": execute_object_inspect,
    "object.set": execute_object_set,
    "content.create_folder": execute_create_folder,
    "content.list": execute_list_content,
    "asset.inspect": execute_inspect_asset,
    "asset.search": execute_asset_search,
    "asset.describe_types": execute_asset_describe_types,
    "asset.create": execute_asset_create,
    "asset.duplicate": execute_asset_duplicate,
    "asset.save": execute_asset_save,
    "material.create": execute_create_material,
    "material.inspect": execute_inspect_material,
    "material_instance.create": execute_create_material_instance,
    "material_instance.set_parameters": execute_set_material_instance_parameters,
    "blueprint.create": execute_create_blueprint,
    "blueprint.inspect": execute_inspect_blueprint,
    "graph.describe": execute_graph_describe,
    "backend.capabilities": execute_backend_capabilities,
    "backend.describe": execute_backend_describe,
    "backend.operation": execute_backend_operation,
    "graph.search_nodes": execute_graph_search_nodes,
    "graph.describe_node": execute_graph_describe_node,
    "blueprint.compile": execute_compile_blueprint,
    "blueprint.edit": execute_edit_blueprint,
    "blueprint.graph.inspect": execute_graph_inspect,
    "blueprint.graph.add_node": execute_graph_add_node,
    "blueprint.graph.connect": execute_graph_connect,
    "blueprint.graph.set_pin_value": execute_graph_set_pin_value,
    "blueprint.graph.remove_node": execute_graph_remove_node,
    "blueprint.graph.disconnect": execute_graph_disconnect,
    "level.inspect": execute_inspect_level,
    "level.spawn_actor": execute_spawn_actor,
    "level.set_actor_transform": execute_set_actor_transform,
    "level.set_actor_property": execute_set_actor_property,
    "project.save": execute_save_project,
    "recipe.validate": execute_recipe_validate,
    "recipe.execute": execute_recipe_execute,
}

MUTATING_ACTIONS = {
    "object.set",
    "object.call",
    "content.create_folder",
    "asset.create",
    "material.create",
    "material_instance.create",
    "material_instance.set_parameters",
    "blueprint.create",
    "blueprint.compile",
    "blueprint.edit",
    "blueprint.graph.add_node",
    "blueprint.graph.connect",
    "blueprint.graph.set_pin_value",
    "blueprint.graph.remove_node",
    "blueprint.graph.disconnect",
    "asset.duplicate",
    "asset.save",
    "level.spawn_actor",
    "level.set_actor_transform",
    "level.set_actor_property",
    "project.save",
}


def validate_document(document):
    require(isinstance(document, dict), "Document root must be an object", "invalid_document")
    require(document.get("format_version") == "1.0", "Unsupported format_version", "unsupported_format")
    commands = document.get("commands")
    require(isinstance(commands, list), "'commands' must be an array", "invalid_document")
    require(len(commands) <= MAX_COMMANDS, "Too many commands; maximum is " + str(MAX_COMMANDS), "too_many_commands")
    seen = set()
    for index, command in enumerate(commands):
        require(isinstance(command, dict), "Command at index {} must be an object".format(index), "invalid_command")
        command_id = command.get("id")
        action = command.get("action")
        require(isinstance(command_id, str) and command_id, "Command id must be a non-empty string", "invalid_command")
        require(command_id not in seen, "Duplicate command id: " + command_id, "duplicate_command_id")
        require(action in ACTION_HANDLERS, "Unsupported action: " + str(action), "unsupported_action")
        require(isinstance(command.get("arguments", {}), dict), "arguments must be an object: " + command_id, "invalid_arguments")
        dependencies = command.get("depends_on", [])
        require(isinstance(dependencies, list) and all(isinstance(item, str) for item in dependencies), "depends_on must be an array of strings", "invalid_dependencies")
        missing = [item for item in dependencies if item not in seen]
        require(not missing, "Dependencies must refer to earlier commands: " + ", ".join(missing), "invalid_dependencies")
        if "condition" in command:
            require(isinstance(command["condition"], dict), "condition must be an object", "invalid_condition")
        seen.add(command_id)
    return commands


def execute_command(command):
    global CURRENT_CHANGED_OBJECTS, RESULT_CONTEXT
    CURRENT_CHANGED_OBJECTS = []
    command_id = command.get("id")
    action = command.get("action")
    require(command_id, "Command does not contain an id")
    require(action, "Command does not contain an action")
    if command.get("condition") is not None and not evaluate_condition(command["condition"]):
        result = {"id": command_id, "action": action, "success": True, "skipped": True, "skip_reason": "condition_false", "data": {}, "changed_objects": []}
        RESULT_CONTEXT[command_id] = result
        return result
    handler = ACTION_HANDLERS.get(action)
    require(handler is not None, "Unsupported action: " + action)
    arguments = resolve_result_reference(command.get("arguments", {}))
    log("Executing: " + command_id + " -> " + action)
    if action in MUTATING_ACTIONS and ACTIVE_RECIPE_TRANSACTION is None:
        transaction = unreal.ScopedEditorTransaction("Codex: " + action)
        try:
            data = handler(arguments)
        except Exception:
            transaction.cancel()
            raise
        finally:
            del transaction
    else:
        data = handler(arguments)
    result = {"id": command_id, "action": action, "success": True, "data": data, "changed_objects": list(CURRENT_CHANGED_OBJECTS)}
    if command.get("recipe_id"):
        result["recipe_id"] = command["recipe_id"]
    RESULT_CONTEXT[command_id] = result
    return result


def main():
    global RESULT_CONTEXT
    RESULT_CONTEXT = {}
    result = {
        "format_version": "1.0",
        "harness_version": HARNESS_VERSION,
        "run_id": str(uuid.uuid4()),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "success": True,
        "commands": [],
        "changed_objects": [],
        "recipes": [],
        "errors": [],
    }
    try:
        document = load_json_file(ACTIONS_FILE)
        if isinstance(document, dict) and isinstance(document.get("run_id"), str):
            result["run_id"] = document["run_id"]
        document, recipe_summaries = expand_document_recipes(document)
        result["recipes"] = recipe_summaries
        commands = validate_document(document)
        if document.get("dry_run", False):
            result["dry_run"] = True
            result["plan"] = [
                {
                    "id": command["id"],
                    "action": command["action"],
                    "mutating": command["action"] in MUTATING_ACTIONS,
                    "depends_on": command.get("depends_on", []),
                    "recipe_id": command.get("recipe_id"),
                    "arguments": command.get("arguments", {}),
                }
                for command in commands
            ]
            commands = []
        command_status = {}
        for command in commands:
            command_id = command.get("id", "<missing id>")
            failed_dependencies = [
                dependency
                for dependency in command.get("depends_on", [])
                if command_status.get(dependency) is not True
            ]
            if failed_dependencies:
                dependency_error = HarnessError(
                    "dependency_failed",
                    "Failed dependencies: " + ", ".join(failed_dependencies),
                )
                result["commands"].append(
                    {
                        "id": command_id,
                        "action": command.get("action"),
                        "recipe_id": command.get("recipe_id"),
                        "success": False,
                        "skipped": True,
                        "error": str(dependency_error),
                        "error_code": dependency_error.code,
                        "changed_objects": [],
                    }
                )
                command_status[command_id] = False
                result["success"] = False
                continue
            try:
                command_result = execute_command(command)
                result["commands"].append(command_result)
                merge_changed(result["changed_objects"], command_result["changed_objects"])
                command_status[command_id] = True
            except Exception as error:
                error_text = str(error)
                log_error(command_id + ": " + error_text)
                error_details = getattr(error, "details", {})
                result_command = {
                    "id": command_id,
                    "action": command.get("action"),
                    "recipe_id": command.get("recipe_id") or error_details.get("recipe_id"),
                    "success": False,
                    "error": error_text,
                    "error_code": getattr(error, "code", "execution_failed"),
                    "changed_objects": list(CURRENT_CHANGED_OBJECTS),
                }
                result["commands"].append(result_command)
                merge_changed(result["changed_objects"], CURRENT_CHANGED_OBJECTS)
                result["errors"].append(error_record(error, command_id, True))
                command_status[command_id] = False
                result["success"] = False
    except Exception as error:
        result["success"] = False
        result["errors"].append(error_record(error, None, True))
        log_error(str(error))

    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    save_json_file(RESULT_FILE, result)
    if result["success"]:
        log("All commands completed successfully")
    else:
        log_error("Some commands failed")
    log("Result file: " + RESULT_FILE)


main()
