import json
import runpy
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXECUTOR = ROOT / "templates/unreal-project/Content/Python/execute_actions.py"
ACTION_FIXTURE = ROOT / "tests/fixtures/actions-v0.1.json"


class ExecutorContractTests(unittest.TestCase):
    def run_document(self, document, configure_unreal=None):
        with tempfile.TemporaryDirectory(prefix="unreal-codex-executor-") as directory:
            root = Path(directory)
            shutil.copy2(EXECUTOR, root / "execute_actions.py")
            (root / "actions.json").write_text(json.dumps(document), encoding="utf-8")
            fake_unreal = types.ModuleType("unreal")
            fake_unreal.log = lambda message: None
            fake_unreal.log_error = lambda message: None
            if configure_unreal:
                configure_unreal(fake_unreal)
            previous = sys.modules.get("unreal")
            sys.modules["unreal"] = fake_unreal
            try:
                runpy.run_path(str(root / "execute_actions.py"), run_name="__main__")
            finally:
                if previous is None:
                    del sys.modules["unreal"]
                else:
                    sys.modules["unreal"] = previous
            return json.loads((root / "result.json").read_text(encoding="utf-8"))

    def test_result_preserves_submitted_run_id(self):
        run_id = "smoke-run-123"
        result = self.run_document(
            {
                "format_version": "1.0",
                "run_id": run_id,
                "dry_run": True,
                "commands": [],
            }
        )
        self.assertEqual(result["run_id"], run_id)

    def test_dry_run_validates_without_unreal_calls(self):
        result = self.run_document(
            {
                "format_version": "1.0",
                "dry_run": True,
                "commands": [
                    {
                        "id": "inspect",
                        "action": "level.inspect",
                        "arguments": {"limit": 10},
                    },
                    {
                        "id": "save",
                        "action": "project.save",
                        "depends_on": ["inspect"],
                        "arguments": {},
                    },
                ],
            }
        )
        self.assertTrue(result["success"])
        self.assertTrue(result["dry_run"])
        self.assertEqual([item["id"] for item in result["plan"]], ["inspect", "save"])
        self.assertFalse(result["plan"][0]["mutating"])
        self.assertEqual(result["changed_objects"], [])

    def test_dry_run_structure_is_deterministic(self):
        document = {
            "format_version": "1.0",
            "dry_run": True,
            "commands": [
                {"id": "capabilities", "action": "system.capabilities", "arguments": {}},
                {"id": "inspect", "action": "level.inspect", "arguments": {"limit": 10}},
            ],
        }
        first = self.run_document(document)
        second = self.run_document(document)
        for result in (first, second):
            result.pop("run_id")
            result.pop("started_at")
            result.pop("finished_at")
        self.assertEqual(first, second)

    def test_duplicate_ids_fail_before_execution(self):
        result = self.run_document(
            {
                "format_version": "1.0",
                "commands": [
                    {"id": "same", "action": "level.inspect", "arguments": {}},
                    {"id": "same", "action": "level.inspect", "arguments": {}},
                ],
            }
        )
        self.assertFalse(result["success"])
        self.assertIn("Duplicate command id", result["errors"][0]["message"])
        self.assertEqual(result["errors"][0]["code"], "duplicate_command_id")
        self.assertEqual(result["changed_objects"], [])

    def test_forward_dependency_fails_before_execution(self):
        result = self.run_document(
            {
                "format_version": "1.0",
                "commands": [
                    {
                        "id": "first",
                        "action": "project.save",
                        "depends_on": ["later"],
                        "arguments": {},
                    },
                    {"id": "later", "action": "level.inspect", "arguments": {}},
                ],
            }
        )
        self.assertFalse(result["success"])
        self.assertIn("Dependencies must refer to earlier commands", result["errors"][0]["message"])
        self.assertEqual(result["errors"][0]["code"], "invalid_dependencies")

    def test_all_documented_actions_have_a_dry_run_fixture(self):
        actions = json.loads(ACTION_FIXTURE.read_text(encoding="utf-8"))
        document = {
            "format_version": "1.0",
            "dry_run": True,
            "commands": [
                {"id": "fixture_{}".format(index), "action": action, "arguments": arguments}
                for index, (action, arguments) in enumerate(actions.items())
            ],
        }
        result = self.run_document(document)
        self.assertTrue(result["success"])
        self.assertEqual(len(result["plan"]), 39)
        self.assertEqual({item["action"] for item in result["plan"]}, set(actions))

    def test_content_list_normalizes_unreal_array_values_to_strings(self):
        class UnrealPath:
            def __str__(self):
                return "/Game/Test/BP_Test.BP_Test"

        def configure(fake_unreal):
            fake_unreal.EditorAssetLibrary = types.SimpleNamespace(
                list_assets=lambda path, recursive, include_folder: [UnrealPath()]
            )

        result = self.run_document(
            {
                "format_version": "1.0",
                "commands": [
                    {
                        "id": "list",
                        "action": "content.list",
                        "arguments": {"path": "/Game/Test", "limit": 10},
                    }
                ],
            },
            configure,
        )
        self.assertTrue(result["success"])
        self.assertEqual(
            result["commands"][0]["data"]["assets"],
            ["/Game/Test/BP_Test.BP_Test"],
        )

    def test_blueprint_compile_reports_unreal_compile_error(self):
        class Blueprint:
            def get_editor_property(self, name):
                self.assert_status_property(name)
                return "error"

            @staticmethod
            def assert_status_property(name):
                if name != "status":
                    raise AssertionError("Unexpected property: " + name)

            @staticmethod
            def get_path_name():
                return "/Game/Test/BP_Broken.BP_Broken"

        class Transaction:
            def __init__(self, description):
                self.description = description

            def cancel(self):
                pass

        def configure(fake_unreal):
            fake_unreal.Blueprint = Blueprint
            fake_unreal.BlueprintStatus = types.SimpleNamespace(BS_ERROR="error")
            fake_unreal.BlueprintEditorLibrary = types.SimpleNamespace(
                compile_blueprint=lambda blueprint: None
            )
            fake_unreal.ScopedEditorTransaction = Transaction
            fake_unreal.load_asset = lambda path: Blueprint()

        result = self.run_document(
            {
                "format_version": "1.0",
                "commands": [
                    {
                        "id": "compile",
                        "action": "blueprint.compile",
                        "arguments": {
                            "blueprint": "/Game/Test/BP_Broken.BP_Broken",
                            "save": False,
                        },
                    }
                ],
            },
            configure,
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["errors"][0]["code"], "blueprint_compile_failed")

    def test_object_reflection_read_and_write_contract(self):
        class ReflectedClass:
            def get_path_name(self):
                return "/Script/Engine.Actor"

        class ReflectedObject:
            def __init__(self):
                self.values = {"label": "Before", "enabled": False}
                self.modified = False

            def get_path_name(self):
                return "/Game/Test/BP_Test.BP_Test_C"

            def get_name(self):
                return "BP_Test_C"

            def get_class(self):
                return ReflectedClass()

            def get_editor_property_names(self):
                return list(self.values)

            def get_editor_property(self, name):
                return self.values[name]

            def set_editor_property(self, name, value):
                self.values[name] = value

            def modify(self):
                self.modified = True

        reflected = ReflectedObject()

        class Transaction:
            def __init__(self, description):
                self.description = description

            def cancel(self):
                raise AssertionError("unexpected transaction cancel")

        def configure(fake_unreal):
            fake_unreal.load_object = lambda outer, path: reflected
            fake_unreal.ScopedEditorTransaction = Transaction

        result = self.run_document(
            {
                "format_version": "1.0",
                "commands": [
                    {"id": "describe", "action": "object.describe", "arguments": {"object": reflected.get_path_name()}},
                    {"id": "inspect", "action": "object.inspect", "arguments": {"object": reflected.get_path_name(), "properties": ["label", "enabled"]}},
                    {"id": "set", "action": "object.set", "arguments": {"object": reflected.get_path_name(), "properties": {"label": "After", "enabled": True}}},
                ],
            },
            configure,
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["commands"][0]["data"]["class"], "/Script/Engine.Actor")
        self.assertEqual(result["commands"][1]["data"]["properties"]["label"], "Before")
        self.assertEqual(reflected.values, {"label": "After", "enabled": True})
        self.assertTrue(reflected.modified)
        self.assertEqual(result["commands"][2]["changed_objects"], [reflected.get_path_name()])

    def test_object_reflection_rejects_unknown_property_before_mutation(self):
        class ReflectedObject:
            def get_path_name(self):
                return "/Game/Test/Object.Object"

            def get_class(self):
                return types.SimpleNamespace(get_path_name=lambda: "/Script/Engine.Object")

            def get_editor_property_names(self):
                return ["label"]

            def modify(self):
                raise AssertionError("modify must not run")

        def configure(fake_unreal):
            fake_unreal.load_object = lambda outer, path: ReflectedObject()
            fake_unreal.ScopedEditorTransaction = type("Transaction", (), {"__init__": lambda self, description: None, "cancel": lambda self: None})

        result = self.run_document(
            {
                "format_version": "1.0",
                "commands": [{"id": "set", "action": "object.set", "arguments": {"object": "/Game/Test/Object.Object", "properties": {"missing": 1}}}],
            },
            configure,
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["errors"][0]["code"], "property_not_found")
    def test_object_set_decodes_typed_reference_before_mutation(self):
        class ReflectedObject:
            def __init__(self):
                self.values = {"asset": None}

            def get_path_name(self):
                return "/Game/Test/Object.Object"

            def get_class(self):
                return types.SimpleNamespace(get_path_name=lambda: "/Script/Engine.Object")

            def get_editor_property_names(self):
                return ["asset"]

            def modify(self):
                pass

            def set_editor_property(self, name, value):
                self.values[name] = value

        target = ReflectedObject()
        reference = types.SimpleNamespace(get_path_name=lambda: "/Game/Test/Asset.Asset")

        def configure(fake_unreal):
            fake_unreal.load_object = lambda outer, path: target if path == target.get_path_name() else reference
            fake_unreal.ScopedEditorTransaction = type("Transaction", (), {"__init__": lambda self, description: None, "cancel": lambda self: None})

        result = self.run_document(
            {
                "format_version": "1.0",
                "commands": [{"id": "set", "action": "object.set", "arguments": {"object": target.get_path_name(), "properties": {"asset": {"$type": "object", "path": reference.get_path_name()}}}}],
            },
            configure,
        )
        self.assertTrue(result["success"])
        self.assertIs(target.values["asset"], reference)


    def test_object_set_rejects_metadata_flags_and_type_before_modify(self):
        class ReflectedObject:
            def get_path_name(self):
                return "/Game/Test/Object.Object"

            def get_class(self):
                return types.SimpleNamespace(get_path_name=lambda: "/Script/Engine.Object")

            def get_editor_property_names(self):
                return ["locked", "enabled"]

            def get_editor_property_metadata(self, name):
                return {"flags": ["EditConst"]} if name == "locked" else {"type": "bool"}

            def modify(self):
                raise AssertionError("modify must not run")

        def configure(fake_unreal):
            fake_unreal.load_object = lambda outer, path: ReflectedObject()
            fake_unreal.ScopedEditorTransaction = type("Transaction", (), {"__init__": lambda self, description: None, "cancel": lambda self: None})

        for property_name, property_value, code in (("locked", 1, "property_not_writable"), ("enabled", "yes", "property_type_mismatch")):
            result = self.run_document(
                {"format_version": "1.0", "commands": [{"id": "set", "action": "object.set", "arguments": {"object": "/Game/Test/Object.Object", "properties": {property_name: property_value}}}]},
                configure,
            )
            self.assertFalse(result["success"])
            self.assertEqual(result["errors"][0]["code"], code)

    def test_object_set_validates_enum_and_struct_metadata(self):
        class ReflectedObject:
            def __init__(self):
                self.values = {"mode": "Idle", "transform": {"x": 0, "y": 0}}
                self.modified = False

            def get_path_name(self):
                return "/Game/Test/Object.Object"

            def get_class(self):
                return types.SimpleNamespace(get_path_name=lambda: "/Script/Engine.Object")

            def get_editor_property_names(self):
                return list(self.values)

            def get_editor_property_metadata(self, name):
                if name == "mode":
                    return {"type": "enum", "values": ["Idle", "Run"]}
                return {"type": "struct", "fields": ["x", "y"], "required": ["x", "y"]}

            def modify(self):
                self.modified = True

            def set_editor_property(self, name, value):
                self.values[name] = value

        target = ReflectedObject()

        def configure(fake_unreal):
            fake_unreal.load_object = lambda outer, path: target
            fake_unreal.ScopedEditorTransaction = type("Transaction", (), {"__init__": lambda self, description: None, "cancel": lambda self: None})

        invalid = self.run_document({"format_version": "1.0", "commands": [{"id": "set", "action": "object.set", "arguments": {"object": target.get_path_name(), "properties": {"mode": {"$type": "enum", "name": "Broken"}}}}]}, configure)
        self.assertFalse(invalid["success"])
        self.assertEqual(invalid["errors"][0]["code"], "enum_value_invalid")
        self.assertFalse(target.modified)

        valid = self.run_document({"format_version": "1.0", "commands": [{"id": "set", "action": "object.set", "arguments": {"object": target.get_path_name(), "properties": {"mode": {"$type": "enum", "name": "Run"}, "transform": {"x": 10, "y": 20}}}}]}, configure)
        self.assertTrue(valid["success"])
        self.assertEqual(target.values["mode"], "Run")
        self.assertEqual(target.values["transform"], {"x": 10, "y": 20})

    def test_object_describe_functions_exposes_only_allowlist(self):
        class Context:
            def get_path_name(self):
                return "/Game/Test/Context.Context"

            def get_class(self):
                return types.SimpleNamespace(get_path_name=lambda: "/Script/EnhancedInput.InputMappingContext")

        context = Context()

        def configure(fake_unreal):
            fake_unreal.load_object = lambda outer, path: context

        result = self.run_document(
            {"format_version": "1.0", "commands": [{
                "id": "describe",
                "action": "object.describe_functions",
                "arguments": {"object": context.get_path_name()},
            }]},
            configure,
        )
        self.assertTrue(result["success"])
        functions = result["commands"][0]["data"]["functions"]
        self.assertEqual([item["name"] for item in functions], ["MapKey"])
        self.assertTrue(functions[0]["metadata"]["editor_safe"])
        self.assertEqual(
            [(item["name"], item["type"]) for item in functions[0]["returns"]],
            [("action", "object"), ("key", "struct"), ("triggers", "array"), ("modifiers", "array")],
        )

    def test_object_call_denied_function_does_not_modify(self):
        class Context:
            def __init__(self):
                self.called = False

            def get_path_name(self):
                return "/Game/Test/Context.Context"

            def get_class(self):
                return types.SimpleNamespace(get_path_name=lambda: "/Script/EnhancedInput.InputMappingContext")

            def modify(self):
                self.called = True

            def call_method(self, name, **kwargs):
                self.called = True

        context = Context()

        def configure(fake_unreal):
            fake_unreal.load_object = lambda outer, path: context
            fake_unreal.ScopedEditorTransaction = type("Transaction", (), {"__init__": lambda self, description: None, "cancel": lambda self: None})

        result = self.run_document(
            {"format_version": "1.0", "commands": [{
                "id": "call",
                "action": "object.call",
                "arguments": {"object": context.get_path_name(), "function": "Rename", "arguments": {}},
            }]},
            configure,
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["errors"][0]["code"], "function_not_allowed")
        self.assertFalse(context.called)
        self.assertEqual(result["changed_objects"], [])

    def test_object_call_validates_typed_object_before_modify(self):
        class Context:
            def __init__(self):
                self.modified = False
                self.calls = []

            def get_path_name(self):
                return "/Game/Test/Context.Context"

            def get_class(self):
                return types.SimpleNamespace(get_path_name=lambda: "/Script/EnhancedInput.InputMappingContext")

            def modify(self):
                self.modified = True

            def call_method(self, name, **kwargs):
                self.calls.append((name, kwargs))
                return None

        context = Context()
        wrong = types.SimpleNamespace(get_class=lambda: types.SimpleNamespace(get_path_name=lambda: "/Script/Engine.Object"))

        def configure(fake_unreal):
            fake_unreal.load_object = lambda outer, path: context if path == context.get_path_name() else wrong
            fake_unreal.ScopedEditorTransaction = type("Transaction", (), {"__init__": lambda self, description: None, "cancel": lambda self: None})

        result = self.run_document(
            {"format_version": "1.0", "commands": [{
                "id": "call",
                "action": "object.call",
                "arguments": {
                    "object": context.get_path_name(),
                    "function": "MapKey",
                    "arguments": {
                        "action": {"$type": "object", "path": "/Game/Test/Wrong.Wrong"},
                        "key": {"code": "K"},
                    },
                },
            }]},
            configure,
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["errors"][0]["code"], "function_argument_invalid")
        self.assertFalse(context.modified)
        self.assertEqual(context.calls, [])
    def test_object_call_serializes_structured_return(self):
        class Context:
            def get_path_name(self):
                return "/Game/Test/Context.Context"

            def get_class(self):
                return types.SimpleNamespace(get_path_name=lambda: "/Script/EnhancedInput.InputMappingContext")

            def modify(self):
                pass

            def call_method(self, name, **kwargs):
                return types.SimpleNamespace(
                    get_editor_property=lambda field: {
                        "action": action,
                        "key": {"key_name": "SpaceBar"},
                        "triggers": [],
                        "modifiers": [],
                    }[field]
                )

        action = types.SimpleNamespace(
            get_path_name=lambda: "/Game/Test/Action.Action",
            get_class=lambda: types.SimpleNamespace(get_path_name=lambda: "/Script/EnhancedInput.InputAction"),
        )
        context = Context()

        def configure(fake_unreal):
            fake_unreal.load_object = lambda outer, path: context if path == context.get_path_name() else action
            fake_unreal.ScopedEditorTransaction = type("Transaction", (), {"__init__": lambda self, description: None, "cancel": lambda self: None})
            fake_unreal.Key = lambda **fields: fields

        result = self.run_document(
            {"format_version": "1.0", "commands": [{
                "id": "call",
                "action": "object.call",
                "arguments": {
                    "object": context.get_path_name(),
                    "function": "MapKey",
                    "arguments": {
                        "action": {"$type": "object", "path": "/Game/Test/Action.Action"},
                        "to_key": {"$type": "struct", "class": "Key", "value": {"key_name": "SpaceBar"}},
                    },
                },
            }]},
            configure,
        )
        self.assertTrue(result["success"])
        returned = result["commands"][0]["data"]["return"]
        self.assertEqual(returned["action"], {"$type": "object", "path": "/Game/Test/Action.Action"})
        self.assertEqual(returned["key"]["key_name"], "SpaceBar")
        self.assertEqual(returned["triggers"], [])
        self.assertEqual(returned["modifiers"], [])
    def test_object_call_exception_cancels_transaction_without_changes(self):
        class Context:
            def __init__(self):
                self.modified = False

            def get_path_name(self):
                return "/Game/Test/Context.Context"

            def get_class(self):
                return types.SimpleNamespace(get_path_name=lambda: "/Script/EnhancedInput.InputMappingContext")

            def modify(self):
                self.modified = True

            def call_method(self, name, **kwargs):
                raise RuntimeError("native function failed")

        class Transaction:
            cancelled = False

            def __init__(self, description):
                self.description = description

            def cancel(self):
                type(self).cancelled = True

        context = Context()
        action = types.SimpleNamespace(
            get_class=lambda: types.SimpleNamespace(get_path_name=lambda: "/Script/EnhancedInput.InputAction")
        )

        def load_object(outer, path):
            return context if path == context.get_path_name() else action

        def configure(fake_unreal):
            fake_unreal.load_object = load_object
            fake_unreal.Key = lambda **fields: fields
            fake_unreal.ScopedEditorTransaction = Transaction

        result = self.run_document(
            {"format_version": "1.0", "commands": [{
                "id": "call",
                "action": "object.call",
                "arguments": {
                    "object": context.get_path_name(),
                    "function": "MapKey",
                    "arguments": {
                        "action": {"$type": "object", "path": "/Game/Test/Action.Action"},
                        "to_key": {"$type": "struct", "class": "Key", "value": {"key_name": "SpaceBar"}},
                    },
                },
            }]},
            configure,
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["errors"][0]["code"], "function_call_failed")
        self.assertTrue(Transaction.cancelled)
        self.assertTrue(context.modified)
        self.assertEqual(result["commands"][0]["changed_objects"], [])
        self.assertEqual(result["changed_objects"], [])


    def test_recipe_duplicate_nested_command_ids_fail_preflight(self):
        result = self.run_document({
            "format_version": "1.0",
            "commands": [{"id": "execute", "action": "recipe.execute", "arguments": {"recipe": {
                "commands": [
                    {"id": "same", "action": "level.inspect", "arguments": {}},
                    {"id": "same", "action": "level.inspect", "arguments": {}},
                ]
            }}}]
        })
        self.assertFalse(result["success"])
        self.assertEqual(result["errors"][0]["code"], "duplicate_command_id")
        self.assertEqual(result["changed_objects"], [])

    def test_recipe_forward_and_missing_references_fail_preflight(self):
        for reference in ("later.success", "missing.success"):
            with self.subTest(reference=reference):
                result = self.run_document({
                    "format_version": "1.0",
                    "commands": [{"id": "validate", "action": "recipe.validate", "arguments": {"recipe": {
                        "commands": [{"id": "first", "action": "level.inspect", "arguments": {"value": {"$ref": reference}}}]
                    }}}]
                })
                self.assertFalse(result["success"])
                self.assertEqual(result["errors"][0]["code"], "invalid_reference")
                self.assertEqual(result["changed_objects"], [])

    def test_recipe_rejects_invalid_condition_and_argument_shapes(self):
        cases = (({"condition": "yes"}, "invalid_condition"), ({"arguments": []}, "invalid_arguments"))
        for override, code in cases:
            with self.subTest(code=code):
                command = {"id": "invalid", "action": "level.inspect", "arguments": {}}
                command.update(override)
                result = self.run_document({
                    "format_version": "1.0",
                    "commands": [{"id": "validate", "action": "recipe.validate", "arguments": {"recipe": {"commands": [command]}}}]
                })
                self.assertFalse(result["success"])
                self.assertEqual(result["errors"][0]["code"], code)
                self.assertEqual(result["changed_objects"], [])

    def test_recipe_preflight_failure_reports_no_changed_objects(self):
        result = self.run_document({
            "format_version": "1.0",
            "commands": [{"id": "execute", "action": "recipe.execute", "arguments": {"recipe": {
                "commands": [{"id": "broken", "action": "level.inspect", "arguments": []}]
            }}}]
        })
        self.assertFalse(result["success"])
        self.assertEqual(result["errors"][0]["code"], "invalid_arguments")
        self.assertEqual(result["changed_objects"], [])

    def test_recipe_late_nested_failure_preserves_provenance_and_changed_objects(self):
        class ReflectedObject:
            def __init__(self, path):
                self.path = path
                self.values = {"label": "Before"}

            def get_path_name(self):
                return self.path

            def get_class(self):
                return types.SimpleNamespace(get_path_name=lambda: "/Script/Engine.Object")

            def get_editor_property_names(self):
                return ["label"]

            def get_editor_property(self, name):
                return self.values[name]

            def set_editor_property(self, name, value):
                self.values[name] = value

            def modify(self):
                pass

        target = ReflectedObject("/Game/Test/Object.Object")
        transactions = []

        class Transaction:
            def __init__(self, description):
                self.description = description
                self.cancelled = False
                transactions.append(self)

            def cancel(self):
                self.cancelled = True

        def configure(fake_unreal):
            fake_unreal.load_object = lambda outer, path: target
            fake_unreal.ScopedEditorTransaction = Transaction

        result = self.run_document({
            "format_version": "1.0",
            "commands": [{"id": "run", "action": "recipe.execute", "arguments": {"recipe": {
                "id": "late-failure",
                "commands": [
                    {"id": "write", "action": "object.set", "arguments": {"object": target.get_path_name(), "properties": {"label": "After"}}},
                    {"id": "fail", "action": "object.set", "arguments": {"object": target.get_path_name(), "properties": {"missing": "x"}}},
                ],
            }}}],
        }, configure)

        self.assertFalse(result["success"])
        self.assertEqual(result["commands"][0]["error_code"], "recipe_command_failed")
        self.assertEqual(result["commands"][0]["recipe_id"], "late-failure")
        self.assertEqual(result["commands"][0]["changed_objects"], [])
        details = result["errors"][0]["details"]
        self.assertEqual(details["recipe_id"], "late-failure")
        self.assertEqual(details["command_id"], "fail")
        self.assertEqual(details["commands"][0]["changed_objects"], [target.get_path_name()])
        self.assertEqual(result["changed_objects"], [])
        self.assertEqual(len(transactions), 1)
        self.assertTrue(transactions[0].cancelled)

    def test_recipe_patterns_expand_before_validation(self):
        result = self.run_document(
            {
                "format_version": "1.0",
                "dry_run": True,
                "commands": [],
                "recipes": [
                    {
                        "id": "patterns",
                        "conflict_mode": "reuse",
                        "commands": [
                            {
                                "$repeat": {
                                    "items": [
                                        {"name": "A", "limit": 3},
                                        {"name": "B", "limit": 4},
                                    ],
                                    "template": {
                                        "id": "repeat_${name}",
                                        "action": "level.inspect",
                                        "arguments": {"query": "${name}", "limit": "${limit}"},
                                    },
                                }
                            },
                            {
                                "$mirror": {
                                    "axis": "x",
                                    "item": {"name": "Left", "x": -10},
                                    "overrides": {"name": "Right"},
                                    "template": {
                                        "id": "mirror_${name}",
                                        "action": "level.inspect",
                                        "arguments": {"query": "x=${x}"},
                                    },
                                }
                            },
                            {
                                "$grid": {
                                    "axes": {"x": [-1, 1], "y": [10, 20]},
                                    "template": {
                                        "id": "grid_${x_index}_${y_index}",
                                        "action": "level.inspect",
                                        "arguments": {"query": "${x},${y}"},
                                    },
                                }
                            },
                        ],
                    }
                ],
            }
        )
        self.assertTrue(result["success"])
        self.assertEqual(len(result["plan"]), 8)
        self.assertEqual(result["recipes"][0]["conflict_mode"], "reuse")
        self.assertTrue(all(item["recipe_id"] == "patterns" for item in result["plan"]))
        self.assertTrue(
            all(item["arguments"]["conflict_mode"] == "reuse" for item in result["plan"])
        )
        repeat_a = next(item for item in result["plan"] if item["id"] == "repeat_A")
        self.assertEqual(repeat_a["arguments"]["limit"], 3)
        mirror_right = next(item for item in result["plan"] if item["id"] == "mirror_Right")
        self.assertEqual(mirror_right["arguments"]["query"], "x=10")

    def test_recipe_rejects_unknown_conflict_mode(self):
        result = self.run_document(
            {
                "format_version": "1.0",
                "recipes": [
                    {"id": "invalid", "conflict_mode": "overwrite", "commands": []}
                ],
            }
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["errors"][0]["code"], "invalid_conflict_mode")


    def test_recipe_validate_checks_parameter_schema(self):
        result = self.run_document({
            "format_version": "1.0",
            "commands": [{"id": "validate", "action": "recipe.validate", "arguments": {"recipe": {
                "parameters": {"count": 2},
                "parameter_schema": {"required": ["count"], "types": {"count": "number"}},
                "commands": [{"id": "inspect", "action": "level.inspect", "arguments": {"limit": "${count}"}}]
            }}}]
        })
        self.assertTrue(result["success"])
        self.assertEqual(result["commands"][0]["data"]["commands"][0]["arguments"]["limit"], 2)

    def test_result_reference_and_condition_skip_mutation(self):
        result = self.run_document({
            "format_version": "1.0",
            "dry_run": True,
            "commands": [
                {"id": "first", "action": "level.inspect", "arguments": {"limit": 1}},
                {"id": "second", "action": "level.inspect", "condition": {"ref": "first.success", "equals": True}, "arguments": {"limit": {"$ref": "first.success"}}}
            ]
        })
        self.assertTrue(result["success"])
        self.assertEqual(result["plan"][1]["arguments"]["limit"]["$ref"], "first.success")
    def test_asset_create_is_exposed_as_mutating_dry_run_action(self):
        result = self.run_document(
            {
                "format_version": "1.0",
                "dry_run": True,
                "commands": [
                    {
                        "id": "create",
                        "action": "asset.create",
                        "arguments": {
                            "folder": "/Game/Test",
                            "name": "DA_Test",
                            "class": "/Script/Engine.DataAsset",
                        },
                    }
                ],
            }
        )
        self.assertTrue(result["success"])
        self.assertTrue(result["plan"][0]["mutating"])
        self.assertEqual(result["plan"][0]["action"], "asset.create")

if __name__ == "__main__":
    unittest.main()
