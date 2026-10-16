"""Read-back checks for common mutations, with explicit coverage limitations."""
import math
from .action_contract import is_mutating


def equivalent(actual, expected):
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return isinstance(actual, (int, float)) and not isinstance(actual, bool) and math.isclose(actual, expected, abs_tol=0.001, rel_tol=0.0001)
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(equivalent(a, e) for a, e in zip(actual, expected))
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and equivalent(actual[k], v) for k, v in expected.items())
    return type(actual) is type(expected) and actual == expected


def build_verification(plan, executed):
    """Generate probes from actual execution targets, never guessed model paths."""
    commands, checks, uncovered = [], [], []
    pairs = []
    results = {r.get("id"): r for r in executed.get("commands", [])}
    def resolve(value):
        if isinstance(value, dict) and set(value) == {"$ref"}:
            parts = value["$ref"].split(".")
            current = results[parts[0]]
            for part in parts[1:]:
                current = current[part]
            return current
        if isinstance(value, dict):
            return {key: resolve(item) for key, item in value.items()}
        if isinstance(value, list):
            return [resolve(item) for item in value]
        return value
    for command in plan.get("commands", []):
        result = results.get(command["id"], {})
        if result.get("skipped"):
            if is_mutating(command):
                uncovered.append(command["id"] + ": mutation was skipped")
            continue
        if not is_mutating(command):
            continue
        if result.get("success") is not True:
            uncovered.append(command["id"] + ": missing successful execution result")
            continue
        if command["action"] in {"recipe.execute", "backend.operation", "object.call"}:
            uncovered.append(command["id"] + ": requires an explicit task-specific verification")
            continue
        try:
            command = dict(command, arguments=resolve(command.get("arguments", {})))
        except (KeyError, TypeError):
            uncovered.append(command["id"] + ": cannot resolve execution result references")
            continue
        pairs.append((command, result.get("data", {})))
    # Compare final state after repeated writes, not intermediate expected values.
    merged = []
    targets = {}
    for command, data in pairs:
        name, args = command["action"], command.get("arguments", {})
        target = args.get("object") if name == "object.set" else data.get("name", data.get("actor_name")) if name in {"level.spawn_actor", "level.set_actor_transform"} else args.get("blueprint") if name == "blueprint.edit" else args.get("material_instance") if name == "material_instance.set_parameters" else None
        key = ("actor" if name.startswith("level.") else name, str(target))
        if target and key in targets:
            previous, _ = merged[targets[key]]
            previous_args = previous["arguments"]
            if name == "blueprint.edit":
                combined_args = dict(args, operations=previous_args["operations"] + args["operations"])
            elif name == "material_instance.set_parameters":
                combined_args = dict(args)
                for field in ("scalar", "vector", "static_switch"):
                    combined_args[field] = {**previous_args.get(field, {}), **args.get(field, {})}
            else:
                field = "properties" if name == "object.set" else "transform"
                combined_args = dict(args, **{field: {**previous_args.get(field, {}), **args.get(field, {})}})
            combined = dict(command, arguments=combined_args)
            merged[targets[key]] = (combined, data)
        else:
            if target:
                targets[key] = len(merged)
            merged.append((command, data))
    pairs = merged
    for command, data in pairs:
        name, args = command["action"], command.get("arguments", {})
        probe_action, probe_args, expected = None, {}, {}
        required_identity = "asset_path" if name in {"material.create", "material_instance.create", "blueprint.create"} else "actor" if name == "level.set_actor_property" else None
        if required_identity and not data.get(required_identity):
            uncovered.append(command["id"] + ": executor did not return " + required_identity)
            continue
        if name in {"project.save", "asset.save"}:
            # Persistence is checked by the UE handler; it must check native returns.
            continue
        if name == "content.create_folder":
            probe_action, probe_args, expected = "content.list", {"path": data.get("path", args.get("path")), "limit": 1}, {"directory_exists": True}
        elif name == "material.create":
            probe_action, probe_args = "material.inspect", {"material": data["asset_path"]}
            expected = {"parameters": {"scalar": {"Metallic": args.get("metallic", 0.0), "Roughness": args.get("roughness", 0.5)},
                                       "vector": {"BaseColor": args.get("base_color", [0.18, 0.2, 0.22, 1.0])}}}
        elif name == "material_instance.create":
            probe_action, probe_args = "material.inspect", {"material": data["asset_path"]}
            expected = {"parent": args["parent"]}
        elif name == "blueprint.create":
            probe_action, probe_args = "blueprint.inspect", {"blueprint": data["asset_path"]}
            expected = {"path": data["asset_path"], "parent_class": args["parent_class"]}
        elif name in {"asset.create", "asset.duplicate"}:
            path = data.get("asset_path", data.get("path"))
            if not path:
                uncovered.append(command["id"] + ": executor did not return an asset path")
                continue
            probe_action, probe_args, expected = "asset.inspect", {"asset": path}, {"path": path}
        elif name == "object.set":
            probe_action = "object.inspect"
            probe_args = {"object": args["object"], "properties": list(args["properties"])}
            expected = {"properties": args["properties"]}
        elif name in {"level.spawn_actor", "level.set_actor_transform"}:
            actor_name = data.get("actor_name", data.get("name"))
            if not actor_name:
                uncovered.append(command["id"] + ": missing actor identity")
                continue
            probe_action, probe_args = "level.inspect", {"query": actor_name, "limit": 5000}
            expected = {"actor_name": actor_name, "transform": args.get("transform", {})}
        elif name == "level.set_actor_property":
            probe_action, probe_args = "object.inspect", {"object": data["actor"], "properties": [args["property"]]}
            expected = {"properties": {args["property"]: args["value"]}}
        elif name == "blueprint.edit":
            probe_action, probe_args = "blueprint.inspect", {"blueprint": data.get("blueprint", args["blueprint"])}
            expected = {"operations": args["operations"]}
            unsupported = [op["operation"] for op in args["operations"] if op["operation"] in {"add_variable", "set_class_property"}]
            if unsupported:
                uncovered.append(command["id"] + ": no read-back contract for " + ", ".join(unsupported))
        elif name == "blueprint.compile":
            probe_action, probe_args = "blueprint.inspect", {"blueprint": args["blueprint"]}
        elif name == "material_instance.set_parameters":
            probe_action, probe_args = "material.inspect", {"material": args["material_instance"]}
            expected = {"parameters": args}
        else:
            uncovered.append(command["id"] + ": no automatic value verification for " + name)
            continue
        # Deferred result references need a caller-supplied check after resolution.
        if "$ref" in str(probe_args) or "$ref" in str(expected):
            uncovered.append(command["id"] + ": verification contains unresolved result references")
            continue
        probe_id = "verify_" + str(len(commands))
        commands.append({"id": probe_id, "action": probe_action, "arguments": probe_args})
        checks.append({"id": probe_id, "source": command["id"], "expected": expected})
    return {"format_version": "1.0", "commands": commands}, checks, uncovered


def evaluate_verification(result, checks):
    errors = []
    results = {r.get("id"): r for r in result.get("commands", [])}
    for check in checks:
        envelope = results.get(check["id"], {})
        data, expected = envelope.get("data", {}), check["expected"]
        valid = envelope.get("success") is True
        if "actor_name" in expected:
            actor = next((a for a in data.get("actors", []) if a.get("name") == expected["actor_name"]), None)
            valid = valid and actor is not None
            if actor:
                valid = valid and all(equivalent(actor.get(k), v) for k, v in expected["transform"].items())
        elif "operations" in expected:
            components = {c.get("name", "").removesuffix("_GEN_VARIABLE"): c for c in data.get("components", [])}
            final_operations = {}
            for op in expected["operations"]:
                kind, component_name = op["operation"], op.get("component_name")
                if kind == "set_component_transform":
                    key = (component_name, kind)
                    final_operations[key] = {**final_operations.get(key, {}), **op}
                else:
                    key = (component_name, kind, op.get("property"), op.get("slot", 0))
                    final_operations[key] = op
            for op in final_operations.values():
                if "component_name" not in op:
                    continue
                component = components.get(op["component_name"])
                valid = valid and component is not None
                if not component:
                    continue
                kind = op["operation"]
                if kind == "set_component_transform":
                    for field, target in (("location", "relative_location"), ("rotation", "relative_rotation"), ("scale", "relative_scale")):
                        if field in op:
                            valid = valid and equivalent(component.get(target), op[field])
                elif kind == "set_component_property":
                    if op["property"] == "static_mesh":
                        valid = valid and component.get("static_mesh") == op["value"]
                    else:
                        valid = False
                elif kind == "set_component_material":
                    materials, slot = component.get("materials", []), op.get("slot", 0)
                    valid = valid and len(materials) > slot and materials[slot] == op["material"]
        elif "parameters" in expected:
            for kind, key in (("scalar", "scalar_parameters"), ("vector", "vector_parameters"), ("static_switch", "static_switch_parameters")):
                actual = {v["name"]: v.get("value") for v in data.get(key, [])}
                valid = valid and equivalent(actual, expected["parameters"].get(kind, {}))
        else:
            valid = valid and equivalent(data, expected)
        if not valid:
            errors.append(check["source"] + ": read-back did not match expected state")
    return errors
