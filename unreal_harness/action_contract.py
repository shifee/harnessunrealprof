"""Host-side validation derived from the Unreal executor action registry.

This fallback is kept in sync with ACTION_HANDLERS and MUTATING_ACTIONS in
Content/Python/execute_actions.py. Unreal's reflected runtime remains the final
authority for object properties, backend capabilities, and engine-specific types.
"""
from __future__ import annotations

from typing import Any

MAX_COMMANDS = 200

# Required fields mirror direct indexing and explicit requirements in handlers.
# Unknown keys are rejected where the host contract is sufficiently knowable;
# open-ended reflected values and recipe/backend payloads remain opaque.
ACTION_CONTRACT = {
    "system.capabilities": (False, {}, True), "system.describe_actions": (False, {}, True),
    "object.describe": (False, {"object": "str"}, True), "object.describe_functions": (False, {"object": "str"}, True),
    "object.call": (True, {"object": "str", "function": "str"}, False), "object.inspect": (False, {"object": "str", "properties": "list"}, True),
    "object.set": (False, {"object": "str", "properties": "dict"}, False), "content.create_folder": (False, {"path": "str"}, False),
    "content.list": (False, {}, True), "asset.inspect": (False, {"asset": "str"}, True), "asset.search": (False, {}, True),
    "asset.describe_types": (False, {}, True), "asset.create": (False, {"folder": "str", "name": "str"}, False),
    "asset.duplicate": (False, {"source": "str", "destination": "str"}, False), "asset.save": (False, {}, False),
    "material.create": (False, {"folder": "str", "name": "str"}, False), "material.inspect": (False, {"material": "str"}, True),
    "material_instance.create": (False, {"folder": "str", "name": "str", "parent": "str"}, False),
    "material_instance.set_parameters": (False, {"material_instance": "str"}, False), "blueprint.create": (False, {"folder": "str", "name": "str", "parent_class": "str"}, False),
    "blueprint.inspect": (False, {"blueprint": "str"}, True), "graph.describe": (False, {"blueprint": "str"}, True),
    "backend.capabilities": (False, {}, True), "backend.describe": (False, {"backend": "str"}, True),
    "backend.operation": (False, {"backend": "str", "operation": "str"}, False), "graph.search_nodes": (False, {"blueprint": "str"}, True),
    "graph.describe_node": (False, {"kind": "str"}, True), "blueprint.compile": (False, {"blueprint": "str"}, False),
    "blueprint.edit": (False, {"blueprint": "str", "operations": "list"}, False), "blueprint.graph.inspect": (False, {"blueprint": "str"}, True),
    "blueprint.graph.add_node": (False, {"blueprint": "str", "node_id": "str", "node": "dict"}, False),
    "blueprint.graph.connect": (False, {"blueprint": "str", "from": "dict", "to": "dict"}, False),
    "blueprint.graph.set_pin_value": (False, {"blueprint": "str", "node": "str", "pin": "str"}, False),
    "blueprint.graph.remove_node": (False, {"blueprint": "str"}, False), "blueprint.graph.disconnect": (False, {"blueprint": "str"}, False),
    "level.inspect": (False, {}, True), "level.spawn_actor": (False, {"class": "str"}, False),
    "level.set_actor_transform": (False, {}, False), "level.set_actor_property": (False, {"property": "str", "value": "any"}, False),
    "project.save": (False, {}, False), "recipe.validate": (False, {"recipe": "dict"}, True), "recipe.execute": (False, {"recipe": "dict"}, False),
}

ACTION_ARGUMENTS = {
    "system.capabilities": (), "system.describe_actions": (), "object.describe": ("object", "limit"), "object.describe_functions": ("object", "limit"),
    "object.call": ("object", "function", "arguments"), "object.inspect": ("object", "properties", "depth"), "object.set": ("object", "properties"),
    "content.create_folder": ("path", "conflict_mode"), "content.list": ("path", "recursive", "limit", "query"), "asset.inspect": ("asset",),
    "asset.search": ("path", "query", "class", "limit", "offset", "recursive"), "asset.describe_types": ("limit", "offset"),
    "asset.create": ("folder", "name", "conflict_mode", "class", "asset_class", "factory", "replace_existing"),
    "asset.duplicate": ("source", "destination"), "asset.save": ("assets", "asset_paths", "packages", "package_paths"),
    "material.create": ("folder", "name", "conflict_mode", "base_color", "metallic", "roughness", "replace_existing"), "material.inspect": ("material",),
    "material_instance.create": ("folder", "name", "parent"), "material_instance.set_parameters": ("material_instance", "scalar", "vector", "static_switch"),
    "blueprint.create": ("folder", "name", "parent_class", "conflict_mode", "replace_existing"), "blueprint.inspect": ("blueprint",), "graph.describe": ("blueprint", "graph"),
    "backend.capabilities": (), "backend.describe": ("backend",), "backend.operation": ("backend", "operation", "payload"),
    "graph.search_nodes": ("blueprint", "graph", "query"), "graph.describe_node": ("kind",), "blueprint.compile": ("blueprint", "save"),
    "blueprint.edit": ("blueprint", "operations", "compile", "save", "conflict_mode"), "blueprint.graph.inspect": ("blueprint", "graph"),
    "blueprint.graph.add_node": ("blueprint", "graph", "node_id", "node", "position"), "blueprint.graph.connect": ("blueprint", "graph", "from", "to"),
    "blueprint.graph.set_pin_value": ("blueprint", "graph", "node", "pin", "value"), "blueprint.graph.remove_node": ("blueprint", "graph", "node", "node_id"),
    "blueprint.graph.disconnect": ("blueprint", "graph", "from", "to", "all", "node", "pin"), "level.inspect": ("query", "class", "selected_only", "limit"),
    "level.spawn_actor": ("level", "class", "transform", "actor_label", "actor_name", "conflict_mode"), "level.set_actor_transform": ("actor_name", "actor_label", "transform"),
    "level.set_actor_property": ("actor_name", "actor_label", "property", "value"), "project.save": ("save_level", "save_assets"),
    "recipe.validate": ("recipe",), "recipe.execute": ("recipe",),
}

# Stable metadata surface for planners, documentation generators, and tests.
# The Unreal registry remains authoritative for runtime-only reflected details.
def _example_value(kind: str) -> Any:
    return {"str": "<value>", "list": [], "dict": {}, "any": None}.get(kind, None)

ACTION_METADATA = {
    action: {
        "name": action,
        "required": dict(required),
        "optional": [key for key in ACTION_ARGUMENTS[action] if key not in required],
        "types": {
            key: required.get(key, "any")
            for key in ACTION_ARGUMENTS[action]
        },
        "mutates": not read_only,
        "example": {"id": action.replace(".", "_"), "action": action, "arguments": {key: _example_value(kind) for key, kind in required.items()}},
    }
    for action, (_, required, read_only) in ACTION_CONTRACT.items()
}

def describe_contract() -> list[dict[str, Any]]:
    """Return JSON-serializable action metadata for planner context."""
    return [dict(ACTION_METADATA[name]) for name in sorted(ACTION_METADATA)]
class ActionValidationError(ValueError):
    """Invalid action document; carries a stable machine-readable code."""
    def __init__(self, message: str, code: str = "validation_error"):
        super().__init__(message)
        self.code = code


def validate_document(document: Any) -> dict:
    if not isinstance(document, dict):
        raise ActionValidationError("Document root must be an object", "invalid_document")
    if document.get("format_version", "1.0") != "1.0":
        raise ActionValidationError("Unsupported format_version", "unsupported_format")
    commands = document.get("commands")
    if not isinstance(commands, list):
        raise ActionValidationError("'commands' must be an array", "invalid_document")
    if len(commands) > MAX_COMMANDS:
        raise ActionValidationError("Too many commands; maximum is 200", "too_many_commands")
    seen = set()
    mutating_actions = []
    allowed_command_fields = {"id", "action", "arguments", "depends_on", "condition", "recipe_id", "conflict_mode"}
    for i, command in enumerate(commands):
        if not isinstance(command, dict):
            raise ActionValidationError(f"Command at index {i} must be an object", "invalid_command")
        if set(command) - allowed_command_fields:
            raise ActionValidationError("Unknown command fields", "invalid_command")
        ident, action = command.get("id"), command.get("action")
        if not isinstance(ident, str) or not ident:
            raise ActionValidationError("Command id must be a non-empty string", "invalid_command")
        if ident in seen:
            raise ActionValidationError("Duplicate command id: " + ident, "duplicate_command_id")
        if action not in ACTION_CONTRACT:
            raise ActionValidationError("Unsupported action: " + str(action), "unsupported_action")
        args = command.get("arguments", {})
        if not isinstance(args, dict):
            raise ActionValidationError("arguments must be an object: " + ident, "invalid_arguments")
        allowed_arguments = set(ACTION_ARGUMENTS[action]) | {"conflict_mode"}
        if set(args) - allowed_arguments:
            raise ActionValidationError("Unknown arguments for " + action, "invalid_arguments")
        _, required, read_only = ACTION_CONTRACT[action]
        for key, kind in required.items():
            if key not in args:
                raise ActionValidationError(f"{action} requires argument {key}", "missing_argument")
            value = args[key]
            valid = (kind == "any" or (kind == "str" and isinstance(value, str) and bool(value)) or
                     (kind == "dict" and isinstance(value, dict)) or (kind == "list" and isinstance(value, list)))
            if not valid:
                raise ActionValidationError(f"{action}.{key} must be {kind}", "invalid_arguments")
        if action == "asset.save" and not any(key in args for key in ("assets", "asset_paths", "packages", "package_paths")):
            raise ActionValidationError("asset.save requires assets or packages", "missing_argument")
        dependencies = command.get("depends_on", [])
        if not isinstance(dependencies, list) or any(not isinstance(x, str) for x in dependencies):
            raise ActionValidationError("depends_on must be an array of strings", "invalid_dependencies")
        if any(x not in seen for x in dependencies):
            raise ActionValidationError("Dependencies must refer to earlier commands", "invalid_dependencies")
        if "condition" in command and not isinstance(command["condition"], dict):
            raise ActionValidationError("condition must be an object", "invalid_condition")
        if not read_only:
            mutating_actions.append(action)
        seen.add(ident)
    if not any(action != "project.save" for action in mutating_actions) and "project.save" in mutating_actions:
        raise ActionValidationError("Read-only plans must not save the project", "read_only_save")
    return document
