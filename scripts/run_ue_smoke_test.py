#!/usr/bin/env python3
"""Run the installed harness end to end in an Unreal Engine 5.8 test project."""

import argparse
import json
import shutil
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CAPABILITIES_FIXTURE = REPO_ROOT / "tests/fixtures/capabilities-v0.1.json"
DEFAULT_LINUX_EDITOR = Path("/opt/UnrealEngine/Engine/Binaries/Linux/UnrealEditor-Cmd")
DEFAULT_WINDOWS_EDITOR = Path(
    "C:/Program Files/Epic Games/UE_5.8/Engine/Binaries/Win64/UnrealEditor-Cmd.exe"
)

def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path, help="Path to the test .uproject")
    parser.add_argument("--editor-cmd", type=Path, help="Path to UnrealEditor-Cmd executable")
    parser.add_argument("--namespace", help="Unique asset folder name under /Game/CodexHarnessSmoke")
    parser.add_argument("--recovery", action="store_true", help="Verify failed compile and same-Blueprint repair")
    parser.add_argument("--asset-create", action="store_true", help="Verify native asset.create, registry visibility, conflict safety, and explicit save")
    parser.add_argument("--native-reflection", action="store_true", help="Verify native UObject reflection, typed mutation, and preflight rollback")
    parser.add_argument("--material", action="store_true", help="Verify native material and Blueprint material assignment")
    parser.add_argument("--reopen", action="store_true", help="Run a second Unreal process to inspect the saved Blueprint or material")
    return parser.parse_args(argv)


def resolve_editor(explicit):
    candidates = []
    if explicit:
        candidates.append(explicit.expanduser())
    discovered = shutil.which("UnrealEditor-Cmd")
    if discovered:
        candidates.append(Path(discovered))
    if sys.platform == "linux":
        candidates.extend(
            [
                Path("/mnt/omarchy-data/UnrealEngine-5.8/Engine/Binaries/Linux/UnrealEditor-Cmd"),
                Path("/mnt/omarchy-data/UnrealEngine-5.8.3/Engine/Binaries/Linux/UnrealEditor-Cmd"),
                DEFAULT_LINUX_EDITOR,
            ]
        )
    elif sys.platform == "win32":
        candidates.append(DEFAULT_WINDOWS_EDITOR)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise ValueError("UnrealEditor-Cmd was not found; pass --editor-cmd")


def smoke_document(namespace):
    folder = "/Game/CodexHarnessSmoke/" + namespace
    material = folder + "/M_Smoke.M_Smoke"
    blueprint = folder + "/BP_Smoke.BP_Smoke"
    actor_label = "CodexHarnessSmoke_" + namespace
    return {
        "format_version": "1.0",
        "run_id": "codex-smoke-" + uuid.uuid4().hex,
        "commands": [
            {"id": "capabilities", "action": "system.capabilities", "arguments": {}},
            {"id": "folder", "action": "content.create_folder", "arguments": {"path": folder}},
            {
                "id": "material",
                "action": "material.create",
                "depends_on": ["folder"],
                "arguments": {
                    "folder": folder,
                    "name": "M_Smoke",
                    "base_color": [0.12, 0.14, 0.17, 1.0],
                    "metallic": 1.0,
                    "roughness": 0.35,
                },
            },
            {
                "id": "blueprint",
                "action": "blueprint.create",
                "depends_on": ["folder"],
                "arguments": {
                    "folder": folder,
                    "name": "BP_Smoke",
                    "parent_class": "/Script/Engine.Actor",
                },
            },
            {
                "id": "components",
                "action": "blueprint.edit",
                "depends_on": ["material", "blueprint"],
                "arguments": {
                    "blueprint": blueprint,
                    "operations": [
                        {
                            "operation": "add_component",
                            "component_type": "/Script/Engine.StaticMeshComponent",
                            "component_name": "SmokeMesh",
                            "attach_to": "DefaultSceneRoot",
                        },
                        {
                            "operation": "set_component_property",
                            "component_name": "SmokeMesh",
                            "property": "static_mesh",
                            "value": "/Engine/BasicShapes/Sphere.Sphere",
                        },
                        {
                            "operation": "set_component_material",
                            "component_name": "SmokeMesh",
                            "slot": 0,
                            "material": material,
                        },
                    ],
                    "compile": True,
                    "save": False,
                },
            },
            {
                "id": "begin_play",
                "action": "blueprint.graph.add_node",
                "depends_on": ["blueprint"],
                "arguments": {
                    "blueprint": blueprint,
                    "graph": "EventGraph",
                    "node_id": "begin_play",
                    "node": {
                        "kind": "event",
                        "owner_class": "/Script/Engine.Actor",
                        "function": "ReceiveBeginPlay",
                    },
                    "position": [0, 0],
                },
            },
            {
                "id": "print",
                "action": "blueprint.graph.add_node",
                "depends_on": ["blueprint"],
                "arguments": {
                    "blueprint": blueprint,
                    "graph": "EventGraph",
                    "node_id": "print",
                    "node": {
                        "kind": "function_call",
                        "owner_class": "/Script/Engine.KismetSystemLibrary",
                        "function": "PrintString",
                    },
                    "position": [350, 0],
                },
            },
            {
                "id": "message",
                "action": "blueprint.graph.set_pin_value",
                "depends_on": ["print"],
                "arguments": {
                    "blueprint": blueprint,
                    "graph": "EventGraph",
                    "node": "print",
                    "pin": "InString",
                    "value": "Unreal Codex Harness smoke test",
                },
            },
            {
                "id": "level_name",
                "action": "blueprint.graph.add_node",
                "depends_on": ["blueprint"],
                "arguments": {
                    "blueprint": blueprint,
                    "graph": "EventGraph",
                    "node_id": "level_name",
                    "node": {
                        "kind": "function_call",
                        "owner_class": "/Script/Engine.KismetSystemLibrary",
                        "function": "GetGameTimeInSeconds",
                    },
                    "position": [350, 180],
                },
            },
            {
                "id": "wire_data",
                "action": "blueprint.graph.connect",
                "depends_on": ["level_name", "print"],
                "arguments": {
                    "blueprint": blueprint,
                    "graph": "EventGraph",
                    "from": {"node": "level_name", "pin": "ReturnValue"},
                    "to": {"node": "print", "pin": "InString"},
                },
            },
            {
                "id": "wire",
                "action": "blueprint.graph.connect",
                "depends_on": ["begin_play", "print"],
                "arguments": {
                    "blueprint": blueprint,
                    "graph": "EventGraph",
                    "from": {"node": "begin_play", "pin": "then"},
                    "to": {"node": "print", "pin": "execute"},
                },
            },
            {
                "id": "compile",
                "action": "blueprint.compile",
                "depends_on": ["components", "message", "wire", "wire_data"],
                "arguments": {"blueprint": blueprint, "save": False},
            },
            {
                "id": "inspect_graph",
                "action": "blueprint.graph.inspect",
                "depends_on": ["compile"],
                "arguments": {"blueprint": blueprint, "graph": "EventGraph"},
            },
            {
                "id": "spawn",
                "action": "level.spawn_actor",
                "depends_on": ["compile"],
                "arguments": {
                    "level": "current",
                    "class": blueprint + "_C",
                    "actor_label": actor_label,
                    "transform": {"location": [0, 0, 100]},
                },
            },
            {
                "id": "inspect_actor",
                "action": "level.inspect",
                "depends_on": ["spawn"],
                "arguments": {"query": actor_label, "limit": 10},
            },
            {
                "id": "save_assets",
                "action": "project.save",
                "depends_on": ["compile"],
                "arguments": {"save_level": False, "save_assets": True},
            },
        ],
    }
def native_reflection_document(namespace):
    document = smoke_document(namespace)
    actor = "/Temp/Untitled_0.Untitled:PersistentLevel.BP_Smoke_C_0"
    document["commands"].extend([
        {"id": "inspect_actor_properties", "action": "object.inspect", "depends_on": ["inspect_actor"], "arguments": {"object": actor, "properties": ["Tags"], "depth": 1}},
        {"id": "set_actor_properties", "action": "object.set", "depends_on": ["inspect_actor_properties"], "arguments": {"object": actor, "properties": {"Tags": ["CodexNativeReflection"]}}},
        {"id": "inspect_actor_after_set", "action": "object.inspect", "depends_on": ["set_actor_properties"], "arguments": {"object": actor, "properties": ["Tags"], "depth": 2}},
        {"id": "reject_actor_partial_set", "action": "object.set", "depends_on": ["inspect_actor_after_set"], "arguments": {"object": actor, "properties": {"Tags": [], "MissingProperty": True}}},
        {"id": "inspect_actor_after_reject", "action": "object.inspect", "arguments": {"object": actor, "properties": ["Tags"], "depth": 2}}
    ])
    return document
def material_document(namespace):
    folder = "/Game/CodexHarnessSmoke/" + namespace
    material = folder + "/M_Material.M_Material"
    instance = folder + "/MI_Material.MI_Material"
    blueprint = folder + "/BP_Material.BP_Material"
    return {
        "format_version": "1.0",
        "run_id": "codex-material-" + uuid.uuid4().hex,
        "commands": [
            {"id": "folder", "action": "content.create_folder", "arguments": {"path": folder}},
            {"id": "material", "action": "material.create", "depends_on": ["folder"], "arguments": {"folder": folder, "name": "M_Material", "base_color": [0.1, 0.2, 0.3, 1.0], "metallic": 0.8, "roughness": 0.25}},
            {"id": "inspect_material", "action": "material.inspect", "depends_on": ["material"], "arguments": {"material": material}},
            {"id": "instance", "action": "material_instance.create", "depends_on": ["material"], "arguments": {"folder": folder, "name": "MI_Material", "parent": material}},
            {"id": "parameters", "action": "material_instance.set_parameters", "depends_on": ["instance"], "arguments": {"material_instance": instance, "scalar": {"Metallic": 0.65, "Roughness": 0.15}, "vector": {"BaseColor": [0.8, 0.1, 0.05, 1.0]}}},
            {"id": "inspect_instance", "action": "material.inspect", "depends_on": ["parameters"], "arguments": {"material": instance}},
            {"id": "blueprint", "action": "blueprint.create", "depends_on": ["folder"], "arguments": {"folder": folder, "name": "BP_Material", "parent_class": "/Script/Engine.Actor"}},
            {"id": "component", "action": "blueprint.edit", "depends_on": ["blueprint", "instance"], "arguments": {"blueprint": blueprint, "operations": [{"operation": "add_component", "component_type": "/Script/Engine.StaticMeshComponent", "component_name": "MaterialMesh", "attach_to": "DefaultSceneRoot"}, {"operation": "set_component_property", "component_name": "MaterialMesh", "property": "static_mesh", "value": "/Engine/BasicShapes/Cube.Cube"}, {"operation": "set_component_material", "component_name": "MaterialMesh", "slot": 0, "material": instance}], "compile": True, "save": False}},
            {"id": "compile", "action": "blueprint.compile", "depends_on": ["component"], "arguments": {"blueprint": blueprint, "save": True}},
            {"id": "inspect_blueprint", "action": "blueprint.inspect", "depends_on": ["compile"], "arguments": {"blueprint": blueprint}},
            {"id": "save", "action": "project.save", "depends_on": ["compile"], "arguments": {"save_level": False, "save_assets": True}},
        ],
    }

def material_reopen_document(namespace):
    folder = "/Game/CodexHarnessSmoke/" + namespace
    return {"format_version": "1.0", "run_id": "codex-material-reopen-" + uuid.uuid4().hex, "commands": [
        {"id": "material", "action": "material.inspect", "arguments": {"material": folder + "/M_Material.M_Material"}},
        {"id": "instance", "action": "material.inspect", "arguments": {"material": folder + "/MI_Material.MI_Material"}},
        {"id": "blueprint", "action": "blueprint.inspect", "arguments": {"blueprint": folder + "/BP_Material.BP_Material"}},
    ]}

def validate_material_result(result):
    if result.get("success") is not True:
        raise AssertionError("Material acceptance failed: {}".format(result.get("errors")))
    commands = {item["id"]: item for item in result["commands"]}
    if commands["inspect_material"]["data"].get("expression_count") != 3:
        raise AssertionError("Material graph did not persist three expressions")
    values = {item["name"]: item.get("value") for item in commands["inspect_instance"]["data"].get("scalar_parameters", [])}
    if abs(values.get("Metallic", -1.0) - 0.65) > 0.0001 or abs(values.get("Roughness", -1.0) - 0.15) > 0.0001:
        raise AssertionError("Material instance scalar parameters were not persisted")
    if not result.get("changed_objects"):
        raise AssertionError("Material acceptance reported no mutations")

def validate_material_reopen_result(result):
    if result.get("success") is not True:
        raise AssertionError("Material reopen failed: {}".format(result.get("errors")))
    commands = {item["id"]: item for item in result["commands"]}
    if commands["instance"]["data"].get("parent", "") == "":
        raise AssertionError("Reopened material instance has no parent")
    if result.get("changed_objects"):
        raise AssertionError("Read-only material reopen reported mutations")
def asset_create_document(namespace):
    folder = "/Game/CodexHarnessSmoke/" + namespace
    material = folder + "/M_Smoke.M_Smoke"
    assets = {
        "curve": folder + "/Curve_Smoke.Curve_Smoke",
        "material": material,
        "instance": folder + "/MI_Smoke.MI_Smoke",
        "action": folder + "/IA_Smoke.IA_Smoke",
        "mapping": folder + "/IMC_Smoke.IMC_Smoke",
        "duplicate": folder + "/Curve_Smoke_Copy.Curve_Smoke_Copy",
    }
    return {
        "format_version": "1.0",
        "run_id": "codex-asset-create-" + uuid.uuid4().hex,
        "commands": [
            {"id": "folder", "action": "content.create_folder", "arguments": {"path": folder}},
            {"id": "curve", "action": "asset.create", "depends_on": ["folder"], "arguments": {"folder": folder, "name": "Curve_Smoke", "class": "/Script/Engine.CurveFloat"}},
            {"id": "material", "action": "material.create", "depends_on": ["folder"], "arguments": {"folder": folder, "name": "M_Smoke", "base_color": [0.2, 0.4, 0.8, 1.0]}},
            {"id": "instance", "action": "material_instance.create", "depends_on": ["material"], "arguments": {"folder": folder, "name": "MI_Smoke", "parent": material}},
            {"id": "action", "action": "asset.create", "depends_on": ["folder"], "arguments": {"folder": folder, "name": "IA_Smoke", "class": "/Script/EnhancedInput.InputAction", "factory": "/Script/InputEditor.InputAction_Factory"}},
            {"id": "mapping", "action": "asset.create", "depends_on": ["folder"], "arguments": {"folder": folder, "name": "IMC_Smoke", "class": "/Script/EnhancedInput.InputMappingContext", "factory": "/Script/InputEditor.InputMappingContext_Factory"}},
            {"id": "duplicate", "action": "asset.duplicate", "depends_on": ["curve"], "arguments": {"source": assets["curve"], "destination": assets["duplicate"]}},
            {"id": "save", "action": "asset.save", "depends_on": ["duplicate", "instance", "action", "mapping"], "arguments": {"assets": list(assets.values())}},
            {"id": "reject_duplicate", "action": "asset.duplicate", "depends_on": ["save"], "arguments": {"source": assets["curve"], "destination": assets["duplicate"]}},
        ],
    }


def validate_asset_create_result(result, namespace):
    commands = {item["id"]: item for item in result.get("commands", [])}
    errors = {error.get("command_id"): error for error in result.get("errors", [])}
    if result.get("success") is not False:
        raise AssertionError("Asset lifecycle batch should contain an intentional duplicate rejection")
    if errors.get("reject_duplicate", {}).get("code") != "conflict":
        raise AssertionError("Duplicate destination was not rejected with conflict")
    for command_id in ("curve", "material", "instance", "action", "mapping", "duplicate", "save"):
        if commands.get(command_id, {}).get("success") is not True:
            raise AssertionError("Native asset command failed: " + command_id)
    if commands["curve"]["data"].get("created") is not True:
        raise AssertionError("Curve asset was not created")
    if len(commands["save"]["data"].get("assets_saved", [])) != 6:
        raise AssertionError("Explicit asset.save did not save all assets")


def asset_reopen_document(namespace):
    folder = "/Game/CodexHarnessSmoke/" + namespace
    return {
        "format_version": "1.0",
        "run_id": "codex-asset-reopen-" + uuid.uuid4().hex,
        "commands": [{"id": "search", "action": "asset.search", "arguments": {"path": folder, "limit": 20}}],
    }


def validate_asset_reopen_result(result, namespace):
    if result.get("success") is not True:
        raise AssertionError("Reopened asset search failed: {}".format(result.get("errors")))
    assets = {item.get("path") for item in result["commands"][0]["data"].get("assets", [])}
    folder = "/Game/CodexHarnessSmoke/" + namespace
    expected = {folder + "/" + name + "." + name for name in ("Curve_Smoke", "M_Smoke", "MI_Smoke", "IA_Smoke", "IMC_Smoke", "Curve_Smoke_Copy")}
    if not expected.issubset(assets):
        raise AssertionError("Reopened process did not find all saved assets")

def recovery_document(namespace):
    document = smoke_document(namespace)
    commands = document["commands"]
    blueprint = "/Game/CodexHarnessSmoke/{}/BP_Smoke.BP_Smoke".format(namespace)
    commands.extend([
        {"id": "invalid_cast", "action": "blueprint.graph.add_node", "depends_on": ["compile"], "arguments": {"blueprint": blueprint, "graph": "EventGraph", "node_id": "invalid_cast", "node": {"kind": "dynamic_cast", "target_class": "/Script/Engine.Actor"}, "position": [700, 0]}},
        {"id": "cast_wire", "action": "blueprint.graph.connect", "depends_on": ["invalid_cast"], "arguments": {"blueprint": blueprint, "graph": "EventGraph", "from": {"node": "begin_play", "pin": "then"}, "to": {"node": "invalid_cast", "pin": "execute"}}},
        {"id": "invalid_compile", "action": "blueprint.compile", "depends_on": ["cast_wire"], "arguments": {"blueprint": blueprint, "save": False}},
        {"id": "remove_invalid_cast", "action": "blueprint.graph.remove_node", "arguments": {"blueprint": blueprint, "graph": "EventGraph", "node": "invalid_cast"}},
        {"id": "recovered_compile", "action": "blueprint.compile", "depends_on": ["remove_invalid_cast"], "arguments": {"blueprint": blueprint, "save": False}},
        {"id": "recovered_inspect", "action": "blueprint.graph.inspect", "depends_on": ["recovered_compile"], "arguments": {"blueprint": blueprint, "graph": "EventGraph"}},
        {"id": "recovery_save", "action": "project.save", "depends_on": ["recovered_compile"], "arguments": {"save_level": False, "save_assets": True}},
        {"id": "reopened_inspect", "action": "blueprint.graph.inspect", "depends_on": ["recovery_save"], "arguments": {"blueprint": blueprint, "graph": "EventGraph"}},
    ])
    return document


def reopen_document(namespace):
    blueprint = "/Game/CodexHarnessSmoke/{}/BP_Smoke.BP_Smoke".format(namespace)
    return {
        "format_version": "1.0",
        "run_id": "codex-reopen-" + uuid.uuid4().hex,
        "commands": [
            {"id": "inspect_reopened", "action": "blueprint.graph.inspect", "arguments": {"blueprint": blueprint, "graph": "EventGraph"}},
            {"id": "compile_reopened", "action": "blueprint.compile", "depends_on": ["inspect_reopened"], "arguments": {"blueprint": blueprint, "save": False}},
        ],
    }


def validate_run_result(result, run_id):
    if result.get("run_id") != run_id:
        raise AssertionError("Harness result run_id does not match submitted smoke run")


def validate_reopen_result(result, run_id):
    validate_run_result(result, run_id)
    if result.get("success") is not True:
        raise AssertionError("Reopened Blueprint inspection failed: {}".format(result.get("errors")))
    commands = {item["id"]: item for item in result.get("commands", [])}
    if commands.get("inspect_reopened", {}).get("success") is not True:
        raise AssertionError("Reopened graph inspection failed")
    if commands.get("compile_reopened", {}).get("success") is not True:
        raise AssertionError("Reopened Blueprint did not compile")


def validate_result(result):
    if result.get("success") is not True:
        raise AssertionError("Smoke result was not successful: {}".format(result.get("errors")))
    commands = {item["id"]: item for item in result.get("commands", [])}
    required = {"capabilities", "folder", "material", "blueprint", "components", "begin_play", "print", "message", "level_name", "wire", "wire_data", "compile", "inspect_graph", "spawn", "inspect_actor", "save_assets"}
    if set(commands) != required:
        raise AssertionError("Unexpected smoke command results: {}".format(sorted(commands)))
    if commands["wire_data"].get("success") is not True:
        raise AssertionError("Smoke data connection command failed")
    if commands["inspect_actor"]["data"].get("total") != 1:
        raise AssertionError("Spawned smoke actor was not found uniquely")
    if not result.get("changed_objects"):
        raise AssertionError("Mutation audit did not report changed_objects")
    for command in result["commands"]:
        if "changed_objects" not in command:
            raise AssertionError("Command lacks changed_objects: " + command["id"])


def validate_native_result(result):
    commands = {item["id"]: item for item in result.get("commands", [])}
    errors = {error.get("command_id"): error for error in result.get("errors", [])}
    if result.get("success") is not False or errors.get("reject_actor_partial_set", {}).get("code") != "property_not_found":
        raise AssertionError("Native reflection rejection was not reported as property_not_found")
    expected_ids = {
        "capabilities", "folder", "material", "blueprint", "components",
        "begin_play", "print", "message", "level_name", "wire", "wire_data",
        "compile", "inspect_graph", "spawn", "inspect_actor", "inspect_actor_properties",
        "set_actor_properties", "inspect_actor_after_set", "reject_actor_partial_set",
        "inspect_actor_after_reject", "save_assets",
    }
    if set(commands) != expected_ids:
        raise AssertionError("Unexpected command results: {}".format(sorted(commands)))
    if commands["inspect_actor_properties"].get("success") is not True:
        raise AssertionError("Native actor reflection inspection failed")
    after_set = commands["inspect_actor_after_set"].get("data", {}).get("properties", {})
    if after_set.get("Tags") != ["CodexNativeReflection"]:
        raise AssertionError("Native Tags mutation was not observed")
    if commands["reject_actor_partial_set"].get("changed_objects"):
        raise AssertionError("Rejected native set reported mutation")
    after_reject = commands["inspect_actor_after_reject"].get("data", {}).get("properties", {})
    if after_reject.get("Tags") != ["CodexNativeReflection"]:
        raise AssertionError("Rejected native set partially changed Tags")
    fixture = json.loads(CAPABILITIES_FIXTURE.read_text(encoding="utf-8"))
    capabilities = commands["capabilities"]["data"]
    if capabilities.get("harness_version") != fixture["harness_version"]:
        raise AssertionError("Harness version differs from capability fixture")
    if commands["wire_data"].get("success") is not True:
        raise AssertionError("Smoke data connection command failed")
    if capabilities.get("actions") != fixture["actions"]:
        raise AssertionError("Action catalog differs from capability fixture")
    node_kinds = capabilities.get("graph_bridge", {}).get("node_kinds")
    if node_kinds != fixture["graph_node_kinds"]:
        raise AssertionError("Graph node kinds differ from capability fixture")
    if commands["inspect_actor"]["data"].get("total") != 1:
        raise AssertionError("Spawned smoke actor was not found uniquely")
    if not result.get("changed_objects"):
        raise AssertionError("Mutation audit did not report changed_objects")
    for command in result["commands"]:
        if "changed_objects" not in command:
            raise AssertionError("Command lacks changed_objects: " + command["id"])



def validate_recovery_result(result):
    if result.get("success") is not False:
        raise AssertionError("Recovery batch should contain an intentional compile failure")
    commands = {item["id"]: item for item in result.get("commands", [])}
    if commands.get("invalid_compile", {}).get("error_code") != "blueprint_compile_failed":
        raise AssertionError("Invalid Blueprint compile failure was not observed")
    for command_id in ("recovered_compile", "recovered_inspect", "recovery_save", "reopened_inspect"):
        if commands.get(command_id, {}).get("success") is not True:
            raise AssertionError("Recovery command failed: " + command_id)
    if not result.get("changed_objects"):
        raise AssertionError("Recovery mutation audit was empty")

def main(argv=None):
    args = parse_args(argv)
    project = args.project.expanduser().resolve()
    if not project.is_file() or project.suffix.lower() != ".uproject":
        raise ValueError("Invalid .uproject path: {}".format(project))
    editor = resolve_editor(args.editor_cmd)

    python_dir = project.parent / "Content/Python"
    executor = python_dir / "execute_actions.py"
    actions = python_dir / "actions.json"
    result_path = python_dir / "result.json"
    if not executor.is_file():
        raise ValueError("Harness is not installed: {}".format(executor))

    namespace = args.namespace or "Run_{}_{}".format(
        datetime.now().strftime("%Y%m%d_%H%M%S"), uuid.uuid4().hex[:6]
    )
    backups = {}
    for path in (actions, result_path):
        backups[path] = path.read_bytes() if path.exists() else None
    try:
        if args.material:
            document = material_document(namespace)
        elif args.asset_create:
            document = asset_create_document(namespace)
        elif args.native_reflection:
            document = native_reflection_document(namespace)
        else:
            document = recovery_document(namespace) if args.recovery else smoke_document(namespace)
        run_id = document["run_id"]
        result_path.unlink(missing_ok=True)
        actions.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        command = [
            str(editor),
            str(project),
            "-run=pythonscript",
            "-script={}".format(executor),
            "-unattended",
            "-nop4",
            "-nosplash",
            "-NullRHI",
        ]
        capture_output = sys.platform != "linux"
        if capture_output:
            command.append("-stdout")
        try:
            completed = subprocess.run(
                command,
                cwd=str(project.parent),
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE if capture_output else subprocess.DEVNULL,
                stderr=subprocess.STDOUT if capture_output else subprocess.DEVNULL,
                timeout=180,
            )
        except subprocess.TimeoutExpired as error:
            raise AssertionError("Unreal commandlet timed out") from error
        expected_partial_failure = args.native_reflection or args.asset_create or args.recovery
        if completed.returncode != 0 and not (expected_partial_failure and result_path.is_file()):
            raise AssertionError("Unreal commandlet exited with status {}".format(completed.returncode))
        if not result_path.is_file():
            raise AssertionError("Unreal did not write result.json")
        result = json.loads(result_path.read_text(encoding="utf-8-sig"))
        if result.get("run_id") != run_id:
            raise AssertionError("Unreal result run_id does not match this smoke run")
        if args.native_reflection:
            shutil.copyfile(result_path, project.parent / "native-reflection-result.json")
        if args.asset_create:
            shutil.copyfile(result_path, project.parent / "asset-create-result.json")
        if args.material:
            validate_material_result(result)
        elif args.asset_create:
            validate_asset_create_result(result, namespace)
        elif args.native_reflection:
            validate_native_result(result)
        elif args.recovery:
            validate_recovery_result(result)
        else:
            validate_result(result)
        if args.reopen and not args.recovery:
            if args.material:
                reopen = material_reopen_document(namespace)
            elif args.asset_create:
                reopen = asset_reopen_document(namespace)
            else:
                reopen = reopen_document(namespace)
            reopen_id = reopen["run_id"]
            result_path.unlink(missing_ok=True)
            actions.write_text(json.dumps(reopen, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            reopen_completed = subprocess.run(command, cwd=str(project.parent), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180, check=False)
            if reopen_completed.returncode != 0:
                raise AssertionError("Unreal reopen commandlet exited with status {}".format(reopen_completed.returncode))
            if not result_path.is_file():
                raise AssertionError("Unreal did not write reopen result.json")
            reopened_result = json.loads(result_path.read_text(encoding="utf-8-sig"))
            if reopened_result.get("run_id") != reopen_id:
                raise AssertionError("Unreal reopen result run_id does not match this smoke run")
            if args.material:
                validate_material_reopen_result(reopened_result)
            elif args.asset_create:
                validate_asset_reopen_result(reopened_result, namespace)
            else:
                validate_reopen_result(reopened_result, reopen_id)
            print("Reopen verification: passed")
        print("Namespace: /Game/CodexHarnessSmoke/{}".format(namespace))
        print("Changed objects: {}".format(len(result["changed_objects"])))
        return 0
    finally:
        for path, content in backups.items():
            if content is None:
                if path.exists():
                    path.unlink()
            else:
                path.write_bytes(content)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print("FAIL: {}".format(error), file=sys.stderr)
        sys.exit(1)
