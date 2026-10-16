import ast
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from unreal_harness.action_contract import ACTION_METADATA, ActionValidationError, describe_contract, validate_document
from unreal_harness.agent import AgentRun
from unreal_harness.execution import FileExecutionTransport, UnrealExecutionError
from unreal_harness.providers import OpenAICompatiblePlanner, ProviderError
from unreal_harness.verification import build_verification, evaluate_verification

ROOT = Path(__file__).resolve().parents[1]


def response(plan):
    return {"choices": [{"message": {"content": json.dumps(plan)}}]}


def batch(action, arguments=None):
    return {"format_version": "1.0", "commands": [{"id": "task", "action": action, "arguments": arguments or {}}]}


class PlanningSafetyTests(unittest.TestCase):
    def test_malformed_capabilities_stop_before_model_call(self):
        for commands in ([], [{"data": {"actions": []}}], [{"data": {"actions": [42]}}]):
            with self.subTest(commands=commands):
                class Transport:
                    def submit(self, document):
                        return {"success": True, "commands": commands}
                planner = unittest.mock.Mock()
                result = AgentRun(Transport(), planner).run("inspect")
                self.assertFalse(result["success"])
                self.assertEqual(result["phase"], "capabilities")
                planner.assert_not_called()

    def test_unavailable_verification_action_blocks_mutation(self):
        class Transport:
            calls = 0
            def submit(self, document):
                self.calls += 1
                return {"success": True, "commands": [{"data": {"actions": ["content.create_folder"]}}]}
        transport = Transport()
        result = AgentRun(transport, lambda task, context: batch("content.create_folder", {"path": "/Game/Test"})).run("create", verify=batch("content.list"))
        self.assertFalse(result["success"])
        self.assertEqual(transport.calls, 1)

    def test_absent_graph_bridge_is_excluded_from_model_catalog(self):
        contexts = []
        class Transport:
            def submit(self, document):
                return {"success": True, "commands": [{"data": {"graph_bridge_available": False}}]}
        def planner(task, context):
            contexts.append(context)
            return batch("level.inspect")
        AgentRun(Transport(), planner).run("inspect")
        self.assertTrue(contexts[0]["action_catalog"])
        self.assertFalse(any(m["name"].startswith(("blueprint.graph.", "graph.")) for m in contexts[0]["action_catalog"]))

    def test_verification_transport_error_preserves_execution_audit(self):
        class Transport:
            calls = 0
            def submit(self, document):
                self.calls += 1
                if self.calls == 4:
                    raise UnrealExecutionError("connection lost")
                return {"success": True, "audit_path": "executed.json", "changed_objects": ["/Game/Test"],
                        "commands": [{"id": c["id"], "success": True, "data": {"path": "/Game/Test"}} for c in document["commands"]]}
        result = AgentRun(Transport(), lambda task, context: batch("content.create_folder", {"path": "/Game/Test"})).run("create")
        self.assertFalse(result["success"])
        self.assertTrue(result["execution_succeeded"])
        self.assertEqual(result["result"]["audit_path"], "executed.json")
        self.assertEqual(result["verification"]["errors"][0]["message"], "connection lost")

    def test_skipped_explicit_verification_does_not_report_success(self):
        class Transport:
            calls = 0
            def submit(self, document):
                self.calls += 1
                return {"success": True, "commands": [{"id": c["id"], "success": True, "skipped": self.calls == 4, "data": {}} for c in document["commands"]]}
        result = AgentRun(Transport(), lambda task, context: batch("content.create_folder", {"path": "/Game/Test"})).run("create", verify=batch("content.list", {"path": "/Game/Test"}))
        self.assertFalse(result["success"])
        self.assertIn("skipped", result["error"])

    def test_contract_matches_runtime_handlers(self):
        tree = ast.parse((ROOT / "templates/unreal-project/Content/Python/execute_actions.py").read_text(encoding="utf-8"))
        mapping = next(n.value for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "ACTION_HANDLERS" for t in n.targets))
        self.assertEqual({k.value for k in mapping.keys}, set(ACTION_METADATA))

    def test_all_catalog_examples_validate(self):
        for meta in describe_contract():
            with self.subTest(action=meta["name"]):
                validate_document({"commands": [meta["example"]]})

    def test_nested_operation_and_scalar_types_fail_before_file_write(self):
        bad = [batch("blueprint.edit", {"blueprint": "/Game/BP.BP", "operations": [{"operation": "set_component_material", "component_name": "Body"}]}),
               batch("material.create", {"folder": "/Game/Materials", "name": "M_Test", "metallic": True}),
               batch("asset.save", {"assets": []}), batch("blueprint.create"),
               batch("level.inspect", {"limit": 0})]
        with tempfile.TemporaryDirectory() as directory:
            transport = FileExecutionTransport(directory, timeout=0.01)
            for document in bad:
                with self.subTest(document=document), self.assertRaises(UnrealExecutionError):
                    transport.submit(document)
                self.assertFalse(transport.input_path.exists())

    def test_recipe_late_invalid_command_is_preflight_error(self):
        document = batch("recipe.execute", {"recipe": {"commands": [
            {"id": "first", "action": "content.create_folder", "arguments": {"path": "/Game/Created"}},
            {"id": "bad", "action": "blueprint.create", "arguments": {"name": "BP_Test"}},
        ]}})
        with self.assertRaises(ActionValidationError):
            validate_document(document)

    def test_invalid_reference_and_condition_rejected(self):
        document = batch("object.inspect", {"object": {"$ref": "missing.data.path"}, "properties": ["tags"]})
        with self.assertRaises(ActionValidationError) as caught:
            validate_document(document)
        self.assertEqual(caught.exception.code, "invalid_reference")

    def test_planner_corrects_missing_arguments_with_previous_plan(self):
        planner = OpenAICompatiblePlanner(model="test")
        valid = batch("blueprint.create", {"folder": "/Game/Blueprints", "name": "BP_Test", "parent_class": "/Script/Engine.Actor"})
        with patch.object(planner, "_request", side_effect=[response(batch("blueprint.create")), response(valid)]) as request:
            self.assertEqual(planner("create blueprint", {"capabilities": {"actions": ["blueprint.create"]}}), valid)
        messages = request.call_args_list[1].args[0]["messages"]
        self.assertEqual(messages[-2]["role"], "assistant")
        self.assertIn("folder", messages[-1]["content"])
        supplied = json.loads(messages[1]["content"])["action_catalog"][0]
        self.assertIn("parent_class", supplied["arguments_schema"]["required"])

    def test_malformed_json_gets_bounded_corrections(self):
        planner = OpenAICompatiblePlanner(model="test")
        malformed = {"choices": [{"message": {"content": "not json"}}]}
        with patch.object(planner, "_request", return_value=malformed) as request:
            with self.assertRaises(ProviderError):
                planner("inspect", {"capabilities": {"actions": ["level.inspect"]}})
        self.assertEqual(request.call_count, 3)

    def test_read_only_task_actually_reads_unreal(self):
        calls = []
        class Transport:
            def submit(self, document):
                calls.append(document)
                if len(calls) == 1:
                    return {"success": True, "commands": [{"data": {"actions": ["level.inspect"]}}]}
                if document.get("dry_run"):
                    return {"success": True, "commands": [], "plan": document["commands"]}
                return {"success": True, "commands": [{"success": True, "data": {"total": 1, "actors": [{"name": "PlayerStart"}]}}]}
        result = AgentRun(Transport(), lambda task, context: batch("level.inspect")).run("inspect")
        self.assertTrue(result["success"])
        self.assertEqual(len(calls), 3)
        self.assertFalse(calls[-1]["dry_run"])
        self.assertEqual(result["result"]["commands"][0]["data"]["total"], 1)

    def test_explicit_read_only_mode_rejects_model_mutation(self):
        calls = []
        class Transport:
            def submit(self, doc):
                calls.append(doc)
                return {"success": True, "commands": [{"data": {}}]}
        plan = batch("content.create_folder", {"path": "/Game/Unexpected"})
        result = AgentRun(Transport(), lambda task, context: plan).run("inspect", read_only=True)
        self.assertFalse(result["success"])
        self.assertEqual(len(calls), 1)

    def test_mutating_verification_rejected_before_execution(self):
        class Transport:
            calls = 0
            def submit(self, doc):
                self.calls += 1
                return {"success": True, "commands": [{"data": {}}]}
        transport = Transport()
        result = AgentRun(transport, lambda task, context: batch("content.create_folder", {"path": "/Game/Test"})).run(
            "create", verify=batch("project.save"))
        self.assertFalse(result["success"])
        self.assertEqual(transport.calls, 1)

    def test_material_read_back_checks_values_not_only_existence(self):
        plan = batch("material.create", {"folder": "/Game/Materials", "name": "M_Test", "metallic": 1})
        executed = {"commands": [{"id": "task", "success": True, "data": {"asset_path": "/Game/Materials/M_Test.M_Test"}}]}
        document, checks, uncovered = build_verification(plan, executed)
        self.assertEqual(document["commands"][0]["action"], "material.inspect")
        self.assertFalse(uncovered)
        data = {"scalar_parameters": [{"name": "Metallic", "value": 0}, {"name": "Roughness", "value": 0.5}],
                "vector_parameters": [{"name": "BaseColor", "value": [0.18, 0.2, 0.22, 1]}]}
        verification = {"commands": [{"id": "verify_0", "success": True, "data": data}]}
        self.assertTrue(evaluate_verification(verification, checks))
        data["scalar_parameters"][0]["value"] = 1
        self.assertFalse(evaluate_verification(verification, checks))

    def test_verification_resolves_result_references(self):
        plan = {"commands": [
            {"id": "create", "action": "asset.create", "arguments": {"folder": "/Game", "name": "A", "class": "/Script/Engine.Material"}},
            {"id": "set", "action": "object.set", "arguments": {"object": {"$ref": "create.data.asset_path"}, "properties": {"two_sided": True}}},
        ]}
        executed = {"commands": [
            {"id": "create", "success": True, "data": {"asset_path": "/Game/A.A"}},
            {"id": "set", "success": True, "data": {}},
        ]}
        document, _, uncovered = build_verification(plan, executed)
        self.assertFalse(uncovered)
        self.assertEqual(document["commands"][1]["arguments"]["object"], "/Game/A.A")

    def test_missing_execution_identity_reports_unverified_changes(self):
        _, _, uncovered = build_verification(batch("material.create", {"folder": "/Game", "name": "M"}),
                                             {"commands": [{"id": "task", "success": True, "data": {}}]})
        self.assertIn("asset_path", uncovered[0])

    def test_repeated_blueprint_edits_verify_final_component_state(self):
        plan = {"commands": [
            {"id": "first", "action": "blueprint.edit", "arguments": {"blueprint": "/Game/BP.BP", "operations": [
                {"operation": "set_component_transform", "component_name": "Mesh", "location": [1, 2, 3], "scale": [2, 2, 2]}]}},
            {"id": "last", "action": "blueprint.edit", "arguments": {"blueprint": "/Game/BP.BP", "operations": [
                {"operation": "set_component_transform", "component_name": "Mesh", "location": [4, 5, 6]}]}},
        ]}
        executed = {"commands": [{"id": c["id"], "success": True, "data": {}} for c in plan["commands"]]}
        document, checks, uncovered = build_verification(plan, executed)
        self.assertFalse(uncovered)
        self.assertEqual(len(document["commands"]), 1)
        result = {"commands": [{"id": "verify_0", "success": True, "data": {"components": [
            {"name": "Mesh_GEN_VARIABLE", "relative_location": [4, 5, 6], "relative_scale": [2, 2, 2]}]}}]}
        self.assertFalse(evaluate_verification(result, checks))
        result["commands"][0]["data"]["components"][0]["relative_scale"] = [1, 1, 1]
        self.assertTrue(evaluate_verification(result, checks))

    def test_repeated_material_parameters_keep_final_values(self):
        plan = {"commands": [
            {"id": "first", "action": "material_instance.set_parameters", "arguments": {"material_instance": "/Game/MI.MI", "scalar": {"A": 1, "B": 2}}},
            {"id": "last", "action": "material_instance.set_parameters", "arguments": {"material_instance": "/Game/MI.MI", "scalar": {"A": 3}}},
        ]}
        executed = {"commands": [{"id": c["id"], "success": True, "data": {}} for c in plan["commands"]]}
        document, checks, uncovered = build_verification(plan, executed)
        self.assertEqual(len(document["commands"]), 1)
        self.assertFalse(uncovered)
        self.assertEqual(checks[0]["expected"]["parameters"]["scalar"], {"A": 3, "B": 2})


if __name__ == "__main__":
    unittest.main()
